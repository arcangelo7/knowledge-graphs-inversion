#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from rdflib import Graph, Namespace
from rdflib.namespace import RDF
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.disable(logging.CRITICAL)

import rmlmapper  # noqa: E402
from benchmarks.krown_plots import plot_timing_charts  # noqa: E402
from benchmarks.krown_stats import (  # noqa: E402
    aggregate_scenario_statistics,
    calculate_timing_statistics,
)
from benchmarks.krown_validator import KrownValidator  # noqa: E402
from kgi.core import reconstruct  # noqa: E402
from kgi.models import ReconstructedTable  # noqa: E402

console = Console(width=max(shutil.get_terminal_size().columns, 100))

R2RML = Namespace("http://www.w3.org/ns/r2rml#")
SPARQL_ENGINE = "pyoxigraph"


@dataclass(frozen=True)
class KrownScenario:
    name: str
    rows: int
    properties: int
    value_size: int

    @property
    def generated_name(self) -> str:
        return f"raw_{self.rows}_{self.properties}_{self.value_size}"

    @property
    def columns(self) -> int:
        return self.properties + 1

    @property
    def expected_rdf_triples(self) -> int:
        return self.rows * self.properties

    def parameters(self) -> dict[str, int]:
        return {
            "number_of_members": self.rows,
            "number_of_properties": self.properties,
            "value_size": self.value_size,
            "number_of_columns": self.columns,
        }


@dataclass(frozen=True)
class KrownSeries:
    name: str
    title: str
    parameter: str
    parameter_label: str
    scenario_names: tuple[str, str, str]


SCENARIOS = (
    KrownScenario("rows_low", 1_000, 5, 100),
    KrownScenario("baseline", 10_000, 5, 100),
    KrownScenario("rows_high", 50_000, 5, 100),
    KrownScenario("properties_low", 10_000, 3, 100),
    KrownScenario("properties_high", 10_000, 8, 100),
    KrownScenario("value_size_low", 10_000, 5, 50),
    KrownScenario("value_size_high", 10_000, 5, 150),
)
SERIES = (
    KrownSeries(
        "rows",
        "Rows",
        "number_of_members",
        "Rows",
        ("rows_low", "baseline", "rows_high"),
    ),
    KrownSeries(
        "properties",
        "Properties",
        "number_of_properties",
        "Properties",
        ("properties_low", "baseline", "properties_high"),
    ),
    KrownSeries(
        "value_size",
        "Value size",
        "value_size",
        "Value size (characters)",
        ("value_size_low", "baseline", "value_size_high"),
    ),
)
SCENARIOS_BY_NAME = {scenario.name: scenario for scenario in SCENARIOS}
SCENARIOS_BY_GENERATED_NAME = {
    scenario.generated_name: scenario for scenario in SCENARIOS
}


def generate_scenarios(
    config_file: Path, scenarios_root: Path, data_generator_dir: Path
) -> None:
    if scenarios_root.exists():
        shutil.rmtree(scenarios_root)
    scenarios_root.mkdir(parents=True)

    subprocess.run(
        [
            sys.executable,
            str(data_generator_dir / "exgentool"),
            "generate",
            f"--scenario={config_file.resolve()}",
            f"--root={scenarios_root.resolve()}",
        ],
        cwd=data_generator_dir,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _format_confidence_interval(statistics: dict) -> str:
    mean = statistics["mean"]
    lower = statistics["ci_95_lower"]
    upper = statistics["ci_95_upper"]
    margin = max(mean - lower, upper - mean)
    return f"{mean:.2f}±{margin:.2f}"


def _format_percentage_confidence_interval(statistics: dict) -> str:
    mean = statistics["mean"]
    lower = statistics["ci_95_lower"]
    upper = statistics["ci_95_upper"]
    margin = max(mean - lower, upper - mean)
    return f"{mean:.1f}±{margin:.1f}%"


class KrownBenchmarkRunner:
    def __init__(self, cleanup_tables: bool = True, iterations: int = 1):
        project_root = Path(__file__).resolve().parent.parent
        self.data_generator_dir = project_root / "KROWN" / "data-generator"
        benchmark_dir = Path(__file__).resolve().parent / "krown"
        self.config_file = benchmark_dir / "config" / "kg-inversion-benchmark.json"
        self.scenarios_root = benchmark_dir / "scenarios"
        self.results_dir = benchmark_dir / "results"
        self.cleanup_tables = cleanup_tables
        self.iterations = iterations
        self.connection_string = (
            f"postgresql://{os.environ['BENCHMARK_DB_USER']}:"
            f"{os.environ['BENCHMARK_DB_PASSWORD']}@"
            f"{os.environ['BENCHMARK_DB_HOST']}:"
            f"{os.environ['BENCHMARK_DB_PORT']}/"
            f"{os.environ['BENCHMARK_DB_NAME']}"
        )
        self.validator = KrownValidator(self.connection_string)

    def prepare_results_directory(self) -> None:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        for path in self.results_dir.iterdir():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    def find_krown_scenarios(self) -> list[Path]:
        discovered: dict[str, list[Path]] = {}
        for metadata_file in self.scenarios_root.rglob("metadata.json"):
            scenario_path = metadata_file.parent
            discovered.setdefault(scenario_path.name, []).append(scenario_path)

        if set(discovered) != set(SCENARIOS_BY_GENERATED_NAME) or any(
            len(paths) != 1 for paths in discovered.values()
        ):
            raise ValueError(
                "KROWN must generate exactly the seven configured RawData scenarios"
            )

        return [discovered[scenario.generated_name][0] for scenario in SCENARIOS]

    @staticmethod
    def mapping_component_counts(mapping_file: Path) -> tuple[int, int]:
        graph = Graph()
        graph.parse(mapping_file)
        triples_maps = set(graph.subjects(RDF.type, R2RML.TriplesMap))
        predicate_object_maps = set(graph.subjects(RDF.type, R2RML.PredicateObjectMap))
        return len(triples_maps), len(predicate_object_maps)

    @staticmethod
    def count_rdf_triples(rdf_file: Path) -> int:
        with rdf_file.open(encoding="utf-8") as file:
            return sum(1 for line in file if line.strip())

    def execute_krown_scenario(self, scenario_path: Path) -> dict:
        scenario = SCENARIOS_BY_GENERATED_NAME[scenario_path.name]
        metadata = json.loads(
            (scenario_path / "metadata.json").read_text(encoding="utf-8")
        )
        shared_dir = scenario_path / "data" / "shared"
        started = time.perf_counter()

        self.execute_load_rdb_step(metadata, shared_dir, scenario)
        rmlmapper_time = self.execute_forward_mapping_step(metadata, shared_dir)
        rdf_file = shared_dir / "out.nt"
        rdf_triples = self.count_rdf_triples(rdf_file)
        if rdf_triples != scenario.expected_rdf_triples:
            raise ValueError(
                f"Unexpected RDF triple count for {scenario.name}: "
                f"expected={scenario.expected_rdf_triples}, actual={rdf_triples}"
            )

        inversion_results, inversion_time = self.execute_inversion_step(
            metadata, shared_dir
        )
        reconstructed_table_names = [result.name for result in inversion_results]
        if reconstructed_table_names != ["data"]:
            raise ValueError(
                f"Unexpected reconstructed tables for {scenario.name}: "
                f"{reconstructed_table_names}"
            )
        self.materialize_reconstructed_tables(scenario.name, inversion_results)
        validation_results = self.validator.validate_inversion(
            original_table=f"{scenario.name}_original_data",
            reconstructed_table=f"{scenario.name}_data",
            scenario_name=scenario.name,
        )
        if validation_results["outcome"] != "FULL":
            raise ValueError(
                "Exact reconstruction validation failed: "
                f"{validation_results['checks']}"
            )
        total_time = time.perf_counter() - started
        inversion_overhead_percentage = inversion_time / rmlmapper_time * 100

        mapping_file = shared_dir / "mapping.r2rml.ttl"
        data_file = shared_dir / "data.csv"
        triples_maps, predicate_object_maps = self.mapping_component_counts(
            mapping_file
        )
        if (triples_maps, predicate_object_maps) != (1, scenario.properties):
            raise ValueError(
                f"Unexpected mapping structure for {scenario.name}: "
                f"triples_maps={triples_maps}, "
                f"predicate_object_maps={predicate_object_maps}"
            )

        return {
            "scenario_name": scenario.name,
            "generated_scenario_name": scenario.generated_name,
            "parameters": scenario.parameters(),
            "execution_time": total_time,
            "timing_breakdown": {
                "rmlmapper_time": rmlmapper_time,
                "inversion_time": inversion_time,
                "inversion_overhead_percentage": inversion_overhead_percentage,
                "total_time": total_time,
            },
            "throughput": {
                "rows_per_second": scenario.rows / inversion_time,
                "cells_per_second": scenario.rows * scenario.columns / inversion_time,
            },
            "mapping_file": str(mapping_file),
            "data_file": str(data_file),
            "mapping_size_bytes": mapping_file.stat().st_size,
            "data_size_bytes": data_file.stat().st_size,
            "rdf_triples": rdf_triples,
            "triples_maps_count": triples_maps,
            "predicate_object_maps_count": predicate_object_maps,
            "inversion_count": len(inversion_results),
            "validation_results": validation_results,
        }

    @staticmethod
    def _metadata_step(metadata: dict, command: str) -> dict:
        matching_steps = [
            step for step in metadata["steps"] if step["command"] == command
        ]
        if len(matching_steps) != 1:
            raise ValueError(f"Expected one {command} step in KROWN metadata")
        return matching_steps[0]

    def execute_load_rdb_step(
        self,
        metadata: dict,
        shared_dir: Path,
        scenario: KrownScenario,
    ) -> None:
        load_step = self._metadata_step(metadata, "load")
        parameters = load_step["parameters"]
        csv_file = shared_dir / parameters["csv_file"]
        table_name = parameters["table"]
        data = pd.read_csv(csv_file)
        expected_columns = ["id"] + [
            f"p{number}" for number in range(1, scenario.properties + 1)
        ]
        if list(data.columns) != expected_columns:
            raise ValueError(
                f"Unexpected CSV columns for {scenario.name}: {list(data.columns)}"
            )
        if len(data) != scenario.rows:
            raise ValueError(
                f"Unexpected CSV row count for {scenario.name}: {len(data)}"
            )

        engine = create_engine(self.connection_string)
        try:
            data.to_sql(
                f"{scenario.name}_original_{table_name}",
                engine,
                if_exists="replace",
                index=False,
            )
            self.load_source_table_with_id_pk(engine, table_name, data)
        finally:
            engine.dispose()

    @staticmethod
    def load_source_table_with_id_pk(
        engine: Engine, table_name: str, data: pd.DataFrame
    ) -> None:
        columns = list(data.columns)
        column_definitions = ["id INTEGER PRIMARY KEY"]
        column_definitions.extend(f'"{column}" TEXT' for column in columns[1:])
        with engine.begin() as connection:
            connection.execute(text(f'DROP TABLE IF EXISTS "{table_name}"'))
            connection.execute(
                text(f'CREATE TABLE "{table_name}" ({", ".join(column_definitions)})')
            )
        data.to_sql(table_name, engine, if_exists="append", index=False)

    def execute_forward_mapping_step(self, metadata: dict, shared_dir: Path) -> float:
        mapping_step = self._metadata_step(metadata, "execute_mapping")
        parameters = mapping_step["parameters"]
        mapping_file = shared_dir / parameters["mapping_file"]
        output_file = shared_dir / parameters["output_file"]
        started = time.perf_counter()

        sqlalchemy_url = self.connection_string.replace(
            "postgresql://", "postgresql+psycopg2://"
        )
        jdbc_dsn, username, password = rmlmapper.sqlalchemy_to_jdbc(sqlalchemy_url)
        return_code = rmlmapper.run(
            str(mapping_file),
            str(output_file),
            dsn=jdbc_dsn,
            username=username,
            password=password,
            timeout=1800,
        )
        if return_code != 0:
            raise RuntimeError(f"RMLMapper failed with exit code {return_code}")
        return time.perf_counter() - started

    def execute_inversion_step(
        self, metadata: dict, shared_dir: Path
    ) -> tuple[list[ReconstructedTable], float]:
        self.clear_loaded_tables(metadata)
        started = time.perf_counter()
        source_db_url = self.connection_string.replace(
            "postgresql://", "postgresql+psycopg2://"
        )
        inversion_results = reconstruct(
            mapping=str(shared_dir / "mapping.r2rml.ttl"),
            rdf_graph=str(shared_dir / "out.nt"),
            source_db_url=source_db_url,
        )
        return inversion_results, time.perf_counter() - started

    def clear_loaded_tables(self, metadata: dict) -> None:
        load_step = self._metadata_step(metadata, "load")
        table_name = load_step["parameters"]["table"]
        engine = create_engine(self.connection_string)
        try:
            with engine.begin() as connection:
                connection.execute(text(f'TRUNCATE TABLE "{table_name}"'))
        finally:
            engine.dispose()

    def materialize_reconstructed_tables(
        self, scenario_name: str, results: list[ReconstructedTable]
    ) -> None:
        engine = create_engine(self.connection_string)
        try:
            for result in results:
                result.data.to_sql(
                    f"{scenario_name}_{result.name}",
                    engine,
                    if_exists="replace",
                    index=False,
                )
        finally:
            engine.dispose()

    @staticmethod
    def _series_data() -> list[dict]:
        return [
            {
                "name": series.name,
                "title": series.title,
                "parameter": series.parameter,
                "parameter_label": series.parameter_label,
                "scenarios": list(series.scenario_names),
            }
            for series in SERIES
        ]

    def save_results(
        self, scenario_runs: dict[str, list[dict]]
    ) -> tuple[Path, Path, dict]:
        timestamp = int(time.time())
        raw_file = self.results_dir / f"krown_benchmark_results_raw_{timestamp}.json"
        stats_file = (
            self.results_dir / f"krown_benchmark_results_stats_{timestamp}.json"
        )
        raw_data = {
            "timestamp": timestamp,
            "benchmark_type": "KROWN RawData",
            "framework": "Knowledge Graph Inversion",
            "environment": "Docker",
            "sparql_engine": SPARQL_ENGINE,
            "iterations": self.iterations,
            "series": self._series_data(),
            "scenarios": scenario_runs,
        }
        raw_file.write_text(json.dumps(raw_data, indent=2) + "\n", encoding="utf-8")

        aggregated_scenarios = {}
        for scenario in SCENARIOS:
            runs = scenario_runs[scenario.name]
            statistics = aggregate_scenario_statistics(runs)
            throughputs = [run["throughput"] for run in runs]
            statistics["rows_per_second"] = calculate_timing_statistics(
                [throughput["rows_per_second"] for throughput in throughputs]
            )
            statistics["cells_per_second"] = calculate_timing_statistics(
                [throughput["cells_per_second"] for throughput in throughputs]
            )
            metadata = statistics["metadata"]
            metadata.update(
                {
                    "rdf_triples": scenario.expected_rdf_triples,
                    "number_of_members": scenario.rows,
                    "number_of_properties": scenario.properties,
                    "value_size": scenario.value_size,
                    "number_of_columns": scenario.columns,
                }
            )
            aggregated_scenarios[scenario.name] = {
                "parameters": scenario.parameters(),
                "raw_runs": runs,
                "statistics": statistics,
            }

        stats_data = {
            "timestamp": timestamp,
            "benchmark_type": "KROWN RawData",
            "framework": "Knowledge Graph Inversion",
            "environment": "Docker",
            "sparql_engine": SPARQL_ENGINE,
            "iterations": self.iterations,
            "series": self._series_data(),
            "scenarios": aggregated_scenarios,
        }
        stats_file.write_text(json.dumps(stats_data, indent=2) + "\n", encoding="utf-8")
        return raw_file, stats_file, stats_data

    def print_aggregated_summary(self, stats_data: dict) -> None:
        scenarios_data = stats_data["scenarios"]
        iteration_label = "iteration" if self.iterations == 1 else "iterations"
        for series in SERIES:
            table = Table(
                title=(
                    f"KROWN {series.title} "
                    f"({SPARQL_ENGINE}, {self.iterations} {iteration_label})"
                )
            )
            table.add_column("Parameter", justify="right")
            table.add_column("CSV MiB", justify="right")
            table.add_column("RDF triples", justify="right")
            table.add_column("RMLMapper", justify="right")
            table.add_column("Inversion", justify="right")
            table.add_column("Overhead", justify="right")
            table.add_column("Rows/s", justify="right")
            table.add_column("Cells/s", justify="right")

            for scenario_name in series.scenario_names:
                scenario = SCENARIOS_BY_NAME[scenario_name]
                statistics = scenarios_data[scenario_name]["statistics"]
                metadata = statistics["metadata"]
                parameter_value = scenario.parameters()[series.parameter]
                data_size_mib = metadata["data_size_bytes"] / (1024**2)
                table.add_row(
                    f"{parameter_value:,}",
                    f"{data_size_mib:.2f}",
                    f"{metadata['rdf_triples']:,}",
                    _format_confidence_interval(statistics["rmlmapper_time"]),
                    _format_confidence_interval(statistics["inversion_time"]),
                    _format_percentage_confidence_interval(
                        statistics["inversion_overhead_percentage"]
                    ),
                    f"{statistics['rows_per_second']['mean']:,.0f}",
                    f"{statistics['cells_per_second']['mean']:,.0f}",
                )
            console.print(table)

    def cleanup(self) -> None:
        self.validator.dispose()
        if not self.cleanup_tables:
            return

        engine = create_engine(self.connection_string)
        try:
            self.cleanup_database_tables(engine)
        finally:
            engine.dispose()

    @staticmethod
    def cleanup_database_tables(engine: Engine) -> None:
        with engine.begin() as connection:
            table_rows = connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            for row in table_rows:
                connection.execute(text(f'DROP TABLE IF EXISTS "{row[0]}" CASCADE'))

    def run_benchmark(self) -> int:
        console.print(f"Starting KROWN RawData benchmark ({SPARQL_ENGINE})")
        try:
            self.prepare_results_directory()
            generate_scenarios(
                self.config_file, self.scenarios_root, self.data_generator_dir
            )
            scenarios = self.find_krown_scenarios()

            scenario_runs: dict[str, list[dict]] = {
                scenario.name: [] for scenario in SCENARIOS
            }
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                iteration_task = progress.add_task("Iterations", total=self.iterations)
                for iteration in range(1, self.iterations + 1):
                    progress.update(
                        iteration_task,
                        description=f"Iteration {iteration}/{self.iterations}",
                    )
                    scenario_task = progress.add_task("Scenarios", total=len(scenarios))
                    for scenario_path in scenarios:
                        scenario = SCENARIOS_BY_GENERATED_NAME[scenario_path.name]
                        progress.update(scenario_task, description=scenario.name)
                        result = self.execute_krown_scenario(scenario_path)
                        scenario_runs[scenario.name].append(result)
                        progress.advance(scenario_task)
                    progress.remove_task(scenario_task)
                    progress.advance(iteration_task)

            raw_file, stats_file, stats_data = self.save_results(scenario_runs)
            console.print(f"Raw results saved to {raw_file}")
            console.print(f"Statistics saved to {stats_file}")

            self.print_aggregated_summary(stats_data)
            plot_files = plot_timing_charts(stats_data, self.results_dir)
            for plot_file in plot_files:
                console.print(f"Plot saved to {plot_file}")
            console.print("Benchmark completed with FULL results")
            return 0
        finally:
            self.cleanup()


def positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Iterations must be positive")
    return parsed


def main() -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Run the fully invertible KROWN RawData benchmark"
    )
    parser.add_argument(
        "--iterations",
        type=positive_integer,
        default=1,
        help="Number of times to run each scenario",
    )
    args = parser.parse_args()
    runner = KrownBenchmarkRunner(iterations=args.iterations)
    return runner.run_benchmark()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

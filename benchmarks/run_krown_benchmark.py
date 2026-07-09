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
from pathlib import Path
from typing import Literal

import pandas as pd
from rdflib import Graph, Namespace
from rdflib.namespace import RDF
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    MofNCompleteColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).parent.parent))

# Suppress all console logging - Rich handles console output
logging.disable(logging.CRITICAL)

import rmlmapper  # noqa: E402
from benchmarks.krown_validator import KrownValidator  # noqa: E402
from benchmarks.krown_stats import aggregate_scenario_statistics  # noqa: E402
from kgi.core import reconstruct  # noqa: E402
from kgi.exceptions import NonInvertibleError  # noqa: E402
from kgi.models import ReconstructedTable  # noqa: E402

from benchmarks.krown_plots import plot_timing_bar_charts  # noqa: E402

console = Console()

ExpectedOutcome = Literal["partial"]
R2RML = Namespace("http://www.w3.org/ns/r2rml#")
SPARQL_ENGINE = "pyoxigraph"


def expected_outcome(scenario_name: str) -> ExpectedOutcome:
    parts = scenario_name.split("_")
    if len(parts) != 3 or parts[0] != "mappings":
        raise ValueError(
            "KROWN benchmark only supports Mappings scenarios named "
            f"mappings_{{tms}}_{{poms}}: {scenario_name}"
        )

    tms, poms = (int(part) for part in parts[1:3])
    if tms >= poms:
        raise ValueError(
            "KROWN benchmark only supports partial Mappings scenarios with "
            f"tms < poms: {scenario_name}"
        )
    return "partial"


def generate_scenarios(
    config_file: Path, scenarios_root: Path, data_generator_dir: Path
) -> None:
    if scenarios_root.exists():
        shutil.rmtree(scenarios_root)
    scenarios_root.mkdir(parents=True)

    process = subprocess.run(
        [
            sys.executable,
            str(data_generator_dir / "exgentool"),
            "generate",
            f"--scenario={config_file.resolve()}",
            f"--root={scenarios_root.resolve()}",
        ],
        cwd=data_generator_dir,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise RuntimeError(
            "KROWN data generation failed with exit code "
            f"{process.returncode}:\n{process.stdout}\n{process.stderr}"
        )


class KrownBenchmarkRunner:
    def __init__(
        self,
        validate: bool = False,
        cleanup_tables: bool = True,
        iterations: int = 1,
    ):
        self.project_root = Path(__file__).parent.parent
        self.krown_dir = self.project_root / "KROWN"
        self.data_generator_dir = self.krown_dir / "data-generator"
        self.config_file = (
            Path(__file__).parent / "krown" / "config" / "kg-inversion-benchmark.json"
        )
        self.scenarios_root = Path(__file__).parent / "krown" / "scenarios"
        self.validate = validate
        self.cleanup_tables = cleanup_tables
        self.iterations = iterations
        self.validator: KrownValidator | None = None

        self.db_config = {
            "host": os.environ["BENCHMARK_DB_HOST"],
            "port": os.environ["BENCHMARK_DB_PORT"],
            "user": os.environ["BENCHMARK_DB_USER"],
            "password": os.environ["BENCHMARK_DB_PASSWORD"],
            "database": os.environ["BENCHMARK_DB_NAME"],
        }

    def get_connection_string(self) -> str:
        return (
            f"postgresql://{self.db_config['user']}:{self.db_config['password']}@"
            f"{self.db_config['host']}:{self.db_config['port']}/{self.db_config['database']}"
        )

    def run_krown_data_generation(self) -> None:
        generate_scenarios(
            self.config_file, self.scenarios_root, self.data_generator_dir
        )

    def find_krown_scenarios(self) -> list[Path]:
        if not self.scenarios_root.exists():
            return []
        return sorted(p.parent for p in self.scenarios_root.rglob("metadata.json"))

    @staticmethod
    def mapping_component_counts(mapping_file: Path) -> tuple[int, int]:
        graph = Graph()
        graph.parse(mapping_file)
        triples_maps = set(graph.subjects(RDF.type, R2RML.TriplesMap))
        predicate_object_maps = set(graph.subjects(RDF.type, R2RML.PredicateObjectMap))
        return len(triples_maps), len(predicate_object_maps)

    def outcome_matches_expectation(self, result: dict) -> bool:
        expected_outcome(result["scenario_name"])
        if result["status"] != "completed":
            return False
        if not self.validate:
            return True
        validation = result["validation_results"]
        if not validation["validation_passed"]:
            return False
        return validation["lost_columns"] == ["id"]

    @staticmethod
    def outcome_cell(result: dict) -> str:
        if result["outcome_matches_expectation"]:
            return "[yellow]PARTIAL (expected)[/yellow]"
        if result["status"] == "failed":
            kind = result["failure_kind"]
            label = "NON-INVERTIBLE" if kind == "non_invertible" else "FAILED"
            return f"[red]{label} (unexpected)[/red]"
        return "[red]UNEXPECTED[/red]"

    def execute_and_classify_scenario(self, scenario_path: Path) -> dict:
        result = self.execute_krown_scenario(scenario_path)
        result["expected_outcome"] = expected_outcome(result["scenario_name"])
        result["outcome_matches_expectation"] = self.outcome_matches_expectation(result)
        return result

    def execute_krown_scenario(self, scenario_path: Path) -> dict:
        scenario_name = scenario_path.name

        metadata_file = scenario_path / "metadata.json"
        with open(metadata_file, "r") as f:
            metadata = json.load(f)

        shared_dir = scenario_path / "data" / "shared"
        start_time = time.time()

        try:
            self.execute_load_rdb_step(metadata, shared_dir, scenario_name)
            rmlmapper_time = self.execute_forward_mapping_step(metadata, shared_dir)
            inversion_results, inversion_time = self.execute_inversion_step(
                metadata, shared_dir
            )

            validation_results = None
            if self.validate:
                self.materialize_reconstructed_tables(scenario_name, inversion_results)
                validation_results = self.validate_scenario(scenario_name)

            end_time = time.time()
            total_time = end_time - start_time

            inversion_overhead_percentage = (
                (inversion_time / rmlmapper_time * 100) if rmlmapper_time > 0 else 0
            )

            mapping_file = shared_dir / "mapping.r2rml.ttl"
            data_file = shared_dir / "data.csv"

            tm_count, pom_count = self.mapping_component_counts(mapping_file)
            inv_count = len(inversion_results)

            return {
                "status": "completed",
                "scenario_name": scenario_name,
                "execution_time": total_time,
                "timing_breakdown": {
                    "rmlmapper_time": rmlmapper_time,
                    "inversion_time": inversion_time,
                    "inversion_overhead_percentage": inversion_overhead_percentage,
                    "total_time": total_time,
                },
                "mapping_file": str(mapping_file),
                "data_file": str(data_file),
                "mapping_size_bytes": mapping_file.stat().st_size,
                "data_size_bytes": data_file.stat().st_size,
                "triples_maps_count": tm_count,
                "predicate_object_maps_count": pom_count,
                "inversion_count": inv_count,
                "validation_results": validation_results,
            }

        except NonInvertibleError as e:
            end_time = time.time()
            return {
                "status": "failed",
                "failure_kind": "non_invertible",
                "scenario_name": scenario_name,
                "execution_time": end_time - start_time,
                "error": str(e),
            }
        except Exception as e:
            end_time = time.time()
            return {
                "status": "failed",
                "failure_kind": "runtime_error",
                "scenario_name": scenario_name,
                "execution_time": end_time - start_time,
                "error": str(e),
            }

    def execute_load_rdb_step(
        self, metadata: dict, shared_dir: Path, scenario_name: str | None = None
    ) -> None:
        load_step = None
        for step in metadata["steps"]:
            if step["command"] == "load":
                load_step = step
                break

        if not load_step:
            raise ValueError("No load step found in metadata")

        csv_file = shared_dir / load_step["parameters"]["csv_file"]
        table_name = load_step["parameters"]["table"]

        if not csv_file.exists():
            raise FileNotFoundError(f"CSV file not found: {csv_file}")

        conn_string = self.get_connection_string()
        df = pd.read_csv(csv_file)
        engine = create_engine(conn_string)

        try:
            if self.validate and scenario_name:
                original_table_name = f"{scenario_name}_original_{table_name}"
                df.to_sql(original_table_name, engine, if_exists="replace", index=False)

            self.load_source_table_with_id_pk(engine, table_name, df)
        finally:
            engine.dispose()

    @staticmethod
    def load_source_table_with_id_pk(engine, table_name: str, df: pd.DataFrame) -> None:
        columns = list(df.columns)
        if columns[0] != "id":
            raise ValueError("KROWN benchmark data must have id as first column")

        column_definitions = ["id INTEGER PRIMARY KEY"]
        column_definitions.extend(f'"{column}" TEXT' for column in columns[1:])

        with engine.begin() as conn:
            conn.execute(text(f'DROP TABLE IF EXISTS "{table_name}"'))
            conn.execute(
                text(f'CREATE TABLE "{table_name}" ({", ".join(column_definitions)})')
            )

        df.to_sql(table_name, engine, if_exists="append", index=False)

    def execute_forward_mapping_step(self, metadata: dict, shared_dir: Path) -> float:
        start_time = time.time()

        mapping_step = None
        for step in metadata["steps"]:
            if step["command"] == "execute_mapping":
                mapping_step = step
                break

        if not mapping_step:
            raise ValueError("No mapping step found in metadata")

        mapping_file = str(shared_dir / mapping_step["parameters"]["mapping_file"])
        output_file = str(shared_dir / mapping_step["parameters"]["output_file"])

        conn_string = self.get_connection_string()
        sa_url = conn_string.replace("postgresql://", "postgresql+psycopg2://")
        jdbc_dsn, username, password = rmlmapper.sqlalchemy_to_jdbc(sa_url)

        rc = rmlmapper.run(
            mapping_file,
            output_file,
            dsn=jdbc_dsn,
            username=username,
            password=password,
            timeout=1800,
        )

        if rc != 0:
            raise RuntimeError(f"RMLMapper failed with exit code {rc}")

        if not Path(output_file).exists():
            raise FileNotFoundError(f"Expected output file not found: {output_file}")

        return time.time() - start_time

    def execute_inversion_step(
        self, metadata: dict, shared_dir: Path
    ) -> tuple[list[ReconstructedTable], float]:
        self.clear_loaded_tables(metadata)
        start_time = time.time()

        mapping_file = shared_dir / "mapping.r2rml.ttl"
        rdf_file = shared_dir / "out.nt"
        conn_string = self.get_connection_string()
        source_db_url = conn_string.replace("postgresql://", "postgresql+psycopg2://")

        original_cwd = os.getcwd()
        try:
            os.chdir(shared_dir)
            inversion_results = reconstruct(
                mapping=str(mapping_file),
                rdf_graph=str(rdf_file),
                source_db_url=source_db_url,
            )
            return inversion_results, time.time() - start_time
        finally:
            os.chdir(original_cwd)

    def clear_loaded_tables(self, metadata: dict) -> None:
        conn_string = self.get_connection_string()
        engine = create_engine(conn_string)
        try:
            with engine.begin() as conn:
                for step in metadata["steps"]:
                    if step["command"] == "load":
                        table_name = step["parameters"]["table"]
                        conn.execute(text(f'TRUNCATE TABLE "{table_name}"'))
        finally:
            engine.dispose()

    def materialize_reconstructed_tables(
        self, scenario_name: str, results: list[ReconstructedTable]
    ) -> None:
        conn_string = self.get_connection_string()
        engine = create_engine(conn_string)
        try:
            for result in results:
                table_name = f"{scenario_name}_{result.name}"
                result.data.to_sql(table_name, engine, if_exists="replace", index=False)
        finally:
            engine.dispose()

    def save_results(self, results: list[dict]) -> Path:
        results_dir = Path(__file__).parent / "krown" / "results"
        results_dir.mkdir(exist_ok=True, parents=True)

        timestamp = int(time.time())
        results_file = results_dir / f"krown_benchmark_results_{timestamp}.json"

        benchmark_data = {
            "timestamp": timestamp,
            "benchmark_type": "KROWN",
            "framework": "Knowledge Graph Inversion",
            "environment": "Docker",
            "sparql_engine": SPARQL_ENGINE,
            "iterations": self.iterations,
            "total_scenarios": len(results),
            "completed_scenarios": len(
                [r for r in results if r["status"] == "completed"]
            ),
            "failed_scenarios": len([r for r in results if r["status"] == "failed"]),
            "unexpected_outcomes": len(
                [r for r in results if not r["outcome_matches_expectation"]]
            ),
            "results": results,
        }

        with open(results_file, "w") as f:
            json.dump(benchmark_data, f, indent=2)

        return results_file

    def save_aggregated_results(
        self, scenario_runs: dict[str, list[dict]]
    ) -> tuple[Path, Path]:
        results_dir = Path(__file__).parent / "krown" / "results"
        results_dir.mkdir(exist_ok=True, parents=True)

        timestamp = int(time.time())

        raw_file = results_dir / f"krown_benchmark_results_raw_{timestamp}.json"
        raw_data = {
            "timestamp": timestamp,
            "benchmark_type": "KROWN",
            "framework": "Knowledge Graph Inversion",
            "environment": "Docker",
            "sparql_engine": SPARQL_ENGINE,
            "iterations": self.iterations,
            "scenarios": {name: runs for name, runs in scenario_runs.items()},
        }

        with open(raw_file, "w") as f:
            json.dump(raw_data, f, indent=2)

        stats_file = results_dir / f"krown_benchmark_results_stats_{timestamp}.json"
        stats_data = {
            "timestamp": timestamp,
            "benchmark_type": "KROWN",
            "framework": "Knowledge Graph Inversion",
            "environment": "Docker",
            "sparql_engine": SPARQL_ENGINE,
            "iterations": self.iterations,
            "scenarios": {},
        }

        for scenario_name, runs in scenario_runs.items():
            completed_runs = [r for r in runs if r["status"] == "completed"]
            if completed_runs:
                stats_data["scenarios"][scenario_name] = {
                    "raw_runs": runs,
                    "statistics": aggregate_scenario_statistics(completed_runs),
                }

        with open(stats_file, "w") as f:
            json.dump(stats_data, f, indent=2)

        return raw_file, stats_file

    def generate_validation_summary(self, results: list[dict]) -> dict | None:
        if not self.validate:
            return None

        validation_summary: dict = {
            "total_scenarios": 0,
            "passed": 0,
            "failed": 0,
            "scenario_results": [],
        }

        for result in results:
            if result["status"] == "completed" and "validation_results" in result:
                validation_summary["total_scenarios"] += 1
                val_result = result["validation_results"]
                validation_summary["scenario_results"].append(val_result)

                if val_result["validation_passed"]:
                    validation_summary["passed"] += 1
                else:
                    validation_summary["failed"] += 1

        if validation_summary["total_scenarios"] > 0:
            validation_summary["validation_rate"] = (
                validation_summary["passed"]
                / validation_summary["total_scenarios"]
                * 100
            )
        else:
            validation_summary["validation_rate"] = 0

        return validation_summary

    def print_summary(self, results: list[dict]) -> None:
        completed = [r for r in results if r["status"] == "completed"]
        failed = [r for r in results if r["status"] == "failed"]

        table = Table(title=f"Benchmark results ({SPARQL_ENGINE})")
        table.add_column("Scenario")
        table.add_column("Time", justify="right")
        table.add_column("RMLMapper", justify="right")
        table.add_column("Inversion", justify="right")
        table.add_column("Overhead", justify="right")
        table.add_column("TM/POM/INV", justify="right")
        if self.validate:
            table.add_column("Valid")
        table.add_column("Outcome")

        for r in completed:
            t = r["timing_breakdown"]
            row = [
                r["scenario_name"],
                f"{r['execution_time']:.2f}s",
                f"{t['rmlmapper_time']:.2f}s",
                f"{t['inversion_time']:.2f}s",
                f"{t['inversion_overhead_percentage']:.1f}%",
                f"{r['triples_maps_count']}/{r['predicate_object_maps_count']}/{r['inversion_count']}",
            ]
            if self.validate:
                row.append(
                    "PASS" if r["validation_results"]["validation_passed"] else "FAIL"
                )
            row.append(self.outcome_cell(r))
            table.add_row(*row)

        for r in failed:
            row = [
                r["scenario_name"],
                f"{r['execution_time']:.2f}s",
                "",
                "",
                "",
                "",
            ]
            if self.validate:
                row.append("")
            row.append(self.outcome_cell(r))
            table.add_row(*row)

        console.print(table)
        console.print(
            f"Completed: {len(completed)}/{len(results)}, Failed: {len(failed)}"
        )

    def print_aggregated_summary(self, scenario_runs: dict[str, list[dict]]) -> None:
        table = Table(
            title=f"Benchmark results ({SPARQL_ENGINE}, {self.iterations} iterations)"
        )
        table.add_column("Scenario")
        table.add_column("Runs", justify="right")
        table.add_column("Exec time", justify="right")
        table.add_column("RMLMapper", justify="right")
        table.add_column("Inversion", justify="right")
        table.add_column("Overhead", justify="right")
        table.add_column("Outcome")

        for scenario_name, runs in sorted(scenario_runs.items()):
            completed_runs = [r for r in runs if r["status"] == "completed"]
            all_expected = all(r["outcome_matches_expectation"] for r in runs)
            outcome = (
                self.outcome_cell(runs[-1]) if all_expected else "[red]UNEXPECTED[/red]"
            )

            if completed_runs:
                stats = aggregate_scenario_statistics(completed_runs)
                exec_stats = stats["execution_time"]
                rmlmapper_stats = stats["rmlmapper_time"]
                inv_stats = stats["inversion_time"]
                overhead_stats = stats["inversion_overhead_percentage"]

                table.add_row(
                    scenario_name,
                    f"{len(completed_runs)}/{len(runs)}",
                    f"{exec_stats['mean']:.2f}s +/- {exec_stats['std']:.2f}s",
                    f"{rmlmapper_stats['mean']:.2f}s +/- {rmlmapper_stats['std']:.2f}s",
                    f"{inv_stats['mean']:.2f}s +/- {inv_stats['std']:.2f}s",
                    f"{overhead_stats['mean']:.1f}% +/- {overhead_stats['std']:.1f}%",
                    outcome,
                )
            else:
                table.add_row(scenario_name, f"0/{len(runs)}", "", "", "", "", outcome)

        console.print(table)

    def validate_scenario(self, scenario_name: str) -> dict:
        try:
            original_table = f"{scenario_name}_original_data"
            inverted_table = f"{scenario_name}_data"
            expected_outcome(scenario_name)

            assert self.validator is not None
            return self.validator.validate_inversion(
                original_table=original_table,
                inverted_table=inverted_table,
                scenario_name=scenario_name,
                expected_lost_columns=["id"],
            )

        except Exception as e:
            return {"validation_passed": False, "error": str(e)}

    def cleanup(self) -> None:
        if self.cleanup_tables:
            if self.validator:
                self.validator.cleanup_tables()
            self.cleanup_database_tables()

    def cleanup_database_tables(self) -> None:
        conn_string = self.get_connection_string()
        engine = create_engine(conn_string)

        try:
            with engine.connect() as conn:
                result = conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
                tables = [row[0] for row in result]

                for table_name in tables:
                    conn.execute(text(f"DROP TABLE IF EXISTS {table_name} CASCADE"))

                conn.commit()
        except Exception:
            pass
        finally:
            engine.dispose()

    def generate_plots(self, stats_file: Path) -> None:
        with open(stats_file, "r") as f:
            stats_data = json.load(f)

        plot_timing_bar_charts(stats_data, stats_file.parent)

    def run_benchmark(self) -> int:
        console.print(f"Starting KROWN benchmark ({SPARQL_ENGINE})")

        try:
            if self.validate:
                conn_string = self.get_connection_string()
                self.validator = KrownValidator(conn_string)

            self.run_krown_data_generation()

            scenarios = self.find_krown_scenarios()
            if not scenarios:
                console.print("[red]No scenarios found[/red]")
                return 1

            all_runs: list[dict] = []

            if self.iterations == 1:
                results = []
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    MofNCompleteColumn(),
                    TimeElapsedColumn(),
                    console=console,
                ) as progress:
                    task = progress.add_task("Scenarios", total=len(scenarios))
                    for scenario_path in scenarios:
                        progress.update(task, description=scenario_path.name)
                        result = self.execute_and_classify_scenario(scenario_path)
                        results.append(result)
                        all_runs.append(result)
                        progress.advance(task)

                results_file = self.save_results(results)
                self.print_summary(results)

                if self.validate and self.validator:
                    validation_summary = self.generate_validation_summary(results)
                    if validation_summary:
                        validation_file = (
                            results_file.parent / f"validation_{results_file.name}"
                        )
                        self.validator.save_validation_report(
                            validation_summary, validation_file
                        )
                        self.validator.print_summary(validation_summary)

                console.print(f"Results saved to {results_file}")

            else:
                scenario_runs: dict[str, list[dict]] = {
                    scenario.name: [] for scenario in scenarios
                }

                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    MofNCompleteColumn(),
                    TimeElapsedColumn(),
                    console=console,
                ) as progress:
                    iter_task = progress.add_task("Iterations", total=self.iterations)

                    for iteration in range(1, self.iterations + 1):
                        progress.update(
                            iter_task,
                            description=f"Iteration {iteration}/{self.iterations}",
                        )
                        scenario_task = progress.add_task(
                            "  Scenarios", total=len(scenarios)
                        )

                        for scenario_path in scenarios:
                            progress.update(
                                scenario_task, description=f"  {scenario_path.name}"
                            )
                            result = self.execute_and_classify_scenario(scenario_path)
                            scenario_runs[scenario_path.name].append(result)
                            all_runs.append(result)
                            progress.advance(scenario_task)

                        progress.remove_task(scenario_task)
                        progress.advance(iter_task)

                raw_file, stats_file = self.save_aggregated_results(scenario_runs)
                self.print_aggregated_summary(scenario_runs)

                console.print(f"Raw results saved to {raw_file}")
                console.print(f"Statistics saved to {stats_file}")

                try:
                    self.generate_plots(stats_file)
                    console.print(f"Plots saved to {stats_file.parent}")
                except Exception:
                    console.print(
                        f"[yellow]Failed to generate plots. Try manually: "
                        f"uv run python -m benchmarks.krown_plots {stats_file}[/yellow]"
                    )

            unexpected = sorted(
                {
                    r["scenario_name"]
                    for r in all_runs
                    if not r["outcome_matches_expectation"]
                }
            )
            if unexpected:
                console.print(
                    f"[red]Unexpected outcomes: {', '.join(unexpected)}[/red]"
                )
                return 1

            console.print("Benchmark completed")
            return 0

        except KeyboardInterrupt:
            console.print("[yellow]Benchmark interrupted[/yellow]")
            return 1
        except Exception as e:
            console.print(f"[red]Benchmark failed: {e}[/red]")
            return 1
        finally:
            self.cleanup()


def main():  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="KROWN Benchmark Runner for Knowledge Graph Inversion (Docker)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage:
  make benchmark-krown I=10

Notes:
  # When using --iterations > 1, plots are automatically generated
  # Results and plots are saved to benchmarks/krown/results/
        """,
    )

    parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Number of times to run each scenario (default: 1). Use multiple iterations for statistical analysis.",
    )

    args = parser.parse_args()

    runner = KrownBenchmarkRunner(
        validate=True,
        cleanup_tables=True,
        iterations=args.iterations,
    )
    return runner.run_benchmark()


if __name__ == "__main__":
    sys.exit(main())

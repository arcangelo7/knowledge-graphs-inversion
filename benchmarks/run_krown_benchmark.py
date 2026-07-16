#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import argparse
import csv
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

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
from kgi.exceptions import NonInvertibleError  # noqa: E402

console = Console(width=max(shutil.get_terminal_size().columns, 100))

R2RML = Namespace("http://www.w3.org/ns/r2rml#")
SPARQL_ENGINE = "pyoxigraph"
RMLMAPPER_JAVA_OPTIONS = (
    "-XX:InitialRAMPercentage=50.0",
    "-XX:MaxRAMPercentage=50.0",
)
RMLMAPPER_TIMEOUT_SECONDS = 3 * 60 * 60
SUITES = ("raw", "mappings", "named-graphs", "joins")
GENERATOR_SUITES = {
    "RawData": "raw",
    "Mappings": "mappings",
    "NamedGraph": "named-graphs",
    "JoinsRelation": "joins",
    "JoinsMultiple": "joins",
}
ParameterValue = bool | int | float | str


def _dictionary(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("Expected a dictionary")
    return value


def _number(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value))


def _process_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _integer_parameter(parameters: dict[str, ParameterValue], name: str) -> int:
    value = parameters[name]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"KROWN parameter {name} must be an integer")
    return value


def _float_parameter(parameters: dict[str, ParameterValue], name: str) -> float:
    value = parameters[name]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"KROWN parameter {name} must be numeric")
    return float(value)


def _boolean_parameter(parameters: dict[str, ParameterValue], name: str) -> bool:
    value = parameters[name]
    if not isinstance(value, bool):
        raise TypeError(f"KROWN parameter {name} must be boolean")
    return value


@dataclass(frozen=True)
class KrownScenario:
    identifier: str
    display_name: str
    generator: str
    parameters: dict[str, ParameterValue]

    @property
    def suite(self) -> str:
        return GENERATOR_SUITES[self.generator]

    @property
    def expected_outcome(self) -> str:
        if self.generator == "RawData":
            return "FULL"
        if self.generator == "Mappings" and _integer_parameter(
            self.parameters, "number_of_tms"
        ) > _integer_parameter(self.parameters, "number_of_poms"):
            return "NON_INVERTIBLE"
        return "PARTIAL"

    @property
    def generated_name(self) -> str:
        parameters = self.parameters
        if self.generator == "RawData":
            return (
                f"raw_{_integer_parameter(parameters, 'number_of_members')}_"
                f"{_integer_parameter(parameters, 'number_of_properties')}_"
                f"{_integer_parameter(parameters, 'value_size')}"
            )
        if self.generator == "Mappings":
            return (
                f"mappings_{_integer_parameter(parameters, 'number_of_tms')}_"
                f"{_integer_parameter(parameters, 'number_of_poms')}"
            )
        if self.generator == "NamedGraph":
            static = _boolean_parameter(parameters, "static")
            return (
                "namedgraph_"
                f"{_integer_parameter(parameters, 'number_of_ng_s')}SM-NG_"
                f"{_integer_parameter(parameters, 'number_of_ng_pom')}POM-NG_"
                f"{_integer_parameter(parameters, 'number_of_tms')}TM_"
                f"{_integer_parameter(parameters, 'number_of_poms')}POM_{static}"
            )
        if self.generator == "JoinsRelation":
            percentage = _float_parameter(parameters, "percentage")
            return (
                "joins_relations_"
                f"{_integer_parameter(parameters, 'n')}-"
                f"{_integer_parameter(parameters, 'm')}_{percentage}"
            )
        if self.generator == "JoinsMultiple":
            percentage = _float_parameter(parameters, "percentage")
            return (
                "joins_mutiple_"
                f"{_integer_parameter(parameters, 'n')}-"
                f"{_integer_parameter(parameters, 'm')}_"
                f"{_integer_parameter(parameters, 'jc')}jc_{percentage}"
            )
        raise ValueError(f"Unsupported KROWN generator: {self.generator}")

    @property
    def source_table_count(self) -> int:
        return 2 if self.suite == "joins" else 1

    @property
    def source_rows(self) -> int:
        return (
            _integer_parameter(self.parameters, "number_of_members")
            * self.source_table_count
        )

    @property
    def source_cells(self) -> int:
        columns = _integer_parameter(self.parameters, "number_of_properties") + 1
        return self.source_rows * columns

    @property
    def expected_rdf_statements(self) -> int | None:
        rows = _integer_parameter(self.parameters, "number_of_members")
        if self.generator == "RawData":
            return rows * _integer_parameter(self.parameters, "number_of_properties")
        if self.generator == "Mappings":
            return (
                rows
                * _integer_parameter(self.parameters, "number_of_tms")
                * _integer_parameter(self.parameters, "number_of_poms")
            )
        if self.generator == "NamedGraph":
            graph_count = _integer_parameter(
                self.parameters, "number_of_ng_s"
            ) + _integer_parameter(self.parameters, "number_of_ng_pom")
            return (
                rows
                * _integer_parameter(self.parameters, "number_of_poms")
                * graph_count
            )
        return None

    def config_instance(self) -> dict[str, object]:
        return {
            "@id": self.identifier,
            "name": self.display_name,
            "generator": self.generator,
            "parameters": self.parameters,
        }


@dataclass(frozen=True)
class KrownSeries:
    name: str
    title: str
    suite: str
    parameter_label: str
    points: tuple[tuple[str, ParameterValue], ...]


@dataclass(frozen=True)
class SourceTable:
    name: str
    csv_file: Path


class ScenarioExecutionFailure(RuntimeError):
    def __init__(
        self,
        stage: str,
        kind: str,
        message: str,
        diagnostic: str = "",
    ):
        super().__init__(message)
        self.stage = stage
        self.kind = kind
        self.diagnostic = diagnostic

    @property
    def outcome(self) -> str:
        if self.kind == "out_of_memory":
            return "OUT_OF_MEMORY"
        if self.kind == "timeout":
            return "TIMEOUT"
        return "FAILED"


def _raw_name(members: int, properties: int, value_size: int) -> str:
    return f"raw_{members}_{properties}_{value_size}"


def _mappings_name(triples_maps: int, predicate_object_maps: int) -> str:
    return f"mappings_{triples_maps}_{predicate_object_maps}"


def _named_graph_name(
    subject_graphs: int,
    predicate_object_graphs: int,
    predicate_object_maps: int,
    static: bool,
) -> str:
    return (
        f"namedgraph_{subject_graphs}SM-NG_{predicate_object_graphs}POM-NG_"
        f"1TM_{predicate_object_maps}POM_{static}"
    )


def _join_relation_name(n: int, m: int) -> str:
    return f"joins_relations_{n}-{m}_50.0"


def _join_conditions_name(conditions: int) -> str:
    return f"joins_mutiple_1-1_{conditions}jc_50.0"


SERIES = (
    KrownSeries(
        "raw_rows",
        "Raw data: rows",
        "raw",
        "Rows",
        tuple(
            (_raw_name(value, 20, 0), value)
            for value in (10_000, 100_000, 1_000_000, 10_000_000)
        ),
    ),
    KrownSeries(
        "raw_properties",
        "Raw data: properties",
        "raw",
        "Properties",
        tuple((_raw_name(100_000, value, 0), value) for value in (1, 10, 20, 30)),
    ),
    KrownSeries(
        "raw_value_size",
        "Raw data: value size",
        "raw",
        "Value size (characters)",
        tuple(
            (_raw_name(100_000, 20, value), value)
            for value in (500, 1_000, 5_000, 10_000)
        ),
    ),
    KrownSeries(
        "mappings_triples_maps",
        "Mappings: Triples Maps",
        "mappings",
        "Triples Maps",
        tuple((_mappings_name(value, 5), value) for value in (1, 10, 20, 30)),
    ),
    KrownSeries(
        "mappings_predicate_object_maps",
        "Mappings: Predicate-Object Maps",
        "mappings",
        "Predicate-Object Maps",
        tuple((_mappings_name(20, value), value) for value in (1, 3, 5, 10)),
    ),
    *tuple(
        KrownSeries(
            f"named_graphs_subject_{'static' if static else 'dynamic'}",
            f"Named graphs in subject map ({'static' if static else 'dynamic'})",
            "named-graphs",
            "Named graphs",
            tuple(
                (_named_graph_name(value, 0, 20, static), value)
                for value in (1, 5, 10, 15)
            ),
        )
        for static in (True, False)
    ),
    *tuple(
        KrownSeries(
            f"named_graphs_pom_{'static' if static else 'dynamic'}",
            (
                "Named graphs in predicate-object map "
                f"({'static' if static else 'dynamic'})"
            ),
            "named-graphs",
            "Named graphs",
            tuple(
                (_named_graph_name(0, value, 1, static), value)
                for value in (1, 5, 10, 15)
            ),
        )
        for static in (True, False)
    ),
    *tuple(
        KrownSeries(
            f"named_graphs_both_{'static' if static else 'dynamic'}",
            (
                "Named graphs in subject and predicate-object maps "
                f"({'static' if static else 'dynamic'})"
            ),
            "named-graphs",
            "Named graphs in each map",
            tuple(
                (_named_graph_name(value, value, 10, static), value)
                for value in (1, 5, 10, 15)
            ),
        )
        for static in (True, False)
    ),
    KrownSeries(
        "joins_one_to_many",
        "Joins: 1-N relations",
        "joins",
        "Relation",
        tuple(
            (_join_relation_name(1, value), f"1-{value}") for value in (1, 5, 10, 15)
        ),
    ),
    KrownSeries(
        "joins_many_to_one",
        "Joins: N-1 relations",
        "joins",
        "Relation",
        tuple(
            (_join_relation_name(value, 1), f"{value}-1") for value in (1, 5, 10, 15)
        ),
    ),
    KrownSeries(
        "joins_many_to_many",
        "Joins: N-M relations",
        "joins",
        "Relation",
        tuple(
            (_join_relation_name(n, m), f"{n}-{m}")
            for n, m in ((3, 3), (3, 5), (5, 3), (10, 5), (5, 10))
        ),
    ),
    KrownSeries(
        "joins_conditions",
        "Joins: conditions",
        "joins",
        "Join conditions",
        tuple((_join_conditions_name(value), value) for value in (1, 5, 10, 15)),
    ),
)


def load_scenarios(config_file: Path) -> tuple[KrownScenario, ...]:
    catalog = json.loads(config_file.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict) or not isinstance(catalog["instances"], list):
        raise TypeError("Invalid KROWN benchmark catalog")

    scenarios = []
    for value in catalog["instances"]:
        if not isinstance(value, dict) or not isinstance(value["parameters"], dict):
            raise TypeError("Invalid KROWN scenario")
        raw_parameters = value["parameters"]
        parameters: dict[str, ParameterValue] = {}
        for name, parameter_value in raw_parameters.items():
            if not isinstance(name, str) or not isinstance(
                parameter_value, (bool, int, float, str)
            ):
                raise TypeError("Invalid KROWN scenario parameter")
            parameters[name] = parameter_value
        scenarios.append(
            KrownScenario(
                identifier=str(value["@id"]),
                display_name=str(value["name"]),
                generator=str(value["generator"]),
                parameters=parameters,
            )
        )

    generated_names = [scenario.generated_name for scenario in scenarios]
    if len(generated_names) != len(set(generated_names)):
        raise ValueError("KROWN benchmark catalog contains duplicate scenarios")

    catalog_names = set(generated_names)
    series_names = {
        scenario_name for series in SERIES for scenario_name, _ in series.points
    }
    if catalog_names != series_names:
        missing = sorted(series_names - catalog_names)
        unreferenced = sorted(catalog_names - series_names)
        raise ValueError(
            f"KROWN catalog and series differ: missing={missing}, "
            f"unreferenced={unreferenced}"
        )

    suite_order = {suite: index for index, suite in enumerate(SUITES)}
    return tuple(sorted(scenarios, key=lambda scenario: suite_order[scenario.suite]))


def generate_scenario(
    scenario: KrownScenario, scenarios_root: Path, data_generator_dir: Path
) -> Path:
    if scenarios_root.exists():
        shutil.rmtree(scenarios_root)
    scenarios_root.mkdir(parents=True)

    config = {
        "@id": "http://example.com/kg-inversion-benchmark/generated",
        "name": scenario.display_name,
        "description": "Single KROWN benchmark scenario",
        "instances": [scenario.config_instance()],
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", encoding="utf-8"
    ) as config_file:
        json.dump(config, config_file)
        config_file.flush()
        process = subprocess.run(
            [
                sys.executable,
                str(data_generator_dir / "exgentool"),
                "generate",
                f"--scenario={config_file.name}",
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

    metadata_files = list(scenarios_root.rglob("metadata.json"))
    if len(metadata_files) != 1:
        raise ValueError("KROWN must generate exactly one requested scenario")
    scenario_path = metadata_files[0].parent
    if scenario_path.name != scenario.generated_name:
        raise ValueError(
            f"Unexpected generated scenario: expected={scenario.generated_name}, "
            f"actual={scenario_path.name}"
        )
    return scenario_path


def _format_confidence_interval(statistics: dict[str, object]) -> str:
    mean = _number(statistics["mean"])
    lower = _number(statistics["ci_95_lower"])
    upper = _number(statistics["ci_95_upper"])
    margin = max(mean - lower, upper - mean)
    return f"{mean:.2f}±{margin:.2f}"


def _format_percentage_confidence_interval(
    statistics: dict[str, object],
) -> str:
    mean = _number(statistics["mean"])
    lower = _number(statistics["ci_95_lower"])
    upper = _number(statistics["ci_95_upper"])
    margin = max(mean - lower, upper - mean)
    return f"{mean:.1f}±{margin:.1f}%"


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _original_table_name(table_name: str) -> str:
    return f"_krown_original_{table_name}"


class KrownBenchmarkRunner:
    def __init__(
        self,
        cleanup_tables: bool = True,
        iterations: int = 1,
        suites: tuple[str, ...] = SUITES,
        scenario_name: str | None = None,
    ):
        project_root = Path(__file__).resolve().parent.parent
        self.data_generator_dir = project_root / "KROWN" / "data-generator"
        benchmark_dir = Path(__file__).resolve().parent / "krown"
        self.config_file = benchmark_dir / "config" / "kg-inversion-benchmark.json"
        self.scenarios_root = benchmark_dir / "scenarios"
        self.results_dir = benchmark_dir / "results"
        self.cleanup_tables = cleanup_tables
        self.iterations = iterations
        catalog = load_scenarios(self.config_file)
        scenario_names = {scenario.generated_name for scenario in catalog}
        if scenario_name is not None and scenario_name not in scenario_names:
            raise ValueError(f"Unknown KROWN scenario: {scenario_name}")

        suite_scenarios = tuple(
            scenario for scenario in catalog if scenario.suite in suites
        )
        if scenario_name is not None:
            suite_scenarios = tuple(
                scenario
                for scenario in suite_scenarios
                if scenario.generated_name == scenario_name
            )
            if not suite_scenarios:
                selected_suites = ", ".join(suites)
                raise ValueError(
                    f"KROWN scenario {scenario_name} is not included in "
                    f"selected suites: {selected_suites}"
                )

        self.scenarios = suite_scenarios
        self.suites = tuple(
            suite
            for suite in SUITES
            if any(scenario.suite == suite for scenario in self.scenarios)
        )
        selected_names = {scenario.generated_name for scenario in self.scenarios}
        self.series = tuple(
            KrownSeries(
                name=series.name,
                title=series.title,
                suite=series.suite,
                parameter_label=series.parameter_label,
                points=tuple(
                    point for point in series.points if point[0] in selected_names
                ),
            )
            for series in SERIES
            if any(point[0] in selected_names for point in series.points)
        )
        self.connection_string = (
            f"postgresql://{os.environ['BENCHMARK_DB_USER']}:"
            f"{os.environ['BENCHMARK_DB_PASSWORD']}@"
            f"{os.environ['BENCHMARK_DB_HOST']}:"
            f"{os.environ['BENCHMARK_DB_PORT']}/"
            f"{os.environ['BENCHMARK_DB_NAME']}"
        )
        self.validator = KrownValidator(self.connection_string)

    def prepare_output_directories(self) -> None:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        if self.scenarios_root.exists():
            shutil.rmtree(self.scenarios_root)
        self.scenarios_root.mkdir(parents=True)

    @staticmethod
    def mapping_component_counts(mapping_file: Path) -> tuple[int, int, int, int]:
        graph = Graph()
        graph.parse(mapping_file)
        triples_maps = set(graph.subjects(RDF.type, R2RML.TriplesMap))
        predicate_object_maps = set(graph.subjects(RDF.type, R2RML.PredicateObjectMap))
        join_conditions = set(graph.subjects(RDF.type, R2RML.JoinCondition))
        graph_maps = sum(1 for _ in graph.triples((None, R2RML.graphMap, None)))
        graph_maps += sum(1 for _ in graph.triples((None, R2RML.graph, None)))
        return (
            len(triples_maps),
            len(predicate_object_maps),
            len(join_conditions),
            graph_maps,
        )

    @staticmethod
    def count_rdf_statements(rdf_file: Path) -> int:
        with rdf_file.open(encoding="utf-8") as file:
            return sum(1 for line in file if line.strip())

    @staticmethod
    def _metadata_steps(metadata: dict[str, object]) -> list[dict[str, object]]:
        steps = metadata["steps"]
        if not isinstance(steps, list) or not all(
            isinstance(step, dict) for step in steps
        ):
            raise TypeError("Invalid KROWN metadata steps")
        return steps

    @classmethod
    def _metadata_step(
        cls, metadata: dict[str, object], command: str
    ) -> dict[str, object]:
        matching_steps = [
            step for step in cls._metadata_steps(metadata) if step["command"] == command
        ]
        if len(matching_steps) != 1:
            raise ValueError(f"Expected one {command} step in KROWN metadata")
        return matching_steps[0]

    @classmethod
    def source_tables(
        cls, metadata: dict[str, object], shared_dir: Path
    ) -> tuple[SourceTable, ...]:
        load_steps = [
            step
            for step in cls._metadata_steps(metadata)
            if step["command"] in ("load", "load_multiple")
        ]
        if len(load_steps) != 1:
            raise ValueError("Expected one KROWN database load step")
        load_step = load_steps[0]
        parameters = load_step["parameters"]
        if not isinstance(parameters, dict):
            raise TypeError("Invalid KROWN load parameters")

        if load_step["command"] == "load":
            return (
                SourceTable(
                    name=str(parameters["table"]),
                    csv_file=shared_dir / str(parameters["csv_file"]),
                ),
            )

        csv_files = parameters["csv_files"]
        if not isinstance(csv_files, list):
            raise TypeError("Invalid KROWN multiple-load parameters")
        tables = []
        for value in csv_files:
            if not isinstance(value, dict):
                raise TypeError("Invalid KROWN source file")
            tables.append(
                SourceTable(
                    name=str(value["table"]),
                    csv_file=shared_dir / str(value["file"]),
                )
            )
        return tuple(tables)

    def execute_load_rdb_step(
        self,
        source_tables: tuple[SourceTable, ...],
        scenario: KrownScenario,
    ) -> None:
        engine = create_engine(self.connection_string)
        try:
            for source_table in source_tables:
                self.load_source_table(engine, source_table, scenario)
        finally:
            engine.dispose()

    @staticmethod
    def load_source_table(
        engine: Engine, source_table: SourceTable, scenario: KrownScenario
    ) -> None:
        with source_table.csv_file.open(newline="", encoding="utf-8") as file:
            columns = next(csv.reader(file))
        expected_columns = ["id"] + [
            f"p{number}"
            for number in range(
                1,
                _integer_parameter(scenario.parameters, "number_of_properties") + 1,
            )
        ]
        if columns != expected_columns:
            raise ValueError(
                f"Unexpected CSV columns for {scenario.generated_name}: {columns}"
            )

        table_name = _quoted(source_table.name)
        original_name = _quoted(_original_table_name(source_table.name))
        definitions = [f"{_quoted('id')} INTEGER PRIMARY KEY"]
        definitions.extend(f"{_quoted(column)} TEXT" for column in columns[1:])
        with engine.begin() as connection:
            connection.execute(text(f"DROP TABLE IF EXISTS {table_name} CASCADE"))
            connection.execute(text(f"DROP TABLE IF EXISTS {original_name} CASCADE"))
            connection.execute(
                text(f"CREATE TABLE {table_name} ({', '.join(definitions)})")
            )

        raw_connection = engine.raw_connection()
        try:
            cursor = raw_connection.cursor()
            with source_table.csv_file.open(encoding="utf-8") as file:
                cursor.copy_expert(
                    f"COPY {table_name} FROM STDIN WITH (FORMAT CSV, HEADER TRUE)",
                    file,
                )
            raw_connection.commit()
        finally:
            raw_connection.close()

        with engine.connect() as connection:
            row_count = connection.execute(
                text(f"SELECT COUNT(*) FROM {table_name}")
            ).scalar_one()
        expected_rows = _integer_parameter(scenario.parameters, "number_of_members")
        if row_count != expected_rows:
            raise ValueError(
                f"Unexpected row count for {scenario.generated_name}/"
                f"{source_table.name}: expected={expected_rows}, actual={row_count}"
            )

    @staticmethod
    def run_rmlmapper(
        mapping_file: Path,
        output_file: Path,
        jdbc_dsn: str,
        username: str,
        password: str,
        stage: str,
    ) -> float:
        output_file.unlink(missing_ok=True)
        started = time.perf_counter()
        try:
            process = rmlmapper.execute(
                str(mapping_file),
                str(output_file),
                serialization="nquads",
                dsn=jdbc_dsn,
                username=username,
                password=password,
                timeout=RMLMAPPER_TIMEOUT_SECONDS,
                java_options=RMLMAPPER_JAVA_OPTIONS,
            )
        except subprocess.TimeoutExpired as error:
            diagnostic = _process_output(error.stdout) + _process_output(error.stderr)
            if diagnostic:
                sys.stderr.write(diagnostic)
            raise ScenarioExecutionFailure(
                stage,
                "timeout",
                f"RMLMapper timed out after {RMLMAPPER_TIMEOUT_SECONDS} seconds",
                diagnostic,
            ) from error
        elapsed = time.perf_counter() - started
        if process.returncode != 0:
            diagnostic = process.stdout + process.stderr
            if diagnostic:
                sys.stderr.write(diagnostic)
            failure_kind = (
                "out_of_memory" if "OutOfMemoryError" in diagnostic else "process_error"
            )
            raise ScenarioExecutionFailure(
                stage,
                failure_kind,
                f"RMLMapper failed with exit code {process.returncode}",
                diagnostic,
            )
        if not output_file.exists():
            raise ScenarioExecutionFailure(
                stage,
                "missing_output",
                f"RMLMapper did not create {output_file}",
            )
        return elapsed

    def execute_forward_mapping_step(
        self,
        metadata: dict[str, object],
        shared_dir: Path,
        output_file: Path,
        stage: str,
    ) -> float:
        mapping_step = self._metadata_step(metadata, "execute_mapping")
        parameters = mapping_step["parameters"]
        if not isinstance(parameters, dict):
            raise TypeError("Invalid KROWN mapping parameters")
        mapping_file = shared_dir / str(parameters["mapping_file"])
        output_file.unlink(missing_ok=True)

        sqlalchemy_url = self.connection_string.replace(
            "postgresql://", "postgresql+psycopg2://"
        )
        jdbc_dsn, username, password = rmlmapper.sqlalchemy_to_jdbc(sqlalchemy_url)
        return self.run_rmlmapper(
            mapping_file,
            output_file,
            jdbc_dsn,
            username,
            password,
            stage,
        )

    def archive_source_tables(
        self, source_tables: tuple[SourceTable, ...]
    ) -> dict[str, str]:
        originals = {}
        engine = create_engine(self.connection_string)
        try:
            with engine.begin() as connection:
                for source_table in source_tables:
                    table_name = _quoted(source_table.name)
                    original = _original_table_name(source_table.name)
                    original_name = _quoted(original)
                    connection.execute(
                        text(f"DROP TABLE IF EXISTS {original_name} CASCADE")
                    )
                    connection.execute(
                        text(f"ALTER TABLE {table_name} RENAME TO {_quoted(original)}")
                    )
                    connection.execute(
                        text(
                            f"CREATE TABLE {table_name} "
                            f"(LIKE {original_name} INCLUDING DEFAULTS)"
                        )
                    )
                    originals[source_table.name] = original
        finally:
            engine.dispose()
        return originals

    def execute_inversion_step(self, shared_dir: Path, rdf_file: Path) -> float:
        source_db_url = self.connection_string.replace(
            "postgresql://", "postgresql+psycopg2://"
        )
        started = time.perf_counter()
        reconstruct(
            mapping=str(shared_dir / "mapping.r2rml.ttl"),
            rdf_graph=str(rdf_file),
            dest_db_url=source_db_url,
            source_db_url=source_db_url,
        )
        return time.perf_counter() - started

    def build_scenario_result(
        self,
        scenario: KrownScenario,
        shared_dir: Path,
        source_tables: tuple[SourceTable, ...],
        rdf_statements: int,
        rmlmapper_time: float,
        inversion_time: float,
        total_time: float,
        inversion_count: int,
        validation_results: dict[str, object],
    ) -> dict[str, object]:
        mapping_file = shared_dir / "mapping.r2rml.ttl"
        (
            triples_maps,
            predicate_object_maps,
            join_conditions,
            graph_maps,
        ) = self.mapping_component_counts(mapping_file)
        data_size_bytes = sum(
            source_table.csv_file.stat().st_size for source_table in source_tables
        )
        outcome = str(validation_results["outcome"])
        throughput = None
        if outcome != "NON_INVERTIBLE":
            throughput = {
                "rows_per_second": scenario.source_rows / inversion_time,
                "cells_per_second": scenario.source_cells / inversion_time,
            }

        return {
            "status": "completed",
            "scenario_name": scenario.generated_name,
            "display_name": scenario.display_name,
            "suite": scenario.suite,
            "generator": scenario.generator,
            "expected_outcome": scenario.expected_outcome,
            "parameters": scenario.parameters,
            "execution_time": total_time,
            "timing_breakdown": {
                "rmlmapper_time": rmlmapper_time,
                "inversion_time": inversion_time,
                "inversion_overhead_percentage": inversion_time / rmlmapper_time * 100,
                "total_time": total_time,
            },
            "throughput": throughput,
            "mapping_file": str(mapping_file),
            "data_files": [str(table.csv_file) for table in source_tables],
            "mapping_size_bytes": mapping_file.stat().st_size,
            "data_size_bytes": data_size_bytes,
            "rdf_statements": rdf_statements,
            "source_rows": scenario.source_rows,
            "source_cells": scenario.source_cells,
            "triples_maps_count": triples_maps,
            "predicate_object_maps_count": predicate_object_maps,
            "join_conditions_count": join_conditions,
            "graph_maps_count": graph_maps,
            "inversion_count": inversion_count,
            "validation_results": validation_results,
        }

    @staticmethod
    def build_failure_result(
        scenario: KrownScenario,
        elapsed_seconds: float,
        error: ScenarioExecutionFailure,
    ) -> dict[str, object]:
        return {
            "status": "failed",
            "scenario_name": scenario.generated_name,
            "display_name": scenario.display_name,
            "suite": scenario.suite,
            "generator": scenario.generator,
            "expected_outcome": scenario.expected_outcome,
            "parameters": scenario.parameters,
            "execution_time": elapsed_seconds,
            "failure": {
                "stage": error.stage,
                "kind": error.kind,
                "outcome": error.outcome,
                "message": str(error),
                "diagnostic": error.diagnostic,
            },
        }

    def execute_krown_scenario(
        self, scenario: KrownScenario, scenario_path: Path
    ) -> dict[str, object]:
        metadata_value = json.loads(
            (scenario_path / "metadata.json").read_text(encoding="utf-8")
        )
        if not isinstance(metadata_value, dict):
            raise TypeError("Invalid KROWN metadata")
        metadata: dict[str, object] = metadata_value
        shared_dir = scenario_path / "data" / "shared"
        source_tables = self.source_tables(metadata, shared_dir)
        started = time.perf_counter()

        self.execute_load_rdb_step(source_tables, scenario)
        rdf_file = shared_dir / "out.nq"
        rmlmapper_time = self.execute_forward_mapping_step(
            metadata,
            shared_dir,
            rdf_file,
            "forward_mapping",
        )
        rdf_statements = self.count_rdf_statements(rdf_file)
        expected_rdf_statements = scenario.expected_rdf_statements
        if (
            expected_rdf_statements is not None
            and rdf_statements != expected_rdf_statements
        ):
            raise ScenarioExecutionFailure(
                "forward_validation",
                "invalid_output",
                (
                    f"Unexpected RDF statement count for {scenario.generated_name}: "
                    f"expected={expected_rdf_statements}, actual={rdf_statements}"
                ),
            )

        original_tables = self.archive_source_tables(source_tables)
        inversion_started = time.perf_counter()
        try:
            inversion_time = self.execute_inversion_step(shared_dir, rdf_file)
        except NonInvertibleError as error:
            inversion_time = time.perf_counter() - inversion_started
            if scenario.expected_outcome != "NON_INVERTIBLE":
                raise ScenarioExecutionFailure(
                    "inversion",
                    "unexpected_non_invertible",
                    str(error),
                ) from error
            validation_results: dict[str, object] = {
                "scenario": scenario.generated_name,
                "validation_passed": True,
                "expected_outcome": scenario.expected_outcome,
                "outcome": "NON_INVERTIBLE",
                "error": str(error),
            }
            return self.build_scenario_result(
                scenario=scenario,
                shared_dir=shared_dir,
                source_tables=source_tables,
                rdf_statements=rdf_statements,
                rmlmapper_time=rmlmapper_time,
                inversion_time=inversion_time,
                total_time=time.perf_counter() - started,
                inversion_count=0,
                validation_results=validation_results,
            )
        except MemoryError as error:
            raise ScenarioExecutionFailure(
                "inversion",
                "out_of_memory",
                "Python raised MemoryError during inversion",
            ) from error
        expected_table_names = [source_table.name for source_table in source_tables]
        total_time = time.perf_counter() - started

        roundtrip_file = shared_dir / "roundtrip.nq"
        self.execute_forward_mapping_step(
            metadata,
            shared_dir,
            roundtrip_file,
            "roundtrip_mapping",
        )
        validation_results = self.validator.validate_inversion(
            original_tables=original_tables,
            reconstructed_tables=expected_table_names,
            scenario_name=scenario.generated_name,
            expected_outcome=scenario.expected_outcome,
            original_rdf=rdf_file,
            roundtrip_rdf=roundtrip_file,
        )
        if validation_results["validation_passed"] is not True:
            raise ScenarioExecutionFailure(
                "roundtrip_validation",
                "mismatch",
                (
                    f"Unexpected inversion result for {scenario.generated_name}: "
                    f"{validation_results}"
                ),
            )

        return self.build_scenario_result(
            scenario=scenario,
            shared_dir=shared_dir,
            source_tables=source_tables,
            rdf_statements=rdf_statements,
            rmlmapper_time=rmlmapper_time,
            inversion_time=inversion_time,
            total_time=total_time,
            inversion_count=len(expected_table_names),
            validation_results=validation_results,
        )

    def _series_data(self) -> list[dict[str, object]]:
        return [
            {
                "name": series.name,
                "title": series.title,
                "suite": series.suite,
                "parameter_label": series.parameter_label,
                "points": [
                    {"scenario": scenario_name, "value": value}
                    for scenario_name, value in series.points
                ],
            }
            for series in self.series
        ]

    def save_results(
        self, scenario_runs: dict[str, list[dict[str, object]]]
    ) -> tuple[Path, Path, dict[str, object]]:
        timestamp = int(time.time())
        raw_file = self.results_dir / f"krown_benchmark_results_raw_{timestamp}.json"
        stats_file = (
            self.results_dir / f"krown_benchmark_results_stats_{timestamp}.json"
        )
        failed_scenarios = sum(
            any(run["status"] == "failed" for run in runs)
            for runs in scenario_runs.values()
        )
        common = {
            "timestamp": timestamp,
            "benchmark_type": "KROWN",
            "framework": "Knowledge Graph Inversion",
            "environment": "Docker",
            "sparql_engine": SPARQL_ENGINE,
            "iterations": self.iterations,
            "failed_scenarios": failed_scenarios,
            "suites": list(self.suites),
            "series": self._series_data(),
        }
        raw_data = {**common, "scenarios": scenario_runs}
        raw_file.write_text(json.dumps(raw_data, indent=2) + "\n", encoding="utf-8")

        aggregated_scenarios = {}
        for scenario in self.scenarios:
            runs = scenario_runs[scenario.generated_name]
            failed_run = next((run for run in runs if run["status"] == "failed"), None)
            scenario_data: dict[str, object] = {
                "display_name": scenario.display_name,
                "suite": scenario.suite,
                "generator": scenario.generator,
                "expected_outcome": scenario.expected_outcome,
                "parameters": scenario.parameters,
                "raw_runs": runs,
            }
            if failed_run is not None:
                scenario_data["status"] = "failed"
                scenario_data["failure"] = failed_run["failure"]
                scenario_data["statistics"] = None
            else:
                statistics = aggregate_scenario_statistics(runs)
                throughputs = [
                    _dictionary(run["throughput"])
                    for run in runs
                    if run["throughput"] is not None
                ]
                if throughputs:
                    statistics["rows_per_second"] = calculate_timing_statistics(
                        [
                            _number(throughput["rows_per_second"])
                            for throughput in throughputs
                        ]
                    )
                    statistics["cells_per_second"] = calculate_timing_statistics(
                        [
                            _number(throughput["cells_per_second"])
                            for throughput in throughputs
                        ]
                    )
                scenario_data["status"] = "completed"
                scenario_data["statistics"] = statistics
            aggregated_scenarios[scenario.generated_name] = scenario_data

        stats_data: dict[str, object] = {
            **common,
            "scenarios": aggregated_scenarios,
        }
        stats_file.write_text(json.dumps(stats_data, indent=2) + "\n", encoding="utf-8")
        return raw_file, stats_file, stats_data

    def print_aggregated_summary(self, stats_data: dict[str, object]) -> None:
        scenarios_data = stats_data["scenarios"]
        if not isinstance(scenarios_data, dict):
            raise TypeError("Invalid KROWN statistics")
        iteration_label = "iteration" if self.iterations == 1 else "iterations"
        for series in self.series:
            table = Table(
                title=(
                    f"KROWN {series.title} "
                    f"({SPARQL_ENGINE}, {self.iterations} {iteration_label})"
                )
            )
            table.add_column(series.parameter_label, justify="right")
            table.add_column("CSV MiB", justify="right")
            table.add_column("RDF statements", justify="right")
            table.add_column("RMLMapper", justify="right")
            table.add_column("Inversion", justify="right")
            table.add_column("Overhead", justify="right")
            table.add_column("Rows/s", justify="right")
            table.add_column("Outcome")

            for scenario_name, parameter_value in series.points:
                scenario_data = _dictionary(scenarios_data[scenario_name])
                parameter_label = (
                    f"{parameter_value:,}"
                    if isinstance(parameter_value, (int, float))
                    else str(parameter_value)
                )
                if scenario_data["status"] == "failed":
                    failure = _dictionary(scenario_data["failure"])
                    table.add_row(
                        parameter_label,
                        "-",
                        "-",
                        "-",
                        "-",
                        "-",
                        "-",
                        str(failure["outcome"]),
                    )
                    continue

                statistics = _dictionary(scenario_data["statistics"])
                metadata = _dictionary(statistics["metadata"])
                data_size_mib = _number(metadata["data_size_bytes"]) / (1024**2)
                raw_runs = scenario_data["raw_runs"]
                if not isinstance(raw_runs, list) or not raw_runs:
                    raise TypeError("Invalid KROWN runs")
                first_run = _dictionary(raw_runs[0])
                validation = _dictionary(first_run["validation_results"])
                rows_per_second = "-"
                if "rows_per_second" in statistics:
                    rows_statistics = _dictionary(statistics["rows_per_second"])
                    rows_per_second = f"{_number(rows_statistics['mean']):,.0f}"
                table.add_row(
                    parameter_label,
                    f"{data_size_mib:.2f}",
                    f"{int(_number(metadata['rdf_statements'])):,}",
                    _format_confidence_interval(
                        _dictionary(statistics["rmlmapper_time"])
                    ),
                    _format_confidence_interval(
                        _dictionary(statistics["inversion_time"])
                    ),
                    _format_percentage_confidence_interval(
                        _dictionary(statistics["inversion_overhead_percentage"])
                    ),
                    rows_per_second,
                    str(validation["outcome"]),
                )
            console.print(table)

    def cleanup(self) -> None:
        self.validator.dispose()
        if self.scenarios_root.exists():
            shutil.rmtree(self.scenarios_root)
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
                connection.execute(
                    text(f"DROP TABLE IF EXISTS {_quoted(str(row[0]))} CASCADE")
                )

    def run_benchmark(self) -> int:
        suite_label = ", ".join(self.suites)
        console.print(f"Starting KROWN benchmark ({suite_label}; {SPARQL_ENGINE})")
        try:
            self.prepare_output_directories()
            scenario_runs: dict[str, list[dict[str, object]]] = {
                scenario.generated_name: [] for scenario in self.scenarios
            }
            failed_scenarios: list[str] = []
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task(
                    "Scenarios", total=len(self.scenarios) * self.iterations
                )
                for scenario in self.scenarios:
                    scenario_path = generate_scenario(
                        scenario, self.scenarios_root, self.data_generator_dir
                    )
                    for iteration in range(1, self.iterations + 1):
                        progress.update(
                            task,
                            description=(
                                f"{scenario.generated_name} "
                                f"({iteration}/{self.iterations})"
                            ),
                        )
                        iteration_started = time.perf_counter()
                        try:
                            result = self.execute_krown_scenario(
                                scenario, scenario_path
                            )
                        except ScenarioExecutionFailure as error:
                            result = self.build_failure_result(
                                scenario,
                                time.perf_counter() - iteration_started,
                                error,
                            )
                            result["iteration"] = iteration
                            scenario_runs[scenario.generated_name].append(result)
                            failed_scenarios.append(scenario.generated_name)
                            skipped_iterations = self.iterations - iteration
                            failure_message = (
                                f"{scenario.generated_name}: {error.outcome} "
                                f"during {error.stage}"
                            )
                            if skipped_iterations:
                                failure_message += (
                                    f"; skipped {skipped_iterations} "
                                    "remaining iterations"
                                )
                            console.print(failure_message)
                            progress.advance(task, advance=skipped_iterations + 1)
                            break
                        result["iteration"] = iteration
                        scenario_runs[scenario.generated_name].append(result)
                        progress.advance(task)

            raw_file, stats_file, stats_data = self.save_results(scenario_runs)
            console.print(f"Raw results saved to {raw_file}")
            console.print(f"Statistics saved to {stats_file}")
            self.print_aggregated_summary(stats_data)
            plot_files = plot_timing_charts(stats_data, self.results_dir)
            for plot_file in plot_files:
                console.print(f"Plot saved to {plot_file}")
            if failed_scenarios:
                console.print(
                    f"Benchmark completed with {len(failed_scenarios)} failed scenarios"
                )
                return 1
            console.print("Benchmark completed with the expected outcomes")
            return 0
        finally:
            self.cleanup()


def positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Iterations must be positive")
    return parsed


def parse_suites(value: str) -> tuple[str, ...]:
    if value == "all":
        return SUITES
    requested = tuple(part.strip() for part in value.split(",") if part.strip())
    unknown = sorted(set(requested) - set(SUITES))
    if not requested or unknown:
        choices = ", ".join(("all", *SUITES))
        raise argparse.ArgumentTypeError(
            f"Suites must be a comma-separated subset of {choices}"
        )
    return tuple(suite for suite in SUITES if suite in requested)


def main() -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Run the official KROWN scenarios relevant to KG inversion"
    )
    parser.add_argument(
        "--iterations",
        type=positive_integer,
        default=1,
        help="Number of times to run each scenario",
    )
    parser.add_argument(
        "--suites",
        type=parse_suites,
        default=SUITES,
        help="Comma-separated suites: raw,mappings,named-graphs,joins (default: all)",
    )
    parser.add_argument(
        "--scenario",
        help="Exact generated name of one scenario to run",
    )
    args = parser.parse_args()
    try:
        runner = KrownBenchmarkRunner(
            iterations=args.iterations,
            suites=args.suites,
            scenario_name=args.scenario,
        )
    except ValueError as error:
        parser.error(str(error))
    return runner.run_benchmark()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

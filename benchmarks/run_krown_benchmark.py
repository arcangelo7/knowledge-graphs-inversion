#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import argparse
import csv
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from rdflib import Graph, Namespace
from rdflib.namespace import RDF
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import Engine

import rmlmapper
from benchmarks.forward_engines import (
    FORWARD_ENGINES,
    ForwardEngine,
    ForwardEngineDefinition,
)
from benchmarks.krown_catalog import (
    KROWN_REPOSITORY,
    SERIES,
    SUITES,
    KrownScenario,
    KrownSeries,
    load_scenarios,
)
from benchmarks.krown_metrics import (
    OfficialKrownExecutor,
    OfficialRunResult,
    SynchronousCollector,
    generate_official_statistics,
    load_mapping_resource,
    read_official_step_summary,
    read_step_duration,
    resource_config_directory,
)
from benchmarks.krown_plots import plot_timing_charts
from benchmarks.krown_stats import (
    aggregate_scenario_statistics,
    calculate_timing_statistics,
)
from benchmarks.krown_validator import KrownValidator
from benchmarks.souffle_inversion import (
    SUPPORT_REPORT,
    SouffleInversionError,
    assemble_rows,
    attach_database_to_krown_network,
    load_relation,
    parse_source_relations,
    preserve_rdf_facts,
    restore_rdf_facts,
    reverse_souffle_resource,
    write_rdf_dataset,
    write_rdf_facts,
)
from kgi.core import reconstruct
from kgi.exceptions import NonInvertibleError

console = Console(width=max(shutil.get_terminal_size().columns, 100))

R2RML = Namespace("http://www.w3.org/ns/r2rml#")
SPARQL_ENGINE = "pyoxigraph"
DATALOG_RDF_FILE = "out.nq"
RMLMAPPER_JAVA_OPTIONS = (
    "-XX:InitialRAMPercentage=50.0",
    "-XX:MaxRAMPercentage=50.0",
)
RMLMAPPER_TIMEOUT_SECONDS = 3 * 60 * 60
KROWN_COOLDOWN_SECONDS = 15
SOURCE_SCHEMA = "source"
DESTINATION_SCHEMA = "destination"
BENCHMARK_DATABASE_CONTAINER = "kgi-benchmark-postgresql"
BENCHMARK_DATABASE_INTERNAL_PORT = 5432
BenchmarkMode = Literal["forward", "backward", "roundtrip"]
InversionEngine = Literal["kgi", "souffle"]

EXIT_TIMEOUT = 20
EXIT_OUT_OF_MEMORY = 21
EXIT_NON_INVERTIBLE = 23


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _format_confidence_interval(statistics: dict[str, object]) -> str:
    mean = cast(float, statistics["mean"])
    lower = cast(float, statistics["ci_95_lower"])
    upper = cast(float, statistics["ci_95_upper"])
    margin = max(mean - lower, upper - mean)
    return f"{mean:.2f}±{margin:.2f}"


def _format_percentage_confidence_interval(
    statistics: dict[str, object],
) -> str:
    mean = cast(float, statistics["mean"])
    lower = cast(float, statistics["ci_95_lower"])
    upper = cast(float, statistics["ci_95_upper"])
    margin = max(mean - lower, upper - mean)
    return f"{mean:.1f}±{margin:.1f}%"


@dataclass(frozen=True)
class SourceTable:
    name: str
    csv_file: Path


@dataclass(frozen=True)
class ForwardMeasurement:
    iteration: int
    rdf_file: Path
    facts_directory: Path | None
    duration: float
    rdf_statements: int
    run_path: Path
    metrics_step: int


@dataclass(frozen=True)
class DatabaseSettings:
    username: str
    password: str
    host: str
    port: int
    database: str

    def sqlalchemy_url(self, schema: str | None = None) -> str:
        url = URL.create(
            "postgresql+psycopg2",
            username=self.username,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
        )
        rendered_url = url.render_as_string(hide_password=False)
        if schema is None:
            return rendered_url
        return f"{rendered_url}?options=-csearch_path={schema}"

    def jdbc_url(self, schema: str) -> str:
        return (
            f"jdbc:postgresql://{self.host}:{self.port}/{self.database}"
            f"?currentSchema={schema}"
        )


HOST_DATABASE = DatabaseSettings(
    username="r2rml",
    password="r2rml",
    host="127.0.0.1",
    port=5434,
    database="r2rml",
)
COMPOSE_DATABASE = DatabaseSettings(
    username="r2rml",
    password="r2rml",
    host="benchmark_postgresql",
    port=5432,
    database="r2rml",
)


class ScenarioExecutionFailure(RuntimeError):
    def __init__(
        self,
        stage: str,
        kind: str,
        message: str,
        diagnostic: str = "",
        elapsed_seconds: float = 0.0,
        iteration: int | None = None,
    ):
        super().__init__(message)
        self.stage = stage
        self.kind = kind
        self.diagnostic = diagnostic
        self.elapsed_seconds = elapsed_seconds
        self.iteration = iteration

    @property
    def outcome(self) -> str:
        return {
            "out_of_memory": "OUT_OF_MEMORY",
            "timeout": "TIMEOUT",
        }[self.kind]


def _rename_nquads_output(rdf_file: Path) -> Path:
    nquads_file = rdf_file.with_suffix(".nq")
    rdf_file.rename(nquads_file)
    return nquads_file


def generate_scenario(
    scenario: KrownScenario,
    scenarios_root: Path,
    data_generator_dir: Path,
    resource: str,
) -> Path:
    if scenarios_root.exists():
        shutil.rmtree(scenarios_root)
    scenarios_root.mkdir(parents=True)

    config = {
        "@id": "http://example.com/kg-inversion-benchmark/generated",
        "name": scenario.display_name,
        "description": "Single KROWN benchmark scenario",
        "instances": [scenario.config_instance(resource)],
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


class ScenarioOperations:
    def __init__(
        self,
        scenario: KrownScenario,
        scenario_path: Path,
        database: DatabaseSettings,
    ):
        self.scenario = scenario
        self.scenario_path = scenario_path
        self.database = database
        metadata_value = json.loads(
            (scenario_path / "metadata.json").read_text(encoding="utf-8")
        )
        self.metadata = cast(dict[str, object], metadata_value)
        self.shared_dir = scenario_path / "data" / "shared"
        self.source_tables = self._source_tables()

    @staticmethod
    def mapping_component_counts(
        mapping_file: Path,
    ) -> tuple[int, int, int, int]:
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

    def _metadata_steps(self) -> list[dict[str, object]]:
        return cast(list[dict[str, object]], self.metadata["steps"])

    def _metadata_step(self, command: str) -> dict[str, object]:
        matching_steps = [
            step for step in self._metadata_steps() if step["command"] == command
        ]
        if len(matching_steps) != 1:
            raise ValueError(f"Expected one {command} step in KROWN metadata")
        return matching_steps[0]

    def _source_tables(self) -> tuple[SourceTable, ...]:
        load_steps = [
            step
            for step in self._metadata_steps()
            if step["command"] in ("load", "load_multiple")
        ]
        if len(load_steps) != 1:
            raise ValueError("Expected one KROWN database load step")
        parameters = cast(dict[str, object], load_steps[0]["parameters"])

        if load_steps[0]["command"] == "load":
            return (
                SourceTable(
                    name=cast(str, parameters["table"]),
                    csv_file=self.shared_dir / cast(str, parameters["csv_file"]),
                ),
            )

        csv_files = cast(list[dict[str, str]], parameters["csv_files"])
        tables = []
        for value in csv_files:
            tables.append(
                SourceTable(
                    name=value["table"],
                    csv_file=self.shared_dir / value["file"],
                )
            )
        return tuple(tables)

    @property
    def mapping_file(self) -> Path:
        parameters = cast(
            dict[str, str],
            self._metadata_step("execute_mapping")["parameters"],
        )
        return self.shared_dir / parameters["mapping_file"]

    def reset_schema(self, schema: str) -> None:
        engine = create_engine(self.database.sqlalchemy_url())
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(f"DROP SCHEMA IF EXISTS {_quoted(schema)} CASCADE")
                )
                connection.execute(text(f"CREATE SCHEMA {_quoted(schema)}"))
        finally:
            engine.dispose()

    def prepare_source(self) -> None:
        self.reset_schema(SOURCE_SCHEMA)
        self.reset_schema(DESTINATION_SCHEMA)
        engine = create_engine(self.database.sqlalchemy_url(SOURCE_SCHEMA))
        try:
            for source_table in self.source_tables:
                self._load_source_table(engine, source_table)
        finally:
            engine.dispose()

    def _load_source_table(self, engine: Engine, source_table: SourceTable) -> None:
        with source_table.csv_file.open(newline="", encoding="utf-8") as file:
            columns = next(csv.reader(file))
        expected_columns = ["id"] + [
            f"p{number}"
            for number in range(
                1,
                cast(int, self.scenario.parameters["number_of_properties"]) + 1,
            )
        ]
        if columns != expected_columns:
            raise ValueError(
                f"Unexpected CSV columns for {self.scenario.generated_name}: {columns}"
            )

        table_name = _quoted(source_table.name)
        definitions = [f"{_quoted('id')} INTEGER PRIMARY KEY"]
        definitions.extend(f"{_quoted(column)} TEXT" for column in columns[1:])
        with engine.begin() as connection:
            connection.execute(text(f"DROP TABLE IF EXISTS {table_name} CASCADE"))
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
        expected_rows = cast(int, self.scenario.parameters["number_of_members"])
        if row_count != expected_rows:
            raise ValueError(
                f"Unexpected row count for {self.scenario.generated_name}/"
                f"{source_table.name}: expected={expected_rows}, actual={row_count}"
            )

    def forward(self, schema: str, rdf_file: Path) -> None:
        rdf_file.unlink(missing_ok=True)
        try:
            process = rmlmapper.execute(
                str(self.mapping_file),
                str(rdf_file),
                serialization="nquads",
                dsn=self.database.jdbc_url(schema),
                username=self.database.username,
                password=self.database.password,
                timeout=RMLMAPPER_TIMEOUT_SECONDS,
                java_options=RMLMAPPER_JAVA_OPTIONS,
            )
        except subprocess.TimeoutExpired as error:
            diagnostic = f"{error.stdout or ''}{error.stderr or ''}"
            raise ScenarioExecutionFailure(
                "rmlmapper",
                "timeout",
                f"RMLMapper timed out after {RMLMAPPER_TIMEOUT_SECONDS} seconds",
                diagnostic,
            ) from error
        if process.returncode != 0:
            diagnostic = process.stdout + process.stderr
            if "OutOfMemoryError" in diagnostic:
                raise ScenarioExecutionFailure(
                    "rmlmapper",
                    "out_of_memory",
                    f"RMLMapper failed with exit code {process.returncode}",
                    diagnostic,
                )
            process.check_returncode()

    def forward_resource(
        self,
        definition: ForwardEngineDefinition,
        schema: str,
        rdf_file: Path,
    ) -> None:
        project_root = Path(__file__).resolve().parent.parent
        resource = load_mapping_resource(project_root, definition)(
            str(self.scenario_path / "data"),
            str(resource_config_directory(project_root)),
            str(self.scenario_path),
            False,
        )
        attach_database_to_krown_network(BENCHMARK_DATABASE_CONTAINER)
        rdf_file.unlink(missing_ok=True)
        if not resource.execute_mapping(
            self.mapping_file.name,
            rdf_file.name,
            "nquads",
            rdb_username=self.database.username,
            rdb_password=self.database.password,
            rdb_host=BENCHMARK_DATABASE_CONTAINER,
            rdb_port=BENCHMARK_DATABASE_INTERNAL_PORT,
            rdb_name=definition.database_name(self.database.database, schema),
            rdb_type="PostgreSQL",
        ):
            raise RuntimeError(
                f"{definition.label} failed to map the {schema} schema of "
                f"{self.scenario.generated_name}"
            )
        if definition.writes_facts:
            write_rdf_dataset(self.shared_dir, rdf_file)

    def backward(self, rdf_file: Path) -> None:
        reconstruct(
            mapping=str(self.mapping_file),
            rdf_graph=str(rdf_file),
            dest_db_url=self.database.sqlalchemy_url(DESTINATION_SCHEMA),
            source_db_url=self.database.sqlalchemy_url(SOURCE_SCHEMA),
        )

    def backward_souffle(self, rdf_file: Path, facts_directory: Path | None) -> None:
        """Invert with the Datalog approach of the KROWN_Extended submodule.

        The reverse Datalog program consumes the RDF graph as tab separated facts
        and derives one tuple per recovered triple, so the tuples are assembled
        into rows before they reach the destination schema. A Datalog forward phase
        already wrote those facts, and reusing them keeps both directions on the terms
        the forward program built.
        """
        project_root = Path(__file__).resolve().parent.parent
        if facts_directory is None:
            write_rdf_facts(rdf_file, self.shared_dir)
        else:
            restore_rdf_facts(facts_directory, self.shared_dir)
        resource = reverse_souffle_resource(project_root)(
            str(self.scenario_path / "data"),
            str(resource_config_directory(project_root)),
            str(self.scenario_path),
            False,
        )
        attach_database_to_krown_network(BENCHMARK_DATABASE_CONTAINER)
        if not resource.execute_mapping(
            self.mapping_file.name,
            "out.nt",
            "ntriples",
            support_report=SUPPORT_REPORT,
            rdb_username=self.database.username,
            rdb_password=self.database.password,
            rdb_host=BENCHMARK_DATABASE_CONTAINER,
            rdb_port=BENCHMARK_DATABASE_INTERNAL_PORT,
            rdb_name=f"{self.database.database}?currentSchema={SOURCE_SCHEMA}",
            rdb_type="PostgreSQL",
        ):
            raise SouffleInversionError(
                f"ReverseSouffle failed for {self.scenario.generated_name}"
            )

        engine = create_engine(self.database.sqlalchemy_url())
        try:
            for relation in parse_source_relations(self.shared_dir):
                load_relation(
                    engine,
                    relation,
                    assemble_rows(self.shared_dir, relation),
                    SOURCE_SCHEMA,
                    DESTINATION_SCHEMA,
                )
        finally:
            engine.dispose()


def _scenario_by_name(project_root: Path, name: str) -> KrownScenario:
    matching = [
        scenario
        for scenario in load_scenarios(project_root)
        if scenario.generated_name == name
    ]
    if len(matching) != 1:
        raise ValueError(f"Unknown KROWN scenario: {name}")
    return matching[0]


def _internal_stage_main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        required=True,
        choices=(
            "prepare",
            "reset-destination",
            "forward-destination",
            "backward",
        ),
    )
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--scenario-path", type=Path, required=True)
    parser.add_argument("--rdf-file", type=Path)
    args = parser.parse_args(arguments)

    project_root = Path(__file__).resolve().parent.parent
    scenario = _scenario_by_name(project_root, args.scenario)
    scenario_path = project_root / args.scenario_path
    operations = ScenarioOperations(scenario, scenario_path, COMPOSE_DATABASE)
    try:
        if args.stage == "prepare":
            operations.prepare_source()
        elif args.stage == "reset-destination":
            operations.reset_schema(DESTINATION_SCHEMA)
        else:
            if args.rdf_file is None:
                parser.error(f"--rdf-file is required for {args.stage}")
            rdf_file = project_root / args.rdf_file
            if args.stage == "forward-destination":
                operations.forward(DESTINATION_SCHEMA, rdf_file)
            else:
                operations.backward(rdf_file)
    except ScenarioExecutionFailure as error:
        if error.diagnostic:
            sys.stderr.write(error.diagnostic)
        if error.kind == "timeout":
            return EXIT_TIMEOUT
        if error.kind == "out_of_memory":
            return EXIT_OUT_OF_MEMORY
        raise
    except NonInvertibleError as error:
        if scenario.expected_outcome != "NON_INVERTIBLE":
            raise
        sys.stderr.write(f"{error}\n")
        return EXIT_NON_INVERTIBLE
    except MemoryError:
        return EXIT_OUT_OF_MEMORY
    return 0


class KrownBenchmarkRunner:
    def __init__(
        self,
        mode: BenchmarkMode,
        iterations: int,
        sample_interval: float,
        suites: tuple[str, ...],
        scenario_name: str | None,
        forward_engine: ForwardEngine = "rmlmapper",
        inversion_engine: InversionEngine = "kgi",
        cleanup_tables: bool = True,
        resume_session: Path | None = None,
    ):
        self.project_root = Path(__file__).resolve().parent.parent
        self.data_generator_dir = self.project_root / "KROWN" / "data-generator"
        benchmark_dir = Path(__file__).resolve().parent / "krown"
        self.scenarios_root = benchmark_dir / "scenarios"
        self.results_dir = benchmark_dir / "results"
        self.mode = mode
        self.forward_engine = forward_engine
        self.forward_definition = FORWARD_ENGINES[forward_engine]
        self.inversion_engine = inversion_engine
        self.iterations = iterations
        self.sample_interval = sample_interval
        self.cleanup_tables = cleanup_tables
        self.local_database_started = False
        self.database = HOST_DATABASE
        self.validator = KrownValidator(
            self.database.sqlalchemy_url(),
            SOURCE_SCHEMA,
            DESTINATION_SCHEMA,
        )
        self.compose_file = self.project_root / "docker-compose.benchmark.yml"
        if resume_session is None:
            self.timestamp = int(time.time())
            self.session_dir = self.results_dir / (
                f"krown_{self.timestamp}_{self.mode}_"
                f"{self.forward_engine}_{self.inversion_engine}"
            )
            self.measured_runs: dict[str, list[dict[str, object]]] = {}
        else:
            self.session_dir = resume_session
            self.timestamp, self.measured_runs = self._read_partial_results(
                resume_session
            )
        self.resource_summaries: dict[str, list[dict[str, int | float | str]]] = {}

        catalog = load_scenarios(self.project_root)
        scenario_names = {scenario.generated_name for scenario in catalog}
        if scenario_name is not None and scenario_name not in scenario_names:
            raise ValueError(f"Unknown KROWN scenario: {scenario_name}")
        selected = tuple(scenario for scenario in catalog if scenario.suite in suites)
        if scenario_name is not None:
            selected = tuple(
                scenario
                for scenario in selected
                if scenario.generated_name == scenario_name
            )
            if not selected:
                raise ValueError(
                    f"KROWN scenario {scenario_name} is not included in "
                    f"selected suites: {', '.join(suites)}"
                )
        self.scenarios = selected
        self.suites = tuple(
            suite
            for suite in SUITES
            if any(scenario.suite == suite for scenario in selected)
        )
        selected_names = {scenario.generated_name for scenario in selected}
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
        unselected = sorted(set(self.measured_runs) - selected_names)
        if unselected:
            raise ValueError(
                "The session to continue holds scenarios outside the selected "
                f"suites, which the results would drop: {', '.join(unselected)}"
            )
        self.krown_commit = subprocess.run(
            ["git", "-C", str(self.project_root / "KROWN"), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _read_partial_results(
        self,
        session_dir: Path,
    ) -> tuple[int, dict[str, list[dict[str, object]]]]:
        """Read the runs an interrupted session measured before it stopped."""
        partial_files = sorted(
            session_dir.glob("krown_benchmark_results_partial_*.json")
        )
        if len(partial_files) != 1:
            raise ValueError(
                f"{session_dir} holds {len(partial_files)} partial result files "
                "instead of one"
            )
        payload = cast(
            dict[str, object],
            json.loads(partial_files[0].read_text(encoding="utf-8")),
        )
        requested: dict[str, object] = {
            "mode": self.mode,
            "iterations": self.iterations,
            "sample_interval_seconds": self.sample_interval,
            "forward_engine": self.forward_engine,
            "inversion_engine": self.inversion_engine,
        }
        mismatched = {
            key: f"{payload[key]} instead of {value}"
            for key, value in requested.items()
            if payload[key] != value
        }
        if mismatched:
            raise ValueError(
                f"{partial_files[0].name} was measured with a different "
                f"configuration: {mismatched}"
            )
        return (
            cast(int, payload["timestamp"]),
            cast(dict[str, list[dict[str, object]]], payload["scenarios"]),
        )

    def _restore_resource_summaries(
        self,
        scenario: KrownScenario,
        runs: list[dict[str, object]],
    ) -> None:
        """Reuse the resource summaries the interrupted session left on disk."""
        stages = cast(
            dict[str, dict[str, object]],
            cast(dict[str, object], runs[0]["metrics"])["stages"],
        )
        for stage_name in self.measured_stages:
            summary = dict(
                read_official_step_summary(
                    self._case_directory(scenario) / stage_name / "results",
                    cast(int, stages[stage_name]["step"]),
                )
            )
            summary["stage_name"] = stage_name
            self.resource_summaries.setdefault(scenario.generated_name, []).append(
                summary
            )

    @property
    def measured_stages(self) -> tuple[str, ...]:
        if self.mode == "forward":
            return ("forward",)
        if self.mode == "backward":
            return ("backward",)
        return ("forward", "backward")

    def prepare_output_directories(self) -> None:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(exist_ok=True)
        if self.scenarios_root.exists():
            shutil.rmtree(self.scenarios_root)
        self.scenarios_root.mkdir(parents=True)

    def _stage_command(
        self,
        stage: str,
        scenario: KrownScenario,
        scenario_path: Path,
        rdf_file: Path | None,
    ) -> list[str]:
        command = [
            "docker",
            "compose",
            "-f",
            str(self.compose_file),
            "run",
            "--rm",
            "--entrypoint",
            "uv",
            "benchmark",
            "run",
            "python",
            "-m",
            "benchmarks.run_krown_benchmark",
            "_stage",
            "--stage",
            stage,
            "--scenario",
            scenario.generated_name,
            "--scenario-path",
            str(scenario_path.relative_to(self.project_root)),
        ]
        if rdf_file is not None:
            command.extend(("--rdf-file", str(rdf_file.relative_to(self.project_root))))
        return command

    def run_stage(
        self,
        stage: str,
        result_stage: str,
        scenario: KrownScenario,
        scenario_path: Path,
        rdf_file: Path | None = None,
    ) -> None:
        started = time.perf_counter()
        process = subprocess.run(
            self._stage_command(stage, scenario, scenario_path, rdf_file),
            capture_output=True,
            text=True,
        )
        elapsed = time.perf_counter() - started
        if process.returncode == 0:
            return

        diagnostic = process.stdout + process.stderr
        if process.returncode == EXIT_TIMEOUT:
            kind = "timeout"
        elif process.returncode in (EXIT_OUT_OF_MEMORY, 137):
            kind = "out_of_memory"
        elif process.returncode == EXIT_NON_INVERTIBLE:
            if scenario.expected_outcome == "NON_INVERTIBLE":
                raise NonInvertibleError(diagnostic)
            raise RuntimeError(
                f"KROWN stage {stage} reported an unexpected non-invertible "
                f"mapping:\n{diagnostic}"
            )
        else:
            raise RuntimeError(
                f"KROWN stage {stage} failed with exit code "
                f"{process.returncode}:\n{diagnostic}"
            )
        raise ScenarioExecutionFailure(
            result_stage,
            kind,
            f"KROWN stage {result_stage} failed",
            diagnostic,
            elapsed,
        )

    def validate_forward_output(
        self,
        scenario: KrownScenario,
        operations: ScenarioOperations,
        rdf_file: Path,
    ) -> int:
        rdf_statements = operations.count_rdf_statements(rdf_file)
        expected = scenario.expected_rdf_statements
        if expected is not None and rdf_statements != expected:
            raise ValueError(
                (
                    f"Unexpected RDF statement count for "
                    f"{scenario.generated_name}: expected={expected}, "
                    f"actual={rdf_statements}"
                ),
            )
        return rdf_statements

    def _case_directory(self, scenario: KrownScenario) -> Path:
        return self.session_dir / scenario.generated_name

    def _build_result(
        self,
        scenario: KrownScenario,
        operations: ScenarioOperations,
        timings: dict[str, float],
        rdf_statements: int,
        inversion_count: int,
        validation_results: dict[str, object],
        metrics: dict[str, dict[str, object]],
    ) -> dict[str, object]:
        (
            triples_maps,
            predicate_object_maps,
            join_conditions,
            graph_maps,
        ) = operations.mapping_component_counts(operations.mapping_file)
        data_size_bytes = sum(
            source_table.csv_file.stat().st_size
            for source_table in operations.source_tables
        )
        execution_time = sum(
            timings[name]
            for name in ("forward_time", "inversion_time")
            if name in timings
        )
        throughput = None
        if (
            "inversion_time" in timings
            and validation_results["outcome"] != "NON_INVERTIBLE"
        ):
            throughput = {
                "rows_per_second": (scenario.source_rows / timings["inversion_time"]),
                "cells_per_second": (scenario.source_cells / timings["inversion_time"]),
            }

        timing_breakdown: dict[str, float] = {
            **timings,
            "total_time": execution_time,
        }
        if self.mode == "roundtrip":
            timing_breakdown["inversion_overhead_percentage"] = (
                timings["inversion_time"] / timings["forward_time"] * 100
            )

        return {
            "status": "completed",
            "scenario_name": scenario.generated_name,
            "display_name": scenario.display_name,
            "suite": scenario.suite,
            "generator": scenario.generator,
            "expected_outcome": scenario.expected_outcome,
            "parameters": scenario.parameters,
            "source_configuration": {
                "identifier": scenario.identifier,
                "file": scenario.source_config,
                "overrides": scenario.configuration_overrides,
            },
            "execution_time": execution_time,
            "timing_breakdown": timing_breakdown,
            "throughput": throughput,
            "mapping_file": str(operations.mapping_file),
            "data_files": [str(table.csv_file) for table in operations.source_tables],
            "mapping_size_bytes": operations.mapping_file.stat().st_size,
            "data_size_bytes": data_size_bytes,
            "rdf_statements": rdf_statements,
            "source_rows": scenario.source_rows,
            "source_cells": scenario.source_cells,
            "triples_maps_count": triples_maps,
            "predicate_object_maps_count": predicate_object_maps,
            "join_conditions_count": join_conditions,
            "graph_maps_count": graph_maps,
            "inversion_count": inversion_count,
            "metrics": {
                "scope": "system",
                "stages": metrics,
            },
            "validation_results": validation_results,
        }

    @staticmethod
    def _build_failure_result(
        scenario: KrownScenario,
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
            "source_configuration": {
                "identifier": scenario.identifier,
                "file": scenario.source_config,
                "overrides": scenario.configuration_overrides,
            },
            "execution_time": error.elapsed_seconds,
            "failure": {
                "stage": error.stage,
                "kind": error.kind,
                "outcome": error.outcome,
                "message": str(error),
                "diagnostic": error.diagnostic,
            },
        }

    def _validate_inversion(
        self,
        scenario: KrownScenario,
        operations: ScenarioOperations,
        original_rdf: Path,
        iteration: int,
    ) -> dict[str, object]:
        expected_tables = [table.name for table in operations.source_tables]
        # The mapping reads columns the reconstruction could not recover, so it cannot
        # rebuild the graph and running it would only fail on the absent columns
        missing_mapped_columns = self.validator.missing_mapped_columns(
            expected_tables, operations.mapping_file
        )
        roundtrip_rdf = None
        if not missing_mapped_columns:
            roundtrip_rdf = operations.shared_dir / f"roundtrip_{iteration}.nq"
            if self.forward_engine == "rmlmapper":
                self.run_stage(
                    "forward-destination",
                    "roundtrip_mapping",
                    scenario,
                    operations.scenario_path,
                    roundtrip_rdf,
                )
            else:
                operations.forward_resource(
                    self.forward_definition,
                    DESTINATION_SCHEMA,
                    roundtrip_rdf,
                )
        validation = self.validator.validate_inversion(
            expected_tables=expected_tables,
            scenario_name=scenario.generated_name,
            expected_outcome=scenario.expected_outcome,
            original_rdf=original_rdf,
            roundtrip_rdf=roundtrip_rdf,
            missing_mapped_columns=missing_mapped_columns,
        )
        if validation["validation_passed"] is not True:
            raise ValueError(f"Unexpected inversion result: {validation}")
        return validation

    def _preserve_forward_results(
        self,
        scenario: KrownScenario,
        executor: OfficialKrownExecutor,
        phase: str,
    ) -> Path:
        destination = self._case_directory(scenario) / phase / "results"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(executor.results_path), destination)
        return destination

    @staticmethod
    def _forward_failure(
        result: OfficialRunResult,
        executor: OfficialKrownExecutor,
        iteration: int,
    ) -> ScenarioExecutionFailure:
        if result.failed_resource != executor.resource:
            raise RuntimeError(
                f"KROWN Executor failed outside {executor.resource}: "
                f"resource={result.failed_resource}, step={result.failed_step}\n"
                f"{result.diagnostic}"
            )
        diagnostic_lower = result.diagnostic.lower()
        if "outofmemoryerror" in diagnostic_lower:
            kind = "out_of_memory"
        elif "timeout" in diagnostic_lower:
            kind = "timeout"
        else:
            raise RuntimeError(
                f"KROWN Executor {executor.resource} step failed:\n" + result.diagnostic
            )
        metrics_file = executor.results_path / f"run_{iteration}" / "metrics.csv"
        duration = read_step_duration(metrics_file, executor.mapping_step)
        return ScenarioExecutionFailure(
            "forward_mapping",
            kind,
            f"KROWN Executor {executor.resource} step failed",
            result.diagnostic,
            duration,
            iteration,
        )

    def _run_forward_phase(
        self,
        scenario: KrownScenario,
        operations: ScenarioOperations,
        runs: int,
        phase: str,
        report_statistics: bool,
        progress: Progress,
        task: TaskID,
    ) -> list[ForwardMeasurement]:
        executor = OfficialKrownExecutor(
            self.project_root,
            operations.scenario_path,
            self.forward_definition,
        )
        engine_directory = self.forward_definition.module_name
        for iteration in range(1, runs + 1):
            progress.update(
                task,
                description=(f"{scenario.generated_name} forward ({iteration}/{runs})"),
            )
            result = executor.run(
                self.sample_interval,
                iteration,
                iteration == runs,
            )
            if not result.success:
                error = self._forward_failure(result, executor, iteration)
                self._preserve_forward_results(scenario, executor, phase)
                raise error
            if self.forward_definition.writes_facts:
                preserve_rdf_facts(
                    operations.shared_dir,
                    executor.results_path / f"run_{iteration}" / engine_directory,
                )

        if report_statistics:
            summary = executor.statistics()
            forward_summary = dict(summary[executor.mapping_step - 1])
            forward_summary["stage_name"] = "forward"
            self.resource_summaries.setdefault(scenario.generated_name, []).append(
                forward_summary
            )

        results_path = self._preserve_forward_results(
            scenario,
            executor,
            phase,
        )
        measurements = []
        for iteration in range(1, runs + 1):
            run_path = results_path / f"run_{iteration}"
            engine_path = run_path / engine_directory
            facts_directory = None
            if self.forward_definition.writes_facts:
                facts_directory = engine_path
                rdf_file = engine_path / DATALOG_RDF_FILE
                write_rdf_dataset(facts_directory, rdf_file)
            else:
                rdf_file = engine_path / executor.output_file
                if scenario.generator == "NamedGraph":
                    rdf_file = _rename_nquads_output(rdf_file)
            rdf_statements = self.validate_forward_output(
                scenario,
                operations,
                rdf_file,
            )
            measurements.append(
                ForwardMeasurement(
                    iteration=iteration,
                    rdf_file=rdf_file,
                    facts_directory=facts_directory,
                    duration=read_step_duration(
                        run_path / "metrics.csv",
                        executor.mapping_step,
                    ),
                    rdf_statements=rdf_statements,
                    run_path=run_path,
                    metrics_step=executor.mapping_step,
                )
            )
        return measurements

    @staticmethod
    def _forward_metrics(
        measurement: ForwardMeasurement,
    ) -> dict[str, object]:
        return {
            "executor": "KROWN Executor",
            "metrics_file": str(measurement.run_path / "metrics.csv"),
            "case_info_file": str(measurement.run_path / "case-info.txt"),
            "step": measurement.metrics_step,
        }

    @staticmethod
    def _backward_metrics(run_path: Path) -> dict[str, object]:
        return {
            "executor": "local inversion",
            "metrics_file": str(run_path / "metrics.csv"),
            "case_info_file": str(run_path / "case-info.txt"),
            "step": 1,
        }

    def _build_forward_result(
        self,
        scenario: KrownScenario,
        operations: ScenarioOperations,
        measurement: ForwardMeasurement,
    ) -> dict[str, object]:
        return self._build_result(
            scenario,
            operations,
            {"forward_time": measurement.duration},
            measurement.rdf_statements,
            0,
            {
                "scenario": scenario.generated_name,
                "validation_passed": True,
                "expected_outcome": None,
                "outcome": "FORWARD",
                "checks": {"rdf_statements": True},
            },
            {"forward": self._forward_metrics(measurement)},
        )

    def _prepare_local_source(
        self,
        scenario: KrownScenario,
        operations: ScenarioOperations,
    ) -> None:
        self.run_stage(
            "prepare",
            "database_setup",
            scenario,
            operations.scenario_path,
        )
        self.local_database_started = True

    def _execute_backward_iteration(
        self,
        scenario: KrownScenario,
        operations: ScenarioOperations,
        iteration: int,
        forward: ForwardMeasurement,
    ) -> dict[str, object]:
        self.run_stage(
            "reset-destination",
            "destination_setup",
            scenario,
            operations.scenario_path,
        )
        case_directory = self._case_directory(scenario) / "backward"
        run_path = case_directory / "results" / f"run_{iteration}"
        run_path.mkdir(parents=True)
        collector = SynchronousCollector(
            project_root=self.project_root,
            case_name=f"{scenario.generated_name}:backward",
            run_path=run_path,
            sample_interval=self.sample_interval,
            number_of_steps=1,
            run_id=iteration,
            case_directory=case_directory,
        )
        scenario_failure: ScenarioExecutionFailure | None = None
        non_invertible_error: NonInvertibleError | None = None
        try:
            if self.inversion_engine == "souffle":
                operations.backward_souffle(forward.rdf_file, forward.facts_directory)
            else:
                self.run_stage(
                    "backward",
                    "inversion",
                    scenario,
                    operations.scenario_path,
                    forward.rdf_file,
                )
        except ScenarioExecutionFailure as error:
            if error.kind not in ("timeout", "out_of_memory"):
                raise
            scenario_failure = error
        except NonInvertibleError as error:
            if scenario.expected_outcome != "NON_INVERTIBLE":
                raise
            non_invertible_error = error
        finally:
            collector.stop()

        inversion_time = read_step_duration(run_path / "metrics.csv", 1)
        time.sleep(KROWN_COOLDOWN_SECONDS)
        if scenario_failure is not None:
            raise ScenarioExecutionFailure(
                scenario_failure.stage,
                scenario_failure.kind,
                str(scenario_failure),
                scenario_failure.diagnostic,
                inversion_time,
                iteration,
            ) from scenario_failure

        if self.mode == "roundtrip":
            timings = {
                "forward_time": forward.duration,
                "inversion_time": inversion_time,
            }
            metrics = {
                "forward": self._forward_metrics(forward),
                "backward": self._backward_metrics(run_path),
            }
        else:
            timings = {"inversion_time": inversion_time}
            metrics = {"backward": self._backward_metrics(run_path)}

        if non_invertible_error is not None:
            result = self._build_result(
                scenario,
                operations,
                timings,
                forward.rdf_statements,
                0,
                {
                    "scenario": scenario.generated_name,
                    "validation_passed": True,
                    "expected_outcome": scenario.expected_outcome,
                    "outcome": "NON_INVERTIBLE",
                    "error": str(non_invertible_error),
                },
                metrics,
            )
        else:
            validation = self._validate_inversion(
                scenario,
                operations,
                forward.rdf_file,
                iteration,
            )
            result = self._build_result(
                scenario,
                operations,
                timings,
                forward.rdf_statements,
                len(operations.source_tables),
                validation,
                metrics,
            )
        return result

    def _generate_backward_statistics(self, scenario: KrownScenario) -> None:
        case_directory = self._case_directory(scenario) / "backward"
        summary = generate_official_statistics(
            project_root=self.project_root,
            results_path=case_directory / "results",
            number_of_steps=1,
            case_directory=case_directory,
        )
        backward_summary = dict(summary[0])
        backward_summary["stage_name"] = "backward"
        self.resource_summaries.setdefault(scenario.generated_name, []).append(
            backward_summary
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

    def _common_payload(
        self,
        scenario_runs: dict[str, list[dict[str, object]]],
    ) -> dict[str, object]:
        failed_scenarios = sum(
            any(run["status"] == "failed" for run in runs)
            for runs in scenario_runs.values()
        )
        common = {
            "timestamp": self.timestamp,
            "benchmark_type": "KROWN",
            "framework": "KROWN Executor with local inversion",
            "environment": "KROWN Executor and Docker Compose",
            "mode": self.mode,
            "forward_engine": self.forward_engine,
            "inversion_engine": self.inversion_engine,
            "measurement_scope": "system",
            "sample_interval_seconds": self.sample_interval,
            "sparql_engine": SPARQL_ENGINE,
            "iterations": self.iterations,
            "failed_scenarios": failed_scenarios,
            "suites": list(self.suites),
            "series": self._series_data(),
            "provenance": {
                "krown_repository": KROWN_REPOSITORY,
                "krown_commit": self.krown_commit,
                "forward_engine_version": self.forward_definition.version,
                "forward_executor": "KROWN Executor",
                "backward_executor": (
                    "KROWN_Extended ReverseSouffle"
                    if self.inversion_engine == "souffle"
                    else "local inversion adapter"
                ),
                "metrics_implementation": "KROWN Collector and Stats",
                "forward_postgresql_version": "14.5",
                "backward_postgresql_version": "13",
                "rdf_serialization": "KROWN scenario metadata",
                "measured_stages": list(self.measured_stages),
                "excluded_from_measurement": [
                    "generation",
                    "database_setup",
                    "backward_rdf_preparation",
                    "validation",
                    "cooldown",
                ],
            },
        }
        return common

    def _write_raw_results(
        self,
        scenario_runs: dict[str, list[dict[str, object]]],
        raw_file: Path,
    ) -> dict[str, object]:
        common = self._common_payload(scenario_runs)
        raw_data = {**common, "scenarios": scenario_runs}
        raw_file.write_text(json.dumps(raw_data, indent=2) + "\n", encoding="utf-8")
        return common

    def save_partial_results(
        self,
        scenario_runs: dict[str, list[dict[str, object]]],
    ) -> Path:
        """Record the runs measured before an aborted benchmark stopped.

        Statistics are omitted: aggregation assumes every iteration completed.
        """
        partial_file = (
            self.session_dir / f"krown_benchmark_results_partial_{self.timestamp}.json"
        )
        measured = {name: runs for name, runs in scenario_runs.items() if runs}
        self._write_raw_results(measured, partial_file)
        return partial_file

    def save_results(
        self,
        scenario_runs: dict[str, list[dict[str, object]]],
    ) -> tuple[Path, Path, dict[str, object]]:
        raw_file = (
            self.session_dir / f"krown_benchmark_results_raw_{self.timestamp}.json"
        )
        stats_file = (
            self.session_dir / f"krown_benchmark_results_stats_{self.timestamp}.json"
        )
        common = self._write_raw_results(scenario_runs, raw_file)

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
                "source_configuration": {
                    "identifier": scenario.identifier,
                    "file": scenario.source_config,
                    "overrides": scenario.configuration_overrides,
                },
                "raw_runs": runs,
            }
            if failed_run is not None:
                scenario_data["status"] = "failed"
                scenario_data["failure"] = failed_run["failure"]
                scenario_data["statistics"] = None
                scenario_data["resource_summary"] = None
            else:
                statistics = aggregate_scenario_statistics(runs)
                throughputs = [
                    cast(dict[str, float], run["throughput"])
                    for run in runs
                    if run["throughput"] is not None
                ]
                if throughputs:
                    statistics["rows_per_second"] = calculate_timing_statistics(
                        [throughput["rows_per_second"] for throughput in throughputs]
                    )
                    statistics["cells_per_second"] = calculate_timing_statistics(
                        [throughput["cells_per_second"] for throughput in throughputs]
                    )
                scenario_data["status"] = "completed"
                scenario_data["statistics"] = statistics
                scenario_data["resource_summary"] = self.resource_summaries[
                    scenario.generated_name
                ]
            aggregated_scenarios[scenario.generated_name] = scenario_data

        stats_data: dict[str, object] = {
            **common,
            "scenarios": aggregated_scenarios,
        }
        stats_file.write_text(json.dumps(stats_data, indent=2) + "\n", encoding="utf-8")
        return raw_file, stats_file, stats_data

    def print_aggregated_summary(self, stats_data: dict[str, object]) -> None:
        scenarios_data = cast(
            dict[str, dict[str, object]],
            stats_data["scenarios"],
        )
        for series in self.series:
            table = Table(
                title=(
                    f"KROWN {series.title} ({self.mode}; {self.iterations} iterations)"
                )
            )
            table.add_column(series.parameter_label, justify="right")
            table.add_column("CSV MiB", justify="right")
            table.add_column("RDF statements", justify="right")
            if self.mode in ("forward", "roundtrip"):
                table.add_column(self.forward_definition.label, justify="right")
            if self.mode in ("backward", "roundtrip"):
                table.add_column("Inversion", justify="right")
                table.add_column("Rows/s", justify="right")
            if self.mode == "roundtrip":
                table.add_column("Overhead", justify="right")
            table.add_column("Outcome")

            for scenario_name, parameter_value in series.points:
                scenario_data = scenarios_data[scenario_name]
                parameter_label = (
                    f"{parameter_value:,}"
                    if isinstance(parameter_value, (int, float))
                    else str(parameter_value)
                )
                if scenario_data["status"] == "failed":
                    failure = cast(dict[str, object], scenario_data["failure"])
                    empty_columns = len(table.columns) - 2
                    table.add_row(
                        parameter_label,
                        *(["-"] * empty_columns),
                        str(failure["outcome"]),
                    )
                    continue

                statistics = cast(dict[str, object], scenario_data["statistics"])
                metadata = cast(dict[str, object], statistics["metadata"])
                raw_runs = cast(list[dict[str, object]], scenario_data["raw_runs"])
                validation = cast(
                    dict[str, object],
                    raw_runs[0]["validation_results"],
                )
                row = [
                    parameter_label,
                    f"{cast(int, metadata['data_size_bytes']) / (1024**2):.2f}",
                    f"{cast(int, metadata['rdf_statements']):,}",
                ]
                if self.mode in ("forward", "roundtrip"):
                    row.append(
                        _format_confidence_interval(
                            cast(dict[str, object], statistics["forward_time"])
                        )
                    )
                if self.mode in ("backward", "roundtrip"):
                    row.append(
                        _format_confidence_interval(
                            cast(dict[str, object], statistics["inversion_time"])
                        )
                    )
                    if "rows_per_second" in statistics:
                        rows_statistics = cast(
                            dict[str, object],
                            statistics["rows_per_second"],
                        )
                        row.append(f"{cast(float, rows_statistics['mean']):,.0f}")
                    else:
                        row.append("-")
                if self.mode == "roundtrip":
                    row.append(
                        _format_percentage_confidence_interval(
                            cast(
                                dict[str, object],
                                statistics["inversion_overhead_percentage"],
                            )
                        )
                    )
                row.append(str(validation["outcome"]))
                table.add_row(*row)
            console.print(table)

    def cleanup(self) -> None:
        self.validator.dispose()
        if self.scenarios_root.exists():
            shutil.rmtree(self.scenarios_root)
        if not self.cleanup_tables or not self.local_database_started:
            return
        engine = create_engine(self.database.sqlalchemy_url())
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(f"DROP SCHEMA IF EXISTS {_quoted(SOURCE_SCHEMA)} CASCADE")
                )
                connection.execute(
                    text(f"DROP SCHEMA IF EXISTS {_quoted(DESTINATION_SCHEMA)} CASCADE")
                )
        finally:
            engine.dispose()

    def run_benchmark(self) -> int:
        console.print(
            f"Starting KROWN benchmark "
            f"({', '.join(self.suites)}; {self.mode}; system metrics)"
        )
        scenario_runs: dict[str, list[dict[str, object]]] = {
            scenario.generated_name: [] for scenario in self.scenarios
        }
        scenario_runs.update(self.measured_runs)
        failed_scenarios: list[str] = [
            name
            for name, runs in self.measured_runs.items()
            if any(run["status"] == "failed" for run in runs)
        ]
        results_saved = False
        try:
            self.prepare_output_directories()
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                stages_per_iteration = 2 if self.mode == "roundtrip" else 1
                task = progress.add_task(
                    "Scenarios",
                    total=(
                        len(self.scenarios) * self.iterations * stages_per_iteration
                    ),
                )
                for scenario in self.scenarios:
                    measured = scenario_runs[scenario.generated_name]
                    if measured:
                        if scenario.generated_name not in failed_scenarios:
                            self._restore_resource_summaries(scenario, measured)
                        progress.advance(
                            task,
                            advance=self.iterations * stages_per_iteration,
                        )
                        continue
                    scenario_path = generate_scenario(
                        scenario,
                        self.scenarios_root,
                        self.data_generator_dir,
                        self.forward_definition.resource,
                    )
                    operations = ScenarioOperations(
                        scenario, scenario_path, self.database
                    )
                    report_forward = self.mode in ("forward", "roundtrip")
                    forward_runs = self.iterations if report_forward else 1
                    forward_phase = "forward" if report_forward else "preparation"
                    try:
                        forward_measurements = self._run_forward_phase(
                            scenario,
                            operations,
                            forward_runs,
                            forward_phase,
                            report_forward,
                            progress,
                            task,
                        )
                    except ScenarioExecutionFailure as error:
                        result = self._build_failure_result(scenario, error)
                        result["iteration"] = error.iteration or 1
                        scenario_runs[scenario.generated_name].append(result)
                        failed_scenarios.append(scenario.generated_name)
                        skipped = self.iterations - int(result["iteration"])
                        console.print(
                            f"{scenario.generated_name}: {error.outcome} "
                            f"during {error.stage}; skipped {skipped} "
                            "remaining iterations"
                        )
                        progress.advance(
                            task,
                            advance=self.iterations * stages_per_iteration,
                        )
                        continue

                    if report_forward:
                        progress.advance(task, advance=self.iterations)
                    if self.mode == "forward":
                        for measurement in forward_measurements:
                            result = self._build_forward_result(
                                scenario,
                                operations,
                                measurement,
                            )
                            result["iteration"] = measurement.iteration
                            scenario_runs[scenario.generated_name].append(result)
                        continue

                    self._prepare_local_source(scenario, operations)
                    scenario_failed = False
                    for iteration in range(1, self.iterations + 1):
                        progress.update(
                            task,
                            description=(
                                f"{scenario.generated_name} "
                                f"backward ({iteration}/{self.iterations})"
                            ),
                        )
                        forward = (
                            forward_measurements[iteration - 1]
                            if self.mode == "roundtrip"
                            else forward_measurements[0]
                        )
                        try:
                            result = self._execute_backward_iteration(
                                scenario,
                                operations,
                                iteration,
                                forward,
                            )
                        except ScenarioExecutionFailure as error:
                            result = self._build_failure_result(scenario, error)
                            result["iteration"] = iteration
                            scenario_runs[scenario.generated_name].append(result)
                            failed_scenarios.append(scenario.generated_name)
                            skipped = self.iterations - iteration
                            console.print(
                                f"{scenario.generated_name}: {error.outcome} "
                                f"during {error.stage}; skipped {skipped} "
                                "remaining iterations"
                            )
                            progress.advance(task, advance=skipped + 1)
                            scenario_failed = True
                            break
                        result["iteration"] = iteration
                        scenario_runs[scenario.generated_name].append(result)
                        progress.advance(task)
                    if not scenario_failed:
                        self._generate_backward_statistics(scenario)

            raw_file, stats_file, stats_data = self.save_results(scenario_runs)
            results_saved = True
            console.print(f"Raw results saved to {raw_file}")
            console.print(f"Statistics saved to {stats_file}")
            self.print_aggregated_summary(stats_data)
            for plot_file in plot_timing_charts(stats_data, self.session_dir):
                console.print(f"Plot saved to {plot_file}")
            if failed_scenarios:
                console.print(
                    f"Benchmark completed with {len(failed_scenarios)} failed scenarios"
                )
                return 1
            console.print("Benchmark completed with the expected outcomes")
            return 0
        finally:
            if not results_saved and any(scenario_runs.values()):
                partial_file = self.save_partial_results(scenario_runs)
                console.print(f"Partial results saved to {partial_file}")
            self.cleanup()


def parse_iterations(value: str) -> int:
    parsed = int(value)
    if parsed < 3 or parsed % 2 == 0:
        raise argparse.ArgumentTypeError(
            "Iterations must be odd and greater than or equal to 3"
        )
    return parsed


def parse_sample_interval(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Sample interval must be positive")
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


def _benchmark_main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Run the KROWN scenarios relevant to KG inversion"
    )
    parser.add_argument(
        "--mode",
        choices=("forward", "backward", "roundtrip"),
        default="roundtrip",
    )
    parser.add_argument(
        "--iterations",
        type=parse_iterations,
        default=5,
        help="Odd number of runs per scenario, at least 3",
    )
    parser.add_argument(
        "--interval",
        type=parse_sample_interval,
        default=0.1,
        help="System metric sample interval in seconds",
    )
    parser.add_argument(
        "--suites",
        type=parse_suites,
        default=SUITES,
        help="Comma-separated suites: raw,mappings,named-graphs,joins",
    )
    parser.add_argument("--scenario")
    parser.add_argument(
        "--resume",
        type=Path,
        help="Session directory of an interrupted run to continue",
    )
    parser.add_argument(
        "--forward-engine",
        choices=tuple(FORWARD_ENGINES),
        default="rmlmapper",
        help="Materialization engine, also used by the round trip validation",
    )
    parser.add_argument(
        "--inversion-engine",
        choices=("kgi", "souffle"),
        default="kgi",
        help="Inversion engine: kgi (SPARQL) or souffle (Datalog)",
    )
    args = parser.parse_args(arguments)
    try:
        runner = KrownBenchmarkRunner(
            mode=args.mode,
            iterations=args.iterations,
            sample_interval=args.interval,
            suites=args.suites,
            scenario_name=args.scenario,
            forward_engine=args.forward_engine,
            inversion_engine=args.inversion_engine,
            resume_session=args.resume,
        )
    except ValueError as error:
        parser.error(str(error))
    return runner.run_benchmark()


def main() -> int:  # pragma: no cover
    if len(sys.argv) > 1 and sys.argv[1] == "_stage":
        return _internal_stage_main(sys.argv[2:])
    return _benchmark_main(sys.argv[1:])


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

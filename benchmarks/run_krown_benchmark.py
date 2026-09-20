#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import cast

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

from benchmarks.forward_engines import SOUFFLE_ENGINE, translator_rml_version
from benchmarks.krown_campaigns import (
    CAMPAIGNS,
    PHASES,
    Campaign,
    Phase,
    campaign_payloads,
)
from benchmarks.krown_catalog import (
    EXCLUDED_SERIES,
    KROWN_REPOSITORY,
    REPORTED_SERIES,
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
    read_official_step_summary,
    read_step_duration,
    resource_config_directory,
)
from benchmarks.krown_plots import failure_label, plot_timing_charts
from benchmarks.krown_stats import (
    aggregate_scenario_statistics,
    calculate_timing_statistics,
)
from benchmarks.krown_validator import KrownValidator
from benchmarks.souffle_inversion import (
    SouffleInversionError,
    SouffleMode,
    copy_souffle_files,
    inversion_input_files,
    load_relation,
    preserve_souffle_files,
    reverse_souffle_resource,
)
from conformance.souffle_artifacts import (
    FACT_FILES,
    FORWARD_PROGRAM,
    FORWARD_PROVENANCE_PROGRAM,
    REVERSE_PROGRAM,
    SUPPORT_REPORT,
    parse_source_relations,
    write_rdf_dataset,
)
from kgi.core import reconstruct

console = Console(width=max(shutil.get_terminal_size().columns, 100))

R2RML = Namespace("http://www.w3.org/ns/r2rml#")
SPARQL_ENGINE = "pyoxigraph"
DATALOG_RDF_FILE = "out.nq"
KROWN_COOLDOWN_SECONDS = 15
REVERSE_R2RML_REPOSITORY = "https://github.com/dtai-kg/ReverseR2RML.git"
SOURCE_SCHEMA = "source"
DESTINATION_SCHEMA = "destination"
BENCHMARK_DATABASE_CONTAINER = "kgi-benchmark-postgresql"
BENCHMARK_DATABASE_INTERNAL_PORT = 5432
DATABASE_READINESS_TIMEOUT_SECONDS = 300
DATABASE_READINESS_POLL_SECONDS = 2
MEASURED_STAGES = ("forward", "backward")
MAPPING_COMMANDS = (
    "execute_mapping",
    "execute_forward_provenance",
    "execute_forward_hybrid",
)
ScenarioRuns = dict[str, dict[str, list[dict[str, object]]]]

EXIT_OUT_OF_MEMORY = 21

KNOWN_FORWARD_FAILURES: dict[str, str] = {
    "raw_10000000_20_0": "out_of_memory",
}


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _git_commit(repository: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


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
    souffle_directory: Path
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


def generate_scenario(
    scenario: KrownScenario,
    scenarios_root: Path,
    data_generator_dir: Path,
) -> Path:
    if scenarios_root.exists():
        shutil.rmtree(scenarios_root)
    scenarios_root.mkdir(parents=True)

    config = {
        "@id": "http://example.com/kg-inversion-benchmark/generated",
        "name": scenario.display_name,
        "description": "Single KROWN benchmark scenario",
        "instances": [scenario.config_instance(SOUFFLE_ENGINE.resource)],
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


def select_forward_mode(scenario_path: Path, souffle_mode: SouffleMode) -> None:
    """Point the KROWN mapping step at the Soufflé forward program of the phase."""
    metadata_file = scenario_path / "metadata.json"
    metadata = cast(
        dict[str, object],
        json.loads(metadata_file.read_text(encoding="utf-8")),
    )
    steps = cast(list[dict[str, object]], metadata["steps"])
    mapping_steps = [step for step in steps if step["command"] in MAPPING_COMMANDS]
    if len(mapping_steps) != 1:
        raise ValueError("Expected one KROWN mapping step")
    if souffle_mode == "rdf":
        mapping_steps[0]["resource"] = SOUFFLE_ENGINE.resource
        mapping_steps[0]["command"] = "execute_mapping"
    else:
        mapping_steps[0]["resource"] = "ReverseSouffle"
        mapping_steps[0]["command"] = f"execute_forward_{souffle_mode}"
    metadata_file.write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )


def expected_outcome(scenario: KrownScenario, campaign: Campaign) -> str:
    if campaign.souffle_mode in ("provenance", "hybrid") and (
        scenario.generator == "Mappings" or scenario.suite == "joins"
    ):
        return "PARTIAL"
    return scenario.expected_outcome


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

    def _mapping_step(self) -> dict[str, object]:
        matching_steps = [
            step
            for step in self._metadata_steps()
            if step["command"] in MAPPING_COMMANDS
        ]
        if len(matching_steps) != 1:
            raise ValueError("Expected one mapping step in KROWN metadata")
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
            self._mapping_step()["parameters"],
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
        definitions = [f"{_quoted('id')} BIGINT"]
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
                    f"COPY {table_name} FROM STDIN WITH "
                    "(FORMAT CSV, HEADER TRUE, NULL 'NULL')",
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

    def backward(self, rdf_file: Path) -> None:
        reconstruct(
            mapping=str(self.mapping_file),
            rdf_graph=str(rdf_file),
            dest_db_url=self.database.sqlalchemy_url(DESTINATION_SCHEMA),
            source_db_url=self.database.sqlalchemy_url(SOURCE_SCHEMA),
        )

    def backward_souffle(
        self,
        souffle_directory: Path,
        souffle_mode: SouffleMode,
    ) -> None:
        """Invert with the Datalog approach of the ReverseR2RML submodule.

        The local ReverseSouffle resource contributes only the container that runs it.
        The reverse Datalog program consumes the facts or per-column provenance from
        the matching forward execution and emits assembled source rows.
        """
        project_root = Path(__file__).resolve().parent.parent
        copy_souffle_files(
            souffle_directory,
            self.shared_dir,
            inversion_input_files(souffle_directory, souffle_mode),
        )
        resource = reverse_souffle_resource(project_root)(
            str(self.scenario_path / "data"),
            str(resource_config_directory(project_root)),
            str(self.scenario_path),
            False,
        )
        if not resource.execute_reverse_only(
            self.mapping_file.name,
            "out.nt",
            "ntriples",
            souffle_mode=souffle_mode,
            support_report=SUPPORT_REPORT,
            rdb_username=self.database.username,
            rdb_password=self.database.password,
            rdb_host=BENCHMARK_DATABASE_CONTAINER,
            rdb_port=BENCHMARK_DATABASE_INTERNAL_PORT,
            rdb_name=f"{self.database.database}?currentSchema={SOURCE_SCHEMA}",
            rdb_type="PostgreSQL",
        ):
            message = f"ReverseSouffle failed for {self.scenario.generated_name}"
            if resource.failure_kind in ("out_of_memory", "timeout"):
                raise ScenarioExecutionFailure(
                    "backward", resource.failure_kind, message, resource.diagnostic
                )
            raise SouffleInversionError(message)

        engine = create_engine(self.database.sqlalchemy_url())
        try:
            for relation in parse_source_relations(self.shared_dir):
                load_relation(
                    engine,
                    relation,
                    self.shared_dir / relation.recovered_file,
                    SOURCE_SCHEMA,
                    DESTINATION_SCHEMA,
                )
        finally:
            engine.dispose()

    def preserve_souffle_artifacts(
        self,
        souffle_directory: Path,
        destination: Path,
        souffle_mode: SouffleMode,
        interrupted: bool,
    ) -> None:
        copy_souffle_files(
            souffle_directory,
            destination,
            inversion_input_files(souffle_directory, souffle_mode),
        )
        if interrupted:
            with os.scandir(self.shared_dir) as entries:
                available_files = tuple(
                    entry.name for entry in entries if entry.is_file()
                )
            copy_souffle_files(self.shared_dir, destination, available_files)
            return
        recovered_files = tuple(
            relation.recovered_file
            for relation in parse_source_relations(self.shared_dir)
        )
        copy_souffle_files(
            self.shared_dir,
            destination,
            (FORWARD_PROGRAM, REVERSE_PROGRAM, SUPPORT_REPORT, *recovered_files),
        )


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
        choices=("prepare", "reset-destination", "backward"),
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
                parser.error("--rdf-file is required for backward")
            operations.backward(project_root / args.rdf_file)
    except MemoryError:
        return EXIT_OUT_OF_MEMORY
    return 0


class KrownBenchmarkRunner:
    def __init__(
        self,
        iterations: int,
        sample_interval: float,
        suites: tuple[str, ...],
        scenario_name: str | None,
        cleanup_tables: bool = True,
        resume_session: Path | None = None,
    ):
        self.project_root = Path(__file__).resolve().parent.parent
        self.data_generator_dir = self.project_root / "KROWN" / "data-generator"
        benchmark_dir = Path(__file__).resolve().parent / "krown"
        self.scenarios_root = benchmark_dir / "scenarios"
        self.results_dir = benchmark_dir / "results"
        self.krown_commit = _git_commit(self.project_root / "KROWN")
        self.reverse_r2rml_commit = _git_commit(self.project_root / "ReverseR2RML")
        self.iterations = iterations
        self.sample_interval = sample_interval
        self.cleanup_tables = cleanup_tables
        self.is_resume = resume_session is not None
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
            self.session_dir = self.results_dir / f"krown_{self.timestamp}"
            self.measured_runs: ScenarioRuns = {
                campaign.name: {} for campaign in CAMPAIGNS
            }
        else:
            self.session_dir = resume_session.resolve()
            self.timestamp, self.measured_runs = self._read_partial_results(
                resume_session
            )
        self.resource_summaries: dict[
            str, dict[str, list[dict[str, int | float | str]]]
        ] = {campaign.name: {} for campaign in CAMPAIGNS}

        catalog = load_scenarios(self.project_root)
        scenario_names = {scenario.generated_name for scenario in catalog}
        if scenario_name is not None and scenario_name not in scenario_names:
            raise ValueError(f"Unknown KROWN scenario: {scenario_name}")
        reported_names = {
            point[0] for series in REPORTED_SERIES for point in series.points
        }
        excluded_names = {
            point[0]
            for series in SERIES
            if series.name in EXCLUDED_SERIES
            for point in series.points
        } - reported_names
        selected = tuple(
            scenario
            for scenario in catalog
            if scenario.suite in suites
            and scenario.generated_name not in excluded_names
        )
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
            for series in REPORTED_SERIES
            if any(point[0] in selected_names for point in series.points)
        )
        measured_names = {name for runs in self.measured_runs.values() for name in runs}
        unselected = sorted(measured_names - selected_names)
        if unselected:
            raise ValueError(
                "The session to continue holds scenarios outside the selected "
                f"suites, which the results would drop: {', '.join(unselected)}"
            )

    def _read_partial_results(
        self,
        session_dir: Path,
    ) -> tuple[int, ScenarioRuns]:
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
        provenance = cast(dict[str, object], payload["provenance"])
        requested: dict[str, object] = {
            "iterations": self.iterations,
            "sample_interval_seconds": self.sample_interval,
            "krown_commit": self.krown_commit,
            "reverse_r2rml_commit": self.reverse_r2rml_commit,
        }
        recorded: dict[str, object] = {
            "iterations": payload["iterations"],
            "sample_interval_seconds": payload["sample_interval_seconds"],
            "krown_commit": provenance["krown_commit"],
            "reverse_r2rml_commit": provenance["reverse_r2rml_commit"],
        }
        mismatched = {
            key: f"{recorded[key]} instead of {value}"
            for key, value in requested.items()
            if recorded[key] != value
        }
        if mismatched:
            raise ValueError(
                f"{partial_files[0].name} was measured with a different "
                f"configuration: {mismatched}"
            )
        campaigns = cast(dict[str, dict[str, object]], payload["campaigns"])
        return (
            cast(int, payload["timestamp"]),
            {
                campaign.name: cast(
                    dict[str, list[dict[str, object]]],
                    campaigns[campaign.name]["scenarios"],
                )
                for campaign in CAMPAIGNS
            },
        )

    def _restore_resource_summaries(
        self,
        scenario: KrownScenario,
        campaign: Campaign,
        runs: list[dict[str, object]],
    ) -> None:
        """Reuse the resource summaries the interrupted session left on disk."""
        stages = cast(
            dict[str, dict[str, object]],
            cast(dict[str, object], runs[0]["metrics"])["stages"],
        )
        for stage_name in MEASURED_STAGES:
            stage_directory = (
                self._forward_directory(scenario, campaign.souffle_mode)
                if stage_name == "forward"
                else self._backward_directory(scenario, campaign)
            )
            summary = dict(
                read_official_step_summary(
                    stage_directory / "results",
                    cast(int, stages[stage_name]["step"]),
                )
            )
            summary["stage_name"] = stage_name
            self.resource_summaries[campaign.name].setdefault(
                scenario.generated_name, []
            ).append(summary)

    def prepare_output_directories(self) -> None:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(exist_ok=True)
        if self.is_resume:
            self._remove_uncheckpointed_scenario_artifacts()
        if self.scenarios_root.exists():
            shutil.rmtree(self.scenarios_root)
        self.scenarios_root.mkdir(parents=True)

    def _phase_measured(self, scenario: KrownScenario, phase: Phase) -> bool:
        return all(
            scenario.generated_name in self.measured_runs[campaign.name]
            for campaign in phase.campaigns
        )

    def _campaign_measured(self, scenario: KrownScenario, campaign: Campaign) -> bool:
        return scenario.generated_name in self.measured_runs[campaign.name]

    def _pending_campaigns(
        self, scenario: KrownScenario, phase: Phase
    ) -> tuple[Campaign, ...]:
        return tuple(
            campaign
            for campaign in phase.campaigns
            if not self._campaign_measured(scenario, campaign)
        )

    def _remove_uncheckpointed_scenario_artifacts(self) -> None:
        for scenario in self.scenarios:
            for phase in PHASES:
                if self._phase_measured(scenario, phase):
                    continue
                for campaign in phase.campaigns:
                    if not self._campaign_measured(scenario, campaign):
                        continue
                    measured = self.measured_runs[campaign.name][
                        scenario.generated_name
                    ]
                    if all(run["status"] == "completed" for run in measured):
                        self._restore_resource_summaries(scenario, campaign, measured)
                directories = [
                    self._forward_directory(scenario, phase.souffle_mode),
                    *(
                        self._backward_directory(scenario, campaign)
                        for campaign in phase.campaigns
                        if not self._campaign_measured(scenario, campaign)
                    ),
                ]
                for directory in directories:
                    if directory.exists():
                        shutil.rmtree(directory)

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
            "--no-deps",
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

    @staticmethod
    def _run_diagnostic_command(command: list[str]) -> dict[str, object]:
        process = subprocess.run(command, capture_output=True, text=True)
        return {
            "command": command,
            "returncode": process.returncode,
            "stdout": process.stdout,
            "stderr": process.stderr,
        }

    def _save_database_diagnostic(self, stage: str, scenario: str) -> Path:
        diagnostic_file = self.session_dir / (
            f"database_diagnostic_{scenario}_{stage}_{time.time_ns()}.json"
        )
        inspection = self._run_diagnostic_command(
            ["docker", "inspect", BENCHMARK_DATABASE_CONTAINER]
        )
        health_history: object = None
        if inspection["returncode"] == 0:
            inspected = json.loads(cast(str, inspection["stdout"]))
            health_history = inspected[0]["State"]["Health"]
        payload = {
            "stage": stage,
            "scenario": scenario,
            "compose_status": self._run_diagnostic_command(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(self.compose_file),
                    "ps",
                    "--all",
                    "--format",
                    "json",
                ]
            ),
            "postgresql_inspection": inspection,
            "health_check_history": health_history,
            "postgresql_logs": self._run_diagnostic_command(
                ["docker", "logs", BENCHMARK_DATABASE_CONTAINER]
            ),
        }
        diagnostic_file.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        return diagnostic_file

    def wait_for_database(self, stage: str, scenario: KrownScenario) -> None:
        deadline = time.monotonic() + DATABASE_READINESS_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            process = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{if .State.Health}}{{.State.Health.Status}}{{end}}",
                    BENCHMARK_DATABASE_CONTAINER,
                ],
                capture_output=True,
                text=True,
            )
            if process.returncode == 0 and process.stdout.strip() == "healthy":
                return
            time.sleep(DATABASE_READINESS_POLL_SECONDS)

        diagnostic_file = self._save_database_diagnostic(stage, scenario.generated_name)
        raise RuntimeError(
            f"PostgreSQL did not become healthy within "
            f"{DATABASE_READINESS_TIMEOUT_SECONDS} seconds during {stage} for "
            f"{scenario.generated_name}. Diagnostic saved to {diagnostic_file}"
        )

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
        diagnostic_file = self._save_database_diagnostic(
            result_stage, scenario.generated_name
        )
        diagnostic = f"{diagnostic}\nDiagnostic saved to {diagnostic_file}"
        if process.returncode not in (EXIT_OUT_OF_MEMORY, 137):
            raise RuntimeError(
                f"KROWN stage {stage} failed with exit code "
                f"{process.returncode}:\n{diagnostic}"
            )
        raise ScenarioExecutionFailure(
            result_stage,
            "out_of_memory",
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

    def _forward_directory(
        self, scenario: KrownScenario, souffle_mode: SouffleMode
    ) -> Path:
        return self._case_directory(scenario) / f"forward_{souffle_mode}"

    def _backward_directory(self, scenario: KrownScenario, campaign: Campaign) -> Path:
        return self._case_directory(scenario) / f"backward_{campaign.name}"

    def _build_result(
        self,
        scenario: KrownScenario,
        campaign: Campaign,
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
        execution_time = timings["forward_time"] + timings["inversion_time"]
        throughput = {
            "rows_per_second": (scenario.source_rows / timings["inversion_time"]),
            "cells_per_second": (scenario.source_cells / timings["inversion_time"]),
        }

        timing_breakdown: dict[str, float] = {
            **timings,
            "total_time": execution_time,
            "inversion_overhead_percentage": (
                timings["inversion_time"] / timings["forward_time"] * 100
            ),
        }

        return {
            "status": "completed",
            "scenario_name": scenario.generated_name,
            "display_name": scenario.display_name,
            "suite": scenario.suite,
            "generator": scenario.generator,
            "expected_outcome": expected_outcome(scenario, campaign),
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

    def _build_failure_result(
        self,
        scenario: KrownScenario,
        campaign: Campaign,
        error: ScenarioExecutionFailure,
        *,
        executed: bool = True,
    ) -> dict[str, object]:
        return {
            "status": "failed" if executed else "skipped",
            "scenario_name": scenario.generated_name,
            "display_name": scenario.display_name,
            "suite": scenario.suite,
            "generator": scenario.generator,
            "expected_outcome": expected_outcome(scenario, campaign),
            "parameters": scenario.parameters,
            "source_configuration": {
                "identifier": scenario.identifier,
                "file": scenario.source_config,
                "overrides": scenario.configuration_overrides,
            },
            "execution_time": error.elapsed_seconds if executed else None,
            "failure": {
                "stage": error.stage,
                "kind": error.kind,
                "outcome": error.outcome,
                "message": str(error),
                "diagnostic": error.diagnostic,
            },
        }

    def _known_forward_failure(
        self,
        scenario: KrownScenario,
    ) -> ScenarioExecutionFailure | None:
        if scenario.generated_name not in KNOWN_FORWARD_FAILURES:
            return None
        return ScenarioExecutionFailure(
            "forward_mapping",
            KNOWN_FORWARD_FAILURES[scenario.generated_name],
            f"Skipped expected {SOUFFLE_ENGINE.label} {SOUFFLE_ENGINE.version} "
            "forward failure; no execution measured",
        )

    def _validate_inversion(
        self,
        scenario: KrownScenario,
        campaign: Campaign,
        operations: ScenarioOperations,
        original_rdf: Path,
    ) -> dict[str, object]:
        expected_tables = [table.name for table in operations.source_tables]
        validation = self.validator.validate_inversion(
            expected_tables=expected_tables,
            scenario_name=scenario.generated_name,
            expected_outcome=expected_outcome(scenario, campaign),
            original_rdf=original_rdf,
            missing_mapped_columns=self.validator.missing_mapped_columns(
                expected_tables, operations.mapping_file
            ),
        )
        if validation["validation_passed"] is not True:
            raise ValueError(f"Unexpected inversion result: {validation}")
        return validation

    def _preserve_forward_results(
        self,
        scenario: KrownScenario,
        executor: OfficialKrownExecutor,
        souffle_mode: SouffleMode,
    ) -> Path:
        destination = self._forward_directory(scenario, souffle_mode) / "results"
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
        phase: Phase,
        campaigns: tuple[Campaign, ...],
        progress: Progress,
        task: TaskID,
    ) -> list[ForwardMeasurement]:
        select_forward_mode(operations.scenario_path, phase.souffle_mode)
        executor = OfficialKrownExecutor(
            self.project_root,
            operations.scenario_path,
            SOUFFLE_ENGINE,
        )
        runs = self.iterations
        engine_directory = executor.resource_directory
        for iteration in range(1, runs + 1):
            progress.update(
                task,
                description=(
                    f"{scenario.generated_name} forward {phase.souffle_mode} "
                    f"({iteration}/{runs})"
                ),
            )
            result = executor.run(
                self.sample_interval,
                iteration,
                iteration == runs,
            )
            if not result.success:
                error = self._forward_failure(result, executor, iteration)
                self._preserve_forward_results(scenario, executor, phase.souffle_mode)
                raise error
            souffle_files = FACT_FILES
            if phase.souffle_mode != "rdf":
                souffle_files = (
                    FORWARD_PROGRAM,
                    FORWARD_PROVENANCE_PROGRAM,
                    REVERSE_PROGRAM,
                    *inversion_input_files(operations.shared_dir, phase.souffle_mode),
                )
            preserve_souffle_files(
                operations.shared_dir,
                executor.results_path / f"run_{iteration}" / engine_directory,
                souffle_files,
            )

        summary = executor.statistics()
        forward_summary = dict(summary[executor.mapping_step - 1])
        forward_summary["stage_name"] = "forward"
        for campaign in campaigns:
            self.resource_summaries[campaign.name].setdefault(
                scenario.generated_name, []
            ).append(dict(forward_summary))

        results_path = self._preserve_forward_results(
            scenario,
            executor,
            phase.souffle_mode,
        )
        measurements = []
        for iteration in range(1, runs + 1):
            run_path = results_path / f"run_{iteration}"
            souffle_directory = run_path / engine_directory
            rdf_file = souffle_directory / DATALOG_RDF_FILE
            write_rdf_dataset(souffle_directory, rdf_file)
            rdf_statements = self.validate_forward_output(
                scenario,
                operations,
                rdf_file,
            )
            measurements.append(
                ForwardMeasurement(
                    iteration=iteration,
                    rdf_file=rdf_file,
                    souffle_directory=souffle_directory,
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

    def _prepare_local_source(
        self,
        scenario: KrownScenario,
        operations: ScenarioOperations,
    ) -> None:
        self.wait_for_database("database_setup", scenario)
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
        campaign: Campaign,
        operations: ScenarioOperations,
        iteration: int,
        forward: ForwardMeasurement,
    ) -> dict[str, object]:
        self.wait_for_database("inversion", scenario)
        self.run_stage(
            "reset-destination",
            "destination_setup",
            scenario,
            operations.scenario_path,
        )
        case_directory = self._backward_directory(scenario, campaign)
        run_path = case_directory / "results" / f"run_{iteration}"
        run_path.mkdir(parents=True)
        collector = SynchronousCollector(
            case_name=f"{scenario.generated_name}:backward_{campaign.name}",
            run_path=run_path,
            sample_interval=self.sample_interval,
            number_of_steps=1,
            run_id=iteration,
            case_directory=case_directory,
        )
        scenario_failure: ScenarioExecutionFailure | None = None
        try:
            if campaign.inversion_engine == "souffle":
                operations.backward_souffle(
                    forward.souffle_directory,
                    campaign.souffle_mode,
                )
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
        finally:
            collector.stop()

        if campaign.inversion_engine == "souffle":
            operations.preserve_souffle_artifacts(
                forward.souffle_directory,
                run_path,
                campaign.souffle_mode,
                interrupted=scenario_failure is not None,
            )

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

        timings = {
            "forward_time": forward.duration,
            "inversion_time": inversion_time,
        }
        metrics = {
            "forward": self._forward_metrics(forward),
            "backward": self._backward_metrics(run_path),
        }

        validation = self._validate_inversion(
            scenario,
            campaign,
            operations,
            forward.rdf_file,
        )
        return self._build_result(
            scenario,
            campaign,
            operations,
            timings,
            forward.rdf_statements,
            len(operations.source_tables),
            validation,
            metrics,
        )

    def _generate_backward_statistics(
        self, scenario: KrownScenario, campaign: Campaign
    ) -> None:
        case_directory = self._backward_directory(scenario, campaign)
        summary = generate_official_statistics(
            project_root=self.project_root,
            results_path=case_directory / "results",
            number_of_steps=1,
            case_directory=case_directory,
        )
        backward_summary = dict(summary[0])
        backward_summary["stage_name"] = "backward"
        self.resource_summaries[campaign.name].setdefault(
            scenario.generated_name, []
        ).append(backward_summary)

    def _remove_forward_data(
        self,
        scenario: KrownScenario,
        phase: Phase,
        campaigns: tuple[Campaign, ...],
        measurements: list[ForwardMeasurement],
    ) -> None:
        copied_inputs: set[str] = set()
        for measurement in measurements:
            copied_inputs.update(
                inversion_input_files(
                    measurement.souffle_directory,
                    phase.souffle_mode,
                )
            )
            shutil.rmtree(measurement.souffle_directory)

        for campaign in campaigns:
            results = self._backward_directory(scenario, campaign) / "results"
            for iteration in range(1, self.iterations + 1):
                for filename in copied_inputs:
                    (results / f"run_{iteration}" / filename).unlink(missing_ok=True)

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

    def _common_payload(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "benchmark_type": "KROWN",
            "framework": "KROWN Executor with local inversion",
            "environment": "KROWN Executor and Docker Compose",
            "forward_engine": "souffle",
            "measurement_scope": "system",
            "sample_interval_seconds": self.sample_interval,
            "sparql_engine": SPARQL_ENGINE,
            "iterations": self.iterations,
            "suites": list(self.suites),
            "series": self._series_data(),
            "provenance": {
                "krown_repository": KROWN_REPOSITORY,
                "krown_commit": self.krown_commit,
                "reverse_r2rml_repository": REVERSE_R2RML_REPOSITORY,
                "reverse_r2rml_commit": self.reverse_r2rml_commit,
                "forward_engine_version": SOUFFLE_ENGINE.version,
                "forward_rml_reader_version": translator_rml_version(),
                "forward_executor": "KROWN Executor",
                "metrics_implementation": "KROWN Collector and Stats",
                "forward_postgresql_version": "14.5",
                "backward_postgresql_version": "13",
                "rdf_serialization": "KROWN scenario metadata",
                "measured_stages": list(MEASURED_STAGES),
                "excluded_from_measurement": [
                    "generation",
                    "database_setup",
                    "backward_rdf_preparation",
                    "validation",
                    "cooldown",
                ],
            },
        }

    @staticmethod
    def _campaign_payload(
        campaign: Campaign,
        runs: dict[str, list[dict[str, object]]],
        scenarios: dict[str, object],
    ) -> dict[str, object]:
        return {
            "inversion_engine": campaign.inversion_engine,
            "souffle_mode": campaign.souffle_mode,
            "backward_executor": (
                "ReverseSouffle"
                if campaign.inversion_engine == "souffle"
                else "local inversion adapter"
            ),
            "failed_scenarios": sum(
                any(run["status"] == "failed" for run in scenario_runs)
                for scenario_runs in runs.values()
            ),
            "skipped_scenarios": sum(
                any(run["status"] == "skipped" for run in scenario_runs)
                for scenario_runs in runs.values()
            ),
            "scenarios": scenarios,
        }

    def _write_raw_results(self, scenario_runs: ScenarioRuns, raw_file: Path) -> None:
        raw_data = {
            **self._common_payload(),
            "campaigns": {
                campaign.name: self._campaign_payload(
                    campaign,
                    scenario_runs[campaign.name],
                    cast(dict[str, object], scenario_runs[campaign.name]),
                )
                for campaign in CAMPAIGNS
            },
        }
        raw_file.write_text(json.dumps(raw_data, indent=2) + "\n", encoding="utf-8")

    def save_partial_results(self, scenario_runs: ScenarioRuns) -> Path:
        """Record the runs measured before an aborted benchmark stopped.

        Statistics are omitted: aggregation assumes every iteration completed.
        """
        partial_file = (
            self.session_dir / f"krown_benchmark_results_partial_{self.timestamp}.json"
        )
        temporary_file = partial_file.with_suffix(".tmp")
        measured = {
            campaign_name: {
                name: runs
                for name, runs in campaign_runs.items()
                if len(runs) == self.iterations
                or any(run["status"] != "completed" for run in runs)
            }
            for campaign_name, campaign_runs in scenario_runs.items()
        }
        try:
            self._write_raw_results(measured, temporary_file)
            os.replace(temporary_file, partial_file)
        finally:
            temporary_file.unlink(missing_ok=True)
        return partial_file

    def _aggregate_campaign(
        self,
        campaign: Campaign,
        campaign_runs: dict[str, list[dict[str, object]]],
    ) -> dict[str, object]:
        aggregated_scenarios: dict[str, object] = {}
        for scenario in self.scenarios:
            runs = campaign_runs[scenario.generated_name]
            unsuccessful_run = next(
                (run for run in runs if run["status"] != "completed"), None
            )
            scenario_data: dict[str, object] = {
                "display_name": scenario.display_name,
                "suite": scenario.suite,
                "generator": scenario.generator,
                "expected_outcome": expected_outcome(scenario, campaign),
                "parameters": scenario.parameters,
                "source_configuration": {
                    "identifier": scenario.identifier,
                    "file": scenario.source_config,
                    "overrides": scenario.configuration_overrides,
                },
                "raw_runs": runs,
            }
            if unsuccessful_run is not None:
                scenario_data["status"] = unsuccessful_run["status"]
                scenario_data["failure"] = unsuccessful_run["failure"]
                scenario_data["statistics"] = None
                scenario_data["resource_summary"] = None
            else:
                statistics = aggregate_scenario_statistics(runs)
                throughputs = [
                    cast(dict[str, float], run["throughput"]) for run in runs
                ]
                statistics["rows_per_second"] = calculate_timing_statistics(
                    [throughput["rows_per_second"] for throughput in throughputs]
                )
                statistics["cells_per_second"] = calculate_timing_statistics(
                    [throughput["cells_per_second"] for throughput in throughputs]
                )
                scenario_data["status"] = "completed"
                scenario_data["statistics"] = statistics
                scenario_data["resource_summary"] = self.resource_summaries[
                    campaign.name
                ][scenario.generated_name]
            aggregated_scenarios[scenario.generated_name] = scenario_data
        return aggregated_scenarios

    def save_results(
        self,
        scenario_runs: ScenarioRuns,
    ) -> tuple[Path, Path, dict[str, object]]:
        raw_file = (
            self.session_dir / f"krown_benchmark_results_raw_{self.timestamp}.json"
        )
        stats_file = (
            self.session_dir / f"krown_benchmark_results_stats_{self.timestamp}.json"
        )
        self._write_raw_results(scenario_runs, raw_file)
        stats_data: dict[str, object] = {
            **self._common_payload(),
            "campaigns": {
                campaign.name: self._campaign_payload(
                    campaign,
                    scenario_runs[campaign.name],
                    self._aggregate_campaign(campaign, scenario_runs[campaign.name]),
                )
                for campaign in CAMPAIGNS
            },
        }
        stats_file.write_text(json.dumps(stats_data, indent=2) + "\n", encoding="utf-8")
        return raw_file, stats_file, stats_data

    def print_aggregated_summary(self, stats_data: dict[str, object]) -> None:
        payloads = campaign_payloads(stats_data)
        for campaign in CAMPAIGNS:
            self._print_campaign_summary(campaign, payloads[campaign.name])

    def _print_campaign_summary(
        self, campaign: Campaign, stats_data: dict[str, object]
    ) -> None:
        scenarios_data = cast(
            dict[str, dict[str, object]],
            stats_data["scenarios"],
        )
        for series in self.series:
            table = Table(
                title=(
                    f"KROWN {series.title} ({campaign.label}; "
                    f"{self.iterations} iterations)"
                )
            )
            table.add_column(series.parameter_label, justify="right")
            table.add_column("CSV MiB", justify="right")
            table.add_column("RDF statements", justify="right")
            table.add_column(SOUFFLE_ENGINE.label, justify="right")
            table.add_column("Inversion", justify="right")
            table.add_column("Rows/s", justify="right")
            table.add_column("Overhead", justify="right")
            table.add_column("Outcome")

            for scenario_name, parameter_value in series.points:
                scenario_data = scenarios_data[scenario_name]
                parameter_label = (
                    f"{parameter_value:,}"
                    if isinstance(parameter_value, (int, float))
                    else str(parameter_value)
                )
                if scenario_data["status"] != "completed":
                    empty_columns = len(table.columns) - 2
                    table.add_row(
                        parameter_label,
                        *(["-"] * empty_columns),
                        failure_label(scenario_data),
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
                row.append(
                    _format_confidence_interval(
                        cast(dict[str, object], statistics["forward_time"])
                    )
                )
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

    def _record_failure(
        self,
        scenario_runs: ScenarioRuns,
        scenario: KrownScenario,
        campaign: Campaign,
        error: ScenarioExecutionFailure,
        iteration: int | None,
    ) -> None:
        result = self._build_failure_result(
            scenario, campaign, error, executed=iteration is not None
        )
        result["iteration"] = iteration
        scenario_runs[campaign.name][scenario.generated_name].append(result)

    def _run_campaign(
        self,
        scenario: KrownScenario,
        campaign: Campaign,
        operations: ScenarioOperations,
        forward_measurements: list[ForwardMeasurement],
        scenario_runs: ScenarioRuns,
        progress: Progress,
        task: TaskID,
    ) -> bool:
        for iteration in range(1, self.iterations + 1):
            progress.update(
                task,
                description=(
                    f"{scenario.generated_name} backward {campaign.name} "
                    f"({iteration}/{self.iterations})"
                ),
            )
            try:
                result = self._execute_backward_iteration(
                    scenario,
                    campaign,
                    operations,
                    iteration,
                    forward_measurements[iteration - 1],
                )
            except ScenarioExecutionFailure as error:
                self._record_failure(
                    scenario_runs, scenario, campaign, error, iteration
                )
                skipped = self.iterations - iteration
                console.print(
                    f"{scenario.generated_name}: {error.outcome} during "
                    f"{error.stage} of {campaign.name}; skipped {skipped} "
                    "remaining iterations"
                )
                progress.advance(task, advance=skipped + 1)
                return False
            result["iteration"] = iteration
            scenario_runs[campaign.name][scenario.generated_name].append(result)
            progress.advance(task)
        self._generate_backward_statistics(scenario, campaign)
        return True

    def _run_phase(
        self,
        scenario: KrownScenario,
        phase: Phase,
        campaigns: tuple[Campaign, ...],
        operations: ScenarioOperations,
        scenario_runs: ScenarioRuns,
        progress: Progress,
        task: TaskID,
    ) -> bool:
        try:
            forward_measurements = self._run_forward_phase(
                scenario,
                operations,
                phase,
                campaigns,
                progress,
                task,
            )
        except ScenarioExecutionFailure as error:
            iteration = error.iteration or 1
            for campaign in campaigns:
                self._record_failure(
                    scenario_runs, scenario, campaign, error, iteration
                )
            console.print(
                f"{scenario.generated_name}: {error.outcome} during {error.stage} "
                f"of the {phase.souffle_mode} forward; skipped "
                f"{self.iterations - iteration} remaining iterations"
            )
            progress.advance(task, advance=self.iterations * (1 + len(campaigns)))
            return False

        progress.advance(task, advance=self.iterations)
        succeeded = True
        for campaign in campaigns:
            if not self._run_campaign(
                scenario,
                campaign,
                operations,
                forward_measurements,
                scenario_runs,
                progress,
                task,
            ):
                succeeded = False
        self._remove_forward_data(scenario, phase, campaigns, forward_measurements)
        return succeeded

    def run_benchmark(self) -> int:
        console.print(
            f"Starting KROWN benchmark ({', '.join(self.suites)}; system metrics)"
        )
        scenario_runs: ScenarioRuns = {
            campaign.name: {scenario.generated_name: [] for scenario in self.scenarios}
            for campaign in CAMPAIGNS
        }
        for campaign_name, measured in self.measured_runs.items():
            scenario_runs[campaign_name].update(measured)
        failed_scenarios = {
            name
            for measured in self.measured_runs.values()
            for name, runs in measured.items()
            if any(run["status"] == "failed" for run in runs)
        }
        skipped_scenarios = 0
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
                task = progress.add_task(
                    "Scenarios",
                    total=len(self.scenarios)
                    * self.iterations
                    * (len(PHASES) + len(CAMPAIGNS)),
                )
                for scenario in self.scenarios:
                    pending_phases: list[tuple[Phase, tuple[Campaign, ...]]] = []
                    for phase in PHASES:
                        if not self._phase_measured(scenario, phase):
                            pending_campaigns = self._pending_campaigns(scenario, phase)
                            pending_phases.append((phase, pending_campaigns))
                            for campaign in phase.campaigns:
                                if campaign in pending_campaigns:
                                    continue
                                progress.advance(task, advance=self.iterations)
                            continue
                        for campaign in phase.campaigns:
                            measured = scenario_runs[campaign.name][
                                scenario.generated_name
                            ]
                            if all(run["status"] == "completed" for run in measured):
                                self._restore_resource_summaries(
                                    scenario, campaign, measured
                                )
                        progress.advance(
                            task,
                            advance=self.iterations * (1 + len(phase.campaigns)),
                        )
                    known_failure = self._known_forward_failure(scenario)
                    if known_failure is not None:
                        skipped_scenarios += 1
                    if not pending_phases:
                        continue
                    if known_failure is not None:
                        for phase, pending_campaigns in pending_phases:
                            for campaign in pending_campaigns:
                                self._record_failure(
                                    scenario_runs,
                                    scenario,
                                    campaign,
                                    known_failure,
                                    None,
                                )
                            progress.advance(
                                task,
                                advance=self.iterations * (1 + len(pending_campaigns)),
                            )
                        console.print(
                            f"{scenario.generated_name}: {known_failure.outcome} "
                            "expected during forward_mapping; skipped without execution"
                        )
                        self.save_partial_results(scenario_runs)
                        continue
                    scenario_path = generate_scenario(
                        scenario,
                        self.scenarios_root,
                        self.data_generator_dir,
                    )
                    operations = ScenarioOperations(
                        scenario, scenario_path, self.database
                    )
                    self._prepare_local_source(scenario, operations)
                    for phase, pending_campaigns in pending_phases:
                        if not self._run_phase(
                            scenario,
                            phase,
                            pending_campaigns,
                            operations,
                            scenario_runs,
                            progress,
                            task,
                        ):
                            failed_scenarios.add(scenario.generated_name)
                        self.save_partial_results(scenario_runs)

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
            console.print(
                "Benchmark completed with the expected outcomes; "
                f"{skipped_scenarios} known forward failures skipped"
            )
            return 0
        finally:
            if not results_saved and any(
                runs
                for campaign_runs in scenario_runs.values()
                for runs in campaign_runs.values()
            ):
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
        description="Run the KROWN scenarios relevant to KG inversion",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--iterations",
        type=parse_iterations,
        required=True,
        help="Odd number of runs per scenario, at least 3",
    )
    parser.add_argument(
        "--interval",
        type=parse_sample_interval,
        required=True,
        help="System metric sample interval in seconds",
    )
    parser.add_argument(
        "--suites",
        type=parse_suites,
        required=True,
        help=(
            "Comma-separated suites: raw,duplicates-empty,mappings,named-graphs,joins"
        ),
    )
    parser.add_argument("--scenario")
    parser.add_argument(
        "--resume",
        type=Path,
        help="Session directory of an interrupted run to continue",
    )
    args = parser.parse_args(arguments)
    try:
        runner = KrownBenchmarkRunner(
            iterations=args.iterations,
            sample_interval=args.interval,
            suites=args.suites,
            scenario_name=args.scenario,
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

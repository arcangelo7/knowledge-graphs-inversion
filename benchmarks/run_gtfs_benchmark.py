#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import argparse
import datetime as dt
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import numpy as np
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
from sqlalchemy.sql.sqltypes import Date, Integer, Numeric, Text

sys.path.insert(0, str(Path(__file__).parent.parent))

import rmlmapper  # noqa: E402
from benchmarks.krown_stats import aggregate_scenario_statistics  # noqa: E402
from kgi.core import reconstruct  # noqa: E402
from kgi.exceptions import NonInvertibleError, UnsupportedMappingError  # noqa: E402

console = Console()

RR = Namespace("http://www.w3.org/ns/r2rml#")
SPARQL_ENGINE = "pyoxigraph"
DEFAULT_SCALES = [1, 5, 10]
DEFAULT_GTFS_MYSQL_TIMEOUT_SECONDS = 120
TABLE_HEADERS = {
    "AGENCY": [
        "agency_id",
        "agency_name",
        "agency_url",
        "agency_timezone",
        "agency_lang",
        "agency_phone",
        "agency_fare_url",
    ],
    "CALENDAR": [
        "service_id",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "start_date",
        "end_date",
    ],
    "CALENDAR_DATES": ["service_id", "date", "exception_type"],
    "FEED_INFO": [
        "feed_publisher_name",
        "feed_publisher_url",
        "feed_lang",
        "feed_start_date",
        "feed_end_date",
        "feed_version",
    ],
    "FREQUENCIES": [
        "trip_id",
        "start_time",
        "end_time",
        "headway_secs",
        "exact_times",
    ],
    "ROUTES": [
        "route_id",
        "agency_id",
        "route_short_name",
        "route_long_name",
        "route_desc",
        "route_type",
        "route_url",
        "route_color",
        "route_text_color",
    ],
    "SHAPES": [
        "shape_id",
        "shape_pt_lat",
        "shape_pt_lon",
        "shape_pt_sequence",
        "shape_dist_traveled",
    ],
    "STOPS": [
        "stop_id",
        "stop_code",
        "stop_name",
        "stop_desc",
        "stop_lat",
        "stop_lon",
        "zone_id",
        "stop_url",
        "location_type",
        "parent_station",
        "stop_timezone",
        "wheelchair_boarding",
    ],
    "STOP_TIMES": [
        "trip_id",
        "arrival_time",
        "departure_time",
        "stop_id",
        "stop_sequence",
        "stop_headsign",
        "pickup_type",
        "drop_off_type",
        "shape_dist_traveled",
    ],
    "TRIPS": [
        "route_id",
        "service_id",
        "trip_id",
        "trip_headsign",
        "trip_short_name",
        "direction_id",
        "block_id",
        "shape_id",
        "wheelchair_accessible",
    ],
}
INTEGER_COLUMNS = {
    "AGENCY": [],
    "CALENDAR": [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ],
    "CALENDAR_DATES": ["exception_type"],
    "FEED_INFO": [],
    "FREQUENCIES": ["headway_secs", "exact_times"],
    "ROUTES": ["route_type"],
    "SHAPES": [],
    "STOPS": ["location_type", "wheelchair_boarding"],
    "STOP_TIMES": ["stop_sequence", "pickup_type", "drop_off_type"],
    "TRIPS": ["direction_id", "wheelchair_accessible"],
}
NUMERIC_COLUMNS = {
    "AGENCY": [],
    "CALENDAR": [],
    "CALENDAR_DATES": [],
    "FEED_INFO": [],
    "FREQUENCIES": [],
    "ROUTES": [],
    "SHAPES": ["shape_pt_lat", "shape_pt_lon", "shape_dist_traveled"],
    "STOPS": ["stop_lat", "stop_lon"],
    "STOP_TIMES": ["shape_dist_traveled"],
    "TRIPS": [],
}
DATE_COLUMNS = {
    "AGENCY": [],
    "CALENDAR": ["start_date", "end_date"],
    "CALENDAR_DATES": ["date"],
    "FEED_INFO": ["feed_start_date", "feed_end_date"],
    "FREQUENCIES": [],
    "ROUTES": [],
    "SHAPES": [],
    "STOPS": [],
    "STOP_TIMES": [],
    "TRIPS": [],
}


@dataclass(frozen=True)
class GtfsMySqlConfig:
    host: str
    port: int
    database: str
    user: str
    password: str


def parse_scales(value: str) -> list[int]:
    try:
        scales = [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as error:
        raise argparse.ArgumentTypeError("Scales must be integers") from error
    if not scales:
        raise argparse.ArgumentTypeError("At least one scale is required")
    if any(scale <= 0 for scale in scales):
        raise argparse.ArgumentTypeError("Scales must be positive integers")
    return scales


def copy_gtfs_mapping(source: Path, target: Path) -> None:
    shutil.copyfile(source, target)


def configure_gtfs_generation(
    generation_dir: Path, mysql_config: GtfsMySqlConfig
) -> None:
    _replace_prefixed_lines(
        generation_dir / "resources" / "configuration.conf",
        {
            "database-url": (
                f"database-url {mysql_config.host}:{mysql_config.port}/"
                f"{mysql_config.database}"
            ),
            "database-user": f"database-user {mysql_config.user}",
            "database-pwd": f"database-pwd {mysql_config.password}",
        },
    )
    _replace_prefixed_lines(
        generation_dir / "resources" / "gtfs.obda",
        {
            "connectionUrl": (
                f"connectionUrl   jdbc:mysql://{mysql_config.host}:"
                f"{mysql_config.port}/{mysql_config.database}"
            ),
            "username": f"username        {mysql_config.user}",
            "password": f"password        {mysql_config.password}",
        },
    )


def _replace_prefixed_lines(path: Path, replacements: dict[str, str]) -> None:
    replaced: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split(maxsplit=1)
        key = parts[0] if parts else ""
        replaced.append(replacements[key] if key in replacements else line)
    path.write_text("\n".join(replaced) + "\n", encoding="utf-8")


def wait_for_gtfs_mysql(
    mysql_config: GtfsMySqlConfig,
    timeout_seconds: int = DEFAULT_GTFS_MYSQL_TIMEOUT_SECONDS,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(
                (mysql_config.host, mysql_config.port), timeout=2
            ):
                return
        except OSError:
            time.sleep(1)
    raise TimeoutError(
        f"GTFS MySQL is not reachable at {mysql_config.host}:{mysql_config.port}"
    )


def generate_gtfs_scenario(
    scale: int,
    scenario_dir: Path,
    gtfs_bench_dir: Path,
    mysql_config: GtfsMySqlConfig,
) -> None:
    if scenario_dir.exists():
        shutil.rmtree(scenario_dir)
    data_dir = scenario_dir / "data"
    data_dir.mkdir(parents=True)

    with tempfile.TemporaryDirectory(prefix="kgi_gtfs_") as tmp:
        tmp_dir = Path(tmp)
        generation_dir = tmp_dir / "generation"
        shutil.copytree(gtfs_bench_dir / "generation", generation_dir)
        configure_gtfs_generation(generation_dir, mysql_config)
        process = subprocess.run(
            [str(generation_dir / "generate.sh"), str(scale), str(generation_dir)],
            capture_output=True,
            text=True,
        )
        if process.returncode != 0:
            raise RuntimeError(
                "GTFS data generation failed with exit code "
                f"{process.returncode}:\n{process.stdout}\n{process.stderr}"
            )

        generated_csv_dir = generation_dir / "resources" / "csvs"
        for table_name in TABLE_HEADERS:
            shutil.copy2(generated_csv_dir / f"{table_name}.csv", data_dir)


def _sqlalchemy_types(table_name: str) -> dict[str, object]:
    column_types: dict[str, object] = {
        column: Text() for column in TABLE_HEADERS[table_name]
    }
    for column in INTEGER_COLUMNS[table_name]:
        column_types[column] = Integer()
    for column in NUMERIC_COLUMNS[table_name]:
        column_types[column] = Numeric()
    for column in DATE_COLUMNS[table_name]:
        column_types[column] = Date()
    return column_types


def load_gtfs_csv(csv_file: Path, table_name: str) -> pd.DataFrame:
    df = pd.read_csv(csv_file, dtype=str, keep_default_na=False)
    expected_columns = TABLE_HEADERS[table_name]
    if list(df.columns) != expected_columns:
        raise ValueError(f"Unexpected columns for {table_name}: {list(df.columns)}")
    df = df.replace({"": None, "0000-00-00": None})
    return df


def load_gtfs_tables(engine: Engine, data_dir: Path) -> dict[str, pd.DataFrame]:
    original_tables: dict[str, pd.DataFrame] = {}
    for table_name in TABLE_HEADERS:
        df = load_gtfs_csv(data_dir / f"{table_name}.csv", table_name)
        lower_table = table_name.lower()
        df.to_sql(
            lower_table,
            engine,
            if_exists="replace",
            index=False,
            dtype=_sqlalchemy_types(table_name),  # type: ignore[reportArgumentType]
        )
        original_tables[table_name] = _read_table(engine, lower_table)
        _create_uppercase_view(engine, table_name, lower_table)
    return original_tables


def _read_table(engine: Engine, table_name: str) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(f'SELECT * FROM "{table_name}"', conn)


def _create_uppercase_view(engine: Engine, table_name: str, lower_table: str) -> None:
    columns = ", ".join(f'"{column}"' for column in TABLE_HEADERS[table_name])
    with engine.begin() as conn:
        conn.execute(text(f'DROP VIEW IF EXISTS "{table_name}" CASCADE'))
        conn.execute(
            text(f'CREATE VIEW "{table_name}" AS SELECT {columns} FROM "{lower_table}"')
        )


def validate_gtfs_inversion(
    original_tables: dict[str, pd.DataFrame],
    reconstructed_tables: dict[str, pd.DataFrame],
    scenario_name: str,
) -> dict[str, object]:
    results: dict[str, object] = {
        "scenario": scenario_name,
        "validation_passed": True,
        "table_results": {},
        "errors": [],
    }
    table_results: dict[str, dict[str, object]] = {}
    errors: list[str] = []

    for table_name, original_df in original_tables.items():
        reconstructed_df = (
            reconstructed_tables[table_name]
            if table_name in reconstructed_tables
            else None
        )
        table_result = _validate_table(
            original_df,
            reconstructed_df,
        )
        table_results[table_name] = table_result
        if not table_result["validation_passed"]:
            results["validation_passed"] = False
            errors.append(f"{table_name}: {table_result['error']}")

    results["table_results"] = table_results
    results["errors"] = errors
    return results


def _validate_table(
    original_df: pd.DataFrame,
    reconstructed_df: pd.DataFrame | None,
) -> dict[str, object]:
    if reconstructed_df is None:
        return {
            "validation_passed": False,
            "error": "Missing reconstructed table",
        }

    expected_columns = list(original_df.columns)
    reconstructed_columns = list(reconstructed_df.columns)
    if set(reconstructed_columns) != set(expected_columns):
        return {
            "validation_passed": False,
            "error": (
                f"Column mismatch: expected={expected_columns}, "
                f"actual={reconstructed_columns}"
            ),
        }

    original_comparable = _normalize_for_comparison(
        original_df.loc[:, expected_columns]
    )
    reconstructed_comparable = _normalize_for_comparison(
        reconstructed_df.loc[:, expected_columns]
    )

    if len(original_comparable) != len(reconstructed_comparable):
        return {
            "validation_passed": False,
            "error": (
                f"Row count mismatch: original={len(original_comparable)}, "
                f"inverted={len(reconstructed_comparable)}"
            ),
        }

    original_sorted = original_comparable.sort_values(by=expected_columns).reset_index(
        drop=True
    )
    reconstructed_sorted = reconstructed_comparable.sort_values(
        by=expected_columns
    ).reset_index(drop=True)

    try:
        pd.testing.assert_frame_equal(
            original_sorted,
            reconstructed_sorted,
            check_dtype=False,
            check_like=False,
        )
    except AssertionError as error:
        return {
            "validation_passed": False,
            "error": f"Data mismatch: {error}",
        }

    return {
        "validation_passed": True,
        "error": None,
        "rows": len(original_sorted),
    }


def _normalize_for_comparison(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy()
    for column in normalized.columns:
        normalized[column] = normalized[column].map(_comparison_value)
    return normalized


def _comparison_value(value: object) -> str:
    if value is None:
        return ""
    missing = pd.isna(value)
    if isinstance(missing, (bool, np.bool_)) and bool(missing):
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return _decimal_to_string(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value)
    if text.lower() == "true":
        return "1"
    if text.lower() == "false":
        return "0"
    return text


def _decimal_to_string(value: Decimal) -> str:
    if value == value.to_integral():
        return str(int(value))
    return format(value.normalize(), "f")


def _metric(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value))


class GtfsBenchmarkRunner:
    def __init__(
        self,
        scales: list[int] | None = None,
        cleanup_tables: bool = True,
        iterations: int = 1,
    ):
        self.project_root = Path(__file__).parent.parent
        self.gtfs_bench_dir = self.project_root / "gtfs-bench"
        self.mapping_source = self.gtfs_bench_dir / "mappings" / "gtfs-rdb.r2rml.ttl"
        self.scenarios_root = Path(__file__).parent / "gtfs" / "scenarios"
        self.results_dir = Path(__file__).parent / "gtfs" / "results"
        self.scales = scales if scales is not None else DEFAULT_SCALES
        self.cleanup_tables = cleanup_tables
        self.iterations = iterations

        self.db_config = {
            "host": "benchmark_postgresql",
            "port": "5432",
            "user": "r2rml",
            "password": "r2rml",
            "database": "r2rml",
        }
        self.gtfs_mysql_config = GtfsMySqlConfig(
            host="gtfs_mysql",
            port=3306,
            database="gtfs",
            user="oeg",
            password="oeg",
        )

    def get_connection_string(self) -> str:
        return (
            f"postgresql://{self.db_config['user']}:{self.db_config['password']}@"
            f"{self.db_config['host']}:{self.db_config['port']}/{self.db_config['database']}"
        )

    def run_gtfs_data_generation(self) -> None:
        wait_for_gtfs_mysql(self.gtfs_mysql_config)
        for scale in self.scales:
            generate_gtfs_scenario(
                scale,
                self.scenarios_root / f"scale_{scale}",
                self.gtfs_bench_dir,
                self.gtfs_mysql_config,
            )

    def find_gtfs_scenarios(self) -> list[Path]:
        if not self.scenarios_root.exists():
            return []
        return sorted(
            path
            for path in self.scenarios_root.iterdir()
            if path.is_dir() and path.name.startswith("scale_")
        )

    def execute_and_classify_scenario(self, scenario_path: Path) -> dict[str, object]:
        result = self.execute_gtfs_scenario(scenario_path)
        result["expected_outcome"] = "observed"
        result["outcome_matches_expectation"] = self.outcome_matches_expectation(result)
        return result

    def outcome_matches_expectation(self, result: dict[str, object]) -> bool:
        if result["status"] in {"non_invertible", "unsupported"}:
            return True
        return result["status"] == "completed" and "validation_results" in result

    def execute_gtfs_scenario(self, scenario_path: Path) -> dict[str, object]:
        scenario_name = scenario_path.name
        scale = int(scenario_name.split("_", 1)[1])
        data_dir = scenario_path / "data"
        mapping_file = scenario_path / "mapping.r2rml.ttl"
        rdf_file = scenario_path / "out.nq"
        start_time = time.time()

        try:
            copy_gtfs_mapping(self.mapping_source, mapping_file)
            original_tables = self.execute_load_step(data_dir)
            forward_time = self.execute_forward_mapping_step(mapping_file, rdf_file)
            self.clear_source_tables()
            inversion_started = time.time()
            try:
                inversion_time = self.execute_inversion_step(mapping_file, rdf_file)
            except NonInvertibleError as error:
                return self.observed_outcome_result(
                    status="non_invertible",
                    scenario_name=scenario_name,
                    scale=scale,
                    start_time=start_time,
                    forward_time=forward_time,
                    inversion_time=time.time() - inversion_started,
                    mapping_file=mapping_file,
                    data_dir=data_dir,
                    error=error,
                )
            except UnsupportedMappingError as error:
                return self.observed_outcome_result(
                    status="unsupported",
                    scenario_name=scenario_name,
                    scale=scale,
                    start_time=start_time,
                    forward_time=forward_time,
                    inversion_time=time.time() - inversion_started,
                    mapping_file=mapping_file,
                    data_dir=data_dir,
                    error=error,
                )
            reconstructed_tables = self.read_reconstructed_tables()
            validation_results = validate_gtfs_inversion(
                original_tables, reconstructed_tables, scenario_name
            )

            total_time = time.time() - start_time
            inversion_overhead_percentage = (
                (inversion_time / forward_time * 100) if forward_time > 0 else 0
            )
            tm_count, pom_count = self.mapping_component_counts(mapping_file)

            return {
                "status": "completed",
                "scenario_name": scenario_name,
                "scale": scale,
                "execution_time": total_time,
                "timing_breakdown": {
                    "forward_time": forward_time,
                    "inversion_time": inversion_time,
                    "inversion_overhead_percentage": inversion_overhead_percentage,
                    "total_time": total_time,
                },
                "mapping_file": str(mapping_file),
                "mapping_size_bytes": mapping_file.stat().st_size,
                "data_size_bytes": sum(
                    path.stat().st_size for path in data_dir.glob("*.csv")
                ),
                "triples_maps_count": tm_count,
                "predicate_object_maps_count": pom_count,
                "inversion_count": len(reconstructed_tables),
                "validation_results": validation_results,
            }
        except Exception as error:
            return {
                "status": "failed",
                "failure_kind": "runtime_error",
                "scenario_name": scenario_name,
                "scale": scale,
                "execution_time": time.time() - start_time,
                "error": str(error),
            }

    def observed_outcome_result(
        self,
        status: str,
        scenario_name: str,
        scale: int,
        start_time: float,
        forward_time: float,
        inversion_time: float,
        mapping_file: Path,
        data_dir: Path,
        error: Exception,
    ) -> dict[str, object]:
        tm_count, pom_count = self.mapping_component_counts(mapping_file)
        total_time = time.time() - start_time
        return {
            "status": status,
            "scenario_name": scenario_name,
            "scale": scale,
            "execution_time": total_time,
            "timing_breakdown": {
                "forward_time": forward_time,
                "inversion_time": inversion_time,
                "inversion_overhead_percentage": (
                    (inversion_time / forward_time * 100) if forward_time > 0 else 0
                ),
                "total_time": total_time,
            },
            "mapping_file": str(mapping_file),
            "mapping_size_bytes": mapping_file.stat().st_size,
            "data_size_bytes": sum(
                path.stat().st_size for path in data_dir.glob("*.csv")
            ),
            "triples_maps_count": tm_count,
            "predicate_object_maps_count": pom_count,
            "inversion_count": 0,
            "error": str(error),
        }

    def execute_load_step(self, data_dir: Path) -> dict[str, pd.DataFrame]:
        engine = create_engine(self.get_connection_string())
        try:
            self.cleanup_database_tables(engine)
            return load_gtfs_tables(engine, data_dir)
        finally:
            engine.dispose()

    def execute_forward_mapping_step(self, mapping_file: Path, rdf_file: Path) -> float:
        start_time = time.time()
        conn_string = self.get_connection_string()
        sa_url = conn_string.replace("postgresql://", "postgresql+psycopg2://")
        jdbc_dsn, username, password = rmlmapper.sqlalchemy_to_jdbc(sa_url)

        rc = rmlmapper.run(
            str(mapping_file),
            str(rdf_file),
            dsn=jdbc_dsn,
            username=username,
            password=password,
            timeout=1800,
        )
        if rc != 0:
            raise RuntimeError(f"RMLMapper failed with exit code {rc}")
        if not rdf_file.exists():
            raise FileNotFoundError(f"Expected output file not found: {rdf_file}")
        return time.time() - start_time

    def clear_source_tables(self) -> None:
        engine = create_engine(self.get_connection_string())
        try:
            with engine.begin() as conn:
                for table_name in TABLE_HEADERS:
                    conn.execute(text(f'DROP VIEW IF EXISTS "{table_name}" CASCADE'))
                for table_name in TABLE_HEADERS:
                    conn.execute(text(f'TRUNCATE TABLE "{table_name.lower()}" CASCADE'))
        finally:
            engine.dispose()

    def execute_inversion_step(self, mapping_file: Path, rdf_file: Path) -> float:
        start_time = time.time()
        conn_string = self.get_connection_string()
        source_db_url = conn_string.replace("postgresql://", "postgresql+psycopg2://")
        reconstruct(
            mapping=str(mapping_file),
            rdf_graph=str(rdf_file),
            dest_db_url=source_db_url,
            source_db_url=source_db_url,
        )
        return time.time() - start_time

    def read_reconstructed_tables(self) -> dict[str, pd.DataFrame]:
        engine = create_engine(self.get_connection_string())
        try:
            return {
                table_name: _read_table(engine, table_name.lower())
                for table_name in TABLE_HEADERS
            }
        finally:
            engine.dispose()

    @staticmethod
    def mapping_component_counts(mapping_file: Path) -> tuple[int, int]:
        graph = Graph()
        graph.parse(mapping_file)
        triples_maps = set(graph.subjects(RDF.type, RR.TriplesMap))
        predicate_object_maps = set(graph.objects(None, RR.predicateObjectMap))
        return len(triples_maps), len(predicate_object_maps)

    @staticmethod
    def outcome_cell(result: dict[str, object]) -> str:
        if result["status"] == "completed":
            validation = result["validation_results"]
            if isinstance(validation, dict) and validation["validation_passed"] is True:
                return "[green]VALID[/green]"
            return "[yellow]INVALID (observed)[/yellow]"
        if result["status"] == "non_invertible":
            return "[yellow]NON-INVERTIBLE (observed)[/yellow]"
        if result["status"] == "unsupported":
            return "[yellow]UNSUPPORTED (observed)[/yellow]"
        if result["status"] == "failed":
            return "[red]FAILED[/red]"
        return "[red]UNEXPECTED[/red]"

    def save_results(self, results: list[dict[str, object]]) -> Path:
        self.results_dir.mkdir(exist_ok=True, parents=True)
        timestamp = int(time.time())
        results_file = self.results_dir / f"gtfs_benchmark_results_{timestamp}.json"
        benchmark_data = {
            "timestamp": timestamp,
            "benchmark_type": "GTFS",
            "framework": "Knowledge Graph Inversion",
            "environment": "Docker",
            "sparql_engine": SPARQL_ENGINE,
            "iterations": self.iterations,
            "scales": self.scales,
            "total_scenarios": len(results),
            "completed_scenarios": len(
                [result for result in results if result["status"] == "completed"]
            ),
            "non_invertible_scenarios": len(
                [result for result in results if result["status"] == "non_invertible"]
            ),
            "unsupported_scenarios": len(
                [result for result in results if result["status"] == "unsupported"]
            ),
            "failed_scenarios": len(
                [result for result in results if result["status"] == "failed"]
            ),
            "unexpected_outcomes": len(
                [
                    result
                    for result in results
                    if not result["outcome_matches_expectation"]
                ]
            ),
            "results": results,
        }
        with open(results_file, "w") as file:
            json.dump(benchmark_data, file, indent=2, default=str)
        return results_file

    def save_aggregated_results(
        self, scenario_runs: dict[str, list[dict[str, object]]]
    ) -> tuple[Path, Path]:
        self.results_dir.mkdir(exist_ok=True, parents=True)
        timestamp = int(time.time())
        raw_file = self.results_dir / f"gtfs_benchmark_results_raw_{timestamp}.json"
        stats_file = self.results_dir / f"gtfs_benchmark_results_stats_{timestamp}.json"

        with open(raw_file, "w") as file:
            json.dump(
                {
                    "timestamp": timestamp,
                    "benchmark_type": "GTFS",
                    "framework": "Knowledge Graph Inversion",
                    "environment": "Docker",
                    "sparql_engine": SPARQL_ENGINE,
                    "iterations": self.iterations,
                    "scales": self.scales,
                    "scenarios": scenario_runs,
                },
                file,
                indent=2,
                default=str,
            )

        stats_data: dict[str, object] = {
            "timestamp": timestamp,
            "benchmark_type": "GTFS",
            "framework": "Knowledge Graph Inversion",
            "environment": "Docker",
            "sparql_engine": SPARQL_ENGINE,
            "iterations": self.iterations,
            "scales": self.scales,
            "scenarios": {},
        }
        scenarios_stats = stats_data["scenarios"]
        assert isinstance(scenarios_stats, dict)
        for scenario_name, runs in scenario_runs.items():
            completed_runs = [run for run in runs if run["status"] == "completed"]
            if completed_runs:
                scenarios_stats[scenario_name] = {
                    "raw_runs": runs,
                    "statistics": aggregate_scenario_statistics(completed_runs),
                }

        with open(stats_file, "w") as file:
            json.dump(stats_data, file, indent=2, default=str)

        return raw_file, stats_file

    def print_summary(self, results: list[dict[str, object]]) -> None:
        completed = [result for result in results if result["status"] == "completed"]
        observed = [
            result
            for result in results
            if result["status"] in {"non_invertible", "unsupported"}
        ]
        failed = [result for result in results if result["status"] == "failed"]
        table = Table(title=f"GTFS benchmark results ({SPARQL_ENGINE})")
        table.add_column("Scenario")
        table.add_column("Time", justify="right")
        table.add_column("RMLMapper", justify="right")
        table.add_column("Inversion", justify="right")
        table.add_column("Overhead", justify="right")
        table.add_column("Tables", justify="right")
        table.add_column("Valid")
        table.add_column("Outcome")

        for result in completed:
            timing = result["timing_breakdown"]
            assert isinstance(timing, dict)
            validation = result["validation_results"]
            assert isinstance(validation, dict)
            table.add_row(
                str(result["scenario_name"]),
                f"{_metric(result['execution_time']):.2f}s",
                f"{_metric(timing['forward_time']):.2f}s",
                f"{_metric(timing['inversion_time']):.2f}s",
                f"{_metric(timing['inversion_overhead_percentage']):.1f}%",
                str(result["inversion_count"]),
                "PASS" if validation["validation_passed"] else "FAIL",
                self.outcome_cell(result),
            )

        for result in observed:
            timing = result["timing_breakdown"]
            assert isinstance(timing, dict)
            table.add_row(
                str(result["scenario_name"]),
                f"{_metric(result['execution_time']):.2f}s",
                f"{_metric(timing['forward_time']):.2f}s",
                f"{_metric(timing['inversion_time']):.2f}s",
                f"{_metric(timing['inversion_overhead_percentage']):.1f}%",
                "0",
                "",
                self.outcome_cell(result),
            )

        for result in failed:
            table.add_row(
                str(result["scenario_name"]),
                f"{_metric(result['execution_time']):.2f}s",
                "",
                "",
                "",
                "",
                "",
                self.outcome_cell(result),
            )

        console.print(table)
        console.print(
            f"Completed: {len(completed)}/{len(results)}, "
            f"Observed: {len(observed)}, Failed: {len(failed)}"
        )

    def print_aggregated_summary(
        self, scenario_runs: dict[str, list[dict[str, object]]]
    ) -> None:
        table = Table(
            title=f"GTFS benchmark results ({SPARQL_ENGINE}, {self.iterations} iterations)"
        )
        table.add_column("Scenario")
        table.add_column("Runs", justify="right")
        table.add_column("Exec time", justify="right")
        table.add_column("RMLMapper", justify="right")
        table.add_column("Inversion", justify="right")
        table.add_column("Overhead", justify="right")
        table.add_column("Outcome")

        for scenario_name, runs in sorted(scenario_runs.items()):
            completed_runs = [run for run in runs if run["status"] == "completed"]
            all_expected = all(run["outcome_matches_expectation"] for run in runs)
            outcome = (
                self.outcome_cell(runs[-1]) if all_expected else "[red]UNEXPECTED[/red]"
            )
            if completed_runs:
                stats = aggregate_scenario_statistics(completed_runs)
                if "forward_time" not in stats:
                    raise ValueError("GTFS statistics lack forward timing")
                if "inversion_time" not in stats:
                    raise ValueError("GTFS statistics lack inversion timing")
                if "inversion_overhead_percentage" not in stats:
                    raise ValueError("GTFS statistics lack inversion overhead")
                table.add_row(
                    scenario_name,
                    f"{len(completed_runs)}/{len(runs)}",
                    f"{stats['execution_time']['mean']:.2f}s +/- {stats['execution_time']['std']:.2f}s",
                    f"{stats['forward_time']['mean']:.2f}s +/- {stats['forward_time']['std']:.2f}s",
                    f"{stats['inversion_time']['mean']:.2f}s +/- {stats['inversion_time']['std']:.2f}s",
                    f"{stats['inversion_overhead_percentage']['mean']:.1f}% +/- {stats['inversion_overhead_percentage']['std']:.1f}%",
                    outcome,
                )
            else:
                table.add_row(scenario_name, f"0/{len(runs)}", "", "", "", "", outcome)

        console.print(table)

    def cleanup(self) -> None:
        if not self.cleanup_tables:
            return
        engine = create_engine(self.get_connection_string())
        try:
            self.cleanup_database_tables(engine)
        finally:
            engine.dispose()

    @staticmethod
    def cleanup_database_tables(engine: Engine) -> None:
        with engine.begin() as conn:
            view_rows = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.views "
                    "WHERE table_schema = 'public'"
                )
            )
            for row in view_rows:
                conn.execute(text(f'DROP VIEW IF EXISTS "{row[0]}" CASCADE'))

            table_rows = conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            for row in table_rows:
                conn.execute(text(f'DROP TABLE IF EXISTS "{row[0]}" CASCADE'))

    def run_benchmark(self) -> int:
        console.print(f"Starting GTFS benchmark ({SPARQL_ENGINE})")
        try:
            self.run_gtfs_data_generation()
            scenarios = self.find_gtfs_scenarios()
            if not scenarios:
                console.print("[red]No scenarios found[/red]")
                return 1

            all_runs: list[dict[str, object]] = []
            if self.iterations == 1:
                results: list[dict[str, object]] = []
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
                console.print(f"Results saved to {results_file}")
            else:
                scenario_runs: dict[str, list[dict[str, object]]] = {
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

            unexpected = sorted(
                {
                    str(result["scenario_name"])
                    for result in all_runs
                    if not result["outcome_matches_expectation"]
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
        except Exception as error:
            console.print(f"[red]Benchmark failed: {error}[/red]")
            return 1
        finally:
            self.cleanup()


def main():  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="GTFS Benchmark Runner for Knowledge Graph Inversion (Docker)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage:
  make benchmark-gtfs I=10 S=1,5,10
        """,
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Number of times to run each scenario",
    )
    parser.add_argument(
        "--scales",
        type=parse_scales,
        default=DEFAULT_SCALES,
        help="Comma-separated GTFS scales",
    )

    args = parser.parse_args()
    runner = GtfsBenchmarkRunner(
        scales=args.scales,
        cleanup_tables=True,
        iterations=args.iterations,
    )
    return runner.run_benchmark()


if __name__ == "__main__":
    sys.exit(main())

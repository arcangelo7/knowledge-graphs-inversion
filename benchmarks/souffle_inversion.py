# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import shutil
import subprocess
from pathlib import Path
from typing import Literal, Protocol, cast

from sqlalchemy import text
from sqlalchemy.engine import Engine

from benchmarks.krown_metrics import load_souffle_module
from conformance.souffle_artifacts import (
    FACT_FILES,
    PROVENANCE_MARKER_FILES,
    SourceRelation,
)

KROWN_NETWORK = "bench_executor"
PROVENANCE_GLOB = "ProvCol_*.csv"
HYBRID_PROVENANCE_GLOB = "HybridProv_*.csv"
SouffleMode = Literal["rdf", "provenance", "hybrid"]


class SouffleInversionError(RuntimeError):
    pass


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def provenance_files(directory: Path) -> tuple[str, ...]:
    column_files = tuple(path.name for path in sorted(directory.glob(PROVENANCE_GLOB)))
    return (*PROVENANCE_MARKER_FILES, *column_files)


def hybrid_provenance_files(directory: Path) -> tuple[str, ...]:
    return tuple(path.name for path in sorted(directory.glob(HYBRID_PROVENANCE_GLOB)))


def inversion_input_files(
    directory: Path, souffle_mode: SouffleMode
) -> tuple[str, ...]:
    if souffle_mode == "provenance":
        return (*FACT_FILES, *provenance_files(directory))
    if souffle_mode == "hybrid":
        return (*FACT_FILES, *hybrid_provenance_files(directory))
    return FACT_FILES


def preserve_souffle_files(
    shared_directory: Path,
    destination: Path,
    filenames: tuple[str, ...],
) -> None:
    """Keep the outputs of one run before the next one overwrites them.

    KROWN collects only the RDF files a resource writes, and the Datalog resource
    writes none.
    """
    destination.mkdir(parents=True, exist_ok=True)
    for name in filenames:
        shutil.move(shared_directory / name, destination / name)


def copy_souffle_files(
    source: Path,
    destination: Path,
    filenames: tuple[str, ...],
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name in filenames:
        shutil.copy(source / name, destination / name)


def load_relation(
    engine: Engine,
    relation: SourceRelation,
    recovered_file: Path,
    source_schema: str,
    destination_schema: str,
) -> None:
    """Materialize the assembled rows in the destination schema.

    The destination table mirrors the source columns and types so that the validator
    can compare the two schemas column by column. It is created from a query rather
    than with LIKE because LIKE also copies NOT NULL, which the primary key of the
    source table carries: the engine leaves unrecovered cells empty, so any table
    whose mapping does not read that key could not be loaded.
    """
    qualified = f"{_quoted(destination_schema)}.{_quoted(relation.table)}"
    with engine.begin() as connection:
        connection.execute(text(f"DROP TABLE IF EXISTS {qualified} CASCADE"))
        connection.execute(
            text(
                f"CREATE TABLE {qualified} AS SELECT * FROM "
                f"{_quoted(source_schema)}.{_quoted(relation.table)} WITH NO DATA"
            )
        )

    columns = ", ".join(_quoted(column) for column in relation.columns)
    raw_connection = engine.raw_connection()
    try:
        cursor = raw_connection.cursor()
        with recovered_file.open(encoding="utf-8") as rows:
            cursor.copy_expert(
                f"COPY {qualified} ({columns}) FROM STDIN WITH "
                "(FORMAT TEXT, DELIMITER E'\\t', NULL '')",
                rows,
            )
        raw_connection.commit()
    finally:
        raw_connection.close()


def attach_database_to_krown_network(container_name: str) -> None:
    """Make the source database reachable from the Soufflé container.

    KROWN resources run on their own Docker network, which the benchmark database
    does not join on its own.
    """
    inspection = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{json .NetworkSettings.Networks}}",
            container_name,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if f'"{KROWN_NETWORK}"' in inspection.stdout:
        return
    subprocess.run(
        ["docker", "network", "connect", KROWN_NETWORK, container_name],
        check=True,
        capture_output=True,
        text=True,
    )


class ReverseSouffleResource(Protocol):
    def execute_reverse_only(
        self,
        mapping_file: str,
        output_file: str,
        serialization: str,
        support_report: str,
        souffle_mode: SouffleMode,
        rdb_username: str,
        rdb_password: str,
        rdb_host: str,
        rdb_port: int,
        rdb_name: str,
        rdb_type: str,
    ) -> bool: ...


class ReverseSouffleFactory(Protocol):
    def __call__(
        self,
        data_path: str,
        config_path: str,
        directory: str,
        verbose: bool,
    ) -> ReverseSouffleResource: ...


def reverse_souffle_resource(project_root: Path) -> ReverseSouffleFactory:
    module = load_souffle_module(project_root, "reverse_souffle")
    return cast(ReverseSouffleFactory, getattr(module, "ReverseSouffle"))

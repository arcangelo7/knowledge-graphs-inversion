# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import csv
import io
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from sqlalchemy import text
from sqlalchemy.engine import Engine

from benchmarks.krown_metrics import load_fork_module

KROWN_NETWORK = "bench_executor"
TRIPLE_FACTS = "triple.csv"
QUADRUPLE_FACTS = "quadruple.csv"
FACT_FILES = (TRIPLE_FACTS, QUADRUPLE_FACTS)
PROVENANCE_GLOB = "ProvCol_*.csv"
FORWARD_PROGRAM = "Datalog_rules.rs"
FORWARD_PROVENANCE_PROGRAM = "Datalog_forward_with_prov.rs"
REVERSE_PROGRAM = "Datalog_reverse.rs"
SUPPORT_REPORT = "support.json"

SOURCE_DECLARATION = re.compile(r"^\.decl (\w+)\((.*)\)$")
SOURCE_INPUT = re.compile(r"^\.input (\w+)")
LOGICAL_TABLE_SUFFIX = re.compile(r"_lt\d+$")


class SouffleInversionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceRelation:
    """A source relation of the forward Datalog program and its recovered form."""

    name: str
    table: str
    columns: tuple[str, ...]

    @property
    def recovered_file(self) -> str:
        return f"Recovered_{self.name}.csv"


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _declared_columns(arguments: str) -> tuple[str, ...]:
    return tuple(
        argument.split(":")[0].strip()
        for argument in arguments.split(",")
        if argument.strip()
    )


def provenance_files(directory: Path) -> tuple[str, ...]:
    filenames = tuple(path.name for path in sorted(directory.glob(PROVENANCE_GLOB)))
    if not filenames:
        raise SouffleInversionError(f"No provenance files found in {directory}")
    return filenames


def inversion_input_files(directory: Path, with_provenance: bool) -> tuple[str, ...]:
    if with_provenance:
        return provenance_files(directory)
    return FACT_FILES


def write_rdf_dataset(facts_directory: Path, rdf_file: Path) -> None:
    """Serialize the fact files a Datalog program wrote into an RDF dataset.

    Every field already holds the N-Triples lexical form of its term, so the statements
    are rebuilt by joining them.
    """
    with rdf_file.open("w", encoding="utf-8") as dataset:
        for name in FACT_FILES:
            with (facts_directory / name).open(encoding="utf-8") as facts:
                for line in facts:
                    dataset.write(" ".join(line.rstrip("\n").split("\t")) + " .\n")


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


def parse_source_relations(shared_directory: Path) -> tuple[SourceRelation, ...]:
    """Read the source relation names and columns from the forward program."""
    declarations: dict[str, tuple[str, ...]] = {}
    inputs: list[str] = []
    for line in (
        (shared_directory / FORWARD_PROGRAM).read_text(encoding="utf-8").splitlines()
    ):
        declaration = SOURCE_DECLARATION.match(line)
        if declaration is not None:
            declarations[declaration.group(1)] = _declared_columns(declaration.group(2))
            continue
        source_input = SOURCE_INPUT.match(line)
        if source_input is not None:
            inputs.append(source_input.group(1))

    return tuple(
        SourceRelation(
            name=name,
            table=LOGICAL_TABLE_SUFFIX.sub("", name),
            columns=declarations[name],
        )
        for name in inputs
    )


def read_recovered_rows(
    shared_directory: Path, relation: SourceRelation
) -> list[tuple[str, ...]]:
    with (shared_directory / relation.recovered_file).open(encoding="utf-8") as file:
        return [tuple(line.rstrip("\n").split("\t")) for line in file]


def load_relation(
    engine: Engine,
    relation: SourceRelation,
    rows: list[tuple[str, ...]],
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

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows(rows)
    buffer.seek(0)

    columns = ", ".join(_quoted(column) for column in relation.columns)
    raw_connection = engine.raw_connection()
    try:
        cursor = raw_connection.cursor()
        cursor.copy_expert(
            f"COPY {qualified} ({columns}) FROM STDIN WITH (FORMAT CSV)", buffer
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
        with_provenance: bool,
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
    module = load_fork_module(project_root, "reverse_souffle")
    return cast(ReverseSouffleFactory, getattr(module, "ReverseSouffle"))

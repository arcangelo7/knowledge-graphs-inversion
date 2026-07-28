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

from pyoxigraph import DefaultGraph, RdfFormat, parse
from sqlalchemy import text
from sqlalchemy.engine import Engine

from benchmarks.krown_metrics import load_fork_module

KROWN_NETWORK = "bench_executor"
TRIPLE_FACTS = "triple.csv"
QUADRUPLE_FACTS = "quadruple.csv"
FACT_FILES = (TRIPLE_FACTS, QUADRUPLE_FACTS)
FORWARD_PROGRAM = "Datalog_rules.rs"
REVERSE_PROGRAM = "Datalog_reverse.rs"
SUPPORT_REPORT = "support.json"

SOURCE_DECLARATION = re.compile(r"^\.decl (\w+)\((.*)\)$")
SOURCE_INPUT = re.compile(r"^\.input (\w+)")
SUBJECT_RULE = re.compile(r"^(Subject\d+_\w+)\(.*?\) :- (\w+)\(")
SUBJECT_PARSER = re.compile(r"^\.decl Parse_(Subject\d+_\w+)\(s:symbol,?(.*)\)$")
LOGICAL_TABLE_SUFFIX = re.compile(r"_lt\d+$")


class SouffleInversionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceRelation:
    """A source relation of the forward Datalog program and its recovered form."""

    name: str
    table: str
    columns: tuple[str, ...]
    key_columns: tuple[str, ...]

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


def write_rdf_facts(rdf_file: Path, shared_directory: Path) -> None:
    """Serialize an RDF dataset into the fact files the reverse program reads.

    Soufflé matches subject, predicate and object against the N-Triples lexical
    forms the forward program builds, so each term is written verbatim.
    """
    with (
        rdf_file.open("rb") as source,
        (shared_directory / TRIPLE_FACTS).open("w", encoding="utf-8") as triples,
        (shared_directory / QUADRUPLE_FACTS).open("w", encoding="utf-8") as quadruples,
    ):
        for quad in parse(source, format=RdfFormat.N_QUADS):
            terms = [str(quad.subject), str(quad.predicate), str(quad.object)]
            if isinstance(quad.graph_name, DefaultGraph):
                triples.write("\t".join(terms) + "\n")
            else:
                quadruples.write("\t".join([*terms, str(quad.graph_name)]) + "\n")


def write_rdf_dataset(facts_directory: Path, rdf_file: Path) -> None:
    """Serialize the fact files a Datalog program wrote into an RDF dataset.

    Every field already holds the N-Triples lexical form of its term, so the statements
    are rebuilt by joining them. This is the inverse of ``write_rdf_facts``.
    """
    with rdf_file.open("w", encoding="utf-8") as dataset:
        for name in FACT_FILES:
            with (facts_directory / name).open(encoding="utf-8") as facts:
                for line in facts:
                    dataset.write(" ".join(line.rstrip("\n").split("\t")) + " .\n")


def preserve_rdf_facts(shared_directory: Path, facts_directory: Path) -> None:
    """Keep the facts of one run before the next one overwrites them.

    KROWN collects only the RDF files a resource writes, and the Datalog resource
    writes none.
    """
    facts_directory.mkdir(parents=True, exist_ok=True)
    for name in FACT_FILES:
        shutil.move(shared_directory / name, facts_directory / name)


def restore_rdf_facts(facts_directory: Path, shared_directory: Path) -> None:
    for name in FACT_FILES:
        shutil.copy(facts_directory / name, shared_directory / name)


def parse_source_relations(shared_directory: Path) -> tuple[SourceRelation, ...]:
    """Read the relation names, columns and subject keys out of the two programs.

    The forward program declares each source relation and binds every subject rule
    to the relation it reads; the reverse program declares which columns the
    matching subject parser recovers from an IRI.
    """
    declarations: dict[str, tuple[str, ...]] = {}
    inputs: list[str] = []
    subject_sources: dict[str, str] = {}
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
            continue
        subject_rule = SUBJECT_RULE.match(line)
        if subject_rule is not None:
            subject_sources[subject_rule.group(1)] = subject_rule.group(2)

    keys: dict[str, set[tuple[str, ...]]] = {name: set() for name in inputs}
    for line in (
        (shared_directory / REVERSE_PROGRAM).read_text(encoding="utf-8").splitlines()
    ):
        parser = SUBJECT_PARSER.match(line)
        if parser is not None:
            source = subject_sources[parser.group(1)]
            keys[source].add(_declared_columns(parser.group(2)))

    relations = []
    for name in inputs:
        key_sets = keys[name]
        if len(key_sets) != 1:
            raise SouffleInversionError(
                f"Relation {name} has {len(key_sets)} distinct subject keys; "
                "recovered tuples cannot be assembled unambiguously"
            )
        relations.append(
            SourceRelation(
                name=name,
                table=LOGICAL_TABLE_SUFFIX.sub("", name),
                columns=declarations[name],
                key_columns=next(iter(key_sets)),
            )
        )
    return tuple(relations)


def assemble_rows(
    shared_directory: Path, relation: SourceRelation
) -> list[tuple[str, ...]]:
    """Merge the per-triple evidence tuples into whole source rows.

    The reverse program derives one tuple per recovered triple, carrying a single
    column plus the subject key. Rows are rebuilt by grouping on that key.
    """
    key_positions = [relation.columns.index(column) for column in relation.key_columns]
    merged: dict[tuple[str, ...], list[str]] = {}
    with (shared_directory / relation.recovered_file).open(encoding="utf-8") as file:
        for line in file:
            values = line.rstrip("\n").split("\t")
            key = tuple(values[position] for position in key_positions)
            row = merged.setdefault(key, [""] * len(relation.columns))
            for position, value in enumerate(values):
                if not value:
                    continue
                if row[position] and row[position] != value:
                    raise SouffleInversionError(
                        f"Conflicting values for {relation.table}."
                        f"{relation.columns[position]} at key {key}: "
                        f"{row[position]!r} and {value!r}"
                    )
                row[position] = value
    return [tuple(row) for row in merged.values()]


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
    def execute_mapping(
        self,
        mapping_file: str,
        output_file: str,
        serialization: str,
        support_report: str,
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

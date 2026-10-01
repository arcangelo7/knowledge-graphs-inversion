# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import re
from dataclasses import dataclass
from pathlib import Path

FACT_FILES = ("triple.csv", "quadruple.csv")
FORWARD_PROGRAM = "Datalog_rules.rs"
FORWARD_PROVENANCE_PROGRAM = "Datalog_forward_with_prov.rs"
REVERSE_PROGRAM = "Datalog_reverse.rs"
SUPPORT_REPORT = "support.json"
PROVENANCE_MARKER_FILES = ("ProvTriple.csv", "ProvQuad.csv")

RECOVERED_DECLARATION = re.compile(r"^\.decl Recovered_(\w+)\((.*)\)$")
OUTPUT_DECLARATION = re.compile(r'^\.output \w+\(filename="([^"]+)"')
LOGICAL_TABLE_SUFFIX = re.compile(r"_lt\d+$")
# The translator names the relation of every query-defined logical table this way
QUERY_RELATION = "query"


@dataclass(frozen=True)
class SourceRelation:
    name: str
    table: str
    columns: tuple[str, ...]

    @property
    def recovered_file(self) -> str:
        return f"Recovered_{self.name}.csv"

    @property
    def facts_file(self) -> str:
        return f"{self.name}.facts"


def translator_column_name(label: str) -> str:
    """The Datalog variable the translator derives from a result column label."""
    normalized = re.sub(r"[^a-z0-9_]", "_", label.lower())
    if normalized == "count":
        return "column_count"
    return f"column_{normalized}" if normalized[0].isdigit() else normalized


def _declared_columns(arguments: str) -> tuple[str, ...]:
    return tuple(
        argument.split(":")[0].strip()
        for argument in arguments.split(",")
        if argument.strip()
    )


def parse_source_relations(shared_directory: Path) -> tuple[SourceRelation, ...]:
    relations: list[SourceRelation] = []
    program = shared_directory / REVERSE_PROGRAM
    for line in program.read_text(encoding="utf-8").splitlines():
        declaration = RECOVERED_DECLARATION.match(line)
        if declaration is None:
            continue
        name = declaration.group(1)
        relations.append(
            SourceRelation(
                name=name,
                table=LOGICAL_TABLE_SUFFIX.sub("", name),
                columns=_declared_columns(declaration.group(2)),
            )
        )
    return tuple(relations)


def declared_output_files(program: Path) -> frozenset[str]:
    return frozenset(
        match.group(1)
        for match in (
            OUTPUT_DECLARATION.match(line)
            for line in program.read_text(encoding="utf-8").splitlines()
        )
        if match
    )


def read_recovered_rows(
    shared_directory: Path, relation: SourceRelation
) -> list[tuple[str, ...]]:
    with (shared_directory / relation.recovered_file).open(encoding="utf-8") as file:
        return [tuple(line.rstrip("\n").split("\t")) for line in file]


def read_fact_rows(shared_directory: Path, relation: SourceRelation) -> set[str]:
    """The rows the translator read from the logical table, one text line each."""
    with (shared_directory / relation.facts_file).open(encoding="utf-8") as file:
        return {line.rstrip("\n") for line in file if line.strip()}


def write_rdf_dataset(facts_directory: Path, rdf_file: Path) -> None:
    with rdf_file.open("w", encoding="utf-8") as dataset:
        for filename in FACT_FILES:
            with (facts_directory / filename).open(encoding="utf-8") as facts:
                for line in facts:
                    dataset.write(" ".join(line.rstrip("\n").split("\t")) + " .\n")

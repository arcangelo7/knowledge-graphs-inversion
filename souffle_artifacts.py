# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import re
from dataclasses import dataclass
from pathlib import Path

FACT_FILES = ("triple.csv", "quadruple.csv")
FORWARD_PROGRAM = "Datalog_rules.rs"
REVERSE_PROGRAM = "Datalog_reverse.rs"
SUPPORT_REPORT = "support.json"

SOURCE_DECLARATION = re.compile(r"^\.decl (\w+)\((.*)\)$")
SOURCE_INPUT = re.compile(r"^\.input (\w+)")
LOGICAL_TABLE_SUFFIX = re.compile(r"_lt\d+$")


@dataclass(frozen=True)
class SourceRelation:
    name: str
    table: str
    columns: tuple[str, ...]

    @property
    def recovered_file(self) -> str:
        return f"Recovered_{self.name}.csv"


def _declared_columns(arguments: str) -> tuple[str, ...]:
    return tuple(
        argument.split(":")[0].strip()
        for argument in arguments.split(",")
        if argument.strip()
    )


def parse_source_relations(shared_directory: Path) -> tuple[SourceRelation, ...]:
    declarations: dict[str, tuple[str, ...]] = {}
    inputs: list[str] = []
    program = shared_directory / FORWARD_PROGRAM
    for line in program.read_text(encoding="utf-8").splitlines():
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


def write_rdf_dataset(facts_directory: Path, rdf_file: Path) -> None:
    with rdf_file.open("w", encoding="utf-8") as dataset:
        for filename in FACT_FILES:
            with (facts_directory / filename).open(encoding="utf-8") as facts:
                for line in facts:
                    dataset.write(" ".join(line.rstrip("\n").split("\t")) + " .\n")

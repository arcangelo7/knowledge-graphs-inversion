# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from dataclasses import dataclass
from typing import Literal

ForwardEngine = Literal["rmlmapper", "souffle", "morphkgc"]


@dataclass(frozen=True)
class ForwardEngineDefinition:
    resource: str
    label: str
    version: str
    schema_query: str
    forked: bool = False
    writes_facts: bool = False

    @property
    def module_name(self) -> str:
        return self.resource.lower()

    def database_name(self, database: str, schema: str) -> str:
        return f"{database}?{self.schema_query.format(schema=schema)}"


FORWARD_ENGINES: dict[ForwardEngine, ForwardEngineDefinition] = {
    "rmlmapper": ForwardEngineDefinition(
        resource="RMLMapper",
        label="RMLMapper",
        version="8.1.0",
        schema_query="currentSchema={schema}",
    ),
    "souffle": ForwardEngineDefinition(
        resource="Souffle",
        label="Soufflé",
        version="1.0.0",
        schema_query="currentSchema={schema}",
        forked=True,
        writes_facts=True,
    ),
    "morphkgc": ForwardEngineDefinition(
        resource="MorphKGC",
        label="Morph-KGC",
        version="2.2.0",
        schema_query="options=-csearch_path={schema}",
    ),
}

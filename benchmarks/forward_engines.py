# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from xml.etree import ElementTree

ForwardEngine = Literal["rmlmapper", "souffle", "morphkgc"]

MAVEN_NAMESPACE = "{http://maven.apache.org/POM/4.0.0}"
TRANSLATOR_POM = (
    Path(__file__).resolve().parent.parent
    / "R2RML2Datalog-Translator"
    / "translator"
    / "pom.xml"
)


def translator_rml_version() -> str:
    """Version of the RMLMapper that rulegen.jar links to read relational sources."""
    root = ElementTree.parse(TRANSLATOR_POM).getroot()
    for dependency in root.iter(f"{MAVEN_NAMESPACE}dependency"):
        if dependency.findtext(f"{MAVEN_NAMESPACE}artifactId") == "rmlmapper":
            return cast(str, dependency.findtext(f"{MAVEN_NAMESPACE}version"))
    raise LookupError(f"No rmlmapper dependency declared in {TRANSLATOR_POM}")


@dataclass(frozen=True)
class ForwardEngineDefinition:
    resource: str
    label: str
    version: str
    schema_query: str
    souffle_resources: bool = False
    writes_facts: bool = False

    @property
    def module_name(self) -> str:
        return self.resource.lower()

    def database_name(self, database: str, schema: str) -> str:
        return f"{database}?{self.schema_query.format(schema=schema)}"


SOUFFLE_RELEASE = "2.5"

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
        souffle_resources=True,
        writes_facts=True,
    ),
    "morphkgc": ForwardEngineDefinition(
        resource="MorphKGC",
        label="Morph-KGC",
        version="2.2.0",
        schema_query="options=-csearch_path={schema}",
    ),
}

# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from dataclasses import dataclass
from pathlib import Path
from typing import cast
from xml.etree import ElementTree

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
    souffle_resources: bool = False

    @property
    def module_name(self) -> str:
        return self.resource.lower()


SOUFFLE_RELEASE = "2.5"

SOUFFLE_ENGINE = ForwardEngineDefinition(
    resource="Souffle",
    label="Soufflé",
    version="1.0.0",
    souffle_resources=True,
)

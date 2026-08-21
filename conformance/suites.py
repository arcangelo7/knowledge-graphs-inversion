# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import csv
import os
from typing import Union

from pyoxigraph import BlankNode, Literal, NamedNode, RdfFormat, Store, Triple

from conformance.config import SUITE_LABELS

RdfSubject = Union[NamedNode, BlankNode, Triple]
RdfTerm = Union[NamedNode, BlankNode, Literal, Triple]

RDB2RDFTEST_DATABASE = NamedNode("http://purl.org/NET/rdb2rdf-test#database")
RDB2RDFTEST_SQL_SCRIPT = NamedNode("http://purl.org/NET/rdb2rdf-test#sqlScriptFile")
RDB2RDFTEST_HAS_EXPECTED_OUTPUT = NamedNode(
    "http://purl.org/NET/rdb2rdf-test#hasExpectedOutput"
)
RDB2RDFTEST_MAPPING_DOC = NamedNode("http://purl.org/NET/rdb2rdf-test#mappingDocument")
RDB2RDFTEST_OUTPUT = NamedNode("http://purl.org/NET/rdb2rdf-test#output")
DCELEMENTS_IDENTIFIER = NamedNode("http://purl.org/dc/terms/identifier")
DCELEMENTS_TITLE = NamedNode("http://purl.org/dc/terms/title")
TESTDEC_PURPOSE = NamedNode("http://www.w3.org/2006/03/test-description#purpose")


class TestSuite:
    suite_id: str
    name: str
    base_dir: str
    test_id_prefix: str

    def list_test_ids(self) -> list[str]:
        raise NotImplementedError

    def get_mapping_path(self, test_id: str) -> str:
        raise NotImplementedError

    def get_sql_script_path(self, test_id: str, database_system: str) -> str:
        raise NotImplementedError

    def get_expected_output_path(self, test_id: str) -> str:
        raise NotImplementedError

    def get_test_metadata(self, test_id: str) -> dict[str, str | bool] | None:
        raise NotImplementedError


class R2RMLTestSuite(TestSuite):
    def __init__(self, base_dir: str, inversion_base_dir: str | None = None):
        self.suite_id = "r2rml"
        self.name = SUITE_LABELS[self.suite_id]
        self.base_dir = base_dir
        self.test_id_prefix = "R2RMLTC"
        self._test_ids: list[str] = []
        self._catalogs: dict[str, tuple[str, Store]] = {}
        self._load_catalog(base_dir, self.test_id_prefix)
        if inversion_base_dir is not None:
            self._load_catalog(inversion_base_dir, "INVTC")

    def _load_catalog(self, base_dir: str, test_id_prefix: str) -> None:
        manifest_store = Store()
        manifest_store.load(
            path=os.path.join(base_dir, "manifest.ttl"), format=RdfFormat.TURTLE
        )
        test_ids = sorted(
            entry
            for entry in os.listdir(base_dir)
            if os.path.isdir(os.path.join(base_dir, entry))
            and entry.startswith(test_id_prefix)
        )
        self._test_ids.extend(test_ids)
        self._catalogs.update(
            {test_id: (base_dir, manifest_store) for test_id in test_ids}
        )

    def list_test_ids(self) -> list[str]:
        return list(self._test_ids)

    def _get_catalog(self, test_id: str) -> tuple[str, Store]:
        return self._catalogs[test_id]

    def _get_mapping_filename(self, test_id: str) -> str:
        letter: str = test_id[-1].lower()
        return f"r2rml{letter}.ttl" if letter.isalpha() else "r2rml.ttl"

    def get_mapping_path(self, test_id: str) -> str:
        base_dir, _ = self._get_catalog(test_id)
        return os.path.join(base_dir, test_id, self._get_mapping_filename(test_id))

    def _find_subject(
        self, manifest_store: Store, predicate: NamedNode, obj: RdfTerm
    ) -> RdfSubject | None:
        for quad in manifest_store.quads_for_pattern(None, predicate, obj):
            return quad.subject
        return None

    def _find_object(
        self,
        manifest_store: Store,
        subject: RdfSubject | None,
        predicate: NamedNode,
    ) -> RdfTerm | None:
        for quad in manifest_store.quads_for_pattern(subject, predicate, None):
            return quad.object
        return None

    def _find_object_value(
        self,
        manifest_store: Store,
        subject: RdfSubject | None,
        predicate: NamedNode,
    ) -> str:
        term = self._find_object(manifest_store, subject, predicate)
        assert isinstance(term, (NamedNode, BlankNode, Literal))
        return term.value

    def get_sql_script_path(self, test_id: str, database_system: str) -> str:
        base_dir, manifest_store = self._get_catalog(test_id)
        test_uri = self._find_subject(
            manifest_store, DCELEMENTS_IDENTIFIER, Literal(test_id)
        )
        database_uri = self._find_object(manifest_store, test_uri, RDB2RDFTEST_DATABASE)
        assert isinstance(database_uri, (NamedNode, BlankNode))
        database_script = self._find_object_value(
            manifest_store, database_uri, RDB2RDFTEST_SQL_SCRIPT
        )
        base_name, ext = os.path.splitext(database_script)
        system_specific = f"{base_name}-{database_system}{ext}"
        databases_dir = os.path.join(base_dir, "databases")
        if os.path.exists(os.path.join(databases_dir, system_specific)):
            return os.path.join(databases_dir, system_specific)
        return os.path.join(databases_dir, database_script)

    def get_expected_output_path(self, test_id: str) -> str:
        base_dir, _ = self._get_catalog(test_id)
        last_char = test_id[-1]
        suffix = last_char.lower() if last_char.isalpha() else ""
        return os.path.join(base_dir, test_id, f"mapped{suffix}.nq")

    def get_test_metadata(self, test_id: str) -> dict[str, str | bool] | None:
        _, manifest_store = self._get_catalog(test_id)
        test_uri = self._find_subject(
            manifest_store, DCELEMENTS_IDENTIFIER, Literal(test_id)
        )
        if test_uri is None:
            return None
        title = self._find_object(manifest_store, test_uri, DCELEMENTS_TITLE)
        purpose = self._find_object(manifest_store, test_uri, TESTDEC_PURPOSE)
        expected_output = self._find_object(
            manifest_store, test_uri, RDB2RDFTEST_HAS_EXPECTED_OUTPUT
        )
        mapping_doc = self._find_object(
            manifest_store, test_uri, RDB2RDFTEST_MAPPING_DOC
        )
        output_file = self._find_object(manifest_store, test_uri, RDB2RDFTEST_OUTPUT)
        has_expected = (
            isinstance(expected_output, Literal)
            and expected_output.value == "true"
            and expected_output.datatype.value
            == "http://www.w3.org/2001/XMLSchema#boolean"
        )

        def _val(term: RdfTerm | None) -> str:
            if term is None:
                return ""
            assert isinstance(term, (NamedNode, BlankNode, Literal))
            return term.value

        return {
            "title": _val(title),
            "purpose": _val(purpose) or "Purpose not specified",
            "expected_output": has_expected,
            "mapping_document": _val(mapping_doc),
            "output_file": _val(output_file),
        }


class RMLTestSuite(TestSuite):
    def __init__(self, base_dir: str):
        self.suite_id = "rml"
        self.name = SUITE_LABELS[self.suite_id]
        self.base_dir = base_dir
        self.test_id_prefix = "RMLTC"
        self.test_cases_dir = os.path.join(base_dir, "test-cases")
        self._metadata = self._load_metadata()

    def _load_metadata(self) -> dict[str, dict[str, str]]:
        metadata: dict[str, dict[str, str]] = {}
        csv_path = os.path.join(self.test_cases_dir, "metadata.csv")
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rml_id = row["ID"]
                if rml_id.startswith("RMLTC") and rml_id.endswith("-RDB"):
                    metadata[rml_id] = dict(row)
        return metadata

    def list_test_ids(self) -> list[str]:
        return sorted(
            [
                d
                for d in os.listdir(self.test_cases_dir)
                if os.path.isdir(os.path.join(self.test_cases_dir, d))
                and d.startswith("RMLTC")
                and d.endswith("-RDB")
            ]
        )

    def get_mapping_path(self, test_id: str) -> str:
        return os.path.join(self.test_cases_dir, test_id, "mapping.ttl")

    def get_sql_script_path(self, test_id: str, database_system: str) -> str:
        return os.path.join(self.test_cases_dir, test_id, "resource.sql")

    def get_expected_output_path(self, test_id: str) -> str:
        return os.path.join(self.test_cases_dir, test_id, "output.nq")

    def get_test_metadata(self, test_id: str) -> dict[str, str | bool] | None:
        row = self._metadata.get(test_id)
        if row is None:
            return None
        error_expected = row.get("error", "false").lower() == "true"
        output_path = self.get_expected_output_path(test_id)
        has_output = os.path.exists(output_path) and os.path.getsize(output_path) > 0
        return {
            "title": row.get("title", ""),
            "purpose": row.get("description", "Purpose not specified"),
            "expected_output": not error_expected and has_output,
            "mapping_document": "mapping.ttl",
            "output_file": "output.nq" if has_output else "",
        }


SUITES: dict[str, TestSuite] = {}


def register_suites(project_root: str) -> None:
    SUITES["r2rml"] = R2RMLTestSuite(
        os.path.join(project_root, "r2rml_test_cases"),
        os.path.join(project_root, "inversion_test_cases"),
    )
    SUITES["rml"] = RMLTestSuite(os.path.join(project_root, "rml_io_registry"))


def get_suite(suite_id: str) -> TestSuite:
    return SUITES[suite_id]

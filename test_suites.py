# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import csv
import os
from typing import Union

from pyoxigraph import BlankNode, Literal, NamedNode, RdfFormat, Store, Triple

RdfSubject = Union[NamedNode, BlankNode, Triple]
RdfTerm = Union[NamedNode, BlankNode, Literal, Triple]

RDB2RDFTEST_DATABASE = NamedNode("http://purl.org/NET/rdb2rdf-test#database")
RDB2RDFTEST_SQL_SCRIPT = NamedNode("http://purl.org/NET/rdb2rdf-test#sqlScriptFile")
RDB2RDFTEST_HAS_EXPECTED_OUTPUT = NamedNode("http://purl.org/NET/rdb2rdf-test#hasExpectedOutput")
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
    source_db_host: str
    dest_db_system: str

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

    def get_engine_output_path(self, test_id: str, database_system: str, output_format: str) -> str:
        raise NotImplementedError

    def get_engine_log_path(self, test_id: str, database_system: str) -> str:
        raise NotImplementedError

    def get_output_file_path(self, output_format: str) -> str:
        raise NotImplementedError

class R2RMLTestSuite(TestSuite):
    def __init__(self, base_dir: str, project_root: str):
        self.suite_id = 'r2rml'
        self.name = 'R2RML'
        self.base_dir = base_dir
        self.test_id_prefix = 'R2RMLTC'
        self.source_db_host = 'postgresql_r2rml'
        self.dest_db_system = 'dest_postgresql_r2rml'
        self.databases_dir = os.path.join(base_dir, 'databases')
        self.manifest_store = Store()
        self.manifest_store.load(path=os.path.join(base_dir, "manifest.ttl"), format=RdfFormat.TURTLE)

    def list_test_ids(self) -> list[str]:
        return sorted([
            f for f in os.listdir(self.base_dir)
            if os.path.isdir(os.path.join(self.base_dir, f)) and f.startswith(self.test_id_prefix)
        ])

    def _get_mapping_filename(self, test_id: str) -> str:
        letter: str = test_id[-1].lower()
        return f'r2rml{letter}.ttl' if letter.isalpha() else 'r2rml.ttl'

    def get_mapping_path(self, test_id: str) -> str:
        return os.path.join(self.base_dir, test_id, self._get_mapping_filename(test_id))

    def _find_subject(self, predicate: NamedNode, obj: RdfTerm) -> RdfSubject | None:
        for quad in self.manifest_store.quads_for_pattern(None, predicate, obj):
            return quad.subject
        return None

    def _find_object(self, subject: RdfSubject | None, predicate: NamedNode) -> RdfTerm | None:
        for quad in self.manifest_store.quads_for_pattern(subject, predicate, None):
            return quad.object
        return None

    def _find_object_value(self, subject: RdfSubject | None, predicate: NamedNode) -> str:
        term = self._find_object(subject, predicate)
        assert isinstance(term, (NamedNode, BlankNode, Literal))
        return term.value

    def get_sql_script_path(self, test_id: str, database_system: str) -> str:
        test_uri = self._find_subject(DCELEMENTS_IDENTIFIER, Literal(test_id))
        database_uri = self._find_object(test_uri, RDB2RDFTEST_DATABASE)
        assert isinstance(database_uri, (NamedNode, BlankNode))
        database_script = self._find_object_value(database_uri, RDB2RDFTEST_SQL_SCRIPT)
        base_name, ext = os.path.splitext(database_script)
        system_specific = f"{base_name}-{database_system}{ext}"
        if os.path.exists(os.path.join(self.databases_dir, system_specific)):
            return os.path.join(self.databases_dir, system_specific)
        return os.path.join(self.databases_dir, database_script)

    def get_expected_output_path(self, test_id: str) -> str:
        last_char = test_id[-1]
        suffix = last_char.lower() if last_char.isalpha() else ''
        return os.path.join(self.base_dir, test_id, f'mapped{suffix}.nq')

    def get_test_metadata(self, test_id: str) -> dict[str, str | bool] | None:
        test_uri = self._find_subject(DCELEMENTS_IDENTIFIER, Literal(test_id))
        if test_uri is None:
            return None
        title = self._find_object(test_uri, DCELEMENTS_TITLE)
        purpose = self._find_object(test_uri, TESTDEC_PURPOSE)
        expected_output = self._find_object(test_uri, RDB2RDFTEST_HAS_EXPECTED_OUTPUT)
        mapping_doc = self._find_object(test_uri, RDB2RDFTEST_MAPPING_DOC)
        output_file = self._find_object(test_uri, RDB2RDFTEST_OUTPUT)
        has_expected = (
            isinstance(expected_output, Literal)
            and expected_output.value == "true"
            and expected_output.datatype.value == "http://www.w3.org/2001/XMLSchema#boolean"
        )
        def _val(term: RdfTerm | None) -> str:
            if term is None:
                return ''
            assert isinstance(term, (NamedNode, BlankNode, Literal))
            return term.value

        return {
            'title': _val(title),
            'purpose': _val(purpose) or 'Purpose not specified',
            'expected_output': has_expected,
            'mapping_document': _val(mapping_doc),
            'output_file': _val(output_file),
        }

    def get_engine_output_path(self, test_id: str, database_system: str, output_format: str) -> str:
        ext = 'ttl' if output_format == 'turtle' else 'nt' if output_format == 'ntriples' else 'nq'
        return os.path.join(self.base_dir, test_id, f'engine_output-{database_system}.{ext}')

    def get_engine_log_path(self, test_id: str, database_system: str) -> str:
        return os.path.join(self.base_dir, test_id, f'engine_output-{database_system}.log')

    def get_output_file_path(self, output_format: str) -> str:
        ext = 'ttl' if output_format == 'turtle' else 'nq' if output_format == 'nquads' else 'nt'
        return os.path.join(self.base_dir, f'output.{ext}')


class RMLTestSuite(TestSuite):
    def __init__(self, base_dir: str, project_root: str):
        self.suite_id = 'rml'
        self.name = 'RML'
        self.base_dir = base_dir
        self.test_id_prefix = 'RMLTC'
        self.source_db_host = 'postgresql_rml'
        self.dest_db_system = 'dest_postgresql_rml'
        self.test_cases_dir = os.path.join(base_dir, 'test-cases')
        self._metadata = self._load_metadata()

    def _load_metadata(self) -> dict[str, dict[str, str]]:
        metadata: dict[str, dict[str, str]] = {}
        csv_path = os.path.join(self.base_dir, 'metadata.csv')
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                rml_id = row['RML id']
                if rml_id.endswith('-PostgreSQL'):
                    metadata[rml_id] = dict(row)
        return metadata

    def list_test_ids(self) -> list[str]:
        return sorted([
            d for d in os.listdir(self.test_cases_dir)
            if os.path.isdir(os.path.join(self.test_cases_dir, d)) and d.endswith('-PostgreSQL')
        ])

    def get_mapping_path(self, test_id: str) -> str:
        return os.path.join(self.test_cases_dir, test_id, 'mapping.ttl')

    def get_sql_script_path(self, test_id: str, database_system: str) -> str:
        return os.path.join(self.test_cases_dir, test_id, 'resource.sql')

    def get_expected_output_path(self, test_id: str) -> str:
        return os.path.join(self.test_cases_dir, test_id, 'output.nq')

    def get_test_metadata(self, test_id: str) -> dict[str, str | bool] | None:
        row = self._metadata.get(test_id)
        if row is None:
            return None
        error_expected = row.get('error expected?', 'false').lower() == 'true'
        output_path = self.get_expected_output_path(test_id)
        has_output = os.path.exists(output_path)
        return {
            'title': row.get('title', ''),
            'purpose': row.get('purpose', 'Purpose not specified'),
            'expected_output': not error_expected and has_output,
            'mapping_document': 'mapping.ttl',
            'output_file': 'output.nq' if has_output else '',
        }

    def get_engine_output_path(self, test_id: str, database_system: str, output_format: str) -> str:
        ext = 'ttl' if output_format == 'turtle' else 'nt' if output_format == 'ntriples' else 'nq'
        return os.path.join(self.test_cases_dir, test_id, f'engine_output-{database_system}.{ext}')

    def get_engine_log_path(self, test_id: str, database_system: str) -> str:
        return os.path.join(self.test_cases_dir, test_id, f'engine_output-{database_system}.log')

    def get_output_file_path(self, output_format: str) -> str:
        ext = 'ttl' if output_format == 'turtle' else 'nq' if output_format == 'nquads' else 'nt'
        return os.path.join(self.test_cases_dir, f'output.{ext}')


SUITES: dict[str, TestSuite] = {}


def register_suites(project_root: str) -> None:
    SUITES['r2rml'] = R2RMLTestSuite(os.path.join(project_root, 'r2rml_test_cases'), project_root)
    SUITES['rml'] = RMLTestSuite(os.path.join(project_root, 'rml_test_cases_repo'), project_root)


def get_suite(suite_id: str) -> TestSuite:
    return SUITES[suite_id]

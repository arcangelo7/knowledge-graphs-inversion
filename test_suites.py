import csv
import os
from configparser import ConfigParser

from rdflib import Dataset, Literal, Namespace

RDB2RDFTEST = Namespace("http://purl.org/NET/rdb2rdf-test#")
TESTDEC = Namespace("http://www.w3.org/2006/03/test-description#")
DCELEMENTS = Namespace("http://purl.org/dc/terms/")
RR = Namespace("http://www.w3.org/ns/r2rml#")


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

    def get_engine_output_path(self, test_id: str, database_system: str, output_format: str) -> str:
        raise NotImplementedError

    def get_engine_log_path(self, test_id: str, database_system: str) -> str:
        raise NotImplementedError

    def get_output_file_path(self, output_format: str) -> str:
        raise NotImplementedError

    def write_morph_kgc_config(self, config_path: str, test_id: str, database_system: str, output_format: str) -> None:
        output_path = self.get_output_file_path(output_format)
        mapping_path = self.get_mapping_path(test_id)

        config = ConfigParser()
        config['CONFIGURATION'] = {
            'output_file': output_path,
            'infer_sql_datatypes': 'yes',
            'logging_level': 'ERROR',
        }
        config['DataSource1'] = {
            'mappings': mapping_path,
            'db_url': 'postgresql+psycopg2://r2rml:r2rml@postgresql:5432/r2rml',
        }
        with open(config_path, 'w') as f:
            config.write(f)


class R2RMLTestSuite(TestSuite):
    def __init__(self, base_dir: str):
        self.suite_id = 'r2rml'
        self.name = 'R2RML'
        self.base_dir = base_dir
        self.test_id_prefix = 'R2RMLTC'
        self.databases_dir = os.path.join(base_dir, 'databases')
        self.manifest_graph = Dataset()
        self.manifest_graph.parse(os.path.join(base_dir, "manifest.ttl"), format='turtle')

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

    def get_sql_script_path(self, test_id: str, database_system: str) -> str:
        test_uri = self.manifest_graph.value(
            subject=None, predicate=DCELEMENTS.identifier, object=Literal(test_id)
        )
        database_uri = self.manifest_graph.value(
            subject=test_uri, predicate=RDB2RDFTEST.database, object=None
        )
        database_script = str(self.manifest_graph.value(
            subject=database_uri, predicate=RDB2RDFTEST.sqlScriptFile, object=None
        ))
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
        test_uri = self.manifest_graph.value(
            subject=None, predicate=DCELEMENTS.identifier, object=Literal(test_id)
        )
        if test_uri is None:
            return None
        title = self.manifest_graph.value(subject=test_uri, predicate=DCELEMENTS.title)
        purpose = self.manifest_graph.value(subject=test_uri, predicate=TESTDEC.purpose)
        expected_output = self.manifest_graph.value(subject=test_uri, predicate=RDB2RDFTEST.hasExpectedOutput)
        mapping_doc = self.manifest_graph.value(subject=test_uri, predicate=RDB2RDFTEST.mappingDocument)
        output_file = self.manifest_graph.value(subject=test_uri, predicate=RDB2RDFTEST.output)
        has_expected = bool(expected_output) and isinstance(expected_output, Literal) and expected_output.toPython() is True
        return {
            'title': str(title) if title else '',
            'purpose': str(purpose) if purpose else 'Purpose not specified',
            'expected_output': has_expected,
            'mapping_document': str(mapping_doc) if mapping_doc else '',
            'output_file': str(output_file) if output_file else '',
        }

    def get_engine_output_path(self, test_id: str, database_system: str, output_format: str) -> str:
        ext = 'ttl' if output_format == 'turtle' else 'nt' if output_format == 'ntriples' else 'nq'
        return os.path.join(self.base_dir, test_id, f'engine_output-{database_system}.{ext}')

    def get_engine_log_path(self, test_id: str, database_system: str) -> str:
        return os.path.join(self.base_dir, test_id, f'engine_output-{database_system}.log')

    def get_output_file_path(self, output_format: str) -> str:
        ext = 'ttl' if output_format == 'turtle' else 'nt'
        return os.path.join(self.base_dir, f'output.{ext}')


class RMLTestSuite(TestSuite):
    def __init__(self, base_dir: str):
        self.suite_id = 'rml'
        self.name = 'RML'
        self.base_dir = base_dir
        self.test_id_prefix = 'RMLTC'
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
        ext = 'ttl' if output_format == 'turtle' else 'nt'
        return os.path.join(self.test_cases_dir, f'output.{ext}')


SUITES: dict[str, TestSuite] = {}


def register_suites(project_root: str) -> None:
    SUITES['r2rml'] = R2RMLTestSuite(os.path.join(project_root, 'r2rml_test_cases'))
    SUITES['rml'] = RMLTestSuite(os.path.join(project_root, 'rml_test_cases_repo'))


def get_suite(suite_id: str) -> TestSuite:
    return SUITES[suite_id]

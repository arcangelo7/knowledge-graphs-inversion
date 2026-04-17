# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import json
import logging
import math
import multiprocessing
import os
import tempfile
import traceback
from configparser import ConfigParser
from datetime import datetime
from queue import Empty

from flask import (Flask, Response, jsonify, render_template, request,
                   stream_with_context)
from pyoxigraph import BlankNode, Quad, RdfFormat, Store

import rmlmapper
from database_connection import DatabaseConnection
from kgi import (
    MappingError,
    NoDataError,
    NonInvertibleError,
    ReconstructedTable,
    UnsupportedMappingError,
)
from kgi.comparison import compare_databases
from kgi.core import _check_for_sql_queries, _parse_mapping_store, reconstruct
from test_suites import RdfTerm, TestSuite, register_suites, get_suite, SUITES

for _logger_name in ('morph_kgc', 'morph_kgc.config', 'morph_kgc.mapping',
                     'morph_kgc.engine', 'morph_kgc.args_parser', 'pyoxigraph',
                     'sqlalchemy', 'pandas', 'kgi'):
    logging.getLogger(_logger_name).setLevel(logging.CRITICAL)
logging.getLogger().setLevel(logging.CRITICAL)


app = Flask(__name__)

config = ConfigParser()
config.read('config.ini')

PROJECT_ROOT = os.path.dirname(__file__)


register_suites(PROJECT_ROOT)

db_connection = DatabaseConnection()


def _normalize_term(term: RdfTerm) -> str:
    if isinstance(term, BlankNode):
        return "_:BNODE"
    return str(term)


def _graphs_isomorphic(store1: Store, store2: Store) -> bool:
    quads1 = list(store1)
    quads2 = list(store2)
    if len(quads1) != len(quads2):
        return False

    has_bnodes = any(
        isinstance(q.subject, BlankNode) or isinstance(q.object, BlankNode)
        for q in quads1 + quads2
    )
    if not has_bnodes:
        return set(quads1) == set(quads2)

    def signature(quads: list[Quad]) -> set[tuple[str, str, str, str]]:
        return {
            (_normalize_term(q.subject), str(q.predicate), _normalize_term(q.object), str(q.graph_name))
            for q in quads
        }

    return signature(quads1) == signature(quads2)


def sanitize_data(data: object) -> object:
    if isinstance(data, dict):
        return {k: sanitize_data(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize_data(v) for v in data]
    elif isinstance(data, float):
        if math.isnan(data) or math.isinf(data):
            return None
        return data
    elif isinstance(data, (int, str, bool, type(None))):
        return data
    else:
        return str(data)


@app.route('/')
def index():
    all_tests: dict[str, list[str]] = {}
    for suite_id, suite in SUITES.items():
        all_tests[suite_id] = suite.list_test_ids()
    return render_template('index.jinja', suites=SUITES, all_tests=all_tests)


@app.route('/run_test', methods=['POST'])
def run_test():
    test_id = request.form['test_id']
    database_system = request.form['database_system']
    suite_id = request.form['suite_id']
    suite = get_suite(suite_id)
    result = run_single_test(test_id, database_system, suite)

    try:
        sanitized_result = sanitize_data(result)
        return jsonify(sanitized_result)
    except Exception as e:
        error_msg = f"Error serializing result for test {test_id}: {str(e)}"
        return jsonify({
            'status': 'error',
            'test_id': test_id,
            'message': error_msg
        }), 500


_SUITE_DONE = '__suite_done__'


def _serialize_result(result: dict[str, object], suite_id: str, suite_name: str) -> dict[str, object]:
    sanitized_raw = sanitize_data(result)
    sanitized: dict[str, object] = sanitized_raw if isinstance(sanitized_raw, dict) else {'_raw': sanitized_raw}
    sanitized['suite_id'] = suite_id
    sanitized['suite_name'] = suite_name
    return sanitized


def _run_suite_tests(sid: str, database_system: str, result_queue: multiprocessing.Queue) -> None:
    global db_connection, config
    config = ConfigParser()
    config.read('config.ini')
    db_connection = DatabaseConnection()
    register_suites(PROJECT_ROOT)
    suite = get_suite(sid)
    for test_id in suite.list_test_ids():
        result = run_single_test(test_id, database_system, suite)
        try:
            result_queue.put(_serialize_result(result, sid, suite.name))
        except Exception as e:
            result_queue.put({
                'status': 'error', 'test_id': test_id, 'message': str(e),
                'suite_id': sid, 'suite_name': suite.name,
            })
    result_queue.put(_SUITE_DONE)


@app.route('/run_all_tests', methods=['GET'])
def run_all_tests():
    database_system = request.args.get('database_system', 'postgresql')
    suite_ids = request.args.get('suite_id', 'r2rml').split(',')
    use_parallel = len(suite_ids) > 1

    all_results: list[dict[str, object]] = []

    def generate():
        if use_parallel:
            result_queue = multiprocessing.Queue()
            processes = [
                multiprocessing.Process(target=_run_suite_tests, args=(sid, database_system, result_queue))
                for sid in suite_ids
            ]
            for p in processes:
                p.start()

            suites_done = 0
            while suites_done < len(suite_ids):
                try:
                    item = result_queue.get(timeout=0.5)
                except Empty:
                    continue
                if item == _SUITE_DONE:
                    suites_done += 1
                    continue
                result_item: dict[str, object] = item  # type: ignore[assignment]
                all_results.append(result_item)
                yield f"data: {json.dumps(result_item)}\n\n"

            for p in processes:
                p.join()
        else:
            for sid in suite_ids:
                suite = get_suite(sid)
                for test_id in suite.list_test_ids():
                    result = run_single_test(test_id, database_system, suite)
                    try:
                        sanitized = _serialize_result(result, sid, suite.name)
                        all_results.append(sanitized)
                        yield f"data: {json.dumps(sanitized)}\n\n"
                    except Exception as e:
                        error: dict[str, object] = {
                            'status': 'error', 'test_id': test_id, 'message': str(e),
                            'suite_id': sid, 'suite_name': suite.name,
                        }
                        all_results.append(error)
                        yield f"data: {json.dumps(error)}\n\n"

        for sid in suite_ids:
            suite = get_suite(sid)
            suite_results = [r for r in all_results if r.get('suite_id') == sid]
            generate_test_report(suite_results, database_system, suite)

        yield "event: complete\ndata: All tests completed\n\n"

    return Response(stream_with_context(generate()), content_type='text/event-stream')


@app.route('/get_file_content', methods=['GET'])
def get_file_content():
    test_id = request.args.get('test_id', '')
    file_type = request.args.get('type')
    database_system = request.args.get('database_system', 'postgresql')
    suite_id = request.args.get('suite_id', 'r2rml')
    suite = get_suite(suite_id)

    if file_type == 'expected':
        file_path = suite.get_expected_output_path(test_id)
    elif file_type == 'actual':
        output_format = config['properties'].get('output_format', 'ntriples')
        file_path = suite.get_engine_output_path(test_id, database_system, output_format)
    else:
        return jsonify({'error': 'Invalid file type'}), 400

    try:
        with open(file_path, 'r') as file:
            content = file.read()
        return jsonify({'content': content})
    except FileNotFoundError:
        return jsonify({'error': 'File not found'}), 404


FAILED = "failed"
PASSED = "passed"


def test_one(test_id: str, database_system: str, config: ConfigParser, suite: TestSuite) -> list[list[str]]:
    try:
        metadata = suite.get_test_metadata(test_id)
        if metadata is None:
            return [["tester", "platform", "rdbms", "testid", "result"],
                    [config["tester"]["tester_name"], config["engine"]["engine_name"],
                     "PostgreSQL", test_id, "error"]]

        return _run_test(test_id, metadata, database_system, config, suite)
    except Exception:
        return [["tester", "platform", "rdbms", "testid", "result"],
                [config["tester"]["tester_name"], config["engine"]["engine_name"],
                 "PostgreSQL", test_id, "error"]]


def _run_test(
    t_identifier: str,
    metadata: dict[str, str | bool],
    database_system: str,
    config: ConfigParser,
    suite: TestSuite,
) -> list[list[str]]:
    results: list[list[str]] = [["tester", "platform", "rdbms", "testid", "result"]]
    expected_output = metadata['expected_output']
    output_format = config['properties'].get('output_format', 'ntriples')

    output_file = suite.get_output_file_path(output_format)
    engine_output_path = suite.get_engine_output_path(t_identifier, database_system, output_format)

    expected_output_store = Store()
    if os.path.isfile(output_file):
        os.system(f"rm {output_file}")

    if expected_output:
        expected_output_file = suite.get_expected_output_path(t_identifier)
        if os.path.isfile(expected_output_file):
            expected_output_store.load(path=expected_output_file, format=RdfFormat.N_QUADS)

    mapping_path = suite.get_mapping_path(t_identifier)
    jdbc_dsn = f"jdbc:postgresql://{suite.source_db_host}:5432/r2rml"
    with tempfile.TemporaryDirectory() as tmp_dir:
        if suite.suite_id == "rml":
            prepared = rmlmapper.prepare_rml_mapping(
                mapping_path, jdbc_dsn, "r2rml", "r2rml", tmp_dir,
            )
            exit_code = rmlmapper.run(prepared, output_file)
        else:
            exit_code = rmlmapper.run(
                mapping_path, output_file,
                dsn=jdbc_dsn, username="r2rml", password="r2rml",
            )

    output_file_has_data = os.path.isfile(output_file) and os.path.getsize(output_file) > 0
    if output_file_has_data:
        os.system(f"cp {output_file} {engine_output_path}")

        if expected_output:
            output_store = Store()
            try:
                rdf_format = RdfFormat.TURTLE if output_format == "turtle" else RdfFormat.N_QUADS if output_format == "nquads" else RdfFormat.N_TRIPLES
                output_store.load(path=output_file, format=rdf_format)
                if _graphs_isomorphic(expected_output_store, output_store):
                    result = PASSED
                else:
                    result = FAILED
            except Exception:
                result = FAILED
        elif exit_code != 0:
            result = PASSED
        else:
            result = FAILED
    else:
        if expected_output:
            if len(expected_output_store) == 0:
                result = PASSED
            else:
                result = FAILED
        else:
            result = PASSED

    results.append([
        config["tester"]["tester_name"], config["engine"]["engine_name"],
        "PostgreSQL", t_identifier, result
    ])
    return results


def run_single_test(test_id: str, database_system: str, suite: TestSuite) -> dict[str, object]:
    source_db = suite.source_db_host
    dest_db = suite.dest_db_system

    try:
        db_connection.drop_all_tables(source_db)
        db_connection.drop_all_tables(dest_db)

        sql_path = suite.get_sql_script_path(test_id, database_system)
        db_connection.load_sql_script(source_db, sql_path)

        mapping_file = suite.get_mapping_path(test_id)
        with open(mapping_file, 'r', encoding='utf-8') as f:
            mapping_content = f.read()

        metadata = suite.get_test_metadata(test_id)
        assert metadata is not None
        purpose = metadata['purpose']

        raw_results = test_one(test_id, database_system, config, suite)

        dest_db_url = db_connection.get_connection_string(dest_db)
        source_db_url = f'postgresql+psycopg2://r2rml:r2rml@{suite.source_db_host}:5432/r2rml'
        output_format = config['properties'].get('output_format', 'ntriples')
        rdf_output_path = suite.get_output_file_path(output_format)

        inversion_result: dict[str, ReconstructedTable] | None = None
        inversion_status: str | None = None
        comparison_status: str | None = None

        rdf_file_has_data = os.path.isfile(rdf_output_path) and os.path.getsize(rdf_output_path) > 0
        if not rdf_file_has_data:
            source_content = db_connection.get_database_content(source_db)
            dest_content = db_connection.get_database_content(dest_db)
            if not metadata["expected_output"]:
                inversion_status = 'forward_mapping_failed'
                databases_equal = None
                comparison_message = "Forward mapping produced no output (error test case)"
                comparison_status = 'forward_mapping_failed'
            else:
                mapping_store = _parse_mapping_store(mapping_file)
                if _check_for_sql_queries(mapping_store):
                    inversion_status = 'not_supported'
                    databases_equal = None
                    comparison_message = "Inversion not supported: SQL query as logical table is not supported"
                    comparison_status = None
                else:
                    databases_equal = True
                    comparison_message = "Forward mapping produced empty output - no data to invert"
                    comparison_status = None

        else:
            try:
                inversion_result = reconstruct(
                    mapping=mapping_file,
                    rdf_graph=rdf_output_path,
                    source_db_url=source_db_url,
                    dest_db_url=dest_db_url,
                )
                source_content = db_connection.get_database_content(source_db)
                dest_content = db_connection.get_database_content(dest_db)
                databases_equal, comparison_message, comparison_status = compare_databases(source_content, dest_content, mapping_content)
            except UnsupportedMappingError as e:
                inversion_status = 'not_supported'
                databases_equal = None
                comparison_message = f"Inversion not supported: {e}"
                source_content = None
                dest_content = None
            except NonInvertibleError as e:
                inversion_status = 'non_invertible'
                source_content = db_connection.get_database_content(source_db)
                dest_content = db_connection.get_database_content(dest_db)
                databases_equal = None
                comparison_message = f"Non-invertible mapping detected: {e}"
                comparison_status = 'non_invertible'
            except MappingError as e:
                inversion_status = 'forward_mapping_failed'
                source_content = db_connection.get_database_content(source_db)
                dest_content = db_connection.get_database_content(dest_db)
                databases_equal = None
                comparison_message = f"Forward mapping failed: {e}"
                comparison_status = 'forward_mapping_failed'
            except NoDataError:
                source_content = db_connection.get_database_content(source_db)
                dest_content = db_connection.get_database_content(dest_db)
                forward_mapping_result = raw_results[1][4] if len(raw_results) > 1 else None
                if forward_mapping_result == FAILED:
                    inversion_status = 'forward_mapping_failed'
                    databases_equal = None
                    comparison_message = "Forward mapping failed - inversion could not be attempted"
                    comparison_status = 'forward_mapping_failed'
                else:
                    databases_equal, comparison_message, comparison_status = compare_databases(source_content, dest_content, mapping_content)
                    if not databases_equal and not dest_content:
                        databases_equal = True
                        comparison_message = "Inversion correctly not performed due to mapping errors - destination database appropriately empty"
                        comparison_status = None

        if (
            inversion_status is None
            and comparison_status
            and comparison_status.startswith("partial:")
        ):
            inversion_status = 'partially_inverted'

        processed_results = process_results(
            raw_results, mapping_content, test_id, database_system,
            config, purpose, inversion_result, databases_equal, comparison_message,
            source_content, dest_content, suite, inversion_status, comparison_status,
            error_test=not metadata["expected_output"],
        )

        return {
            'status': 'success',
            'test_id': test_id,
            'results': processed_results
        }
    except Exception as e:
        error_traceback = traceback.format_exc()
        return {
            'status': 'error',
            'test_id': test_id,
            'message': str(e),
            'traceback': error_traceback
        }


def process_results(
    raw_results: list[list[str]],
    mapping_content: str,
    test_id: str,
    database_system: str,
    config: ConfigParser,
    purpose: str | bool,
    inversion_result: dict[str, ReconstructedTable] | None,
    databases_equal: bool | None,
    comparison_message: str,
    source_content: dict[str, dict[str, list[str]]] | None,
    dest_content: dict[str, dict[str, list[str]]] | None,
    suite: TestSuite,
    inversion_status: str | None = None,
    comparison_status: str | None = None,
    error_test: bool = False,
) -> dict[str, list[str] | list[dict[str, object]]]:
    processed_results: dict[str, list[str] | list[dict[str, object]]] = {
        'headers': ['Test ID', 'Purpose', 'Result', 'Expected Result', 'Actual Result', 'Mapping', 'SPARQL Query', 'Inversion Query', 'Inversion Success', 'Tables Comparison'],
        'data': []
    }
    data_list: list[dict[str, object]] = []

    for row in raw_results[1:]:
        expected_content, actual_content = get_file_contents(test_id, database_system, config, suite)

        if inversion_result is None:
            formatted_inversion_result = ""
            formatted_sparql_queries = ""
        else:
            formatted_queries = []
            sparql_queries = []
            for table in inversion_result.values():
                formatted_queries.append(table.sql.strip())  # type: ignore[union-attr]
                sparql_queries.append(table.sparql_query)  # type: ignore[union-attr]
            formatted_inversion_result = "\n\n".join(formatted_queries)
            formatted_sparql_queries = "\n\n".join(filter(None, sparql_queries))

        processed_row = {
            'testid': row[3],
            'purpose': purpose,
            'result': row[4],
            'expected_result': expected_content,
            'actual_result': actual_content,
            'mapping': mapping_content,
            'sparql_query': formatted_sparql_queries,
            'inversion_query': formatted_inversion_result,
            'inversion_success': ('not_supported' if inversion_status == 'not_supported' else
                                'forward_mapping_failed' if inversion_status == 'forward_mapping_failed' else
                                'non_invertible' if inversion_status == 'non_invertible' or comparison_status == 'non_invertible' else
                                'partially_inverted' if inversion_status == 'partially_inverted' else databases_equal),
            'tables_equal': databases_equal,
            'comparison_message': comparison_message,
            'comparison_subcategory': comparison_status if comparison_status and comparison_status.startswith('partial:') else None,
            'original_tables': source_content,
            'inverted_tables': dest_content,
            'error_test': error_test,
        }
        data_list.append(processed_row)

    processed_results['data'] = data_list
    return processed_results


def get_file_contents(
    test_id: str, database_system: str, config: ConfigParser, suite: TestSuite,
) -> tuple[str, str]:
    output_format = config['properties'].get('output_format', 'ntriples')
    expected_file = suite.get_expected_output_path(test_id)
    actual_file = suite.get_engine_output_path(test_id, database_system, output_format)
    return read_file_content(expected_file), read_file_content(actual_file)


def read_file_content(file_path: str) -> str:
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read()
    except FileNotFoundError:
        # Test cases with invalid mappings don't produce RDF output files,
        # so missing expected/actual files is part of the normal flow.
        return ""


def generate_test_report(
    results: list[dict[str, object]], database_system: str | None, suite: TestSuite,
) -> None:
    results_dir = os.path.join(PROJECT_ROOT, 'test_results')
    os.makedirs(results_dir, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_filename = f"test_report_{suite.suite_id}_{database_system}_{timestamp}.json"
    report_path = os.path.join(results_dir, report_filename)

    total_tests = len(results)
    passed_tests = 0
    failed_tests = 0
    not_supported_tests = 0
    non_invertible_tests = 0
    partially_inverted_tests = 0
    forward_mapping_failed_tests = 0
    error_tests = 0
    partial_subcategory_counts: dict[str, int] = {
        'columns_lost': 0, 'rows_lost': 0, 'multiplicity_lost': 0, 'tables_lost': 0,
    }

    test_details = []

    for result in results:
        if result['status'] == 'error':
            error_tests += 1
            test_details.append({
                'test_id': result['test_id'],
                'status': 'error',
                'message': result['message'],
                'purpose': None,
                'comparison_message': None,
                'subcategory': None,
            })
        elif result['status'] == 'success':
            results_data: dict[str, object] = result['results']  # type: ignore[assignment]
            test_data: dict[str, object] = results_data['data'][0]  # type: ignore[index]
            test_id = result['test_id']
            inversion_success = test_data['inversion_success']
            subcategory_raw = test_data.get('comparison_subcategory')
            subcategory = subcategory_raw if isinstance(subcategory_raw, str) else None

            if inversion_success == 'not_supported':
                not_supported_tests += 1
                status = 'not_supported'
            elif inversion_success == 'forward_mapping_failed':
                forward_mapping_failed_tests += 1
                status = 'forward_mapping_failed'
            elif inversion_success == 'non_invertible':
                non_invertible_tests += 1
                status = 'non_invertible'
            elif inversion_success == 'partially_inverted':
                partially_inverted_tests += 1
                status = 'partially_inverted'
                if subcategory and subcategory.startswith('partial:'):
                    for tag in subcategory[len('partial:'):].split(','):
                        if tag in partial_subcategory_counts:
                            partial_subcategory_counts[tag] += 1
            elif inversion_success is True:
                passed_tests += 1
                status = 'passed'
            else:
                failed_tests += 1
                status = 'failed'

            test_details.append({
                'test_id': test_id,
                'status': status,
                'purpose': test_data['purpose'],
                'result': test_data['result'],
                'inversion_success': inversion_success,
                'comparison_message': test_data['comparison_message'],
                'subcategory': subcategory,
            })

    def pct(n: int) -> float:
        return round((n / total_tests * 100), 2) if total_tests > 0 else 0

    report = {
        'metadata': {
            'timestamp': timestamp,
            'test_suite': suite.suite_id,
            'suite_name': suite.name,
            'database_system': database_system,
            'total_tests': total_tests,
            'execution_date': datetime.now().isoformat()
        },
        'summary': {
            'total': total_tests,
            'passed': passed_tests,
            'failed': failed_tests,
            'not_supported': not_supported_tests,
            'partially_inverted': partially_inverted_tests,
            'non_invertible': non_invertible_tests,
            'forward_mapping_failed': forward_mapping_failed_tests,
            'errors': error_tests,
            'partial_subcategories': partial_subcategory_counts,
            'percentages': {
                'passed': pct(passed_tests),
                'failed': pct(failed_tests),
                'not_supported': pct(not_supported_tests),
                'partially_inverted': pct(partially_inverted_tests),
                'non_invertible': pct(non_invertible_tests),
                'forward_mapping_failed': pct(forward_mapping_failed_tests),
                'errors': pct(error_tests),
            }
        },
        'test_details': test_details
    }

    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    markdown_filename = f"test_report_{suite.suite_id}_{database_system}_{timestamp}.md"
    markdown_path = os.path.join(results_dir, markdown_filename)

    with open(markdown_path, 'w', encoding='utf-8') as f:
        f.write(f"# {suite.name} inversion test report\n\n")
        f.write(f"**Test suite:** {suite.name}\n")
        f.write(f"**Database system:** {database_system}\n")
        f.write(f"**Execution date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"**Total tests:** {total_tests}\n\n")

        f.write("## Summary\n\n")
        f.write("| Status | Count | Percentage |\n")
        f.write("|--------|-------|------------|\n")
        f.write(f"| Fully inverted | {passed_tests} | {pct(passed_tests)}% |\n")
        f.write(f"| Partially inverted | {partially_inverted_tests} | {pct(partially_inverted_tests)}% |\n")
        f.write(f"| Non-invertible | {non_invertible_tests} | {pct(non_invertible_tests)}% |\n")
        f.write(f"| Not supported | {not_supported_tests} | {pct(not_supported_tests)}% |\n")
        f.write(f"| Forward mapping failed | {forward_mapping_failed_tests} | {pct(forward_mapping_failed_tests)}% |\n")
        f.write(f"| Failed | {failed_tests} | {pct(failed_tests)}% |\n")
        f.write(f"| Execution errors | {error_tests} | {pct(error_tests)}% |\n")

        if partially_inverted_tests:
            f.write("\n### Partially inverted subcategories\n\n")
            f.write("| Subcategory | Count |\n|---|---|\n")
            labels = {
                'columns_lost': 'Columns lost (unmapped columns)',
                'rows_lost': 'Rows lost (NULL in subject template)',
                'multiplicity_lost': 'Multiplicity lost (duplicate rows collapsed)',
                'tables_lost': 'Tables lost (unmapped tables)',
            }
            for tag, label in labels.items():
                count = partial_subcategory_counts[tag]
                if count:
                    f.write(f"| {label} | {count} |\n")

        f.write("\n## Test details\n\n")

        for status in ['passed', 'partially_inverted', 'non_invertible', 'not_supported', 'failed', 'forward_mapping_failed', 'error']:
            status_tests = [t for t in test_details if t['status'] == status]
            if status_tests:
                status_label_map = {
                    'passed': 'Fully inverted',
                    'partially_inverted': 'Partially inverted',
                    'non_invertible': 'Non-invertible',
                    'not_supported': 'Not supported',
                    'failed': 'Failed',
                    'forward_mapping_failed': 'Forward mapping failed',
                    'error': 'Execution errors',
                }
                status_label = status_label_map[status]
                f.write(f"\n### {status_label} tests ({len(status_tests)})\n\n")
                for test in status_tests:
                    f.write(f"- **{test['test_id']}**")
                    if test['purpose']:
                        purpose_text = test['purpose']
                        f.write(f": {purpose_text[:100]}..." if len(purpose_text) > 100 else f": {purpose_text}")
                    if status == 'partially_inverted' and test.get('subcategory'):
                        sub = test['subcategory']
                        if isinstance(sub, str) and sub.startswith('partial:'):
                            f.write(f"\n  - Subcategory: {sub[len('partial:'):]}")
                    if test['comparison_message'] and status in ['failed', 'partially_inverted', 'non_invertible', 'forward_mapping_failed']:
                        comp_msg = test['comparison_message']
                        f.write(f"\n  - {comp_msg[:200]}..." if len(comp_msg) > 200 else f"\n  - {comp_msg}")
                    f.write("\n")


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

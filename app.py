import json
import logging
import math
import os
import re
import traceback
from configparser import ConfigParser
from datetime import datetime

import pandas as pd
import sqlalchemy
from flask import (Flask, Response, jsonify, render_template, request,
                   stream_with_context)
from rdflib import Graph, Namespace

from database_connection import DatabaseConnection
from kgi.core import inversion
from r2rml_test_cases.test import database_load, test_one
from test_suites import TestSuite, register_suites, get_suite, SUITES

logging.getLogger('morph_kgc').setLevel(logging.ERROR)
logging.getLogger('morph_kgc.config').setLevel(logging.ERROR)
logging.getLogger('morph_kgc.mapping').setLevel(logging.ERROR)
logging.getLogger('morph_kgc.engine').setLevel(logging.ERROR)
logging.getLogger('morph_kgc.args_parser').setLevel(logging.ERROR)
logging.getLogger('rdflib').setLevel(logging.ERROR)
logging.getLogger('sqlalchemy').setLevel(logging.ERROR)
logging.getLogger('pandas').setLevel(logging.ERROR)
logging.getLogger().setLevel(logging.ERROR)


app = Flask(__name__)

config = ConfigParser()
config.read('config.ini')

PROJECT_ROOT = os.path.dirname(__file__)
MORPH_KCG_CONFIG_FILEPATH = os.path.join(PROJECT_ROOT, 'morph_kgc_config.ini')

RR = Namespace("http://www.w3.org/ns/r2rml#")
RML_OLD = Namespace("http://semweb.mmlab.be/ns/rml#")

DEST_DB_SYSTEM = 'dest_postgresql'

register_suites(PROJECT_ROOT)

db_connection = DatabaseConnection()


def extract_columns_from_mapping(mapping_content: str) -> set[str]:
    g = Graph()
    g.parse(data=mapping_content, format='turtle')
    columns: set[str] = set()
    for _, _, o in g.triples((None, RR.column, None)):
        columns.add(str(o).strip('"'))
    for _, _, o in g.triples((None, RML_OLD.reference, None)):
        columns.add(str(o).strip('"'))
    for _, _, o in g.triples((None, RR.template, None)):
        column_refs = TEMPLATE_COLUMN_REGEX.findall(str(o))
        columns.update(column_refs)
    for _, _, o in g.triples((None, RR.child, None)):
        columns.add(str(o).strip('"'))
    for _, _, o in g.triples((None, RR.parent, None)):
        columns.add(str(o).strip('"'))
    return columns


def check_mapping_column_coverage(mapping_content: str, source_content: dict[str, dict[str, list[str]]]) -> list[str]:
    mapped_columns = extract_columns_from_mapping(mapping_content)
    mapping_issues = []
    for table_name, table_data in source_content.items():
        table_columns = set(table_data['columns'])
        missing_columns = table_columns - mapped_columns
        if missing_columns:
            missing_str = ", ".join(sorted(missing_columns))
            mapping_issues.append(f"Table '{table_name}' has unmapped columns: {missing_str}")
    return mapping_issues


TEMPLATE_COLUMN_REGEX = re.compile(r'\{\\?"?\'?([^"\'{}\\]+)\\?"?\'?\}')


def parse_mapping_graph(mapping_content: str) -> Graph:
    g = Graph()
    g.parse(data=mapping_content, format='turtle')
    return g


def get_mapped_table_names(mapping_graph: Graph) -> set[str]:
    tables: set[str] = set()
    for _, _, o in mapping_graph.triples((None, RR.tableName, None)):
        tables.add(str(o).strip('"'))
    return tables


def find_subject_map_for_table(mapping_graph: Graph, table_name: str):
    for logical_table in mapping_graph.subjects(RR.tableName, None):
        tname = str(mapping_graph.value(logical_table, RR.tableName)).strip('"')
        if tname != table_name:
            continue
        # Check both rr:logicalTable (R2RML) and rml:logicalSource (RML)
        triples_map = next(mapping_graph.subjects(RR.logicalTable, logical_table), None)
        if triples_map is None:
            triples_map = next(mapping_graph.subjects(RML_OLD.logicalSource, logical_table), None)
        if triples_map is None:
            continue
        for subject_map in mapping_graph.objects(triples_map, RR.subjectMap):
            return subject_map
    return None


def check_null_in_subject_template(mapping_graph: Graph, source_df: pd.DataFrame, table_name: str):
    subject_map = find_subject_map_for_table(mapping_graph, table_name)
    if subject_map is None:
        return None, False
    template = mapping_graph.value(subject_map, RR.template)
    if template is None:
        return None, False
    column_refs = TEMPLATE_COLUMN_REGEX.findall(str(template))
    for col in column_refs:
        if col in source_df.columns and bool(source_df[col].isna().any()):
            null_count = int(source_df[col].isna().sum())
            return (
                f"{table_name} (MAPPING ISSUE: NULL values in subject template column "
                f"'{col}' cause {null_count} row(s) to be excluded from RDF)",
                True
            )
    return None, False


def detect_mapping_issue(mapping_graph: Graph, source_df: pd.DataFrame, table_name: str):
    null_msg, is_null = check_null_in_subject_template(mapping_graph, source_df, table_name)
    if is_null:
        return null_msg, True
    return None, False


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


@app.route('/run_all_tests', methods=['GET'])
def run_all_tests():
    database_system = request.args.get('database_system', 'postgresql')
    suite_ids = request.args.get('suite_id', 'r2rml').split(',')

    tests_by_suite: list[tuple[str, str]] = []
    for sid in suite_ids:
        suite = get_suite(sid)
        for test_id in suite.list_test_ids():
            tests_by_suite.append((sid, test_id))

    all_results: list[dict[str, object]] = []

    def generate():
        for suite_id, test_id in tests_by_suite:
            suite = get_suite(suite_id)
            result = run_single_test(test_id, database_system, suite)
            try:
                sanitized_raw = sanitize_data(result)
                sanitized: dict[str, object] = sanitized_raw if isinstance(sanitized_raw, dict) else {'_raw': sanitized_raw}
                sanitized['suite_id'] = suite_id
                sanitized['suite_name'] = suite.name
                all_results.append(sanitized)
                json_result = json.dumps(sanitized)
                yield f"data: {json_result}\n\n"
            except Exception as e:
                error_msg = f"Error serializing result for test {test_id}: {str(e)}"
                error_result: dict[str, object] = {
                    'status': 'error', 'test_id': test_id, 'message': error_msg,
                    'suite_id': suite_id, 'suite_name': suite.name,
                }
                all_results.append(error_result)
                yield f"data: {json.dumps(error_result)}\n\n"

        for suite_id in suite_ids:
            suite = get_suite(suite_id)
            suite_results = [r for r in all_results if r.get('suite_id') == suite_id]
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


def drop_tables(db_connection: DatabaseConnection, database_system: str) -> None:
    connection_string = db_connection.get_connection_string(database_system)
    engine = db_connection.create_engine(connection_string)
    try:
        with engine.begin():
            metadata = sqlalchemy.MetaData()
            metadata.reflect(bind=engine)
            metadata.drop_all(engine)
    finally:
        engine.dispose()


def run_single_test(test_id: str, database_system: str, suite: TestSuite) -> dict[str, object]:
    original_dir = os.getcwd()
    try:
        drop_tables(db_connection, database_system)
        drop_tables(db_connection, DEST_DB_SYSTEM)

        sql_path = suite.get_sql_script_path(test_id, database_system)
        database_load(sql_path)

        mapping_file = suite.get_mapping_path(test_id)
        with open(mapping_file, 'r', encoding='utf-8') as f:
            mapping_content = f.read()

        metadata = suite.get_test_metadata(test_id)
        purpose = metadata['purpose'] if metadata else 'Purpose not specified'

        raw_results = test_one(test_id, database_system, config, suite)

        dest_db_url = db_connection.get_connection_string(DEST_DB_SYSTEM)
        inversion_result = inversion(MORPH_KCG_CONFIG_FILEPATH, test_id, dest_db_url)

        inversion_status: str | None = None
        if isinstance(inversion_result, dict) and '__status__' in inversion_result:
            inversion_status = str(inversion_result['__status__'])
            inversion_reason = str(inversion_result.get('__reason__', ''))
            inversion_success = False
        else:
            inversion_success = bool(inversion_result)
            inversion_reason = ''

        if inversion_success:
            databases_equal, comparison_message, source_content, dest_content, comparison_status = compare_databases(db_connection, database_system, DEST_DB_SYSTEM, mapping_content)
        elif inversion_status == 'not_supported':
            databases_equal = None
            comparison_message = f"Inversion not supported: {inversion_reason}"
            source_content = None
            dest_content = None
            comparison_status = None
        elif inversion_status == 'mapping_issue':
            databases_equal, _, source_content, dest_content, _ = compare_databases(db_connection, database_system, DEST_DB_SYSTEM, mapping_content)
            databases_equal = None
            comparison_message = f"Bad mapping detected: {inversion_reason}"
            comparison_status = 'mapping_issue'
        elif inversion_status == 'mapping_error':
            databases_equal, _, source_content, dest_content, _ = compare_databases(db_connection, database_system, DEST_DB_SYSTEM, mapping_content)
            databases_equal = None
            comparison_message = f"Invalid mapping: {inversion_reason}"
            comparison_status = 'mapping_error'
        elif inversion_status in ['no_input_file', 'no_data_generated']:
            databases_equal, comparison_message, source_content, dest_content, comparison_status = compare_databases(db_connection, database_system, DEST_DB_SYSTEM, mapping_content)
            if not databases_equal and not dest_content:
                databases_equal = True
                comparison_message = "Inversion correctly not performed due to mapping errors - destination database appropriately empty"
                comparison_status = None
        else:
            databases_equal, comparison_message, source_content, dest_content, comparison_status = compare_databases(db_connection, database_system, DEST_DB_SYSTEM, mapping_content)

        processed_results = process_results(
            raw_results, mapping_content, test_id, database_system,
            config, purpose, inversion_result, databases_equal, comparison_message,
            source_content, dest_content, suite, inversion_status, comparison_status
        )

        os.chdir(original_dir)
        return {
            'status': 'success',
            'test_id': test_id,
            'results': processed_results
        }
    except Exception as e:
        error_traceback = traceback.format_exc()
        os.chdir(original_dir)
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
    inversion_result: dict[str, dict[str, str]],
    databases_equal: bool | None,
    comparison_message: str,
    source_content: dict[str, dict[str, list[str]]] | None,
    dest_content: dict[str, dict[str, list[str]]] | None,
    suite: TestSuite,
    inversion_status: str | None = None,
    comparison_status: str | None = None,
) -> dict[str, list[str] | list[dict[str, object]]]:
    processed_results: dict[str, list[str] | list[dict[str, object]]] = {
        'headers': ['Test ID', 'Purpose', 'Result', 'Expected Result', 'Actual Result', 'Mapping', 'SPARQL Query', 'Inversion Query', 'Inversion Success', 'Tables Comparison'],
        'data': []
    }
    data_list: list[dict[str, object]] = []

    for row in raw_results[1:]:
        expected_content, actual_content = get_file_contents(test_id, database_system, config, suite)

        if isinstance(inversion_result, dict) and '__status__' in inversion_result:
            formatted_inversion_result = ""
            formatted_sparql_queries = ""
        else:
            formatted_queries = []
            sparql_queries = []
            for _, result in inversion_result.items():
                formatted_queries.append(result['inverted_query'].strip())
                sparql_queries.append(result['sparql_query'])
            formatted_inversion_result = "\n\n".join(formatted_queries)
            formatted_sparql_queries = "\n\n".join(filter(None, sparql_queries))

        processed_row = {
            'testid': row[3] if len(row) > 3 else 'N/A',
            'purpose': purpose,
            'result': row[4] if len(row) > 4 else 'N/A',
            'expected_result': expected_content,
            'actual_result': actual_content,
            'mapping': mapping_content,
            'sparql_query': formatted_sparql_queries,
            'inversion_query': formatted_inversion_result,
            'inversion_success': ('not_supported' if inversion_status == 'not_supported' else
                                'mapping_issue' if comparison_status == 'mapping_issue' else
                                'mapping_error' if comparison_status == 'mapping_error' else databases_equal),
            'tables_equal': databases_equal,
            'comparison_message': comparison_message,
            'original_tables': source_content,
            'inverted_tables': dest_content
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
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read()
    return "File not found"


def analyze_duplicate_loss(
    source_df: pd.DataFrame, dest_df: pd.DataFrame, table_name: str,
) -> tuple[str | None, bool]:
    source_unique = source_df.drop_duplicates()
    dest_unique = dest_df.drop_duplicates()

    if source_unique.equals(dest_unique) and len(source_df) > len(dest_df):
        duplicate_rows = []
        for _, row in source_unique.iterrows():
            source_count = len(source_df[source_df.eq(row).all(axis=1)])
            dest_count = len(dest_df[dest_df.eq(row).all(axis=1)])
            if source_count > dest_count:
                duplicate_rows.append((source_count, dest_count, dict(row)))

        if duplicate_rows:
            duplicate_info = "; ".join([f"Row {row} appears {src_cnt} times in source but {dst_cnt} times in destination"
                                       for src_cnt, dst_cnt, row in duplicate_rows])
            message = (f"{table_name} (MAPPING ISSUE: Duplicate rows lost during inversion - {duplicate_info}. "
                       "Consider adding unique identifiers to your mapping template to preserve row distinctness)")
            return message, True

    return None, False


def generate_test_report(
    results: list[dict[str, object]], database_system: str | None, suite: TestSuite,
) -> None:
    try:
        results_dir = os.path.join(PROJECT_ROOT, 'test_results')
        os.makedirs(results_dir, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_filename = f"test_report_{suite.suite_id}_{database_system}_{timestamp}.json"
        report_path = os.path.join(results_dir, report_filename)

        total_tests = len(results)
        passed_tests = 0
        failed_tests = 0
        not_supported_tests = 0
        mapping_issue_tests = 0
        mapping_error_tests = 0
        error_tests = 0

        test_details = []

        for result in results:
            if result.get('status') == 'error':
                error_tests += 1
                test_details.append({
                    'test_id': result.get('test_id'),
                    'status': 'error',
                    'message': result.get('message')
                })
            elif result.get('status') == 'success':
                results_data: dict[str, object] = result['results']  # type: ignore[assignment]
                test_data: dict[str, object] = results_data['data'][0]  # type: ignore[index]
                test_id = result.get('test_id')
                inversion_success = test_data.get('inversion_success')

                if inversion_success == 'not_supported':
                    not_supported_tests += 1
                    status = 'not_supported'
                elif inversion_success == 'mapping_issue':
                    mapping_issue_tests += 1
                    status = 'mapping_issue'
                elif inversion_success == 'mapping_error':
                    mapping_error_tests += 1
                    status = 'mapping_error'
                elif inversion_success is True:
                    passed_tests += 1
                    status = 'passed'
                else:
                    failed_tests += 1
                    status = 'failed'

                test_details.append({
                    'test_id': test_id,
                    'status': status,
                    'purpose': test_data.get('purpose'),
                    'result': test_data.get('result'),
                    'inversion_success': inversion_success,
                    'comparison_message': test_data.get('comparison_message')
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
                'mapping_issues': mapping_issue_tests,
                'mapping_errors': mapping_error_tests,
                'errors': error_tests,
                'percentages': {
                    'passed': pct(passed_tests),
                    'failed': pct(failed_tests),
                    'not_supported': pct(not_supported_tests),
                    'mapping_issues': pct(mapping_issue_tests),
                    'mapping_errors': pct(mapping_error_tests),
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
            f.write(f"| Passed | {passed_tests} | {pct(passed_tests)}% |\n")
            f.write(f"| Failed | {failed_tests} | {pct(failed_tests)}% |\n")
            f.write(f"| Not supported | {not_supported_tests} | {pct(not_supported_tests)}% |\n")
            f.write(f"| Mapping issues | {mapping_issue_tests} | {pct(mapping_issue_tests)}% |\n")
            f.write(f"| Mapping errors | {mapping_error_tests} | {pct(mapping_error_tests)}% |\n")
            f.write(f"| Execution errors | {error_tests} | {pct(error_tests)}% |\n")

            f.write("\n## Test details\n\n")

            for status in ['passed', 'failed', 'not_supported', 'mapping_issue', 'mapping_error', 'error']:
                status_tests = [t for t in test_details if t['status'] == status]
                if status_tests:
                    status_label = status.replace('_', ' ').title()
                    f.write(f"\n### {status_label} tests ({len(status_tests)})\n\n")
                    for test in status_tests:
                        f.write(f"- **{test['test_id']}**")
                        if test.get('purpose'):
                            purpose_text = test['purpose']
                            f.write(f": {purpose_text[:100]}..." if len(purpose_text) > 100 else f": {purpose_text}")
                        if test.get('comparison_message') and status in ['failed', 'mapping_issue', 'mapping_error']:
                            comp_msg = test['comparison_message']
                            f.write(f"\n  - {comp_msg[:200]}..." if len(comp_msg) > 200 else f"\n  - {comp_msg}")
                        f.write("\n")

        print(f"Test report generated: {report_path}")
        print(f"Markdown report generated: {markdown_path}")

    except Exception as e:
        print(f"Error generating test report: {str(e)}")
        traceback.print_exc()


def compare_databases(
    db_connection: DatabaseConnection, source_system: str, dest_system: str, mapping_content: str | None = None,
):
    try:
        source_content = db_connection.get_database_content(source_system)
        dest_content = db_connection.get_database_content(dest_system)

        if not source_content and not dest_content:
            return True, "Both databases are empty - comparison successful", None, None, None
        elif not source_content or not dest_content:
            return False, "One database is empty while the other is not", source_content, dest_content, None

        mapping_graph = parse_mapping_graph(mapping_content) if mapping_content else None

        source_tables = set(source_content.keys())
        dest_tables = set(dest_content.keys())
        missing_from_dest = source_tables - dest_tables

        mismatched_tables = []
        has_mapping_issues = False

        if missing_from_dest:
            if mapping_graph:
                mapped_tables = get_mapped_table_names(mapping_graph)
                unmapped_tables = {t for t in missing_from_dest if t not in mapped_tables}
                if unmapped_tables == missing_from_dest:
                    unmapped_str = ", ".join(sorted(unmapped_tables))
                    mismatched_tables.append(f"MAPPING ISSUE: Unmapped tables: {unmapped_str}")
                    has_mapping_issues = True
                else:
                    return False, "Tables in source and destination databases do not match", source_content, dest_content, None
            else:
                return False, "Tables in source and destination databases do not match", source_content, dest_content, None

        common_tables = source_tables & dest_tables
        for table_name in common_tables:
            source_table = source_content[table_name]
            dest_table = dest_content[table_name]

            if set(source_table['columns']) != set(dest_table['columns']):
                mismatched_tables.append(f"{table_name} (columns mismatch)")
                continue

            source_df = pd.DataFrame(source_table['data'], columns=source_table['columns'])
            dest_df = pd.DataFrame(dest_table['data'], columns=dest_table['columns'])

            if source_df.empty and dest_df.empty:
                continue

            source_df = source_df.dropna(how='all')
            dest_df = dest_df.dropna(how='all')
            source_df = source_df.reindex(sorted(source_df.columns), axis=1)
            dest_df = dest_df.reindex(sorted(dest_df.columns), axis=1)
            source_df.reset_index(drop=True, inplace=True)
            dest_df.reset_index(drop=True, inplace=True)
            source_df = source_df.sort_values(by=source_df.columns.tolist()).reset_index(drop=True)
            dest_df = dest_df.sort_values(by=dest_df.columns.tolist()).reset_index(drop=True)

            if not source_df.equals(dest_df):
                resolved = False
                if len(source_df) > len(dest_df):
                    duplicate_analysis, is_dup_issue = analyze_duplicate_loss(source_df, dest_df, table_name)
                    if duplicate_analysis:
                        mismatched_tables.append(duplicate_analysis)
                        if is_dup_issue:
                            has_mapping_issues = True
                        resolved = True

                if not resolved and mapping_graph:
                    issue_msg, is_issue = detect_mapping_issue(mapping_graph, source_df, table_name)
                    if is_issue:
                        mismatched_tables.append(issue_msg)
                        has_mapping_issues = True
                        resolved = True

                if not resolved:
                    mismatched_tables.append(f"{table_name} (data mismatch)")

        if mismatched_tables:
            message = f"Mismatched tables: {', '.join(mismatched_tables)}"

            if mapping_content and not has_mapping_issues:
                mapping_issues = check_mapping_column_coverage(mapping_content, source_content)
                if mapping_issues:
                    mapping_issue_message = "; ".join(mapping_issues)
                    message += f" (MAPPING ISSUE: {mapping_issue_message})"
                    has_mapping_issues = True

            if has_mapping_issues:
                return False, message, source_content, dest_content, "mapping_issue"
            else:
                return False, message, source_content, dest_content, None
        else:
            return True, "All tables in source and destination databases are identical", source_content, dest_content, None
    except Exception as e:
        return False, f"Error comparing databases: {str(e)}", None, None, None


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

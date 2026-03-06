import json
import logging
import math
import os
import traceback
from configparser import ConfigParser
from datetime import datetime

import pandas as pd
import sqlalchemy
from flask import (Flask, Response, jsonify, render_template, request,
                   stream_with_context)
from rdflib import Dataset, Literal, Namespace, Graph
import re

from database_connection import DatabaseConnection
from kgi.core import inversion
from r2rml_test_cases.test import database_load, test_one

# Suppress morph-kgc and other external library logging immediately after imports
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

# Load configuration
config = ConfigParser()
config.read('config.ini')

TEST_CASES_DIR = os.path.join(os.path.dirname(__file__), 'r2rml_test_cases')

MORPH_KCG_CONFIG_FILEPATH = os.path.join(os.path.dirname(__file__), 'morph_kgc_config.ini')

RDB2RDFTEST = Namespace("http://purl.org/NET/rdb2rdf-test#")
TESTDEC = Namespace("http://www.w3.org/2006/03/test-description#")
DCELEMENTS = Namespace("http://purl.org/dc/terms/")
RR = Namespace("http://www.w3.org/ns/r2rml#")

DEST_DB_SYSTEM = 'dest_postgresql'

manifest_graph = Dataset()
manifest_graph.parse(os.path.join(TEST_CASES_DIR, "manifest.ttl"), format='turtle')

db_connection = DatabaseConnection()

def get_mapping_filename(test_id):
    letter: str = test_id[-1].lower()
    return f'r2rml{letter}.ttl' if letter.isalpha() else 'r2rml.ttl'

def extract_columns_from_mapping(mapping_content):
    """Extract all column references from R2RML mapping content."""
    try:
        g = Graph()
        g.parse(data=mapping_content, format='turtle')
        
        columns = set()
        
        # Extract column references from rr:column properties
        for s, p, o in g.triples((None, RR.column, None)):
            column_name = str(o).strip('"')
            columns.add(column_name)
        
        # Extract column references from rr:template properties
        for s, p, o in g.triples((None, RR.template, None)):
            template = str(o)
            # Find column references in templates like {\"ID\"} or {ID} or {"ID"}
            column_refs = re.findall(r'\{\\?"?\'?([^"\'{}\\]+)\\?"?\'?\}', template)
            columns.update(column_refs)
        
        # Extract column references from join conditions (rr:child and rr:parent)
        for s, p, o in g.triples((None, RR.child, None)):
            column_name = str(o).strip('"')
            columns.add(column_name)
            
        for s, p, o in g.triples((None, RR.parent, None)):
            column_name = str(o).strip('"')
            columns.add(column_name)
            
        return columns
    except Exception as e:
        print(f"Error extracting columns from mapping: {e}")
        return set()


def check_mapping_column_coverage(mapping_content, source_content):
    """Check if mapping covers all columns in source tables."""
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


def parse_mapping_graph(mapping_content):
    g = Graph()
    g.parse(data=mapping_content, format='turtle')
    return g


def get_mapped_table_names(mapping_graph):
    tables = set()
    for _, _, o in mapping_graph.triples((None, RR.tableName, None)):
        tables.add(str(o).strip('"'))
    return tables


def find_subject_map_for_table(mapping_graph, table_name):
    for logical_table in mapping_graph.subjects(RR.tableName, None):
        tname = str(mapping_graph.value(logical_table, RR.tableName)).strip('"')
        if tname != table_name:
            continue
        triples_map = next(mapping_graph.subjects(RR.logicalTable, logical_table), None)
        if triples_map is None:
            continue
        for subject_map in mapping_graph.objects(triples_map, RR.subjectMap):
            return subject_map
    return None


def check_null_in_subject_template(mapping_graph, source_df, table_name):
    subject_map = find_subject_map_for_table(mapping_graph, table_name)
    if subject_map is None:
        return None, False
    template = mapping_graph.value(subject_map, RR.template)
    if template is None:
        return None, False
    column_refs = TEMPLATE_COLUMN_REGEX.findall(str(template))
    for col in column_refs:
        if col in source_df.columns and source_df[col].isna().any():
            null_count = int(source_df[col].isna().sum())
            return (
                f"{table_name} (MAPPING ISSUE: NULL values in subject template column "
                f"'{col}' cause {null_count} row(s) to be excluded from RDF)",
                True
            )
    return None, False


def detect_mapping_issue(mapping_graph, source_df, table_name):
    null_msg, is_null = check_null_in_subject_template(mapping_graph, source_df, table_name)
    if is_null:
        return null_msg, True
    return None, False


def sanitize_data(data):
    if isinstance(data, dict):
        return {k: sanitize_data(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize_data(v) for v in data]
    elif isinstance(data, float):
        if math.isnan(data):
            return None  # Convert NaN to null
        elif math.isinf(data):
            return None  # Convert Infinity to null
        else:
            return data  # RETURN NORMAL FLOATS!
    elif isinstance(data, (int, str, bool, type(None))):
        return data
    else:
        return str(data)

@app.route('/')
def index():
    """
    Main index route that displays available test cases and system status.
    
    Returns:
        Rendered template with list of available tests and system status
    """
    tests = sorted([f for f in os.listdir(TEST_CASES_DIR) if os.path.isdir(os.path.join(TEST_CASES_DIR, f)) and f.startswith('R2RMLTC')])
    return render_template('index.jinja', tests=tests)

@app.route('/run_test', methods=['POST'])
def run_test():
    test_id = request.form['test_id']
    database_system = request.form['database_system']
    result = run_single_test(test_id, database_system)

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
    database_system = request.args.get('database_system')
    tests = sorted([f for f in os.listdir(TEST_CASES_DIR) if os.path.isdir(os.path.join(TEST_CASES_DIR, f)) and f.startswith('R2RMLTC')])
    
    # Collect all results for report generation
    all_results = []
    
    def generate():
        for test_id in tests:
            result = run_single_test(test_id, database_system)
            try:
                sanitized_result = sanitize_data(result)
                all_results.append(sanitized_result)
                json_result = json.dumps(sanitized_result)
                yield f"data: {json_result}\n\n"
            except Exception as e:
                error_msg = f"Error serializing result for test {test_id}: {str(e)}"
                error_result = {'status': 'error', 'test_id': test_id, 'message': error_msg}
                all_results.append(error_result)
                yield f"data: {json.dumps(error_result)}\n\n"
        
        # Generate report after all tests are completed
        generate_test_report(all_results, database_system)
        
        yield "event: complete\ndata: All tests completed\n\n"
    
    return Response(stream_with_context(generate()), content_type='text/event-stream')

@app.route('/get_file_content', methods=['GET'])
def get_file_content():
    test_id = request.args.get('test_id')
    file_type = request.args.get('type')  # 'expected' o 'actual'
    database_system = request.args.get('database_system')
    
    if file_type == 'expected':
        file_path = os.path.join(TEST_CASES_DIR, test_id, 'output.ttl')
    elif file_type == 'actual':
        file_path = os.path.join(TEST_CASES_DIR, test_id, f'engine_output-{database_system}.ttl')
    else:
        return jsonify({'error': 'Invalid file type'}), 400

    try:
        with open(file_path, 'r') as file:
            content = file.read()
        return jsonify({'content': content})
    except FileNotFoundError:
        return jsonify({'error': 'File not found'}), 404

def drop_tables(db_connection: DatabaseConnection, database_system):
    try:
        connection_string = db_connection.get_connection_string(database_system)
        engine = db_connection.create_engine(connection_string)
        with engine.begin() as connection:
            # Get the metadata
            metadata = sqlalchemy.MetaData()
            metadata.reflect(bind=engine)
            
            # Drop all tables
            metadata.drop_all(engine)
    finally:
        engine.dispose()

def run_single_test(test_id, database_system):
    test_dir = os.path.join(TEST_CASES_DIR)
    os.chdir(test_dir)

    try:
        # Reset databases for the new test
        drop_tables(db_connection, database_system)
        drop_tables(db_connection, DEST_DB_SYSTEM)

        # Load test-specific data
        test_uri = manifest_graph.value(subject=None, predicate=DCELEMENTS.identifier, object=Literal(test_id))
        database_uri = manifest_graph.value(subject=test_uri, predicate=RDB2RDFTEST.database, object=None)
        database = manifest_graph.value(subject=database_uri, predicate=RDB2RDFTEST.sqlScriptFile, object=None)
        
        # Load the database for the test
        database_load(database, database_system)

        # Get mapping content
        mapping_filename = get_mapping_filename(test_id)
        mapping_file = os.path.join(TEST_CASES_DIR, test_id, mapping_filename)
        with open(mapping_file, 'r', encoding='utf-8') as f:
            mapping_content = f.read()
        
        # Get the purpose of the test
        purpose = manifest_graph.value(subject=test_uri, predicate=TESTDEC.purpose, object=None)
        purpose = purpose.toPython() if purpose else "Purpose not specified"
        
        # Run the R2RML test
        raw_results = test_one(test_id, database_system, config, manifest_graph)        
        
        # Perform inversion
        dest_db_url = db_connection.get_connection_string(DEST_DB_SYSTEM)
        inversion_result = inversion(MORPH_KCG_CONFIG_FILEPATH, test_id, dest_db_url)

        # Check if inversion returned a special status or failed
        inversion_status = None
        if isinstance(inversion_result, dict) and '__status__' in inversion_result:
            inversion_status = inversion_result['__status__']
            inversion_reason = inversion_result.get('__reason__', '')
            inversion_success = False
        else:
            inversion_success = bool(inversion_result)
            inversion_reason = ''

        # Compare original and inverted tables
        if inversion_success:
            databases_equal, comparison_message, source_content, dest_content, comparison_status = compare_databases(db_connection, database_system, DEST_DB_SYSTEM, mapping_content)
        elif inversion_status == 'not_supported':
            databases_equal = None
            comparison_message = f"Inversion not supported: {inversion_reason}"
            source_content = None
            dest_content = None
            comparison_status = None
        elif inversion_status == 'mapping_issue':
            # Still get source content to show original tables, but destination will be empty
            databases_equal, _, source_content, dest_content, _ = compare_databases(db_connection, database_system, DEST_DB_SYSTEM, mapping_content)
            databases_equal = None  # Override since this is a mapping issue, not a comparison result
            comparison_message = f"Bad mapping detected: {inversion_reason}"
            comparison_status = 'mapping_issue'
        elif inversion_status == 'mapping_error':
            # Still get source content to show original tables, but destination will be empty
            databases_equal, _, source_content, dest_content, _ = compare_databases(db_connection, database_system, DEST_DB_SYSTEM, mapping_content)
            databases_equal = None  # Override since this is a mapping error, not a comparison result
            comparison_message = f"Invalid mapping: {inversion_reason}"
            comparison_status = 'mapping_error'
        elif inversion_status in ['no_input_file', 'no_data_generated']:
            # For these cases, we still want to compare databases to recognize empty destination as success
            databases_equal, comparison_message, source_content, dest_content, comparison_status = compare_databases(db_connection, database_system, DEST_DB_SYSTEM, mapping_content)
            
            # If destination is empty as expected due to mapping errors, this is success
            if not databases_equal and not dest_content:
                databases_equal = True
                comparison_message = f"Inversion correctly not performed due to mapping errors - destination database appropriately empty"
                comparison_status = None
        else:
            # Fallback case - should not happen with explicit status handling above
            databases_equal, comparison_message, source_content, dest_content, comparison_status = compare_databases(db_connection, database_system, DEST_DB_SYSTEM, mapping_content)

        # Process and generate results
        processed_results = process_results(
            raw_results, mapping_content, test_id, database_system, 
            config, purpose, inversion_result, databases_equal, comparison_message,
            source_content, dest_content, inversion_status, comparison_status
        )
        
        # Return to original directory
        os.chdir(os.path.dirname(__file__))
        
        return {
            'status': 'success', 
            'test_id': test_id, 
            'results': processed_results
        }
    except Exception as e:
        error_traceback = traceback.format_exc()
        os.chdir(os.path.dirname(__file__))
        return {
            'status': 'error',
            'test_id': test_id,
            'message': str(e),
            'traceback': error_traceback
        }

def process_results(raw_results, mapping_content, test_id, database_system, config, purpose, inversion_result, 
                    databases_equal, comparison_message, source_content, dest_content, inversion_status=None, comparison_status=None):
    processed_results = {
        'headers': ['Test ID', 'Purpose', 'Result', 'Expected Result', 'Actual Result', 'Mapping', 'SPARQL Query', 'Inversion Query', 'Inversion Success', 'Tables Comparison'],
        'data': []
    }
    
    for row in raw_results[1:]:  # Skip the header row
        expected_content, actual_content = get_file_contents(test_id, database_system, config)

        # Handle special status cases
        if isinstance(inversion_result, dict) and '__status__' in inversion_result:
            formatted_queries = []
            sparql_queries = []
            formatted_inversion_result = ""
            formatted_sparql_queries = ""
        else:
            formatted_queries = []
            sparql_queries = []
            for source, result in inversion_result.items():
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
        processed_results['data'].append(processed_row)
    
    return processed_results

def get_file_contents(test_id, database_system, config: ConfigParser):
    output_format = config['properties'].get('output_format', 'ntriples')
    ext = 'ttl' if output_format == 'turtle' else 'nt' if output_format == 'ntriples' else 'nq'
    
    # Get the last character of the test_id
    last_char: str = test_id[-1]
    
    # Determine the suffix for the expected file
    suffix = last_char.lower() if last_char.isalpha() else ''
    
    expected_file = os.path.join(TEST_CASES_DIR, test_id, f'mapped{suffix}.nq')
    actual_file = os.path.join(TEST_CASES_DIR, test_id, f'engine_output-{database_system}.{ext}')

    expected_content = read_file_content(expected_file)
    actual_content = read_file_content(actual_file)
    
    return expected_content, actual_content

def read_file_content(file_path):
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read()
    return "File not found"

def analyze_duplicate_loss(source_df, dest_df, table_name):
    """
    Analyze if the difference between source and destination is due to duplicate rows
    being lost during RDF inversion process.
    
    Returns a tuple (diagnostic_message, is_mapping_issue) where is_mapping_issue is True
    if this is specifically a duplicate loss issue.
    """
    # Check if destination is a subset of source (same unique rows, but missing duplicates)
    source_unique = source_df.drop_duplicates()
    dest_unique = dest_df.drop_duplicates()
    
    # If unique rows match but counts don't, we likely have duplicate loss
    if source_unique.equals(dest_unique) and len(source_df) > len(dest_df):
        # Find which rows have duplicates in source
        duplicate_rows = []
        for _, row in source_unique.iterrows():
            source_count = len(source_df[source_df.eq(row).all(axis=1)])
            dest_count = len(dest_df[dest_df.eq(row).all(axis=1)])
            if source_count > dest_count:
                duplicate_rows.append((source_count, dest_count, dict(row)))
        
        if duplicate_rows:
            duplicate_info = "; ".join([f"Row {row} appears {src_cnt} times in source but {dst_cnt} times in destination" 
                                       for src_cnt, dst_cnt, row in duplicate_rows])
            
            message = f"{table_name} (MAPPING ISSUE: Duplicate rows lost during inversion - {duplicate_info}. " \
                     f"Consider adding unique identifiers to your R2RML mapping template to preserve row distinctness)"
            return message, True
    
    return None, False

def generate_test_report(results, database_system):
    """Generate a comprehensive test report and save it to a file."""
    try:
        results_dir = os.path.join(os.path.dirname(__file__), 'r2rml_inversion_test_results')
        os.makedirs(results_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_filename = f"test_report_{database_system}_{timestamp}.json"
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
            elif result.get('status') == 'success' and result.get('results', {}).get('data'):
                test_data = result['results']['data'][0]
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
                    'r2rml_result': test_data.get('result'),
                    'inversion_success': inversion_success,
                    'comparison_message': test_data.get('comparison_message')
                })
        
        report = {
            'metadata': {
                'timestamp': timestamp,
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
                    'passed': round((passed_tests / total_tests * 100), 2) if total_tests > 0 else 0,
                    'failed': round((failed_tests / total_tests * 100), 2) if total_tests > 0 else 0,
                    'not_supported': round((not_supported_tests / total_tests * 100), 2) if total_tests > 0 else 0,
                    'mapping_issues': round((mapping_issue_tests / total_tests * 100), 2) if total_tests > 0 else 0,
                    'mapping_errors': round((mapping_error_tests / total_tests * 100), 2) if total_tests > 0 else 0,
                    'errors': round((error_tests / total_tests * 100), 2) if total_tests > 0 else 0
                }
            },
            'test_details': test_details
        }
        
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        markdown_filename = f"test_report_{database_system}_{timestamp}.md"
        markdown_path = os.path.join(results_dir, markdown_filename)
        
        with open(markdown_path, 'w', encoding='utf-8') as f:
            f.write(f"# R2RML Inversion Test Report\n\n")
            f.write(f"**Database System:** {database_system}\n")
            f.write(f"**Execution Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"**Total Tests:** {total_tests}\n\n")
            
            f.write(f"## Summary\n\n")
            f.write(f"| Status | Count | Percentage |\n")
            f.write(f"|--------|-------|------------|\n")
            f.write(f"| ✅ Passed | {passed_tests} | {report['summary']['percentages']['passed']}% |\n")
            f.write(f"| ❌ Failed | {failed_tests} | {report['summary']['percentages']['failed']}% |\n")
            f.write(f"| ⚠️ Not Supported | {not_supported_tests} | {report['summary']['percentages']['not_supported']}% |\n")
            f.write(f"| 🔧 Mapping Issues | {mapping_issue_tests} | {report['summary']['percentages']['mapping_issues']}% |\n")
            f.write(f"| ⛔ Mapping Errors | {mapping_error_tests} | {report['summary']['percentages']['mapping_errors']}% |\n")
            f.write(f"| 💥 Execution Errors | {error_tests} | {report['summary']['percentages']['errors']}% |\n")
            
            f.write(f"\n## Test Details\n\n")
            
            for status in ['passed', 'failed', 'not_supported', 'mapping_issue', 'mapping_error', 'error']:
                status_tests = [t for t in test_details if t['status'] == status]
                if status_tests:
                    status_label = status.replace('_', ' ').title()
                    f.write(f"\n### {status_label} Tests ({len(status_tests)})\n\n")
                    for test in status_tests:
                        f.write(f"- **{test['test_id']}**")
                        if test.get('purpose'):
                            f.write(f": {test['purpose'][:100]}..." if len(test.get('purpose', '')) > 100 else f": {test['purpose']}")
                        if test.get('comparison_message') and status in ['failed', 'mapping_issue', 'mapping_error']:
                            f.write(f"\n  - {test['comparison_message'][:200]}..." if len(test.get('comparison_message', '')) > 200 else f"\n  - {test['comparison_message']}")
                        f.write("\n")
        
        print(f"Test report generated: {report_path}")
        print(f"Markdown report generated: {markdown_path}")
        
    except Exception as e:
        print(f"Error generating test report: {str(e)}")
        traceback.print_exc()

def compare_databases(db_connection, source_system, dest_system, mapping_content=None):
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
            
            # Handle empty dataframes
            if source_df.empty and dest_df.empty:
                continue
            
            # Remove rows with all NULL values
            source_df = source_df.dropna(how='all')
            dest_df = dest_df.dropna(how='all')

            source_df = source_df.reindex(sorted(source_df.columns), axis=1)
            dest_df = dest_df.reindex(sorted(dest_df.columns), axis=1)

            # Reset index and sort
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
            
            # If we have mapping content, check for incomplete column coverage
            if mapping_content and not has_mapping_issues:
                mapping_issues = check_mapping_column_coverage(mapping_content, source_content)
                if mapping_issues:
                    mapping_issue_message = "; ".join(mapping_issues)
                    message += f" (MAPPING ISSUE: {mapping_issue_message})"
                    has_mapping_issues = True
            
            # Return a special indicator if this is a mapping issue
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

import csv
import os
import sys
from configparser import ConfigParser, ExtendedInterpolation

import psycopg2
from rdflib import ConjunctiveGraph, compare

from test_suites import TestSuite, register_suites, get_suite

current_dir = os.path.dirname(os.path.abspath(__file__))

failed = "failed"
passed = "passed"


def database_load(sql_script_path: str, host: str) -> None:
    print(f"Loading in {host} the file: {sql_script_path}")
    if not os.path.exists(sql_script_path):
        raise FileNotFoundError(f"SQL script file not found: {sql_script_path}")

    with open(sql_script_path, 'r') as f:
        sql_script = f.read()
        statements = sql_script.split(';')

    cnx = psycopg2.connect(f"dbname='r2rml' user='r2rml' host='{host}' password='r2rml'")
    cursor = cnx.cursor()
    try:
        for statement in statements:
            if statement.strip():
                try:
                    cursor.execute(statement)
                except psycopg2.Error as e:
                    print(f"Error executing statement: {e}")
                    print(f"Problematic statement: {statement}")
                    cnx.rollback()
                    raise
        cnx.commit()
    finally:
        cursor.close()
        cnx.close()
    print(f"Successfully loaded {sql_script_path} into {host}")


def test_one(test_id: str, database_system: str, config: ConfigParser, suite: TestSuite) -> list[list[str]]:
    try:
        metadata = suite.get_test_metadata(test_id)
        if metadata is None:
            print(f"Test {test_id} not found in {suite.name} suite")
            return [["tester", "platform", "rdbms", "testid", "result"],
                    [config["tester"]["tester_name"], config["engine"]["engine_name"],
                     "PostgreSQL", test_id, "error"]]

        print(f"Testing {suite.name} test-case: {test_id} ({metadata['title']})")
        print(f"Purpose of this test is: {metadata['purpose']}")

        sql_path = suite.get_sql_script_path(test_id, database_system)
        try:
            database_load(sql_path, host=suite.source_db_host)
        except Exception as e:
            print(f"Error loading database: {str(e)}")
            return [["tester", "platform", "rdbms", "testid", "result"],
                    [config["tester"]["tester_name"], config["engine"]["engine_name"],
                     "PostgreSQL", test_id, "error"]]

        return run_test(test_id, metadata, database_system, config, suite)
    except Exception as e:
        print(f"Error in test_one: {str(e)}")
        return [["tester", "platform", "rdbms", "testid", "result"],
                [config["tester"]["tester_name"], config["engine"]["engine_name"],
                 "PostgreSQL", test_id, "error"]]


def run_test(
    t_identifier: str,
    metadata: dict[str, str | bool],
    database_system: str,
    config: ConfigParser,
    suite: TestSuite,
) -> list[list[str]]:
    results: list[list[str]] = [["tester", "platform", "rdbms", "testid", "result"]]
    expected_output = metadata['expected_output']
    output_format = config['properties'].get('output_format', 'ntriples')

    suite.write_morph_kgc_config(t_identifier, database_system, output_format)

    output_file = suite.get_output_file_path(output_format)
    engine_output_path = suite.get_engine_output_path(t_identifier, database_system, output_format)
    engine_log_path = suite.get_engine_log_path(t_identifier, database_system)

    expected_output_graph = ConjunctiveGraph()
    if os.path.isfile(output_file):
        os.system(f"rm {output_file}")

    if expected_output:
        expected_output_file = suite.get_expected_output_path(t_identifier)
        if os.path.isfile(expected_output_file):
            expected_output_graph.parse(expected_output_file, format="nquads")

    engine_cmd = config['properties']['engine_command'].format(config_path=suite.morph_kgc_config_path)
    exit_code = os.system(f"{engine_cmd} > {engine_log_path}")

    if os.path.isfile(output_file):
        os.system(f"cp {output_file} {engine_output_path}")

        if expected_output:
            output_graph = ConjunctiveGraph()
            iso_expected = compare.to_isomorphic(expected_output_graph)
            try:
                output_graph.parse(output_file, format=output_format)
                iso_output = compare.to_isomorphic(output_graph)
                if iso_expected == iso_output:
                    result = passed
                else:
                    print("Output RDF does not match with the expected RDF")
                    result = failed
            except Exception:
                print("Output RDF is invalid")
                result = failed
        elif exit_code != 0:
            print("The processor returned a non-zero error code signalling a mistake")
            result = passed
        else:
            print("Output RDF found but none was expected")
            result = failed
    else:
        if expected_output:
            if len(expected_output_graph) == 0:
                result = passed
            else:
                print("No RDF output found while output was expected")
                result = failed
        else:
            result = passed

    results.append([
        config["tester"]["tester_name"], config["engine"]["engine_name"],
        "PostgreSQL", t_identifier, result
    ])
    return results


def database_up() -> None:
    database_path = os.path.join(current_dir, 'databases')
    os.system(f"docker compose -f {database_path}/docker-compose-postgresql.yml stop")
    os.system(f"docker compose -f {database_path}/docker-compose-postgresql.yml rm --force")
    os.system(f"docker compose -f {database_path}/docker-compose-postgresql.yml up -d && sleep 30")


def database_down() -> None:
    database_path = os.path.join(current_dir, 'databases')
    os.system(f"docker compose -f {database_path}/docker-compose-postgresql.yml stop")
    os.system(f"docker compose -f {database_path}/docker-compose-postgresql.yml rm --force")


def generate_results(config: ConfigParser, results: list[list[str]]) -> None:
    with open(os.path.join(current_dir, 'results.csv'), 'w', newline='', encoding='utf8') as file:
        writer = csv.writer(file)
        writer.writerows(results)

    metadata = [
        ["tester_name", "tester_url", "tester_contact", "test_date", "engine_version", "engine_name", "engine_created",
         "engine_url", "database", "database_name"],
        [config["tester"]["tester_name"], config["tester"]["tester_url"], config["tester"]["tester_contact"],
         config["engine"]["test_date"],
         config["engine"]["engine_version"], config["engine"]["engine_name"], config["engine"]["engine_created"],
         config["engine"]["engine_url"], "https://www.postgresql.org/", "PostgreSQL"]]

    with open(os.path.join(current_dir, 'metadata.csv'), 'w', newline='', encoding='utf8') as file:
        writer = csv.writer(file)
        writer.writerows(metadata)

    print("Generating the RDF results using EARL vocabulary")
    os.system(f"java -jar {os.path.join(current_dir, 'rmlmapper.jar')} -m {os.path.join(current_dir, 'mapping.rml.ttl')} -o {os.path.join(current_dir, 'results-postgresql.ttl')} -d")
    os.system(f"rm {os.path.join(current_dir, 'metadata.csv')} && mv {os.path.join(current_dir, 'results.csv')} {os.path.join(current_dir, 'results-postgresql.csv')}")


if __name__ == "__main__":  # pragma: no cover
    if len(sys.argv) != 2:
        print("Configuration file is missing: python3 test.py <config file>")
        sys.exit(1)

    config_file = str(sys.argv[1])
    if not os.path.isfile(config_file):
        print("The configuration file " + config_file + " does not exist.")
        print("Aborting...")
        sys.exit(2)

    config = ConfigParser(interpolation=ExtendedInterpolation())
    config.read(config_file)

    project_root = os.path.dirname(current_dir)
    register_suites(project_root)

    print("Deployment docker container for postgresql...")
    database_up()

    suite = get_suite('r2rml')
    if config["properties"]["tests"] == "all":
        all_results: list[list[str]] = [["tester", "platform", "rdbms", "testid", "result"]]
        for test_id in suite.list_test_ids():
            result = test_one(test_id, "postgresql", config, suite)
            all_results.extend(result[1:])
        generate_results(config, all_results)
    else:
        results = test_one(config["properties"]["tests"], "postgresql", config, suite)
        generate_results(config, results)

    database_down()

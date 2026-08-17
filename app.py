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
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from queue import Empty
from typing import cast

from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    stream_with_context,
)
import rmlmapper
from conformance_config import (
    DATABASE_CONFIGS,
    DEFAULT_ENGINE_PAIR,
    ENGINE_PAIRS,
    RML_MYSQL_UNAVAILABLE,
    SOUFFLE_RML_UNAVAILABLE,
    EnginePair,
    get_database_config,
    is_r2rml_case_available,
    validate_database_suite,
    validate_engine_pair,
)
from conformance_expectations import expected_outcome
from conformance_outcome import (
    LOSS_LABELS,
    OUTCOME_LABELS,
    CaseOutcome,
    InversionOutcome,
    evaluate_kgi_case,
    evaluate_souffle_case,
    forward_conformance_failed,
)
from database_connection import DatabaseConnection
from kgi.comparison import PartialLoss
from souffle_conformance import (
    Database as SouffleDatabase,
    SouffleConformanceAdapter,
    SouffleConformanceError,
)
from test_suites import TestSuite, register_suites, get_suite, SUITES

DEFAULT_MODE = "default"
SOUFFLE_MODES = {"rdf": False, "provenance": True}

for _logger_name in (
    "morph_kgc",
    "morph_kgc.config",
    "morph_kgc.mapping",
    "morph_kgc.engine",
    "morph_kgc.args_parser",
    "pyoxigraph",
    "sqlalchemy",
    "pandas",
    "kgi",
):
    logging.getLogger(_logger_name).setLevel(logging.CRITICAL)
logging.getLogger().setLevel(logging.CRITICAL)


app = Flask(__name__)

config = ConfigParser()
config.read("config.ini")

PROJECT_ROOT = os.path.dirname(__file__)
PROJECT_PATH = Path(PROJECT_ROOT)
SOUFFLE_TRANSLATOR_JAR = Path(os.environ["SOUFFLE_TRANSLATOR_JAR"])
SOUFFLE_REVERSE_SCRIPT = Path(os.environ["SOUFFLE_REVERSE_SCRIPT"])
SOUFFLE_FUNCTOR_LIBRARY = Path(os.environ["SOUFFLE_FUNCTOR_LIBRARY"])
SOUFFLE_EXECUTABLE = os.environ["SOUFFLE_EXECUTABLE"]


register_suites(PROJECT_ROOT)

db_connection = DatabaseConnection()


def _souffle_adapter(log_path: Path) -> SouffleConformanceAdapter:
    return SouffleConformanceAdapter(
        SOUFFLE_TRANSLATOR_JAR,
        SOUFFLE_REVERSE_SCRIPT,
        SOUFFLE_FUNCTOR_LIBRARY,
        execution_mode="local",
        souffle_executable=SOUFFLE_EXECUTABLE,
        log_path=log_path,
    )


def _artifact_path(
    suite: TestSuite,
    test_id: str,
    database_system: str,
    engine_pair: EnginePair,
    output_format: str,
) -> Path:
    extension = {
        "turtle": "ttl",
        "nquads": "nq",
        "ntriples": "nt",
    }[output_format]
    return (
        PROJECT_PATH
        / "test_results"
        / "artifacts"
        / engine_pair
        / suite.suite_id
        / database_system
        / test_id
        / f"output.{extension}"
    )


def _engine_output_format(engine_pair: EnginePair) -> str:
    if engine_pair == "souffle_souffle":
        return "nquads"
    return config["properties"]["output_format"]


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


@app.route("/")
def index():
    all_tests: dict[str, list[str]] = {}
    for suite_id, suite in SUITES.items():
        all_tests[suite_id] = suite.list_test_ids()
    database_options = {
        database_system: {
            "label": database.label,
            "suite_ids": list(database.suite_hosts),
        }
        for database_system, database in DATABASE_CONFIGS.items()
    }
    return render_template(
        "index.jinja",
        suites=SUITES,
        all_tests=all_tests,
        database_options=database_options,
        engine_options=ENGINE_PAIRS,
        default_engine_pair=DEFAULT_ENGINE_PAIR,
        rml_mysql_unavailable=RML_MYSQL_UNAVAILABLE,
        souffle_rml_unavailable=SOUFFLE_RML_UNAVAILABLE,
    )


def _validate_request(
    database_system: str, suite_ids: list[str], engine_pair: str
) -> str | None:
    try:
        for suite_id in suite_ids:
            validate_database_suite(database_system, suite_id)
            validate_engine_pair(engine_pair, suite_id)
    except ValueError as error:
        return str(error)
    return None


@app.route("/run_test", methods=["POST"])
def run_test():
    test_id = request.form["test_id"]
    database_system = request.form["database_system"]
    suite_id = request.form["suite_id"]
    engine_pair = request.form["engine_pair"]
    validation_error = _validate_request(database_system, [suite_id], engine_pair)
    if validation_error:
        return jsonify({"error": validation_error}), 400
    suite = get_suite(suite_id)
    selected_pair = validate_engine_pair(engine_pair, suite_id)
    results = run_single_test(test_id, database_system, suite, selected_pair)

    try:
        sanitized_result = sanitize_data(results)
        return jsonify(sanitized_result)
    except Exception as e:
        error_msg = f"Error serializing result for test {test_id}: {str(e)}"
        return jsonify(
            {"status": "error", "test_id": test_id, "message": error_msg}
        ), 500


_SUITE_DONE = "__suite_done__"


def _serialize_result(
    result: dict[str, object],
    suite_id: str,
    suite_name: str,
    engine_pair: EnginePair,
) -> dict[str, object]:
    sanitized_raw = sanitize_data(result)
    sanitized: dict[str, object] = (
        sanitized_raw if isinstance(sanitized_raw, dict) else {"_raw": sanitized_raw}
    )
    sanitized["suite_id"] = suite_id
    sanitized["suite_name"] = suite_name
    sanitized["engine_pair"] = engine_pair
    return sanitized


def _run_suite_tests(
    sid: str,
    database_system: str,
    engine_pair: EnginePair,
    result_queue: multiprocessing.Queue,
) -> None:
    global db_connection, config
    config = ConfigParser()
    config.read("config.ini")
    db_connection = DatabaseConnection()
    register_suites(PROJECT_ROOT)
    suite = get_suite(sid)
    for test_id in suite.list_test_ids():
        for result in run_single_test(test_id, database_system, suite, engine_pair):
            try:
                result_queue.put(
                    _serialize_result(result, sid, suite.name, engine_pair)
                )
            except Exception as e:
                result_queue.put(
                    {
                        "status": "error",
                        "test_id": test_id,
                        "result_key": result["result_key"],
                        "message": str(e),
                        "suite_id": sid,
                        "suite_name": suite.name,
                        "engine_pair": engine_pair,
                    }
                )
    result_queue.put(_SUITE_DONE)


@app.route("/run_all_tests", methods=["GET"])
def run_all_tests():
    database_system = request.args.get("database_system", "postgresql")
    suite_ids = request.args.get("suite_id", "r2rml").split(",")
    engine_pair = request.args["engine_pair"]
    validation_error = _validate_request(database_system, suite_ids, engine_pair)
    if validation_error:
        return jsonify({"error": validation_error}), 400
    use_parallel = len(suite_ids) > 1
    selected_pair = validate_engine_pair(engine_pair, suite_ids[0])

    all_results: list[dict[str, object]] = []

    def generate():
        if use_parallel:
            result_queue = multiprocessing.Queue()
            processes = [
                multiprocessing.Process(
                    target=_run_suite_tests,
                    args=(sid, database_system, selected_pair, result_queue),
                )
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
                    for result in run_single_test(
                        test_id, database_system, suite, selected_pair
                    ):
                        try:
                            sanitized = _serialize_result(
                                result, sid, suite.name, selected_pair
                            )
                            all_results.append(sanitized)
                            yield f"data: {json.dumps(sanitized)}\n\n"
                        except Exception as e:
                            error: dict[str, object] = {
                                "status": "error",
                                "test_id": test_id,
                                "result_key": result["result_key"],
                                "message": str(e),
                                "suite_id": sid,
                                "suite_name": suite.name,
                                "engine_pair": selected_pair,
                            }
                            all_results.append(error)
                            yield f"data: {json.dumps(error)}\n\n"

        for sid in suite_ids:
            suite = get_suite(sid)
            suite_results = [r for r in all_results if r["suite_id"] == sid]
            generate_test_report(suite_results, database_system, suite, selected_pair)

        yield "event: complete\ndata: All tests completed\n\n"

    return Response(stream_with_context(generate()), content_type="text/event-stream")


@app.route("/get_file_content", methods=["GET"])
def get_file_content():
    test_id = request.args.get("test_id", "")
    file_type = request.args.get("type")
    database_system = request.args.get("database_system", "postgresql")
    suite_id = request.args.get("suite_id", "r2rml")
    engine_pair = request.args["engine_pair"]
    validation_error = _validate_request(database_system, [suite_id], engine_pair)
    if validation_error:
        return jsonify({"error": validation_error}), 400
    suite = get_suite(suite_id)
    selected_pair = validate_engine_pair(engine_pair, suite_id)

    if file_type == "expected":
        file_path = suite.get_expected_output_path(test_id)
    elif file_type == "actual":
        output_format = _engine_output_format(selected_pair)
        file_path = _artifact_path(
            suite, test_id, database_system, selected_pair, output_format
        )
    else:
        return jsonify({"error": "Invalid file type"}), 400

    try:
        with open(file_path, encoding="utf-8") as file:
            content = file.read()
        return jsonify({"content": content})
    except FileNotFoundError:
        return jsonify({"error": "File not found"}), 404


FAILED = "failed"
PASSED = "passed"


def test_one(
    test_id: str,
    database_system: str,
    config: ConfigParser,
    suite: TestSuite,
    engine_pair: EnginePair,
) -> list[list[str]]:
    database_label = get_database_config(database_system).label
    try:
        metadata = suite.get_test_metadata(test_id)
        if metadata is None:
            return [
                ["tester", "platform", "rdbms", "testid", "result"],
                [
                    config["tester"]["tester_name"],
                    config["engine"]["engine_name"],
                    database_label,
                    test_id,
                    "error",
                ],
            ]

        return _run_test(test_id, metadata, database_system, config, suite, engine_pair)
    except Exception:
        return [
            ["tester", "platform", "rdbms", "testid", "result"],
            [
                config["tester"]["tester_name"],
                config["engine"]["engine_name"],
                database_label,
                test_id,
                "error",
            ],
        ]


def _run_test(
    t_identifier: str,
    metadata: dict[str, str | bool],
    database_system: str,
    config: ConfigParser,
    suite: TestSuite,
    engine_pair: EnginePair,
) -> list[list[str]]:
    results: list[list[str]] = [["tester", "platform", "rdbms", "testid", "result"]]
    expected_output = metadata["expected_output"]
    output_format = _engine_output_format(engine_pair)

    output_file = _artifact_path(
        suite, t_identifier, database_system, engine_pair, output_format
    )
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.unlink(missing_ok=True)

    mapping_path = suite.get_mapping_path(t_identifier)
    database = validate_database_suite(database_system, suite.suite_id)
    source_db_url, _ = database.connection_urls(suite.suite_id)
    jdbc_dsn, username, password = rmlmapper.sqlalchemy_to_jdbc(
        source_db_url, database.jdbc_properties
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        if suite.suite_id == "rml":
            prepared = rmlmapper.prepare_rml_mapping(
                mapping_path,
                jdbc_dsn,
                username,
                password,
                tmp_dir,
            )
            exit_code = rmlmapper.run(prepared, str(output_file))
        else:
            exit_code = rmlmapper.run(
                mapping_path,
                str(output_file),
                dsn=jdbc_dsn,
                username=username,
                password=password,
            )

    forward_failed = forward_conformance_failed(
        bool(expected_output),
        suite.get_expected_output_path(t_identifier),
        output_file,
        output_format,
        exit_code,
    )
    result = FAILED if forward_failed else PASSED

    results.append(
        [
            config["tester"]["tester_name"],
            config["engine"]["engine_name"],
            database.label,
            t_identifier,
            result,
        ]
    )
    return results


def _case_result(
    test_id: str,
    database_system: str,
    suite: TestSuite,
    engine_pair: EnginePair,
    mode: str,
    outcome: CaseOutcome,
    raw_results: list[list[str]],
    purpose: str | bool,
    error_test: bool,
) -> dict[str, object]:
    expected = expected_outcome(suite.suite_id, test_id, database_system)
    return {
        "status": "success",
        "test_id": test_id,
        "result_key": _result_key(test_id, mode),
        "mode": mode,
        "database_system": get_database_config(database_system).label,
        "engine_pair": engine_pair,
        "results": process_results(
            raw_results,
            test_id,
            database_system,
            purpose,
            outcome,
            expected,
            suite,
            engine_pair,
            mode,
            error_test,
        ),
    }


def _result_key(test_id: str, mode: str) -> str:
    if mode == DEFAULT_MODE:
        return test_id
    return f"{test_id}|{mode}"


def _run_rmlmapper_kgi_test(
    test_id: str,
    database_system: str,
    suite: TestSuite,
    engine_pair: EnginePair,
) -> list[dict[str, object]]:
    database = validate_database_suite(database_system, suite.suite_id)
    source_db_url, dest_db_url = database.connection_urls(suite.suite_id)

    try:
        db_connection.drop_all_tables(source_db_url)
        db_connection.drop_all_tables(dest_db_url)
        db_connection.load_sql_script(
            source_db_url, suite.get_sql_script_path(test_id, database_system)
        )

        metadata = suite.get_test_metadata(test_id)
        assert metadata is not None

        raw_results = test_one(test_id, database_system, config, suite, engine_pair)
        rdf_output_path = _artifact_path(
            suite,
            test_id,
            database_system,
            engine_pair,
            _engine_output_format(engine_pair),
        )
        outcome = evaluate_kgi_case(
            suite.get_mapping_path(test_id),
            rdf_output_path,
            bool(metadata["expected_output"]),
            raw_results[1][4] == FAILED,
            source_db_url,
            dest_db_url,
        )
        return [
            _case_result(
                test_id,
                database_system,
                suite,
                engine_pair,
                DEFAULT_MODE,
                _with_database_content(outcome, source_db_url, dest_db_url),
                raw_results,
                metadata["purpose"],
                error_test=not metadata["expected_output"],
            )
        ]
    except Exception as error:
        return [
            {
                "status": "error",
                "test_id": test_id,
                "result_key": _result_key(test_id, DEFAULT_MODE),
                "mode": DEFAULT_MODE,
                "engine_pair": engine_pair,
                "message": str(error),
                "traceback": traceback.format_exc(),
            }
        ]


def _with_database_content(
    outcome: CaseOutcome, source_db_url: str, dest_db_url: str
) -> CaseOutcome:
    if outcome.source_content is not None:
        return outcome
    return replace(
        outcome,
        source_content=db_connection.get_database_content(source_db_url),
        dest_content=db_connection.get_database_content(dest_db_url),
    )


def _run_souffle_souffle_test(
    test_id: str,
    database_system: str,
    suite: TestSuite,
    engine_pair: EnginePair,
) -> list[dict[str, object]]:
    database = validate_database_suite(database_system, suite.suite_id)
    metadata = suite.get_test_metadata(test_id)
    assert metadata is not None
    expects_output = bool(metadata["expected_output"])
    mapping_file = Path(suite.get_mapping_path(test_id))
    rdf_output_path = _artifact_path(
        suite,
        test_id,
        database_system,
        engine_pair,
        _engine_output_format(engine_pair),
    )
    rdf_output_path.parent.mkdir(parents=True, exist_ok=True)
    source_db_url, dest_db_url = database.connection_urls(suite.suite_id)
    souffle_database = cast(SouffleDatabase, database_system)

    results: list[dict[str, object]] = []
    for mode, with_provenance in SOUFFLE_MODES.items():
        rdf_output_path.unlink(missing_ok=True)
        execution_log_path = rdf_output_path.with_name(f"execution-{mode}.log")
        execution_log_path.unlink(missing_ok=True)
        raw_results = [
            ["tester", "platform", "rdbms", "testid", "result"],
            [
                config["tester"]["tester_name"],
                "Soufflé",
                database.label,
                test_id,
                PASSED,
            ],
        ]
        db_connection.drop_all_tables(source_db_url)
        db_connection.drop_all_tables(dest_db_url)
        db_connection.load_sql_script(
            source_db_url, suite.get_sql_script_path(test_id, database_system)
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            try:
                outcome = evaluate_souffle_case(
                    _souffle_adapter(execution_log_path),
                    mapping_file,
                    Path(suite.get_expected_output_path(test_id)),
                    rdf_output_path,
                    Path(temporary_directory),
                    expects_output,
                    souffle_database,
                    source_db_url,
                    dest_db_url,
                    with_provenance,
                )
            except SouffleConformanceError as error:
                results.append(
                    {
                        "status": "error",
                        "test_id": test_id,
                        "result_key": _result_key(test_id, mode),
                        "mode": mode,
                        "engine_pair": engine_pair,
                        "stage": error.stage,
                        "message": str(error),
                    }
                )
                continue
        if outcome.outcome is InversionOutcome.MISMATCH:
            raw_results[1][4] = FAILED
        results.append(
            _case_result(
                test_id,
                database_system,
                suite,
                engine_pair,
                mode,
                _with_database_content(outcome, source_db_url, dest_db_url),
                raw_results,
                metadata["purpose"],
                error_test=not expects_output,
            )
        )
    return results


def _postgresql_only_case_result(
    test_id: str,
    database_system: str,
    suite: TestSuite,
    engine_pair: EnginePair,
) -> list[dict[str, object]]:
    database = validate_database_suite(database_system, suite.suite_id)
    metadata = suite.get_test_metadata(test_id)
    assert metadata is not None
    output_path = _artifact_path(
        suite,
        test_id,
        database_system,
        engine_pair,
        _engine_output_format(engine_pair),
    )
    output_path.unlink(missing_ok=True)
    forward_engine = ENGINE_PAIRS[engine_pair]["forward"]
    assert isinstance(forward_engine, str)
    raw_results = [
        ["tester", "platform", "rdbms", "testid", "result"],
        [
            config["tester"]["tester_name"],
            forward_engine,
            database.label,
            test_id,
            "untested",
        ],
    ]
    outcome = CaseOutcome(
        InversionOutcome.NOT_TESTED,
        message="This R2RML test case runs only with PostgreSQL",
    )
    return [
        {
            "status": "success",
            "test_id": test_id,
            "result_key": _result_key(test_id, mode),
            "mode": mode,
            "database_system": database.label,
            "engine_pair": engine_pair,
            "results": process_results(
                raw_results,
                test_id,
                database_system,
                metadata["purpose"],
                outcome,
                outcome,
                suite,
                engine_pair,
                mode,
                error_test=not metadata["expected_output"],
            ),
        }
        for mode in _modes(engine_pair)
    ]


def _modes(engine_pair: EnginePair) -> tuple[str, ...]:
    if engine_pair == "souffle_souffle":
        return tuple(SOUFFLE_MODES)
    return (DEFAULT_MODE,)


def run_single_test(
    test_id: str,
    database_system: str,
    suite: TestSuite,
    engine_pair: EnginePair,
) -> list[dict[str, object]]:
    if suite.suite_id == "r2rml" and not is_r2rml_case_available(
        test_id, database_system
    ):
        return _postgresql_only_case_result(
            test_id, database_system, suite, engine_pair
        )
    if engine_pair == "souffle_souffle":
        return _run_souffle_souffle_test(test_id, database_system, suite, engine_pair)
    return _run_rmlmapper_kgi_test(test_id, database_system, suite, engine_pair)


def process_results(
    raw_results: list[list[str]],
    test_id: str,
    database_system: str,
    purpose: str | bool,
    outcome: CaseOutcome,
    expected: CaseOutcome | None,
    suite: TestSuite,
    engine_pair: EnginePair,
    mode: str,
    error_test: bool,
) -> dict[str, list[str] | list[dict[str, object]]]:
    mapping_content = Path(suite.get_mapping_path(test_id)).read_text(encoding="utf-8")
    expected_content, actual_content = get_file_contents(
        test_id, database_system, suite, engine_pair
    )
    matches_expectation = outcome.outcome is InversionOutcome.NOT_TESTED or (
        expected is not None and outcome == expected
    )
    data_list = [
        {
            "testid": row[3],
            "database_system": row[2],
            "engine_pair": engine_pair,
            "mode": mode,
            "result_key": _result_key(test_id, mode),
            "purpose": purpose,
            "result": row[4],
            "expected_result": expected_content,
            "actual_result": actual_content,
            "mapping": mapping_content,
            "inversion_success": str(outcome.outcome),
            "losses": sorted(str(loss) for loss in outcome.losses),
            "expected_inversion": str(expected.outcome) if expected else None,
            "expected_losses": sorted(str(loss) for loss in expected.losses)
            if expected
            else [],
            "matches_expectation": matches_expectation,
            "comparison_message": outcome.message,
            "original_tables": outcome.source_content,
            "inverted_tables": outcome.dest_content,
            "error_test": error_test,
        }
        for row in raw_results[1:]
    ]
    return {
        "headers": [
            "Test ID",
            "Purpose",
            "Result",
            "Expected Result",
            "Actual Result",
            "Mapping",
            "Inversion Success",
            "Tables Comparison",
        ],
        "data": data_list,
    }


def get_file_contents(
    test_id: str,
    database_system: str,
    suite: TestSuite,
    engine_pair: EnginePair,
) -> tuple[str, str]:
    output_format = _engine_output_format(engine_pair)
    expected_file = suite.get_expected_output_path(test_id)
    actual_file = _artifact_path(
        suite, test_id, database_system, engine_pair, output_format
    )
    return read_file_content(expected_file), read_file_content(str(actual_file))


def read_file_content(file_path: str) -> str:
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return file.read()
    except FileNotFoundError:
        # Test cases with invalid mappings don't produce RDF output files,
        # so missing expected/actual files is part of the normal flow.
        return ""


def generate_test_report(
    results: list[dict[str, object]],
    database_system: str,
    suite: TestSuite,
    engine_pair: EnginePair,
) -> None:
    database_label = get_database_config(database_system).label
    engine_pair_label = ENGINE_PAIRS[engine_pair]["label"]
    assert isinstance(engine_pair_label, str)
    results_dir = os.path.join(PROJECT_ROOT, "test_results")
    os.makedirs(results_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = (
        f"test_report_{engine_pair}_{suite.suite_id}_{database_system}_{timestamp}"
    )

    total_tests = len(results)
    outcome_counts = {outcome: 0 for outcome in InversionOutcome}
    loss_counts = {loss: 0 for loss in PartialLoss}
    regressions = 0
    test_details: list[dict[str, object]] = []

    for result in results:
        if result["status"] == "error":
            outcome_counts[InversionOutcome.ERROR] += 1
            regressions += 1
            test_details.append(
                {
                    "test_id": result["test_id"],
                    "mode": result["mode"],
                    "status": str(InversionOutcome.ERROR),
                    "message": result["message"],
                    "purpose": None,
                    "comparison_message": None,
                    "losses": [],
                    "expected_inversion": None,
                    "expected_losses": [],
                    "matches_expectation": False,
                }
            )
            continue

        results_data: dict[str, object] = result["results"]  # type: ignore[assignment]
        test_data: dict[str, object] = results_data["data"][0]  # type: ignore[index]
        outcome = InversionOutcome(test_data["inversion_success"])
        outcome_counts[outcome] += 1
        for loss in cast(list[str], test_data["losses"]):
            loss_counts[PartialLoss(loss)] += 1
        if not test_data["matches_expectation"]:
            regressions += 1

        test_details.append(
            {
                "test_id": result["test_id"],
                "mode": result["mode"],
                "status": str(outcome),
                "purpose": test_data["purpose"],
                "result": test_data["result"],
                "inversion_success": test_data["inversion_success"],
                "comparison_message": test_data["comparison_message"],
                "losses": test_data["losses"],
                "expected_inversion": test_data["expected_inversion"],
                "expected_losses": test_data["expected_losses"],
                "matches_expectation": test_data["matches_expectation"],
            }
        )

    def pct(n: int) -> float:
        return round((n / total_tests * 100), 2) if total_tests > 0 else 0

    report = {
        "metadata": {
            "timestamp": timestamp,
            "test_suite": suite.suite_id,
            "suite_name": suite.name,
            "database_system": database_label,
            "engine_pair": engine_pair,
            "engine_pair_label": engine_pair_label,
            "total_tests": total_tests,
            "execution_date": datetime.now().isoformat(),
        },
        "summary": {
            "total": total_tests,
            "regressions": regressions,
            "outcomes": {str(k): v for k, v in outcome_counts.items()},
            "losses": {str(k): v for k, v in loss_counts.items()},
            "percentages": {str(k): pct(v) for k, v in outcome_counts.items()},
        },
        "test_details": test_details,
    }

    with open(
        os.path.join(results_dir, f"{base_name}.json"), "w", encoding="utf-8"
    ) as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    with open(os.path.join(results_dir, f"{base_name}.md"), "w", encoding="utf-8") as f:
        f.write(f"# {suite.name} inversion test report\n\n")
        f.write(f"**Test suite:** {suite.name}\n")
        f.write(f"**Database system:** {database_label}\n")
        f.write(f"**Engine pair:** {engine_pair_label}\n")
        f.write(f"**Execution date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"**Total runs:** {total_tests}\n")
        f.write(f"**Differences from the expected outcomes:** {regressions}\n\n")

        f.write("## Summary\n\n")
        f.write("| Outcome | Count | Percentage |\n")
        f.write("|--------|-------|------------|\n")
        for outcome in InversionOutcome:
            count = outcome_counts[outcome]
            f.write(f"| {OUTCOME_LABELS[outcome]} | {count} | {pct(count)}% |\n")

        if outcome_counts[InversionOutcome.PARTIALLY_INVERTED]:
            f.write("\n### Partially inverted subcategories\n\n")
            f.write("| Subcategory | Count |\n|---|---|\n")
            for loss in PartialLoss:
                if loss_counts[loss]:
                    f.write(f"| {LOSS_LABELS[loss]} | {loss_counts[loss]} |\n")

        f.write("\n## Test details\n\n")

        for outcome in InversionOutcome:
            outcome_tests = [t for t in test_details if t["status"] == str(outcome)]
            if not outcome_tests:
                continue
            f.write(f"\n### {OUTCOME_LABELS[outcome]} ({len(outcome_tests)})\n\n")
            for test in outcome_tests:
                f.write(f"- **{test['test_id']}**")
                if test["mode"] != DEFAULT_MODE:
                    f.write(f" [{test['mode']}]")
                purpose_text = test["purpose"]
                if isinstance(purpose_text, str) and purpose_text:
                    f.write(
                        f": {purpose_text[:100]}..."
                        if len(purpose_text) > 100
                        else f": {purpose_text}"
                    )
                losses = cast(list[str], test["losses"])
                if losses:
                    f.write(f"\n  - Subcategory: {', '.join(losses)}")
                if not test["matches_expectation"]:
                    expected_losses = cast(list[str], test["expected_losses"])
                    expected_label = test["expected_inversion"] or "no expectation"
                    if expected_losses:
                        expected_label = (
                            f"{expected_label} ({', '.join(expected_losses)})"
                        )
                    f.write(
                        f"\n  - Differs from the expected outcome: {expected_label}"
                    )
                comp_msg = test["comparison_message"]
                if isinstance(comp_msg, str) and comp_msg:
                    f.write(
                        f"\n  - {comp_msg[:200]}..."
                        if len(comp_msg) > 200
                        else f"\n  - {comp_msg}"
                    )
                f.write("\n")


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

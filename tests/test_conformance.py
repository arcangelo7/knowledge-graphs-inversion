# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import tempfile
from pathlib import Path

import pytest

from conformance.config import is_r2rml_case_available
from conformance.expectations import expected_outcome
from conformance.outcome import (
    describe_difference,
    evaluate_kgi_case,
    forward_conformance_failed,
)
from conformance.suites import R2RMLTestSuite, RMLTestSuite, TestSuite
from kgi import MappingError, analyze_mapping

from .conftest import (
    Database,
    R2RML_TEST_IDS,
    RML_TEST_IDS,
    drop_all_tables,
    load_sql_script,
    run_forward_mapping,
)


def _run_conformance_test(
    test_id: str,
    suite: TestSuite,
    database: Database,
    source_db: str,
    dest_db: str,
    tmp_dir: str,
) -> None:
    drop_all_tables(source_db)
    drop_all_tables(dest_db)
    load_sql_script(source_db, suite.get_sql_script_path(test_id, database))

    mapping_path = suite.get_mapping_path(test_id)
    output_path = Path(tmp_dir, "output.nq")
    exit_code = run_forward_mapping(
        mapping_path, str(output_path), source_db, suite.suite_id, tmp_dir
    )

    metadata = suite.get_test_metadata(test_id)
    assert metadata is not None
    expects_output = bool(metadata["expected_output"])

    observed = evaluate_kgi_case(
        mapping_path,
        output_path,
        expects_output,
        forward_conformance_failed(
            expects_output,
            suite.get_expected_output_path(test_id),
            output_path,
            "nquads",
            exit_code,
        ),
        source_db,
        dest_db,
    )
    expected = expected_outcome(suite.suite_id, test_id)
    assert observed == expected, (
        f"{suite.suite_id}/{database}/{test_id}: "
        f"{describe_difference(expected, observed)}"
    )


def test_kgi_rejects_a_template_naming_a_delimited_column_as_regular(
    r2rml_suite: R2RMLTestSuite,
    database: Database,
    database_urls: tuple[str, str],
) -> None:
    if database != "postgresql":
        pytest.skip("MySQL compares column names without distinguishing case")

    source_db, _ = database_urls
    drop_all_tables(source_db)
    load_sql_script(
        source_db, r2rml_suite.get_sql_script_path("R2RMLTC0002f", database)
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        with pytest.raises(MappingError) as rejection:
            analyze_mapping(
                r2rml_suite.get_mapping_path("R2RMLTC0002f"),
                Path(tmp_dir, "output.nq"),
                source_db_url=source_db,
            )

    assert str(rejection.value) == (
        "Table 'Student' of the source database has no column named id, name"
    )


@pytest.mark.parametrize("test_id", R2RML_TEST_IDS)
def test_r2rml_conformance(
    test_id: str,
    r2rml_suite: R2RMLTestSuite,
    database: Database,
    database_urls: tuple[str, str],
) -> None:
    if not is_r2rml_case_available(test_id, database):
        pytest.skip("R2RML test case runs only with PostgreSQL")

    source_db, dest_db = database_urls
    with tempfile.TemporaryDirectory() as tmp_dir:
        _run_conformance_test(
            test_id, r2rml_suite, database, source_db, dest_db, tmp_dir
        )


@pytest.mark.parametrize("test_id", RML_TEST_IDS)
def test_rml_conformance(
    test_id: str,
    rml_suite: RMLTestSuite,
    database: Database,
    database_urls: tuple[str, str],
) -> None:
    if database == "mysql":
        pytest.skip("RML Core RDB test cases do not yet provide MySQL variants")
    source_db, dest_db = database_urls
    with tempfile.TemporaryDirectory() as tmp_dir:
        _run_conformance_test(test_id, rml_suite, database, source_db, dest_db, tmp_dir)

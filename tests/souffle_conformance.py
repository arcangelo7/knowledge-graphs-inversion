# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from pathlib import Path

import pytest

from conformance_config import is_r2rml_case_available
from kgi.comparison import compare_databases
from kgi.core import _check_for_sql_queries, _parse_mapping_store
from souffle_conformance import (
    SouffleConformanceAdapter,
    SouffleConformanceError,
    rdf_datasets_isomorphic,
)
from test_suites import R2RMLTestSuite

from .conftest import (
    PROJECT_ROOT,
    Database,
    R2RML_TEST_IDS,
    drop_all_tables,
    get_db_content,
    load_sql_script,
)

REVERSE_SCRIPT = Path(
    PROJECT_ROOT,
    "KROWN_Extended",
    "execution-framework",
    "dockers",
    "Souffle",
    "reverseR2RML.py",
)


@pytest.fixture(scope="session")
def souffle_adapter(request: pytest.FixtureRequest) -> SouffleConformanceAdapter:
    translator_jar = Path(str(request.config.getoption("souffle_jar")))
    functor_library = Path(str(request.config.getoption("souffle_library")))
    for label, path in (
        ("translator jar", translator_jar),
        ("functor library", functor_library),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"Soufflé {label} not found: {path}")
    return SouffleConformanceAdapter(
        translator_jar,
        REVERSE_SCRIPT,
        functor_library,
    )


@pytest.mark.parametrize("test_id", R2RML_TEST_IDS)
def test_souffle_r2rml_conformance(
    test_id: str,
    r2rml_suite: R2RMLTestSuite,
    database: Database,
    database_urls: tuple[str, str],
    souffle_adapter: SouffleConformanceAdapter,
    tmp_path: Path,
) -> None:
    if not is_r2rml_case_available(test_id, database):
        pytest.skip("R2RML test case runs only with PostgreSQL")

    source_db, destination_db = database_urls
    drop_all_tables(source_db)
    drop_all_tables(destination_db)
    load_sql_script(source_db, r2rml_suite.get_sql_script_path(test_id, database))

    metadata = r2rml_suite.get_test_metadata(test_id)
    assert metadata is not None
    expects_output = bool(metadata["expected_output"])
    rdf_path = tmp_path / "output.nq"

    try:
        souffle_adapter.run_forward(
            Path(r2rml_suite.get_mapping_path(test_id)),
            rdf_path,
            tmp_path,
            source_db,
            database,
        )
    except SouffleConformanceError as error:
        if not expects_output and error.stage in {
            "forward generation",
            "forward execution",
        }:
            return
        raise

    if not expects_output:
        produced_output = rdf_path.is_file() and rdf_path.stat().st_size > 0
        assert not produced_output, (
            "forward execution failed: the manifest expects no RDF, but Soufflé "
            "produced a non-empty dataset"
        )
        return

    expected_path = Path(r2rml_suite.get_expected_output_path(test_id))
    assert rdf_datasets_isomorphic(expected_path, rdf_path), (
        "forward execution failed: the produced RDF dataset differs from the "
        "expected dataset"
    )

    mapping_path = r2rml_suite.get_mapping_path(test_id)
    if _check_for_sql_queries(_parse_mapping_store(mapping_path)):
        return

    souffle_adapter.run_backward(tmp_path, source_db, destination_db)
    source_content = get_db_content(source_db)
    destination_content = get_db_content(destination_db)
    assert set(source_content) == set(destination_content), (
        "comparison failed: source and destination table names differ: "
        f"{sorted(source_content)} != {sorted(destination_content)}"
    )
    databases_equal, message, _ = compare_databases(source_content, destination_content)
    assert databases_equal, f"comparison failed: {message}"

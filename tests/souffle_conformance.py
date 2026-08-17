# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from pathlib import Path

import pytest

from conformance.config import is_r2rml_case_available
from conformance.expectations import expected_outcome
from conformance.outcome import describe_difference, evaluate_souffle_case
from conformance.souffle import SouffleConformanceAdapter
from conformance.suites import R2RMLTestSuite

from .conftest import (
    PROJECT_ROOT,
    Database,
    R2RML_TEST_IDS,
    drop_all_tables,
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


@pytest.mark.parametrize("with_provenance", (False, True), ids=("rdf", "provenance"))
@pytest.mark.parametrize("test_id", R2RML_TEST_IDS)
def test_souffle_r2rml_conformance(
    test_id: str,
    with_provenance: bool,
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

    observed = evaluate_souffle_case(
        souffle_adapter,
        Path(r2rml_suite.get_mapping_path(test_id)),
        Path(r2rml_suite.get_expected_output_path(test_id)),
        tmp_path / "output.nq",
        tmp_path,
        bool(metadata["expected_output"]),
        database,
        source_db,
        destination_db,
        with_provenance,
    )
    mode = "provenance" if with_provenance else "rdf"
    expected = expected_outcome("r2rml", test_id, database)
    assert observed == expected, (
        f"r2rml/{database}/{test_id}/{mode}: {describe_difference(expected, observed)}"
    )

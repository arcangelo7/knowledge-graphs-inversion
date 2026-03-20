import os
import tempfile

import pytest

from kgi.comparison import compare_databases
from kgi.core import inversion

from .conftest import (
    DEST_R2RML_DB,
    EXPECTED_STATUSES,
    R2RML_TEST_IDS,
    SOURCE_R2RML_DB,
    drop_all_tables,
    get_db_content,
    load_sql_script,
    run_morph_kgc,
    write_morph_config,
)


def _run_conformance_test(
    test_id: str,
    suite: object,
    source_db: str,
    dest_db: str,
    tmp_dir: str,
) -> None:
    drop_all_tables(source_db)
    drop_all_tables(dest_db)

    sql_path = suite.get_sql_script_path(test_id, "postgresql")  # type: ignore[union-attr]
    load_sql_script(source_db, sql_path)

    mapping_path = suite.get_mapping_path(test_id)  # type: ignore[union-attr]
    output_path = os.path.join(tmp_dir, "output.nq")
    config_path = os.path.join(tmp_dir, "morph_kgc_config.ini")

    write_morph_config(mapping_path, output_path, source_db, config_path)
    run_morph_kgc(config_path)

    result = inversion(config_path, test_id, dest_db)

    if isinstance(result, dict) and "__status__" in result:
        status = result["__status__"]
        assert status in EXPECTED_STATUSES, f"Unexpected inversion status: {status} - {result['__reason__']}"
        return

    source_content = get_db_content(source_db)
    dest_content = get_db_content(dest_db)

    with open(mapping_path, "r", encoding="utf-8") as f:
        mapping_content = f.read()

    databases_equal, message, comparison_status = compare_databases(
        source_content, dest_content, mapping_content,
    )

    if comparison_status == "non_invertible":
        return

    assert databases_equal, message


@pytest.mark.parametrize("test_id", R2RML_TEST_IDS)
def test_r2rml_conformance(test_id: str, r2rml_suite: object) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        _run_conformance_test(test_id, r2rml_suite, SOURCE_R2RML_DB, DEST_R2RML_DB, tmp_dir)

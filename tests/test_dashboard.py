# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from flask.testing import FlaskClient
from werkzeug.test import TestResponse

from app import app
from conformance_config import RML_MYSQL_UNAVAILABLE


def _assert_mysql_rml_rejected(response: TestResponse) -> None:
    assert response.status_code == 400
    assert response.get_json() == {"error": RML_MYSQL_UNAVAILABLE}


def test_run_test_rejects_rml_with_mysql() -> None:
    client: FlaskClient = app.test_client()

    response = client.post(
        "/run_test",
        data={
            "test_id": "RMLTC0000-RDB",
            "database_system": "mysql",
            "suite_id": "rml",
        },
    )

    _assert_mysql_rml_rejected(response)


def test_run_all_tests_rejects_rml_with_mysql() -> None:
    client: FlaskClient = app.test_client()

    response = client.get(
        "/run_all_tests",
        query_string={"database_system": "mysql", "suite_id": "r2rml,rml"},
    )

    _assert_mysql_rml_rejected(response)


def test_get_file_content_rejects_rml_with_mysql() -> None:
    client: FlaskClient = app.test_client()

    response = client.get(
        "/get_file_content",
        query_string={
            "test_id": "RMLTC0000-RDB",
            "type": "expected",
            "database_system": "mysql",
            "suite_id": "rml",
        },
    )

    _assert_mysql_rml_rejected(response)

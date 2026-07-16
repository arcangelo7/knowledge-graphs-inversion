# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import pandas as pd
from sqlalchemy import create_engine, inspect

from kgi.templates import RDBTemplate


def test_rdb_template_inserts_every_chunk(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'destination.sqlite'}"
    template = RDBTemplate(database_url)
    chunks = [
        pd.DataFrame(
            [
                {"identifier": 1, "name": "Ada"},
                {"identifier": 2, "name": "Grace"},
            ]
        ),
        pd.DataFrame([{"identifier": 3, "name": "Edsger"}]),
    ]

    template.fill_data(chunks, "people")

    engine = create_engine(database_url)
    try:
        result = pd.read_sql_table("people", engine)
    finally:
        engine.dispose()

    assert result.to_dict(orient="records") == [
        {"identifier": 1, "name": "Ada"},
        {"identifier": 2, "name": "Grace"},
        {"identifier": 3, "name": "Edsger"},
    ]


def test_rdb_template_creates_table_for_empty_result(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'destination.sqlite'}"
    template = RDBTemplate(database_url)
    empty_chunk = pd.DataFrame(
        {
            "identifier": pd.Series(dtype="int64"),
            "name": pd.Series(dtype="object"),
        }
    )

    template.fill_data([empty_chunk], "people")

    engine = create_engine(database_url)
    try:
        columns = inspect(engine).get_columns("people")
        result = pd.read_sql_table("people", engine)
    finally:
        engine.dispose()

    assert [column["name"] for column in columns] == ["identifier", "name"]
    assert result.to_dict(orient="records") == []

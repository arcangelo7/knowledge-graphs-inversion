# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from datetime import date

import pandas as pd
from sqlalchemy import (
    DATE,
    INTEGER,
    VARCHAR,
    LargeBinary,
    MetaData,
    Table,
    create_engine,
    inspect,
    select,
)

from kgi.schema import ColumnInfo, TableSchema
from kgi.templates import RDBTemplate

PEOPLE_SCHEMA = TableSchema(
    table_name="people",
    columns=[
        ColumnInfo("identifier", INTEGER(), int, ordinal_position=1),
        ColumnInfo("name", VARCHAR(50), str, ordinal_position=2),
    ],
    primary_keys=["identifier"],
)


def _read_back(
    database_url: str, table_name: str
) -> tuple[list[str], list[dict[str, object]]]:
    """Read the destination the way the conformance comparison does."""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            table = Table(table_name, MetaData(), autoload_with=connection)
            frame = pd.read_sql(select(table), connection)
        return (
            [str(column.type) for column in table.columns],
            frame.to_dict(orient="records"),
        )
    finally:
        engine.dispose()


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

    template.fill_data(chunks, "people", PEOPLE_SCHEMA)

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

    template.fill_data([empty_chunk], "people", PEOPLE_SCHEMA)

    engine = create_engine(database_url)
    try:
        columns = inspect(engine).get_columns("people")
        result = pd.read_sql_table("people", engine)
    finally:
        engine.dispose()

    assert [column["name"] for column in columns] == ["identifier", "name"]
    assert result.to_dict(orient="records") == []


def test_rdb_template_keeps_the_source_column_types(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'destination.sqlite'}"
    template = RDBTemplate(database_url)
    schema = TableSchema(
        table_name="patient",
        columns=[
            ColumnInfo("birth_date", DATE(), date, ordinal_position=1),
            ColumnInfo("photo", LargeBinary(), bytes, ordinal_position=2),
        ],
        primary_keys=[],
    )
    chunk = pd.DataFrame([{"birth_date": date(1981, 10, 10), "photo": b"\x89PNG"}])

    template.fill_data([chunk], "patient", schema)

    columns, rows = _read_back(database_url, "patient")

    assert columns == ["DATE", "BLOB"]
    assert rows == [{"birth_date": date(1981, 10, 10), "photo": b"\x89PNG"}]

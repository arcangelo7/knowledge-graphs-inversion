# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

"""Template implementations for different data formats."""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd
import sqlalchemy
from sqlalchemy import Column, MetaData, Table
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.sql.sqltypes import Boolean, Date, DateTime, Integer, Numeric, String


class RDBTemplate:
    """Template for relational database format."""

    def __init__(self, db_url: str):
        self.db_url = db_url

    def create_engine(self) -> Engine:
        return sqlalchemy.create_engine(self.db_url)

    def fill_data(self, data_chunks: Iterable[pd.DataFrame], table_name: str) -> None:
        chunks = iter(data_chunks)
        first_chunk = next(chunks)
        engine = self.create_engine()
        try:
            with engine.begin() as connection:
                table = self._get_sqla_table(first_chunk, table_name)
                self._prepare_table(connection, table, first_chunk.empty)
                self._insert_chunk(connection, table, first_chunk)
                for chunk in chunks:
                    self._insert_chunk(connection, table, chunk)
        finally:
            engine.dispose()

    @staticmethod
    def _prepare_table(
        connection: Connection, table: Table, empty_result: bool
    ) -> None:
        inspector = sqlalchemy.inspect(connection)
        if not inspector.has_table(table.name):
            table.create(connection)
            return
        if empty_result:
            return

        existing_columns = inspector.get_columns(table.name)
        existing_column_names = {str(column["name"]) for column in existing_columns}
        new_column_names = {column.name for column in table.columns}

        for column in table.columns:
            if column.name not in existing_column_names:
                connection.execute(
                    sqlalchemy.text(
                        f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column.type}'
                    )
                )

        for column_name in existing_column_names - new_column_names:
            connection.execute(
                sqlalchemy.text(
                    f'ALTER TABLE "{table.name}" DROP COLUMN "{column_name}"'
                )
            )

        for column in table.columns:
            existing_column = next(
                (
                    candidate
                    for candidate in existing_columns
                    if candidate["name"] == column.name
                ),
                None,
            )
            if existing_column and not isinstance(
                existing_column["type"], column.type.__class__
            ):
                connection.execute(
                    sqlalchemy.text(
                        f'ALTER TABLE "{table.name}" ALTER COLUMN "{column.name}" TYPE {column.type}'
                    )
                )

    @staticmethod
    def _insert_chunk(connection: Connection, table: Table, data: pd.DataFrame) -> None:
        if data.empty:
            return
        converted = data.copy()
        for column in table.columns:
            if isinstance(column.type, String):
                converted[column.name] = converted[column.name].map(
                    lambda value: str(value) if value is not None else None
                )
        connection.execute(table.insert(), converted.to_dict(orient="records"))

    @staticmethod
    def _get_sqla_table(df: pd.DataFrame, table_name: str) -> Table:
        metadata = MetaData()
        columns = []

        for column_name, dtype in df.dtypes.items():
            column_values = df[column_name].dropna()
            has_strings = any(isinstance(value, str) for value in column_values)

            if has_strings:
                column_type = String()
            elif "int" in str(dtype):
                column_type = Integer()
            elif "float" in str(dtype):
                column_type = Numeric()
            elif "bool" in str(dtype):
                column_type = Boolean()
            elif "datetime" in str(dtype):
                column_type = DateTime()
            elif "date" in str(dtype):
                column_type = Date()
            else:
                column_type = String()

            columns.append(
                Column(
                    str(column_name),
                    column_type,  # type: ignore[reportArgumentType]
                )
            )

        return Table(table_name, metadata, *columns)

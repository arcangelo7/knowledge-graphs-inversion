# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

"""Template implementations for different data formats."""

from __future__ import annotations

import pandas as pd
import sqlalchemy
from sqlalchemy import Column, MetaData, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from sqlalchemy.sql.sqltypes import Boolean, Date, DateTime, Integer, Numeric, String

from .base import Template


class RDBTemplate(Template):
    """Template for relational database format."""

    def __init__(self, db_url):
        self.db_url = db_url

    def create_engine(self):
        """Create SQLAlchemy engine."""
        return sqlalchemy.create_engine(self.db_url)

    def create_template(self) -> str:
        """RDB template structure is determined by database schema."""
        return "RDB template: structure will be determined by the database schema"

    def fill_data(self, data: pd.DataFrame, source_name: str) -> str:
        """Fill template with data and create SQL statements."""
        table_name = source_name
        engine = self.create_engine()
        table = self._get_sqla_table(data, table_name)

        # Convert data types to match schema before creating insert statement
        data = data.copy()
        for col in table.columns:
            if isinstance(col.type, String):
                data[col.name] = data[col.name].map(
                    lambda x: str(x) if x is not None else None
                )

        insert_stmt = postgresql.insert(table).values(data.to_dict(orient="records"))

        if data.empty:
            # Create only table structure if DataFrame is empty
            with engine.begin() as connection:
                inspector = sqlalchemy.inspect(engine)
                if not inspector.has_table(table_name):
                    table.create(connection)
            return str(CreateTable(table).compile(engine))

        if not self._is_sql_query(table_name):
            with engine.begin() as connection:
                inspector = sqlalchemy.inspect(engine)
                if inspector.has_table(table_name):
                    existing_columns = inspector.get_columns(table_name)
                    existing_column_names = set(col["name"] for col in existing_columns)
                    new_column_names = set(col.name for col in table.columns)

                    # Add missing columns
                    for col in table.columns:
                        if col.name not in existing_column_names:
                            connection.execute(
                                sqlalchemy.text(
                                    f'ALTER TABLE "{table_name}" ADD COLUMN "{col.name}" {col.type}'
                                )
                            )

                    # Remove extra columns
                    for col_name in existing_column_names - new_column_names:
                        connection.execute(
                            sqlalchemy.text(
                                f'ALTER TABLE "{table_name}" DROP COLUMN "{col_name}"'
                            )
                        )

                    # Update column types if necessary
                    for col in table.columns:
                        existing_col = next(
                            (c for c in existing_columns if c["name"] == col.name), None
                        )
                        if existing_col and not isinstance(
                            existing_col["type"], col.type.__class__
                        ):
                            connection.execute(
                                sqlalchemy.text(
                                    f'ALTER TABLE "{table_name}" ALTER COLUMN "{col.name}" TYPE {col.type}'
                                )
                            )
                else:
                    # Create table if it doesn't exist
                    table.create(connection)

                # Generate INSERT statements
                connection.execute(insert_stmt)

        # Generate full query for logging purposes
        create_table_query = str(CreateTable(table).compile(engine))
        insert_query = str(
            insert_stmt.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        full_query = f"{create_table_query};{insert_query};"

        engine.dispose()
        return full_query

    def _is_sql_query(self, table_name: str) -> bool:
        """Check if table_name contains SQL keywords."""
        sql_keywords = ["SELECT", "FROM", "WHERE", "JOIN", "GROUP BY", "ORDER BY"]
        return any(keyword in table_name.upper() for keyword in sql_keywords)

    def _get_sqla_table(self, df: pd.DataFrame, table_name: str):
        """Create SQLAlchemy table from DataFrame."""
        metadata = MetaData()
        columns = []

        for column_name, dtype in df.dtypes.items():
            # Check if column contains mixed types by examining actual values
            column_values = df[column_name].dropna()
            has_strings = any(isinstance(val, str) for val in column_values)
            has_numbers = any(isinstance(val, (int, float)) for val in column_values)

            # If column has mixed strings and numbers, or contains strings, use String type
            if has_strings or (has_strings and has_numbers):
                col_type = String()
            elif "int" in str(dtype):
                col_type = Integer()
            elif "float" in str(dtype):
                col_type = Numeric()
            elif "bool" in str(dtype):
                col_type = Boolean()
            elif "datetime" in str(dtype):
                col_type = DateTime()
            elif "date" in str(dtype):
                col_type = Date()
            else:
                col_type = String()

            columns.append(Column(column_name, col_type))  # type: ignore[arg-type]

        return Table(table_name, metadata, *columns)

    @property
    def columns_decoded(self) -> bool:
        return True

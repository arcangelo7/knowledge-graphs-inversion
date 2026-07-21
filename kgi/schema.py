# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

"""Schema retrieval and management for knowledge graph inversion."""

from dataclasses import dataclass
from typing import Optional, cast

import pandas as pd
import sqlalchemy
from sqlalchemy import inspect
from sqlalchemy.dialects.mysql import TINYINT, VARBINARY


@dataclass
class ColumnInfo:
    """Column metadata from database schema."""

    name: str
    data_type: str
    python_type: type[object]
    nullable: bool = True
    ordinal_position: int = 0


@dataclass
class TableSchema:
    """Table schema information."""

    table_name: str
    columns: list[ColumnInfo]
    primary_keys: list[str]

    @property
    def column_names_ordered(self) -> list[str]:
        """Get column names in database order."""
        return [
            col.name for col in sorted(self.columns, key=lambda c: c.ordinal_position)
        ]

    def get_column_info(self, column_name: str) -> Optional[ColumnInfo]:
        """Get information for a specific column."""
        return next((col for col in self.columns if col.name == column_name), None)


class DatabaseSchemaRetriever:
    """Retrieves schema information from original database."""

    def __init__(self, db_url: str):
        """Initialize with database URL."""
        self.db_url = db_url
        self._engine = None

    @property
    def engine(self):
        """Get or create database engine."""
        if self._engine is None:
            self._engine = sqlalchemy.create_engine(self.db_url)
        return self._engine

    def get_table_schema(self, table_name: str) -> TableSchema:
        """Get schema information for a specific table."""
        inspector = inspect(self.engine)
        columns_info = inspector.get_columns(table_name)
        primary_keys = cast(
            list[str],
            inspector.get_pk_constraint(table_name)["constrained_columns"],
        )

        columns = []
        for idx, col_info in enumerate(columns_info):
            column = ColumnInfo(
                name=cast(str, col_info["name"]),
                data_type=str(col_info["type"]),
                python_type=self._sql_to_python_type(col_info["type"]),
                nullable=cast(bool, col_info["nullable"]),
                ordinal_position=idx + 1,
            )
            columns.append(column)

        return TableSchema(
            table_name=table_name,
            columns=columns,
            primary_keys=primary_keys,
        )

    def _sql_to_python_type(self, sql_type: object) -> type[object]:
        """Convert SQLAlchemy type to Python type."""
        if isinstance(sql_type, TINYINT) and sql_type.display_width == 1:
            return bool
        if isinstance(
            sql_type,
            (sqlalchemy.Integer, sqlalchemy.BigInteger, sqlalchemy.SmallInteger),
        ):
            return int
        elif isinstance(
            sql_type, (sqlalchemy.Float, sqlalchemy.Numeric, sqlalchemy.DECIMAL)
        ):
            return float
        elif isinstance(sql_type, sqlalchemy.Boolean):
            return bool
        elif isinstance(sql_type, sqlalchemy.Date):
            return pd.Timestamp
        elif isinstance(sql_type, (sqlalchemy.DateTime, sqlalchemy.TIMESTAMP)):
            return pd.Timestamp
        elif isinstance(sql_type, sqlalchemy.String):
            return str
        elif isinstance(sql_type, (sqlalchemy.LargeBinary, VARBINARY)):
            return str
        raise TypeError(f"Unsupported SQL type: {sql_type}")

    def dispose(self):
        """Dispose of database engine."""
        if self._engine:
            self._engine.dispose()
            self._engine = None


def infer_type_from_value_with_schema(
    value: object,
    column_info: ColumnInfo,
) -> object:
    if value is None or cast(bool, pd.isna(value)):
        return None

    if column_info.python_type is int:
        return int(float(str(value)))
    if column_info.python_type is float:
        return float(str(value))
    if column_info.python_type is bool:
        if isinstance(value, bool):
            return value
        return {
            "true": True,
            "t": True,
            "1": True,
            "yes": True,
            "y": True,
            "false": False,
            "f": False,
            "0": False,
            "no": False,
            "n": False,
        }[str(value).lower()]
    if column_info.python_type == pd.Timestamp:
        return pd.to_datetime(str(value))
    return str(value)


def apply_schema_ordering(df: pd.DataFrame, schema: TableSchema) -> pd.DataFrame:
    """Apply database column ordering to DataFrame."""
    ordered_columns = [col for col in schema.column_names_ordered if col in df.columns]
    return pd.DataFrame(df[ordered_columns])


def apply_schema_types(df: pd.DataFrame, schema: TableSchema) -> pd.DataFrame:
    """Apply database column types to DataFrame."""
    df = df.copy()

    columns = {column.name: column for column in schema.columns}
    for column_name in df.columns:
        column_info = columns[cast(str, column_name)]
        df[column_name] = df[column_name].apply(
            lambda value: infer_type_from_value_with_schema(value, column_info)
        )

    return df

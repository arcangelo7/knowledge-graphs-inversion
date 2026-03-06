"""Schema retrieval and management for knowledge graph inversion."""

import logging
from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd
import sqlalchemy
from sqlalchemy import inspect


@dataclass
class ColumnInfo:
    """Column metadata from database schema."""

    name: str
    data_type: str
    python_type: type
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
        self.logger = logging.getLogger("kgi")
        self._engine = None

    @property
    def engine(self):
        """Get or create database engine."""
        if self._engine is None:
            self._engine = sqlalchemy.create_engine(self.db_url)
        return self._engine

    def get_table_schema(self, table_name: str) -> Optional[TableSchema]:
        """Get schema information for a specific table."""
        try:
            inspector = inspect(self.engine)

            if not inspector.has_table(table_name):
                self.logger.warning(f"Table {table_name} not found in database")
                return None

            columns_info = inspector.get_columns(table_name)
            primary_keys = inspector.get_pk_constraint(table_name)[
                "constrained_columns"
            ]

            columns = []
            for idx, col_info in enumerate(columns_info):
                column = ColumnInfo(
                    name=col_info["name"],
                    data_type=str(col_info["type"]),
                    python_type=self._sql_to_python_type(col_info["type"]),
                    nullable=col_info.get("nullable", True),
                    ordinal_position=idx + 1,
                )
                columns.append(column)

            return TableSchema(
                table_name=table_name, columns=columns, primary_keys=primary_keys or []
            )

        except Exception as e:
            self.logger.error(f"Error retrieving schema for table {table_name}: {e}")
            return None

    def _sql_to_python_type(self, sql_type) -> type:
        """Convert SQLAlchemy type to Python type."""
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
        elif isinstance(sql_type, (sqlalchemy.Date,)):
            return pd.Timestamp
        elif isinstance(sql_type, (sqlalchemy.DateTime, sqlalchemy.TIMESTAMP)):
            return pd.Timestamp
        else:
            return str

    def dispose(self):
        """Dispose of database engine."""
        if self._engine:
            self._engine.dispose()
            self._engine = None


def infer_type_from_value_with_schema(
    value: Any, column_info: Optional[ColumnInfo] = None
) -> Any:
    """
    Enhanced type inference using schema information when available.

    Args:
        value: The value to convert
        column_info: Optional column schema information

    Returns:
        Converted value with appropriate type
    """
    if column_info is None:
        return _infer_type_from_value(value)

    try:
        # Use schema information to guide conversion
        if column_info.python_type is int:
            return int(float(value)) if value is not None else None
        elif column_info.python_type is float:
            return float(value) if value is not None else None
        elif column_info.python_type is bool:
            if isinstance(value, str):
                return value.lower() in ("true", "t", "1", "yes", "y")
            return bool(value) if value is not None else None
        elif column_info.python_type == pd.Timestamp:
            return pd.to_datetime(value) if value is not None else None
        else:
            return str(value) if value is not None else None

    except (ValueError, TypeError) as e:
        logging.getLogger("kgi.schema").warning(
            f"Failed to convert value {value} to {column_info.python_type.__name__}: {e}"
        )
        # Fallback to basic type inference
        return _infer_type_from_value(value)


def _infer_type_from_value(value: Any) -> Any:
    """Basic type inference from value."""
    if value is None or pd.isna(value):
        return None

    str_value = str(value).strip()

    # Try integer
    try:
        if "." not in str_value:
            return int(str_value)
    except (ValueError, TypeError):
        pass

    # Try float
    try:
        return float(str_value)
    except (ValueError, TypeError):
        pass

    # Try boolean
    if str_value.lower() in ("true", "false", "t", "f", "1", "0"):
        return str_value.lower() in ("true", "t", "1")

    # Try datetime
    try:
        return pd.to_datetime(str_value)
    except (ValueError, TypeError):
        pass

    # Default to string
    return str_value


def apply_schema_ordering(df: pd.DataFrame, schema: TableSchema) -> pd.DataFrame:
    """Apply database column ordering to DataFrame."""
    if df.empty:
        return df

    ordered_columns = [col for col in schema.column_names_ordered if col in df.columns]

    # Add any remaining columns that weren't in the schema
    remaining_columns = [col for col in df.columns if col not in ordered_columns]

    final_order = ordered_columns + remaining_columns

    return pd.DataFrame(df[final_order])


def apply_schema_types(df: pd.DataFrame, schema: TableSchema) -> pd.DataFrame:
    """Apply database column types to DataFrame."""
    if df.empty:
        return df

    df = df.copy()

    for col_name in df.columns:
        column_info = schema.get_column_info(col_name)
        if column_info:
            df[col_name] = df[col_name].apply(
                lambda x: infer_type_from_value_with_schema(x, column_info)
            )

    return df

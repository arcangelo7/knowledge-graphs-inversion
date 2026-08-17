# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import pandas as pd
from sqlalchemy import CHAR, MetaData, Table, create_engine, inspect, select, text

from kgi.comparison import DatabaseContent


def hex_encode_binary_columns(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        df[col] = df[col].apply(
            lambda v: (
                bytes(v).hex().upper() if isinstance(v, (bytes, memoryview)) else v
            )
        )
    return df


class DatabaseConnection:
    def load_sql_script(self, database_url: str, sql_script_path: str) -> None:
        engine = create_engine(database_url)
        try:
            with open(sql_script_path, "r", encoding="utf-8") as f:
                statements = f.read().split(";")
            with engine.begin() as conn:
                for statement in statements:
                    if statement.strip():
                        conn.execute(text(statement))
        finally:
            engine.dispose()

    def drop_all_tables(self, database_url: str) -> None:
        engine = create_engine(database_url)
        try:
            with engine.begin():
                metadata = MetaData()
                metadata.reflect(bind=engine)
                metadata.drop_all(engine)
        finally:
            engine.dispose()

    def get_database_content(self, database_url: str) -> DatabaseContent:
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                metadata = MetaData()
                table_names = inspect(connection).get_table_names()
                db_content: DatabaseContent = {}
                for table_name in table_names:
                    table = Table(table_name, metadata, autoload_with=connection)
                    content = pd.read_sql(select(table), connection)
                    for column in table.columns:
                        if isinstance(column.type, CHAR):
                            content[column.name] = content[column.name].apply(
                                lambda value, length=column.type.length: (
                                    value.ljust(length)
                                    if isinstance(value, str) and length is not None
                                    else value
                                )
                            )
                    content = content.where(pd.notnull(content), None)
                    hex_encode_binary_columns(content)
                    db_content[table_name] = {
                        "columns": content.columns.tolist(),
                        "data": content.values.tolist(),
                    }
                return db_content
        finally:
            engine.dispose()

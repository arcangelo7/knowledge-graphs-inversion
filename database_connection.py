import pandas as pd
from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.engine import Engine


class DatabaseConnection:
    def __init__(self):
        self._connection_strings = {
            'postgresql_r2rml': 'postgresql://r2rml:r2rml@postgresql_r2rml:5432/r2rml',
            'dest_postgresql_r2rml': 'postgresql://r2rml:r2rml@dest_postgresql_r2rml:5432/r2rml',
            'postgresql_rml': 'postgresql://r2rml:r2rml@postgresql_rml:5432/r2rml',
            'dest_postgresql_rml': 'postgresql://r2rml:r2rml@dest_postgresql_rml:5432/r2rml',
        }

    def get_connection_string(self, database_system: str) -> str:
        return self._connection_strings[database_system]

    def load_sql_script(self, database_system: str, sql_script_path: str) -> None:
        connection_string = self.get_connection_string(database_system)
        engine = create_engine(connection_string)
        try:
            with open(sql_script_path, 'r') as f:
                statements = f.read().split(';')
            with engine.begin() as conn:
                for statement in statements:
                    if statement.strip():
                        conn.execute(text(statement))
        finally:
            engine.dispose()

    def drop_all_tables(self, database_system: str) -> None:
        connection_string = self.get_connection_string(database_system)
        engine = create_engine(connection_string)
        try:
            with engine.begin():
                metadata = MetaData()
                metadata.reflect(bind=engine)
                metadata.drop_all(engine)
        finally:
            engine.dispose()

    def get_database_content(self, database_system: str) -> dict[str, dict[str, list[str]]]:
        connection_string = self.get_connection_string(database_system)
        engine = create_engine(connection_string)
        try:
            with engine.connect() as connection:
                table_query = "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname != 'pg_catalog' AND schemaname != 'information_schema';"
                tables = pd.read_sql(table_query, connection)
                table_names = tables.values.flatten()
                db_content = {}
                for table in table_names:
                    db_content[table] = self._get_table_content(engine, table)
                return db_content
        finally:
            engine.dispose()

    def _get_table_content(self, engine: Engine, table_name: str) -> dict[str, list[str]] | None:
        with engine.connect() as connection:
            content_query = f'SELECT * FROM "{table_name}";'
            datatype_query = f"""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = '{table_name}';
            """
            content = pd.read_sql(content_query, connection)
            datatypes = pd.read_sql(datatype_query, connection)
            if datatypes.empty:
                return None
            content = content.where(pd.notnull(content), None)
            return {
                'columns': content.columns.tolist(),
                'data': content.values.tolist()
            }

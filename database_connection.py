import os
import pandas as pd
from sqlalchemy import create_engine


class DatabaseConnection:
    """Simple database connection manager that assumes databases are already running."""
    
    def __init__(self):
        # Always use Docker service names since Docker is the standard setup
        self.hosts = {
            'postgresql': 'postgresql',
            'dest_postgresql': 'dest_postgresql'
        }
        self.ports = {
            'postgresql': 5432,
            'dest_postgresql': 5432
        }
    
    def create_engine(self, connection_string):
        return create_engine(connection_string)

    def get_connection_string(self, database_system):
        host = self.hosts[database_system]
        port = self.ports[database_system]
        if database_system in ['postgresql', 'dest_postgresql']:
            return f"postgresql://r2rml:r2rml@{host}:{port}/r2rml"
        else:
            raise ValueError(f"Unsupported database system: {database_system}")

    def get_database_content(self, database_system):
        connection_string = self.get_connection_string(database_system)
        engine = self.create_engine(connection_string)
        
        try:
            with engine.connect() as connection:
                table_query = "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname != 'pg_catalog' AND schemaname != 'information_schema';"
                tables = pd.read_sql(table_query, connection)
                table_names = tables.values.flatten()
                
                db_content = {}
                for table in table_names:
                    db_content[table] = self.get_table_content(database_system, table)
                
                return db_content
        except Exception as e:
            print(f"Error getting database content: {str(e)}")
            return None
        finally:
            engine.dispose()

    def get_table_content(self, database_system, table_name):
        connection_string = self.get_connection_string(database_system)
        engine = self.create_engine(connection_string)
        
        try:
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
                
                datatypes = datatypes.set_index('column_name')['data_type']
                
                # Replace NaN with None for proper JSON serialization if needed
                content = content.where(pd.notnull(content), None)
                
                return {
                    'columns': content.columns.tolist(),
                    'data': content.values.tolist()
                }
        except Exception as e:
            print(f"Error getting table content: {str(e)}")
            return None
        finally:
            engine.dispose()
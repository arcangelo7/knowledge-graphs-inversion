import os
import pathlib
import subprocess
import time

import docker
import pandas as pd
from docker.errors import APIError, NotFound
from sqlalchemy import create_engine


class DatabaseManager:
    def __init__(self):
        # Try different Docker socket locations for Docker Desktop compatibility
        docker_client = None
        socket_locations = [
            None,  # Use docker.from_env() default
            "unix:///var/run/docker.sock",  # Standard Docker daemon
            f"unix://{os.path.expanduser('~')}/.docker/desktop/docker.sock",  # Docker Desktop
            f"unix://{os.path.expanduser('~')}/.docker/run/docker.sock",  # Alternative Docker Desktop location
        ]
        
        for socket_path in socket_locations:
            try:
                if socket_path is None:
                    docker_client = docker.from_env()
                else:
                    docker_client = docker.DockerClient(base_url=socket_path)
                
                # Test the connection
                docker_client.ping()
                print(f"Successfully connected to Docker using: {socket_path or 'default environment'}")
                break
            except Exception as e:
                if socket_path:
                    print(f"Failed to connect to Docker at {socket_path}: {e}")
                continue
        
        if docker_client is None:
            raise Exception("Could not connect to Docker daemon at any known location")
        
        self.client = docker_client
        self.containers = {}
        self.ports = {'postgresql': 5432, 'mysql': 3306, 'graphdb': 7200, 'dest_postgresql': 5433, 'virtuoso': 8890}
        self.graphdb_initialized = False
        self.virtuoso_initialized = False

    def create_engine(self, connection_string):
        return create_engine(connection_string)

    def get_container(self, database_system):
        if database_system not in self.containers or not self.container_is_running(database_system):
            self.start_container(database_system)
        return self.containers[database_system]

    def ensure_image_available(self, image_name):
        """
        Ensures that the specified Docker image is available locally.
        Downloads it if not present.
        
        Args:
            image_name: Name of the Docker image to ensure is available
        """
        try:
            self.client.images.get(image_name)
            print(f"Image {image_name} already available locally")
        except docker.errors.ImageNotFound:
            print(f"Image {image_name} not found locally. Downloading...")
            try:
                self.client.images.pull(image_name)
                print(f"Successfully downloaded image {image_name}")
            except docker.errors.APIError as e:
                print(f"Failed to download image {image_name}: {e}")
                raise

    def start_container(self, database_system):
        self.stop_existing_services(database_system)
        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                if database_system == 'graphdb':
                    return self.start_graphdb_container()
                elif database_system == 'virtuoso':
                    return self.start_virtuoso_container()
                
                image = 'postgres:13' if database_system in ['postgresql', 'dest_postgresql'] else 'mysql:8'
                port = self.ports[database_system]
                
                self.ensure_image_available(image)
                
                environment = {
                    'POSTGRES_PASSWORD': 'r2rml',
                    'POSTGRES_USER': 'r2rml',
                    'POSTGRES_DB': 'r2rml'
                } if database_system in ['postgresql', 'dest_postgresql'] else {
                    'MYSQL_ROOT_PASSWORD': 'r2rml',
                    'MYSQL_USER': 'r2rml',
                    'MYSQL_PASSWORD': 'r2rml',
                    'MYSQL_DATABASE': 'r2rml'
                }
                
                if database_system in {'postgresql', 'dest_postgresql'}:
                    # Use a custom command to change the port
                    command = f"-c port={port}"
                    container = self.client.containers.run(
                        image,
                        command=command,
                        detach=True,
                        remove=True,
                        environment=environment,
                        ports={f'{port}/tcp': port}
                    )
                else:
                    container = self.client.containers.run(
                        image,
                        detach=True,
                        remove=True,
                        environment=environment,
                        ports={f'{port}/tcp': port}
                    )
                
                print(f"Container started for {database_system}: {container.id}")
                self.containers[database_system] = container
                time.sleep(5)
                return
            except docker.errors.APIError as e:
                if "port is already allocated" in str(e) and attempt < max_attempts - 1:
                    print(f"Port {port} is still in use, attempting to stop services again...")
                    self.stop_existing_services(database_system)
                    continue
                else:
                    print(f"Error starting container: {e}")
                    raise
        raise Exception(f"Failed to start {database_system} container after {max_attempts} attempts")

    def start_graphdb_container(self):
        if not self.graphdb_initialized:
            port = self.ports['graphdb']
            image = 'ontotext/graphdb:10.7.3'
            
            self.ensure_image_available(image)
            
            container = self.client.containers.run(
                image,
                detach=True,
                remove=True,
                ports={f'{port}/tcp': port},
                environment={
                    'GDB_JAVA_OPTS': '-Xmx2g -Xms2g',
                    'GDB_HEAP_SIZE': '2g'
                }
            )
            print(f"GraphDB container started: {container.id}")
            self.containers['graphdb'] = container
            self.graphdb_initialized = True
        return self.containers['graphdb']
    
    def start_virtuoso_container(self):
        if not self.virtuoso_initialized:
            port = self.ports['virtuoso']
            container_name = 'virtuoso-kgi'
            
            containers = self.client.containers.list()
            for container in containers:
                if container.name == container_name:
                    print(f"Virtuoso container '{container_name}' already running")
                    self.containers['virtuoso'] = container
                    self.virtuoso_initialized = True
                    return container
            
            repo_root = pathlib.Path(__file__).parent
            data_dir = repo_root / 'virtuoso-data'
            data_dir.mkdir(exist_ok=True)
            data_dir_str = str(data_dir)
            
            cmd = [
                'uv', 'run', 'python', '-m', 'virtuoso_utilities.launch_virtuoso',
                '--name', container_name,
                '--http-port', str(port),
                '--isql-port', '1111',
                '--data-dir', data_dir_str,
                '--memory', '4g',
                '--dba-password', 'dba',
                '--force-remove',
                '--detach',
                '--wait-ready',
                '--enable-write-permissions'
            ]
            
            print(f"Launching Virtuoso container...")
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                print(result.stdout)
                
                container = self.client.containers.get(container_name)
                self.containers['virtuoso'] = container
                self.virtuoso_initialized = True
                
                print(f"Virtuoso container started: {container.id}")
                return container
                
            except subprocess.CalledProcessError as e:
                print(f"Failed to launch Virtuoso: {e}")
                print(f"Stdout: {e.stdout}")
                print(f"Stderr: {e.stderr}")
                raise
        
        return self.containers['virtuoso']

    def stop_existing_services(self, database_system):
        port = self.ports[database_system]
        
        # Stop Docker containers using the port
        containers = self.client.containers.list()
        for container in containers:
            container_ports = container.attrs['NetworkSettings']['Ports']
            for container_port, host_ports in container_ports.items():
                if host_ports and int(host_ports[0]['HostPort']) == port:
                    print(f"Stopping Docker container {container.id} using port {port}")
                    try:
                        container.stop(timeout=10)
                        container.remove(force=True)
                    except APIError as e:
                        if 'removal of container' in str(e) and 'is already in progress' in str(e):
                            print(f"Container {container.id} is already being removed. Continuing...")
                        else:
                            raise

        # Stop system service (if running)
        if database_system == 'postgresql':
            self._stop_postgresql_service()
        elif database_system == 'mysql':
            self._stop_mysql_service()

    def _stop_postgresql_service(self):
        try:
            subprocess.run(['sudo', 'service', 'postgresql', 'stop'], check=True)
            print("Stopped PostgreSQL system service")
        except subprocess.CalledProcessError:
            print("Failed to stop PostgreSQL system service")

    def _stop_mysql_service(self):
        try:
            subprocess.run(['sudo', 'service', 'mysql', 'stop'], check=True)
            print("Stopped MySQL system service")
        except subprocess.CalledProcessError:
            print("Failed to stop MySQL system service")

    def container_is_running(self, database_system):
        if database_system in self.containers:
            try:
                self.containers[database_system].reload()
                return self.containers[database_system].status == 'running'
            except NotFound:
                return False
        return False

    def reset_database(self, database_system):
        container = self.get_container(database_system)
        if database_system in ['postgresql', 'dest_postgresql']:
            # Terminate all connections
            container.exec_run("""
                psql -U postgres -c "
                SELECT pg_terminate_backend(pg_stat_activity.pid)
                FROM pg_stat_activity
                WHERE pg_stat_activity.datname = 'r2rml'
                AND pid <> pg_backend_pid();
                "
            """)
            # Drop and recreate the database
            container.exec_run("""
                psql -U postgres -c "
                DROP DATABASE IF EXISTS r2rml;
                CREATE DATABASE r2rml;
                "
            """)
            # Reconnect and set permissions
            container.exec_run("""
                psql -U postgres -d r2rml -c "
                CREATE SCHEMA IF NOT EXISTS public;
                GRANT ALL ON SCHEMA public TO r2rml;
                GRANT ALL ON SCHEMA public TO public;
                ALTER DATABASE r2rml OWNER TO r2rml;
                "
            """)
        elif database_system == 'mysql':
            container.exec_run("""
                mysql -u root -pr2rml -e '
                DROP DATABASE IF EXISTS r2rml;
                CREATE DATABASE r2rml;
                GRANT ALL PRIVILEGES ON r2rml.* TO 'r2rml'@'%';
                FLUSH PRIVILEGES;
                '
            """)
        elif database_system == 'graphdb':
            pass

        print(f"Database {database_system} has been reset.")

    def get_connection_string(self, database_system):
        port = self.ports[database_system]
        if database_system in ['postgresql', 'dest_postgresql']:
            return f"postgresql://r2rml:r2rml@localhost:{port}/r2rml"
        elif database_system == 'mysql':
            return f"mysql+pymysql://r2rml:r2rml@localhost:{port}/r2rml"
        elif database_system == 'graphdb':
            return f"http://localhost:{port}"
        elif database_system == 'virtuoso':
            return f"http://localhost:{port}/sparql"

    def get_database_content(self, database_system):
        connection_string = self.get_connection_string(database_system)
        engine = self.create_engine(connection_string)
        
        try:
            with engine.connect() as connection:
                if database_system in ['postgresql', 'dest_postgresql']:
                    table_query = "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname != 'pg_catalog' AND schemaname != 'information_schema';"
                else:  # MySQL
                    table_query = "SHOW TABLES;"
                
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
                if database_system in ['postgresql', 'dest_postgresql']:
                    content_query = f'SELECT * FROM "{table_name}";'
                    datatype_query = f"""
                        SELECT column_name, data_type 
                        FROM information_schema.columns 
                        WHERE table_name = '{table_name}';
                    """
                else:  # MySQL
                    content_query = f"SELECT * FROM `{table_name}`;"
                    datatype_query = f"""
                        SELECT column_name, data_type 
                        FROM information_schema.columns 
                        WHERE table_name = '{table_name}' AND table_schema = DATABASE();
                    """
                
                content = pd.read_sql(content_query, connection)
                datatypes = pd.read_sql(datatype_query, connection)
                
                if datatypes.empty:
                    return None
                
                datatypes = datatypes.set_index('column_name')['data_type']
                
                return {
                    'columns': content.columns.tolist(),
                    'data': content.values.tolist()
                }
        except Exception as e:
            print(f"Error getting table content: {str(e)}")
            return None
        finally:
            engine.dispose()
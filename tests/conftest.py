# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import os
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal, cast

import pytest
from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

import rmlmapper
from conformance_config import get_database_config
from test_suites import R2RMLTestSuite, RMLTestSuite

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

Database = Literal["postgresql", "mysql"]


@dataclass(frozen=True)
class DatabaseConfig:
    name: Database
    sqlalchemy_driver: str
    image: str
    container_port: int
    source_port: int
    dest_port: int
    environment: tuple[str, ...]
    server_options: tuple[str, ...] = ()

    def url(self, port: int) -> str:
        return f"{self.sqlalchemy_driver}://r2rml:r2rml@localhost:{port}/r2rml"


DATABASE_CONFIGS: dict[Database, DatabaseConfig] = {
    "postgresql": DatabaseConfig(
        name="postgresql",
        sqlalchemy_driver="postgresql+psycopg2",
        image="postgres:13",
        container_port=5432,
        source_port=5440,
        dest_port=5441,
        environment=(
            "POSTGRES_USER=r2rml",
            "POSTGRES_PASSWORD=r2rml",
            "POSTGRES_DB=r2rml",
        ),
    ),
    "mysql": DatabaseConfig(
        name="mysql",
        sqlalchemy_driver="mysql+pymysql",
        image="mysql:9.7.1",
        container_port=3306,
        source_port=3307,
        dest_port=3308,
        environment=(
            "MYSQL_ROOT_PASSWORD=r2rml",
            "MYSQL_USER=r2rml",
            "MYSQL_PASSWORD=r2rml",
            "MYSQL_DATABASE=r2rml",
        ),
        server_options=(
            "--sql-mode=ANSI_QUOTES,PAD_CHAR_TO_FULL_LENGTH,PIPES_AS_CONCAT",
        ),
    ),
}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--database",
        choices=tuple(DATABASE_CONFIGS),
        default="postgresql",
        help="database used by conformance tests",
    )
    parser.addoption(
        "--souffle-jar",
        default="",
        help="R2RML-to-Datalog translator jar used by Soufflé conformance tests",
    )
    parser.addoption(
        "--souffle-library",
        default="",
        help="functor library used by Soufflé conformance tests",
    )


@pytest.fixture(scope="session")
def database(request: pytest.FixtureRequest) -> Database:
    return cast(Database, request.config.getoption("database"))


@pytest.fixture(scope="session")
def database_config(database: Database) -> DatabaseConfig:
    return DATABASE_CONFIGS[database]


def _wait_for_database(
    db_url: str, database: Database, port: int, timeout: int = 60
) -> None:
    engine = create_engine(db_url)
    start = time.monotonic()
    try:
        while time.monotonic() - start < timeout:
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                return
            except OperationalError:
                time.sleep(1)
    finally:
        engine.dispose()
    raise RuntimeError(f"{database} on port {port} not ready after {timeout}s")


def _start_database(
    container_name: str,
    port: int,
    db_url: str,
    database_config: DatabaseConfig,
) -> None:
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
    command = ["docker", "run", "-d", "--name", container_name]
    for variable in database_config.environment:
        command.extend(["-e", variable])
    command.extend(
        [
            "-p",
            f"{port}:{database_config.container_port}",
            database_config.image,
            *database_config.server_options,
        ]
    )
    subprocess.run(
        command,
        check=True,
        capture_output=True,
    )
    _wait_for_database(db_url, database_config.name, port)


@pytest.fixture(scope="session")
def _database_containers(
    database_config: DatabaseConfig,
) -> Iterator[tuple[str, str]]:
    source_container = f"kgi-test-r2rml-{database_config.name}-source"
    dest_container = f"kgi-test-r2rml-{database_config.name}-dest"
    database_urls = (
        database_config.url(database_config.source_port),
        database_config.url(database_config.dest_port),
    )
    source_url, dest_url = database_urls
    try:
        _start_database(
            source_container,
            database_config.source_port,
            source_url,
            database_config,
        )
        _start_database(
            dest_container,
            database_config.dest_port,
            dest_url,
            database_config,
        )
        yield database_urls
    finally:
        subprocess.run(["docker", "rm", "-f", source_container], capture_output=True)
        subprocess.run(["docker", "rm", "-f", dest_container], capture_output=True)


@pytest.fixture(scope="session")
def database_urls(_database_containers: tuple[str, str]) -> tuple[str, str]:
    return _database_containers


def drop_all_tables(db_url: str) -> None:
    engine = create_engine(db_url)
    try:
        with engine.begin():
            metadata = MetaData()
            metadata.reflect(bind=engine)
            metadata.drop_all(engine)
    finally:
        engine.dispose()


def load_sql_script(db_url: str, script_path: str) -> None:
    engine = create_engine(db_url)
    try:
        with open(script_path, "r") as f:
            statements = f.read().split(";")
        with engine.begin() as conn:
            for statement in statements:
                if statement.strip():
                    conn.execute(text(statement))
    finally:
        engine.dispose()


def run_forward_mapping(
    mapping_path: str,
    output_path: str,
    db_url: str,
    suite_id: str,
    tmp_dir: str,
) -> int:
    database = get_database_config(make_url(db_url).get_backend_name())
    jdbc_dsn, username, password = rmlmapper.sqlalchemy_to_jdbc(
        db_url, database.jdbc_properties
    )
    if suite_id == "rml":
        prepared = rmlmapper.prepare_rml_mapping(
            mapping_path,
            jdbc_dsn,
            username,
            password,
            tmp_dir,
        )
        return rmlmapper.run(prepared, output_path)
    return rmlmapper.run(
        mapping_path,
        output_path,
        dsn=jdbc_dsn,
        username=username,
        password=password,
    )


def _collect_test_ids(suite_class: type, base_dir: str) -> list[str]:
    if not os.path.isdir(base_dir):
        return []
    suite = suite_class(base_dir)
    return suite.list_test_ids()


R2RML_BASE_DIR = os.path.join(PROJECT_ROOT, "r2rml_test_cases")
RML_BASE_DIR = os.path.join(PROJECT_ROOT, "rml_io_registry")

R2RML_TEST_IDS = _collect_test_ids(R2RMLTestSuite, R2RML_BASE_DIR)
RML_TEST_IDS = _collect_test_ids(RMLTestSuite, RML_BASE_DIR)


@pytest.fixture(scope="session")
def r2rml_suite() -> R2RMLTestSuite:
    return R2RMLTestSuite(R2RML_BASE_DIR)


@pytest.fixture(scope="session")
def rml_suite() -> RMLTestSuite:
    return RMLTestSuite(RML_BASE_DIR)

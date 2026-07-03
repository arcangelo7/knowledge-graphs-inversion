# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import os
import subprocess
import time

import pandas as pd
import pytest
from sqlalchemy import MetaData, create_engine, text

import rmlmapper
from database_connection import hex_encode_binary_columns
from test_suites import R2RMLTestSuite, RMLTestSuite

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SOURCE_CONTAINER = "kgi-test-r2rml-source"
DEST_CONTAINER = "kgi-test-r2rml-dest"
SOURCE_PORT = 5440
DEST_PORT = 5441
SOURCE_R2RML_DB = f"postgresql+psycopg2://r2rml:r2rml@localhost:{SOURCE_PORT}/r2rml"
DEST_R2RML_DB = f"postgresql+psycopg2://r2rml:r2rml@localhost:{DEST_PORT}/r2rml"


def _wait_for_postgres(port: int, timeout: int = 60) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        result = subprocess.run(
            ["pg_isready", "-h", "localhost", "-p", str(port), "-U", "r2rml"],
            capture_output=True,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError(f"PostgreSQL on port {port} not ready after {timeout}s")


def _start_postgres(container_name: str, port: int) -> None:
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "-e",
            "POSTGRES_USER=r2rml",
            "-e",
            "POSTGRES_PASSWORD=r2rml",
            "-e",
            "POSTGRES_DB=r2rml",
            "-p",
            f"{port}:5432",
            "postgres:13",
        ],
        check=True,
        capture_output=True,
    )
    _wait_for_postgres(port)


@pytest.fixture(scope="session", autouse=True)
def _postgres_containers():
    _start_postgres(SOURCE_CONTAINER, SOURCE_PORT)
    _start_postgres(DEST_CONTAINER, DEST_PORT)
    yield
    subprocess.run(["docker", "rm", "-f", SOURCE_CONTAINER], capture_output=True)
    subprocess.run(["docker", "rm", "-f", DEST_CONTAINER], capture_output=True)


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


def get_db_content(db_url: str) -> dict[str, dict[str, list[str]]]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            tables_query = (
                "SELECT tablename FROM pg_catalog.pg_tables "
                "WHERE schemaname != 'pg_catalog' AND schemaname != 'information_schema';"
            )
            tables = pd.read_sql(tables_query, conn)
            table_names = tables.values.flatten()
            content: dict[str, dict[str, list[str]]] = {}
            for table in table_names:
                df = pd.read_sql(f'SELECT * FROM "{table}";', conn)
                df = df.where(pd.notnull(df), None)
                hex_encode_binary_columns(df)
                content[table] = {
                    "columns": df.columns.tolist(),
                    "data": df.values.tolist(),
                }
            return content
    finally:
        engine.dispose()


def run_forward_mapping(
    mapping_path: str,
    output_path: str,
    db_url: str,
    suite_id: str,
    tmp_dir: str,
) -> int:
    jdbc_dsn, username, password = rmlmapper.sqlalchemy_to_jdbc(db_url)
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
    suite = suite_class(base_dir, PROJECT_ROOT)
    return suite.list_test_ids()


R2RML_BASE_DIR = os.path.join(PROJECT_ROOT, "r2rml_test_cases")
RML_BASE_DIR = os.path.join(PROJECT_ROOT, "rml_io_registry")

R2RML_TEST_IDS = _collect_test_ids(R2RMLTestSuite, R2RML_BASE_DIR)
RML_TEST_IDS = _collect_test_ids(RMLTestSuite, RML_BASE_DIR)


@pytest.fixture(scope="session")
def r2rml_suite() -> R2RMLTestSuite:
    return R2RMLTestSuite(R2RML_BASE_DIR, PROJECT_ROOT)


@pytest.fixture(scope="session")
def rml_suite() -> RMLTestSuite:
    return RMLTestSuite(RML_BASE_DIR, PROJECT_ROOT)

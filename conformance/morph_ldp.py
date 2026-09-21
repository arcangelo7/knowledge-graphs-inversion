# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
# SPDX-License-Identifier: ISC

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from rdflib.exceptions import ParserError
from sqlalchemy import Column, MetaData, Table, create_engine, inspect
from sqlalchemy.engine import make_url

from conformance import rmlmapper
from conformance.config import get_database_config
from conformance.database import DatabaseConnection
from conformance.outcome import CaseOutcome, InversionOutcome
from conformance.souffle import rdf_datasets_isomorphic
from conformance.suites import TestSuite
from kgi import NonInvertibleError, UnsupportedMappingError, analyze_mapping
from kgi.comparison import compare_databases, databases_identical

MORPH_REVISION = "130af1ab3b7944953bb95572f8658910c107002a"
LDP_REVISION = "95612fc34ffd14e4bb36b567d2f672565bd95a3f"
LIBRARIES_REVISION = "da3038427da33f8e2a247fe2119027a44ad96ca6"
ASSET_HOME = Path(os.environ.get("MORPH_LDP_HOME", "build/morph-ldp/runtime")).resolve()


@dataclass
class MorphEvaluation:
    outcome: CaseOutcome
    forward_ok: bool
    attempted: bool = False
    subjects_attempted: int = 0
    subjects_rejected: int = 0
    source_equal: bool | None = None
    projection_equal: bool | None = None
    rdf_equal: bool | None = None
    execution_error: str | None = None
    analysis_message: str | None = None
    errors: list[dict[str, str]] = field(default_factory=list)
    validation_ok: bool = True
    sql: str = ""
    log: str = ""
    losses: list[str] = field(default_factory=list)

    def diagnostics(self) -> dict[str, object]:
        return {
            "attempted": self.attempted,
            "subjects_attempted": self.subjects_attempted,
            "subjects_rejected": self.subjects_rejected,
            "validation_ok": self.validation_ok,
            "errors": self.errors,
            "source_equal": self.source_equal,
            "projection_equal": self.projection_equal,
            "rdf_equal": self.rdf_equal,
            "execution_error": self.execution_error,
            "analysis_message": self.analysis_message,
            "sql": self.sql,
            "log": self.log,
            "projection_losses": self.losses,
        }

    def record(self) -> dict[str, object]:
        return {
            **self.diagnostics(),
            "outcome": str(self.outcome.outcome),
            "losses": sorted(self.outcome.losses),
            "message": self.outcome.message,
            "forward_ok": self.forward_ok,
            "source_tables": self.outcome.source_content,
            "recovered_tables": self.outcome.dest_content,
            "revisions": {
                "morph_ldp": LDP_REVISION,
                "morph": MORPH_REVISION,
                "external_libraries": LIBRARIES_REVISION,
                "rmlmapper": rmlmapper.RMLMAPPER_VERSION,
                "rmlmapper_sha256": hashlib.sha256(
                    Path(rmlmapper._get_jar_path()).read_bytes()
                ).hexdigest(),
                "mysql_jdbc": "5.1.49",
            },
        }


def _empty_destination(source_url: str, destination_url: str) -> None:
    source = create_engine(source_url)
    destination = create_engine(destination_url)
    try:
        metadata = MetaData()
        with source.connect() as connection:
            schema = inspect(connection)
            for name in schema.get_table_names():
                Table(
                    name,
                    metadata,
                    *(
                        Column(column["name"], column["type"], nullable=True)
                        for column in schema.get_columns(name)
                    ),
                )
        metadata.create_all(destination)
    finally:
        source.dispose()
        destination.dispose()


def _forward(mapping: str, output: Path, url: str, log: Path) -> int:
    database = get_database_config(make_url(url).get_backend_name())
    dsn, username, password = rmlmapper.sqlalchemy_to_jdbc(
        url, database.jdbc_properties
    )
    result = rmlmapper.execute(
        mapping, str(output), dsn=dsn, username=username, password=password
    )
    log.write_text(result.stdout + result.stderr)
    return result.returncode


def _execute(
    mapping: str, rdf: Path, destination_url: str, directory: Path
) -> subprocess.CompletedProcess[str]:
    url = make_url(destination_url)
    dsn, username, password = rmlmapper.sqlalchemy_to_jdbc(
        destination_url,
        (("useSSL", "false"), ("allowPublicKeyRetrieval", "true"))
        if make_url(destination_url).get_backend_name() == "mysql"
        else (),
    )
    database = url.get_backend_name()
    settings = {
        "mapping": str(Path(mapping).resolve()),
        "database.type": "PostgreSQL" if database == "postgresql" else "MySQL",
        "database.driver": "org.postgresql.Driver"
        if database == "postgresql"
        else "com.mysql.jdbc.Driver",
        "database.url": dsn,
        "database.name": str(url.database),
        "database.user": username,
        "database.password": password,
    }
    properties = directory / "connection.properties"
    properties.write_text(
        "".join(f"{key}={value}\n" for key, value in settings.items())
    )
    return subprocess.run(
        [
            str(ASSET_HOME / "java/bin/java"),
            "-cp",
            f"{ASSET_HOME}/classes:{ASSET_HOME}/lib/*",
            "CreateResources",
            str(properties),
            str(rdf),
            str(directory / "tdb"),
            str(directory / "inserts.sql"),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )


def evaluate_morph_case(
    suite: TestSuite,
    test_id: str,
    database: str,
    source_url: str,
    destination_url: str,
    directory: Path,
) -> MorphEvaluation:
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    connection = DatabaseConnection()
    connection.drop_all_tables(source_url)
    connection.drop_all_tables(destination_url)
    connection.load_sql_script(source_url, suite.get_sql_script_path(test_id, database))
    _empty_destination(source_url, destination_url)
    mapping = suite.get_mapping_path(test_id)
    shutil.copyfile(mapping, directory / "mapping.ttl")
    shutil.copyfile(
        suite.get_sql_script_path(test_id, database), directory / "source.sql"
    )
    output = directory / "output.nq"
    output.unlink(missing_ok=True)
    exit_code = _forward(mapping, output, source_url, directory / "forward.log")
    metadata = suite.get_test_metadata(test_id)
    assert metadata is not None
    expects_output = bool(metadata["expected_output"])
    expected = Path(suite.get_expected_output_path(test_id))
    if expected.is_file():
        shutil.copyfile(expected, directory / "expected.nq")
    produced = output.is_file() and output.stat().st_size != 0
    if not expects_output:
        result = MorphEvaluation(
            CaseOutcome(
                InversionOutcome.ERROR_TEST_CASE,
                message="Specification-negative mapping; inversion not attempted.",
            ),
            forward_ok=not produced,
        )
    elif exit_code != 0:
        result = MorphEvaluation(
            CaseOutcome(
                InversionOutcome.ERROR,
                message="RMLMapper forward execution failed; inversion not attempted.",
            ),
            False,
        )
    else:
        if not output.is_file():
            output.touch()
        forward_ok = rdf_datasets_isomorphic(expected, output)
        if not forward_ok:
            result = MorphEvaluation(
                CaseOutcome(
                    InversionOutcome.MISMATCH,
                    message="Forward RDF differs from the official dataset; inversion not attempted.",
                ),
                False,
            )
        else:
            result = _invert(mapping, output, source_url, destination_url, directory)
    source = connection.get_database_content(source_url)
    destination = connection.get_database_content(destination_url)
    if not result.forward_ok:
        result.validation_ok = False
        result.errors.append(
            {
                "phase": "forward validation",
                "component": "rmlmapper",
                "reason": result.outcome.message,
            }
        )
    outcome = result.outcome
    result.outcome = CaseOutcome(
        outcome.outcome, outcome.losses, outcome.message, source, destination
    )
    (directory / "result.json").write_text(
        json.dumps(result.record(), indent=2, default=str) + "\n"
    )
    return result


def _invert(
    mapping: str, rdf: Path, source_url: str, destination_url: str, directory: Path
) -> MorphEvaluation:
    tdb = directory / "tdb"
    if tdb.exists():
        shutil.rmtree(tdb)
    execution = _execute(mapping, rdf, destination_url, directory)
    log = execution.stdout + execution.stderr
    (directory / "morph.log").write_text(log)
    sql = (directory / "inserts.sql").read_text()
    connection = DatabaseConnection()
    source = connection.get_database_content(source_url)
    destination = connection.get_database_content(destination_url)
    result = MorphEvaluation(
        CaseOutcome(InversionOutcome.MISMATCH),
        True,
        attempted="MORPH_CREATE_RESOURCE " in log,
        subjects_attempted=log.count("MORPH_CREATE_RESOURCE "),
        subjects_rejected=log.count("MORPH_RESOURCE_REJECTED "),
        source_equal=databases_identical(source, destination),
        sql=sql,
        log=log,
    )
    if "Error opening database connection" in log:
        result.execution_error = (
            "Morph could not connect to the destination; see morph.log."
        )
    elif "Error executing query, error message" in log:
        result.execution_error = "Morph reported a SQL execution error; see morph.log."
    elif execution.returncode != 0:
        result.execution_error = (
            f"Morph-LDP exited with code {execution.returncode}; see morph.log."
        )
    losses = frozenset()
    message = ""
    try:
        analysis = analyze_mapping(mapping, rdf, source_db_url=source_url)
    except (NonInvertibleError, UnsupportedMappingError) as error:
        result.analysis_message = str(error)
    else:
        projection_destination = {
            name: table
            for name, table in destination.items()
            if name in analysis or table["data"]
        }
        equal, message, losses = compare_databases(
            source, projection_destination, analysis
        )
        result.projection_equal = equal or bool(losses)
        result.losses = sorted(losses)
    result.rdf_equal, verification_errors = verify_roundtrip(
        mapping, rdf, destination_url, directory
    )
    result.errors.extend(verification_errors)
    result.validation_ok = result.rdf_equal is True and result.execution_error is None
    if result.execution_error:
        result.errors.append(
            {
                "phase": "inversion",
                "component": "sql"
                if "SQL execution" in result.execution_error
                else "morph-ldp",
                "reason": result.execution_error,
            }
        )
    if "Only STG pattern is supported for insert operation!" in log:
        result.validation_ok = False
        result.errors.append(
            {
                "phase": "inversion",
                "component": "morph-ldp",
                "reason": "Only STG patterns are supported for insertion.",
            }
        )
    if result.source_equal:
        outcome = InversionOutcome.FULLY_INVERTED
        message = "All source tables were recovered exactly."
        losses = frozenset()
    elif result.projection_equal:
        outcome = InversionOutcome.PARTIALLY_INVERTED
    elif result.execution_error:
        outcome = InversionOutcome.ERROR
        message = result.execution_error
        losses = frozenset()
    elif "Only STG pattern is supported for insert operation!" in log:
        outcome = InversionOutcome.NOT_SUPPORTED
        message = "Morph rejects this subject group: only STG patterns are supported for insertion."
        losses = frozenset()
    else:
        outcome = InversionOutcome.MISMATCH
        message = (
            message
            or result.analysis_message
            or "Recovered tables differ from the source."
        )
        losses = frozenset()
    result.outcome = CaseOutcome(outcome, losses, message)
    return result


def verify_roundtrip(
    mapping: str, rdf: Path, destination_url: str, directory: Path
) -> tuple[bool | None, list[dict[str, str]]]:
    roundtrip = directory / "roundtrip.nq"
    roundtrip.unlink(missing_ok=True)
    try:
        code = _forward(
            mapping, roundtrip, destination_url, directory / "roundtrip.log"
        )
    except subprocess.TimeoutExpired:
        return None, [
            {
                "phase": "rdf verification",
                "component": "rmlmapper",
                "reason": "Round-trip materialization timed out.",
            }
        ]
    if code != 0:
        return None, [
            {
                "phase": "rdf verification",
                "component": "rmlmapper",
                "reason": f"Round-trip materialization exited with code {code}; see roundtrip.log.",
            }
        ]
    if not roundtrip.is_file():
        roundtrip.touch()
    try:
        equal = rdf_datasets_isomorphic(rdf, roundtrip)
    except ParserError as error:
        return None, [
            {
                "phase": "rdf verification",
                "component": "rdf parser",
                "reason": str(error),
            }
        ]
    return equal, []

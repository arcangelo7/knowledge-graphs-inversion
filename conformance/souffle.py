# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from rdflib import Dataset
from rdflib.compare import to_isomorphic
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    create_engine,
)
from sqlalchemy.dialects.mysql import TINYINT, VARBINARY
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.schema import Column as SchemaColumn

from conformance.souffle_artifacts import (
    FACT_FILES,
    FORWARD_PROGRAM,
    FORWARD_PROVENANCE_PROGRAM,
    PROVENANCE_MARKER_FILES,
    REVERSE_PROGRAM,
    SUPPORT_REPORT,
    SourceRelation,
    parse_source_relations,
    read_recovered_rows,
    write_rdf_dataset,
)

Database = Literal["postgresql", "mysql"]
ExecutionMode = Literal["docker", "local"]
Stage = Literal[
    "forward generation",
    "forward execution",
    "inverse generation",
    "backward execution",
]

SOUFFLE_IMAGE = (
    "alloka/souffle:v1.0.0@sha256:"
    "0e9288ca6f7a63faf93f4358f210de0ffcab6e3e2405d88c365391da6d54fe89"
)


class SouffleConformanceError(RuntimeError):
    def __init__(self, stage: Stage, command: tuple[str, ...], output: str):
        self.stage = stage
        self.command = command
        self.output = output
        command_text = shlex.join(command)
        diagnostic = output.strip() or "<no command output>"
        super().__init__(
            f"{stage} failed\nCommand: {command_text}\nOutput:\n{diagnostic}"
        )


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    output: str


def _normalized_table_name(name: str) -> str:
    return re.sub(r'[\s`"]', "", name).casefold()


def _normalized_column_name(name: str) -> str:
    return re.sub(r"[\s_]", "", name).replace("(", "").replace(")", "").casefold()


def _binary_value(value: str) -> bytes:
    hexadecimal = value[2:] if value.startswith("\\x") else value
    return bytes.fromhex(hexadecimal)


def _boolean_value(value: str) -> bool:
    return {
        "true": True,
        "t": True,
        "1": True,
        "false": False,
        "f": False,
        "0": False,
    }[value.casefold()]


def _convert_value(value: str, column: SchemaColumn[object]) -> object:
    if value == "":
        return None

    sql_type = column.type
    if isinstance(sql_type, (LargeBinary, VARBINARY)):
        return _binary_value(value)
    if isinstance(sql_type, Boolean) or (
        isinstance(sql_type, TINYINT) and sql_type.display_width == 1
    ):
        return _boolean_value(value)
    if isinstance(sql_type, Integer):
        return int(value)
    if isinstance(sql_type, Float):
        return float(value)
    if isinstance(sql_type, Numeric):
        return Decimal(value)
    if isinstance(sql_type, DateTime):
        return datetime.fromisoformat(value)
    if isinstance(sql_type, Date):
        return date.fromisoformat(value)
    if isinstance(sql_type, String):
        return value
    raise TypeError(f"Unsupported SQL type for {column.name}: {sql_type}")


def _jdbc_url(source_url: URL, database: Database, host: str) -> str:
    assert source_url.port is not None
    assert source_url.database is not None
    if database == "postgresql":
        return f"jdbc:postgresql://{host}:{source_url.port}/{source_url.database}"
    return (
        f"jdbc:mysql://{host}:{source_url.port}/{source_url.database}"
        "?allowPublicKeyRetrieval=true&useSSL=false&padCharsWithSpace=true"
    )


def rdf_datasets_isomorphic(expected_path: Path, actual_path: Path) -> bool:
    expected = Dataset()
    expected.parse(expected_path, format="nquads")
    actual = Dataset()
    actual.parse(actual_path, format="nquads")
    expected_graphs = {graph_name for _, _, _, graph_name in expected.quads()}
    actual_graphs = {graph_name for _, _, _, graph_name in actual.quads()}
    if expected_graphs != actual_graphs:
        return False

    for graph_name in expected_graphs:
        expected_graph = expected.graph(graph_name)
        actual_graph = actual.graph(graph_name)
        if to_isomorphic(expected_graph) != to_isomorphic(actual_graph):
            return False
    return True


class SouffleConformanceAdapter:
    def __init__(
        self,
        translator_jar: Path,
        reverse_script: Path,
        functor_library: Path,
        execution_mode: ExecutionMode = "docker",
        souffle_executable: str = "souffle",
        log_path: Path | None = None,
    ):
        self.translator_jar = translator_jar.resolve()
        self.reverse_script = reverse_script.resolve()
        self.functor_library = functor_library.resolve()
        self.execution_mode = execution_mode
        self.souffle_executable = souffle_executable
        self.log_path = log_path.resolve() if log_path is not None else None
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.unlink(missing_ok=True)

    def _write_log(self, stage: Stage, command: tuple[str, ...], output: str) -> None:
        if self.log_path is None:
            return
        with self.log_path.open("a", encoding="utf-8") as log:
            log.write(f"[{stage}]\n{shlex.join(command)}\n{output.rstrip()}\n\n")

    def _shared_path(self, shared_directory: Path, filename: str) -> str:
        if self.execution_mode == "docker":
            return f"/data/shared/{filename}"
        return str((shared_directory / filename).resolve())

    def _translator_path(self) -> str:
        if self.execution_mode == "docker":
            return "/souffle/rulegen.jar"
        return str(self.translator_jar)

    def _reverse_script_path(self) -> str:
        if self.execution_mode == "docker":
            return "/souffle/reverseR2RML.py"
        return str(self.reverse_script)

    def _library_directory(self) -> str:
        if self.execution_mode == "docker":
            return "/souffle/lib"
        return str(self.functor_library.parent)

    def _souffle_path(self) -> str:
        if self.execution_mode == "docker":
            return "/souffle/bin/souffle"
        return self.souffle_executable

    def _docker_command(
        self, shared_directory: Path, inner_command: tuple[str, ...]
    ) -> tuple[str, ...]:
        shell_command = shlex.join(inner_command)
        return (
            "docker",
            "run",
            "--rm",
            "--network",
            "host",
            "-v",
            f"{shared_directory.resolve()}:/data/shared",
            "-v",
            f"{self.translator_jar}:/souffle/rulegen.jar:ro",
            "-v",
            f"{self.reverse_script}:/souffle/reverseR2RML.py:ro",
            "-v",
            f"{self.functor_library}:/souffle/lib/libfunctors.so:ro",
            SOUFFLE_IMAGE,
            "bash",
            "-lc",
            shell_command,
        )

    def _run_stage(
        self,
        stage: Stage,
        shared_directory: Path,
        inner_command: tuple[str, ...],
    ) -> CommandResult:
        command = (
            self._docker_command(shared_directory, inner_command)
            if self.execution_mode == "docker"
            else inner_command
        )
        try:
            completed = subprocess.run(command, capture_output=True, text=True)
        except OSError as error:
            raise SouffleConformanceError(stage, command, str(error)) from error

        output = completed.stdout + completed.stderr
        self._write_log(stage, command, output)
        if completed.returncode != 0:
            raise SouffleConformanceError(stage, command, output)
        return CommandResult(command, output)

    def _run_souffle(
        self,
        stage: Stage,
        shared_directory: Path,
        program: str,
    ) -> CommandResult:
        return self._run_stage(
            stage,
            shared_directory,
            (
                self._souffle_path(),
                "-L",
                self._library_directory(),
                self._shared_path(shared_directory, program),
                "-F",
                self._shared_path(shared_directory, ""),
                "-D",
                self._shared_path(shared_directory, ""),
            ),
        )

    @staticmethod
    def _require_files(
        stage: Stage,
        result: CommandResult,
        shared_directory: Path,
        filenames: tuple[str, ...],
        require_nonempty: bool = False,
    ) -> None:
        missing = [
            name for name in filenames if not (shared_directory / name).is_file()
        ]
        if missing:
            detail = f"Missing files: {', '.join(missing)}"
            output = f"{result.output}\n{detail}".strip()
            raise SouffleConformanceError(stage, result.command, output)
        if require_nonempty:
            empty = [
                name
                for name in filenames
                if (shared_directory / name).stat().st_size == 0
            ]
            if empty:
                detail = f"Empty files: {', '.join(empty)}"
                output = f"{result.output}\n{detail}".strip()
                raise SouffleConformanceError(stage, result.command, output)

    def run_forward(
        self,
        mapping_path: Path,
        rdf_path: Path,
        shared_directory: Path,
        source_db_url: str,
        database: Database,
    ) -> None:
        mapping_copy = shared_directory / "mapping.ttl"
        shutil.copyfile(mapping_path, mapping_copy)
        source_url = make_url(source_db_url)
        assert source_url.username is not None
        assert source_url.password is not None
        assert source_url.host is not None

        generation = self._run_stage(
            "forward generation",
            shared_directory,
            (
                "java",
                "-jar",
                self._translator_path(),
                "-m",
                self._shared_path(shared_directory, "mapping.ttl"),
                "-dsn",
                _jdbc_url(source_url, database, source_url.host),
                "-u",
                source_url.username,
                "-p",
                source_url.password,
                "-o",
                self._shared_path(shared_directory, FORWARD_PROGRAM),
                "-bt",
            ),
        )
        self._require_files(
            "forward generation",
            generation,
            shared_directory,
            (FORWARD_PROGRAM,),
            require_nonempty=True,
        )
        execution = self._run_souffle(
            "forward execution",
            shared_directory,
            FORWARD_PROGRAM,
        )
        self._require_files(
            "forward execution", execution, shared_directory, FACT_FILES
        )
        try:
            write_rdf_dataset(shared_directory, rdf_path)
        except OSError as error:
            output = f"{execution.output}\n{error}".strip()
            raise SouffleConformanceError(
                "forward execution", execution.command, output
            ) from error

    def run_backward(
        self,
        shared_directory: Path,
        source_db_url: str,
        destination_db_url: str,
        with_provenance: bool,
    ) -> None:
        generation_command = (
            "python3",
            self._reverse_script_path(),
            self._shared_path(shared_directory, FORWARD_PROGRAM),
            self._shared_path(
                shared_directory,
                FORWARD_PROVENANCE_PROGRAM if with_provenance else REVERSE_PROGRAM,
            ),
            "--mode",
            "forward" if with_provenance else "reverse",
        )
        if with_provenance:
            generation_command += (
                "--with-provenance",
                "--reverse-output",
                self._shared_path(shared_directory, REVERSE_PROGRAM),
            )
        generation_command += (
            "--support-report",
            self._shared_path(shared_directory, SUPPORT_REPORT),
        )
        generation = self._run_stage(
            "inverse generation",
            shared_directory,
            generation_command,
        )
        generated_programs = (REVERSE_PROGRAM, SUPPORT_REPORT)
        if with_provenance:
            generated_programs = (FORWARD_PROVENANCE_PROGRAM, *generated_programs)
        self._require_files(
            "inverse generation",
            generation,
            shared_directory,
            generated_programs,
            require_nonempty=True,
        )

        if with_provenance:
            provenance_execution = self._run_souffle(
                "forward execution",
                shared_directory,
                FORWARD_PROVENANCE_PROGRAM,
            )
            self._require_files(
                "forward execution",
                provenance_execution,
                shared_directory,
                (*FACT_FILES, *PROVENANCE_MARKER_FILES),
            )
        relations = parse_source_relations(shared_directory)

        execution = self._run_souffle(
            "backward execution",
            shared_directory,
            REVERSE_PROGRAM,
        )
        self._require_files(
            "backward execution",
            execution,
            shared_directory,
            tuple(relation.recovered_file for relation in relations),
        )
        try:
            self._load_recovered_relations(
                relations,
                shared_directory,
                source_db_url,
                destination_db_url,
            )
        except (OSError, KeyError, TypeError, ValueError, SQLAlchemyError) as error:
            detail = f"{type(error).__name__}: {error}"
            output = f"{execution.output}\n{detail}".strip()
            raise SouffleConformanceError(
                "backward execution", execution.command, output
            ) from error

    @staticmethod
    def _destination_table(
        source_table: Table,
        destination_metadata: MetaData,
        destination_engine: Engine,
    ) -> Table:
        destination_table = Table(
            source_table.name,
            destination_metadata,
            *(
                Column(column.name, column.type, nullable=True)
                for column in source_table.columns
            ),
        )
        destination_table.create(destination_engine)
        return destination_table

    def _load_recovered_relations(
        self,
        relations: tuple[SourceRelation, ...],
        shared_directory: Path,
        source_db_url: str,
        destination_db_url: str,
    ) -> None:
        source_engine = create_engine(source_db_url)
        destination_engine = create_engine(destination_db_url)
        try:
            source_metadata = MetaData()
            source_metadata.reflect(bind=source_engine)
            source_tables = {
                _normalized_table_name(table.name): table
                for table in source_metadata.tables.values()
            }
            destination_metadata = MetaData()
            destination_tables: dict[str, Table] = {}

            for relation in relations:
                source_table = source_tables[_normalized_table_name(relation.table)]
                if source_table.name not in destination_tables:
                    destination_tables[source_table.name] = self._destination_table(
                        source_table, destination_metadata, destination_engine
                    )
                destination_table = destination_tables[source_table.name]
                source_columns = {
                    _normalized_column_name(column.name): column
                    for column in source_table.columns
                }
                relation_columns = tuple(
                    source_columns[_normalized_column_name(name)]
                    for name in relation.columns
                )
                rows = read_recovered_rows(shared_directory, relation)
                records: list[dict[str, object]] = []
                for row in rows:
                    if len(row) != len(relation_columns):
                        raise ValueError(
                            f"{relation.recovered_file} has {len(row)} fields; "
                            f"expected {len(relation_columns)}"
                        )
                    records.append(
                        {
                            column.name: _convert_value(value, column)
                            for column, value in zip(relation_columns, row, strict=True)
                        }
                    )
                if records:
                    with destination_engine.begin() as connection:
                        connection.execute(destination_table.insert(), records)
        finally:
            source_engine.dispose()
            destination_engine.dispose()

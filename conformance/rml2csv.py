# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
# SPDX-License-Identifier: ISC

import csv
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from rdflib import Graph, Namespace
from sqlalchemy import MetaData, Table, create_engine
from sqlalchemy.exc import DataError

from conformance.morph_ldp import _empty_destination
from conformance.outcome import CaseOutcome, InversionOutcome
from conformance.relational_comparison import compare_destination

REVISION = "18a914d7991d2644619b9cd3a4cc7f8935d5b6c6"
ASSET_HOME = Path(os.environ.get("RML2CSV_HOME", "build/rml2csv/runtime")).resolve()
RR = Namespace("http://www.w3.org/ns/r2rml#")


@dataclass
class RML2CSVEvaluation:
    outcome: CaseOutcome
    executed: bool = False
    imported: bool = False
    errors: list[dict[str, str]] = field(default_factory=list)
    validation_ok: bool = False
    source_equal: bool | None = None
    rdf_equal: bool | None = None

    def record(self) -> dict[str, object]:
        return {
            "outcome": str(self.outcome.outcome),
            "losses": sorted(self.outcome.losses),
            "message": self.outcome.message,
            "executed": self.executed,
            "imported": self.imported,
            "errors": self.errors,
            "validation_ok": self.validation_ok,
            "source_equal": self.source_equal,
            "rdf_equal": self.rdf_equal,
            "source_content": self.outcome.source_content,
            "dest_content": self.outcome.dest_content,
            "prototype_revision": REVISION,
        }


class CSVImportError(ValueError):
    pass


def import_csv(path: Path, mapping_path: str, destination_url: str) -> None:
    graph = Graph().parse(mapping_path, format="turtle")
    tables = {str(value) for value in graph.objects(None, RR.tableName)}
    if len(tables) != 1:
        raise CSVImportError(
            "The output CSV cannot be assigned to a single destination table"
        )
    table = tables.pop()
    table_name = table[1:-1].replace('""', '"') if table.startswith('"') else table
    engine = create_engine(destination_url)
    try:
        with engine.begin() as connection, path.open(newline="") as source:
            table = Table(table_name, MetaData(), autoload_with=connection)
            reader = csv.reader(source, strict=True)
            header = next(reader, None)
            if not header or len(set(header)) != len(header):
                raise CSVImportError(
                    "CSV header is empty or contains duplicate column names"
                )
            unknown = sorted(set(header) - set(table.columns.keys()))
            if unknown:
                raise CSVImportError(
                    f"CSV names columns absent from the destination: {unknown}"
                )
            for row in reader:
                if not row and len(header) == 1:
                    row = [""]
                if len(row) != len(header):
                    raise CSVImportError("CSV row width differs from its header")
                connection.execute(table.insert(), dict(zip(header, row)))
    finally:
        engine.dispose()


def evaluate_rml2csv_case(
    mapping_path: str,
    rdf_path: Path,
    expects_output: bool,
    forward_failed: bool,
    source_url: str,
    destination_url: str,
    directory: Path,
) -> RML2CSVEvaluation:
    if forward_failed:
        return RML2CSVEvaluation(
            CaseOutcome(
                InversionOutcome.ERROR,
                message="Forward output violates the test specification",
            ),
            errors=[
                {
                    "phase": "forward",
                    "component": "rmlmapper",
                    "reason": "Forward conformance failed",
                }
            ],
        )
    if not expects_output:
        return RML2CSVEvaluation(
            CaseOutcome(
                InversionOutcome.ERROR_TEST_CASE,
                message="Specification-negative forward case",
            ),
            validation_ok=True,
        )
    if not rdf_path.is_file():
        rdf_path.touch()
    result = RML2CSVEvaluation(CaseOutcome(InversionOutcome.ERROR), executed=True)
    csv_path = directory / "recovered.csv"
    log_path = directory / "rml2csv.log"
    with log_path.open("w") as log:
        try:
            execution = subprocess.run(
                [
                    str(ASSET_HOME / "java/bin/java"),
                    "-cp",
                    f"{ASSET_HOME}/classes:{ASSET_HOME}/lib/*",
                    "ReverseDataset",
                    str(Path(mapping_path).resolve()),
                    str(rdf_path.resolve()),
                    str(csv_path.resolve()),
                    str((directory / "tdb").resolve()),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            reason = "RML2CSV exceeded 600 seconds; see rml2csv.log"
        else:
            reason = (
                f"RML2CSV exited with code {execution.returncode}; see rml2csv.log"
                if execution.returncode != 0
                else ""
            )
    if not reason and not csv_path.is_file():
        reason = "RML2CSV produced no CSV; see rml2csv.log"
    if reason:
        result.errors.append(
            {"phase": "inversion", "component": "rml2csv", "reason": reason}
        )
    else:
        try:
            _empty_destination(source_url, destination_url)
            import_csv(csv_path, mapping_path, destination_url)
        except (CSVImportError, csv.Error, DataError) as error:
            result.errors.append(
                {"phase": "import", "component": "csv", "reason": str(error)}
            )
        else:
            result.imported = True
    if result.errors:
        result.outcome = CaseOutcome(
            InversionOutcome.ERROR,
            message="; ".join(error["reason"] for error in result.errors),
        )
        return result
    comparison = compare_destination(
        [], mapping_path, rdf_path, source_url, destination_url, directory
    )
    result.outcome = comparison.outcome
    result.source_equal = comparison.source_equal
    result.rdf_equal = comparison.rdf_equal
    result.validation_ok = comparison.validation_ok
    result.errors.extend(comparison.errors)
    return result

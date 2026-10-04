# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
# SPDX-License-Identifier: ISC

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.engine import make_url

from conformance import rmlmapper
from conformance.database import DatabaseConnection
from conformance.morph_ldp import _empty_destination, verify_roundtrip
from conformance.outcome import CaseOutcome, InversionOutcome, logical_table_content
from kgi import NonInvertibleError, UnsupportedMappingError, analyze_mapping
from kgi.comparison import compare_databases, databases_identical

REVISION = "bbcea34f7e3bfb89e0cc184d0d11865ad143df86"
ASSET_HOME = Path(os.environ.get("SPARQLMAP_HOME", "build/sparqlmap/runtime")).resolve()
JAVA = str(ASSET_HOME / "java/bin/java")


@dataclass
class SparqlMapEvaluation:
    outcome: CaseOutcome
    executed: bool = False
    requests: int = 0
    failures: int = 0
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
            "requests": self.requests,
            "failures": self.failures,
            "errors": self.errors,
            "validation_ok": self.validation_ok,
            "source_equal": self.source_equal,
            "rdf_equal": self.rdf_equal,
            "source_content": self.outcome.source_content,
            "dest_content": self.outcome.dest_content,
            "prototype_revision": REVISION,
        }


def _insert(
    mapping_path: str, rdf_path: Path, destination_url: str, directory: Path
) -> subprocess.CompletedProcess[str]:
    url = make_url(destination_url)
    properties = (
        (("useSSL", "false"), ("allowPublicKeyRetrieval", "true"))
        if url.get_backend_name() == "mysql"
        else ()
    )
    dsn, username, password = rmlmapper.sqlalchemy_to_jdbc(destination_url, properties)
    result = subprocess.run(
        [
            JAVA,
            "-cp",
            f"{ASSET_HOME}/classes:{ASSET_HOME}/lib/*",
            "InsertDataset",
            dsn,
            username,
            password,
            str(Path(mapping_path).resolve()),
            str(rdf_path.resolve()),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    (directory / "sparqlmap.log").write_text(result.stdout + result.stderr)
    return result


def evaluate_sparqlmap_case(
    mapping_path: str,
    rdf_path: Path,
    expects_output: bool,
    forward_failed: bool,
    source_url: str,
    destination_url: str,
    directory: Path,
) -> SparqlMapEvaluation:
    produced_rdf = rdf_path.is_file() and rdf_path.stat().st_size > 0
    if (not expects_output and produced_rdf) or forward_failed:
        return SparqlMapEvaluation(
            CaseOutcome(
                InversionOutcome.ERROR,
                message="The forward mapping did not produce the required RDF dataset",
            ),
            errors=[
                {
                    "phase": "forward",
                    "component": "rmlmapper",
                    "reason": "The forward output violates the test specification",
                }
            ],
        )
    if not expects_output:
        return SparqlMapEvaluation(
            CaseOutcome(
                InversionOutcome.ERROR_TEST_CASE,
                message="Specification-negative forward case",
            ),
            validation_ok=True,
        )
    if not rdf_path.is_file():
        rdf_path.touch()
    _empty_destination(source_url, destination_url)
    execution = _insert(mapping_path, rdf_path, destination_url, directory)
    log = execution.stdout
    summary = re.search(r"SPARQLMAP_SUMMARY requests=(\d+) failures=(\d+)", log)
    result = SparqlMapEvaluation(CaseOutcome(InversionOutcome.ERROR), executed=True)
    if summary is not None:
        result.requests = int(summary.group(1))
        result.failures = int(summary.group(2))
    result.errors = [
        {"phase": "inversion", "component": "sparqlmap", "reason": reason}
        for reason in sorted(
            {
                line.split(" ", 2)[2]
                for line in log.splitlines()
                if line.startswith("SPARQLMAP_REQUEST_FAILED ")
            }
        )
    ]
    if execution.returncode != 0 or summary is None:
        result.errors.append(
            {
                "phase": "inversion",
                "component": "sparqlmap",
                "reason": (
                    f"SparqlMap exited with code {execution.returncode}; see sparqlmap.log"
                    if execution.returncode != 0
                    else "SparqlMap did not report an execution summary; see sparqlmap.log"
                ),
            }
        )
    return _compare(
        result, mapping_path, rdf_path, source_url, destination_url, directory
    )


def _compare(
    result: SparqlMapEvaluation,
    mapping_path: str,
    rdf_path: Path,
    source_url: str,
    destination_url: str,
    directory: Path,
) -> SparqlMapEvaluation:
    connection = DatabaseConnection()
    source_content = connection.get_database_content(source_url)
    dest_content = connection.get_database_content(destination_url)
    result.source_equal = databases_identical(source_content, dest_content)
    result.rdf_equal, verification_errors = verify_roundtrip(
        mapping_path, rdf_path, destination_url, directory
    )
    execution_failed = bool(result.errors) or result.failures != 0
    result.errors.extend(verification_errors)
    result.validation_ok = result.rdf_equal is True and not execution_failed
    losses = frozenset()
    message = "Recovered tables differ from the source"
    try:
        analysis = analyze_mapping(mapping_path, rdf_path, source_db_url=source_url)
    except (NonInvertibleError, UnsupportedMappingError) as error:
        message = str(error)
        equal = result.source_equal
    else:
        projection_destination = {
            name: table
            for name, table in logical_table_content(analysis, destination_url).items()
            if name in analysis or table["data"]
        }
        equal, message, losses = compare_databases(
            logical_table_content(analysis, source_url),
            projection_destination,
            analysis,
        )
    if result.source_equal or equal:
        outcome = InversionOutcome.FULLY_INVERTED
        losses = frozenset()
        message = "All source or logical tables were recovered exactly"
    elif losses:
        outcome = InversionOutcome.PARTIALLY_INVERTED
    elif execution_failed:
        outcome = InversionOutcome.ERROR
        message = "; ".join(error["reason"] for error in result.errors)
        losses = frozenset()
    else:
        outcome = InversionOutcome.MISMATCH
    result.outcome = CaseOutcome(outcome, losses, message, source_content, dest_content)
    return result

# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
# SPDX-License-Identifier: ISC

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.engine import make_url

from conformance import rmlmapper
from conformance.morph_ldp import _empty_destination
from conformance.outcome import CaseOutcome, InversionOutcome
from conformance.relational_comparison import compare_destination

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
    comparison = compare_destination(
        result.errors, mapping_path, rdf_path, source_url, destination_url, directory
    )
    result.outcome = comparison.outcome
    result.source_equal = comparison.source_equal
    result.rdf_equal = comparison.rdf_equal
    result.validation_ok = comparison.validation_ok
    result.errors.extend(comparison.errors)
    return result

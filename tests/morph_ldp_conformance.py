# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
# SPDX-License-Identifier: ISC

import csv
import hashlib
import json
import subprocess
from collections import Counter
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from _pytest.terminal import TerminalReporter

import pytest

from conformance.config import is_r2rml_case_available
from conformance.expectations import expected_outcome
from conformance.morph_ldp import evaluate_morph_case, verify_roundtrip
from conformance.outcome import InversionOutcome, describe_difference, evaluate_kgi_case
from conformance.suites import R2RMLTestSuite
from tests.conftest import (
    DATABASE_CONFIGS,
    R2RML_TEST_IDS,
    Database,
    _start_database,
    drop_all_tables,
)


def case_group(test_id: str) -> str:
    if test_id == "R2RMLTC0000":
        return "empty"
    return "inversion" if test_id.startswith("INVTC") else "official"


@pytest.fixture(scope="session")
def morph_database_urls(database: Database) -> Iterator[tuple[str, str]]:
    config = replace(
        DATABASE_CONFIGS[database],
        source_port=15444 if database == "postgresql" else 13309,
        dest_port=15445 if database == "postgresql" else 13310,
    )
    names = [f"kgi-morph-{database}-{role}" for role in ("source", "destination")]
    urls = (config.url(config.source_port), config.url(config.dest_port))
    try:
        for name, port, url in zip(names, (config.source_port, config.dest_port), urls):
            _start_database(name, port, url, config)
        yield urls
    finally:
        for name in names:
            subprocess.run(
                ["docker", "rm", "-f", "-v", name], check=True, capture_output=True
            )


@pytest.fixture(scope="session")
def morph_report_directory(
    database: Database, pytestconfig: pytest.Config
) -> Iterator[Path]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = Path("build/conformance/morph-ldp", stamp, database)
    directory.mkdir(parents=True)
    files = [Path("Makefile"), Path("Dockerfile"), Path(__file__)]
    files.extend(Path("conformance").glob("*.py"))
    files.extend(Path("conformance/morph_ldp_assets").iterdir())
    files.extend(Path("conformance/rmlmapper_assets").iterdir())
    manifest = {
        "revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "database": database,
        "database_image": DATABASE_CONFIGS[database].image,
        "database_image_id": subprocess.check_output(
            [
                "docker",
                "image",
                "inspect",
                DATABASE_CONFIGS[database].image,
                "--format",
                "{{.Id}}",
            ],
            text=True,
        ).strip(),
        "submodules": subprocess.check_output(
            ["git", "submodule", "status"], text=True
        ),
        "files": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(files)
        },
    }
    (directory / "run.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for path in files:
        relative = path.relative_to(Path.cwd()) if path.is_absolute() else path
        snapshot = directory / "harness" / relative
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(path.read_bytes())
    yield directory
    records = []
    for case in sorted(directory.iterdir()):
        if (case / "result.json").is_file():
            result = json.loads((case / "result.json").read_text())
            comparison = case / "comparison.json"
            kgi = (
                json.loads(comparison.read_text())["kgi"]["outcome"]
                if comparison.is_file()
                else "not_evaluated"
            )
            records.append(
                {
                    "test_id": case.name,
                    "suite": case_group(case.name),
                    "morph_ldp": result["outcome"],
                    "kgi": kgi,
                    **{
                        key: result[key] if key in result else None
                        for key in (
                            "attempted",
                            "source_equal",
                            "projection_equal",
                            "rdf_equal",
                            "validation_ok",
                            "errors",
                        )
                    },
                }
            )
    reporter = cast(
        TerminalReporter, pytestconfig.pluginmanager.getplugin("terminalreporter")
    )
    for row in records:
        reporter.write_line(
            f"{row['test_id']}: {row['morph_ldp']}; source={row['source_equal']}; "
            f"projection={row['projection_equal']}; RDF={row['rdf_equal']}; validation={row['validation_ok']}"
        )
    reporter.write_line(f"Morph-LDP artifacts: {directory.resolve()}")
    if records:
        with (directory / "comparison.tsv").open("w") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(records[0]), delimiter="\t")
            writer.writeheader()
            writer.writerows(records)
    (directory / "summary.json").write_text(
        json.dumps(
            {
                "recorded_cases": len(records),
                "excluded_cases": [
                    row["test_id"]
                    for row in records
                    if row["morph_ldp"] == "not_tested"
                ],
                "outcomes": {
                    suite: dict(
                        Counter(
                            row["morph_ldp"] for row in records if row["suite"] == suite
                        )
                    )
                    for suite in ("official", "inversion", "empty")
                },
            },
            indent=2,
        )
        + "\n"
    )


@pytest.mark.parametrize("test_id", R2RML_TEST_IDS)
def test_morph_ldp_conformance(
    test_id: str,
    r2rml_suite: R2RMLTestSuite,
    database: Database,
    morph_database_urls: tuple[str, str],
    morph_report_directory: Path,
) -> None:
    directory = morph_report_directory / test_id
    directory.mkdir()
    if not is_r2rml_case_available(test_id, database):
        (directory / "result.json").write_text(
            json.dumps(
                {
                    "outcome": "not_tested",
                    "message": "PostgreSQL-only official case",
                    "attempted": False,
                }
            )
            + "\n"
        )
        pytest.skip("PostgreSQL-only official case")
    source, destination = morph_database_urls
    result = evaluate_morph_case(
        r2rml_suite, test_id, database, source, destination, directory
    )
    expected = expected_outcome("r2rml", test_id)
    metadata = r2rml_suite.get_test_metadata(test_id)
    assert metadata is not None
    drop_all_tables(destination)
    kgi = evaluate_kgi_case(
        r2rml_suite.get_mapping_path(test_id),
        directory / "output.nq",
        bool(metadata["expected_output"]),
        not result.forward_ok,
        source,
        destination,
    )
    kgi_rdf_equal = None
    kgi_errors = []
    if kgi.outcome in (
        InversionOutcome.FULLY_INVERTED,
        InversionOutcome.PARTIALLY_INVERTED,
    ):
        kgi_directory = directory / "kgi"
        kgi_directory.mkdir()
        kgi_rdf_equal, kgi_errors = verify_roundtrip(
            r2rml_suite.get_mapping_path(test_id),
            directory / "output.nq",
            destination,
            kgi_directory,
        )
    (directory / "comparison.json").write_text(
        json.dumps(
            {
                "test_id": test_id,
                "suite": case_group(test_id),
                "expected": str(expected.outcome) if expected else None,
                "morph_ldp": result.record(),
                "kgi": {
                    "outcome": str(kgi.outcome),
                    "rdf_equal": kgi_rdf_equal,
                    "errors": kgi_errors,
                    "losses": sorted(kgi.losses),
                    "message": kgi.message,
                    "source_tables": kgi.source_content,
                    "recovered_tables": kgi.dest_content,
                },
            },
            indent=2,
            default=str,
        )
        + "\n"
    )
    assert result.forward_ok is True
    print(
        f"{test_id}: source_equal={result.source_equal}, projection_equal={result.projection_equal}, rdf_equal={result.rdf_equal}, errors={result.errors}"
    )
    assert result.outcome == expected, describe_difference(expected, result.outcome)
    assert result.validation_ok is True, result.diagnostics()

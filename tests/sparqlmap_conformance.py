# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
# SPDX-License-Identifier: ISC

import json
import os
import shutil
import subprocess
from collections import Counter
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from conformance.config import is_r2rml_case_available
from conformance.expectations import expected_outcome
from conformance.outcome import describe_difference, forward_conformance_failed
from conformance.sparqlmap import evaluate_sparqlmap_case
from conformance.suites import R2RMLTestSuite
from .conftest import (
    DATABASE_CONFIGS,
    Database,
    R2RML_TEST_IDS,
    _start_database,
    drop_all_tables,
    load_sql_script,
    run_forward_mapping,
)


@pytest.fixture(scope="session")
def sparqlmap_database_urls(database: Database) -> Iterator[tuple[str, str]]:
    config = replace(
        DATABASE_CONFIGS[database],
        source_port=25444 if database == "postgresql" else 23309,
        dest_port=25445 if database == "postgresql" else 23310,
    )
    names = [f"kgi-sparqlmap-{database}-{role}" for role in ("source", "destination")]
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
def reports(database: Database) -> Iterator[Path]:
    directory = Path("build/conformance/sparqlmap") / database
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    yield directory
    reports = [
        json.loads(report.read_text())
        for case in sorted(os.scandir(directory), key=lambda entry: entry.name)
        if case.is_dir()
        for report in [Path(case.path) / "report.json"]
        if report.is_file()
    ]
    summary = {
        "total": len(reports),
        "executed": sum(report["observed"]["executed"] for report in reports),
        "outcomes": dict(Counter(report["observed"]["outcome"] for report in reports)),
        "matches_expectation": sum(report["matches_expectation"] for report in reports),
    }
    (directory / "summary.json").write_text(json.dumps(summary, indent=2))


@pytest.mark.parametrize("test_id", R2RML_TEST_IDS)
def test_sparqlmap_conformance(
    test_id: str,
    r2rml_suite: R2RMLTestSuite,
    database: Database,
    sparqlmap_database_urls: tuple[str, str],
    reports: Path,
) -> None:
    directory = reports / test_id
    directory.mkdir(parents=True, exist_ok=True)
    if not is_r2rml_case_available(test_id, database):
        (directory / "report.json").write_text(
            json.dumps(
                {
                    "observed": {"outcome": "excluded", "executed": False},
                    "matches_expectation": False,
                }
            )
        )
        pytest.skip("R2RML test case runs only with PostgreSQL")
    source, destination = sparqlmap_database_urls
    drop_all_tables(source)
    drop_all_tables(destination)
    load_sql_script(source, r2rml_suite.get_sql_script_path(test_id, database))
    mapping = r2rml_suite.get_mapping_path(test_id)
    shutil.copyfile(mapping, directory / "mapping.ttl")
    rdf = directory / "output.nq"
    rdf.unlink(missing_ok=True)
    exit_code = run_forward_mapping(mapping, str(rdf), source, "r2rml", str(directory))
    metadata = r2rml_suite.get_test_metadata(test_id)
    assert metadata is not None
    expects_output = bool(metadata["expected_output"])
    observed = evaluate_sparqlmap_case(
        mapping,
        rdf,
        expects_output,
        forward_conformance_failed(
            expects_output,
            r2rml_suite.get_expected_output_path(test_id),
            rdf,
            "nquads",
            exit_code,
        ),
        source,
        destination,
        directory,
    )
    expected = expected_outcome("r2rml", test_id)
    assert expected is not None
    (directory / "report.json").write_text(
        json.dumps(
            {
                "observed": observed.record(),
                "expected": {
                    "outcome": str(expected.outcome),
                    "losses": sorted(expected.losses),
                },
                "matches_expectation": observed.outcome == expected,
                "validation_ok": observed.validation_ok,
            },
            indent=2,
            default=str,
        )
    )
    assert observed.outcome == expected, describe_difference(expected, observed.outcome)
    assert observed.validation_ok is True, observed.record()

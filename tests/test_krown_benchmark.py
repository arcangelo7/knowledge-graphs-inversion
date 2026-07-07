# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import csv
import json
from pathlib import Path

import jsonschema
import pandas as pd
import pytest
from sqlalchemy import create_engine, inspect, text

from benchmarks.krown_plots import _humanize_scenario_name
from benchmarks.run_krown_benchmark import (
    KrownBenchmarkRunner,
    expected_outcome,
    generate_scenarios,
)

PROJECT_ROOT = Path(__file__).parent.parent
DATA_GENERATOR_DIR = PROJECT_ROOT / "KROWN" / "data-generator"
CONFIG_FILE = (
    PROJECT_ROOT / "benchmarks" / "krown" / "config" / "kg-inversion-benchmark.json"
)

MAPPINGS_PARAMETERS = [
    (2, 3, 1000, 3, 50),
    (3, 5, 10000, 5, 100),
    (5, 8, 50000, 8, 150),
]


def test_loader_creates_primary_key_for_source_table(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'benchmark.db'}")
    df = pd.DataFrame({"id": [1, 2], "p1": ["a", "b"], "p2": ["c", "d"]})

    KrownBenchmarkRunner.load_source_table_with_id_pk(engine, "data", df)

    inspector = inspect(engine)
    with engine.connect() as conn:
        rows = conn.execute(text('SELECT id, p1, p2 FROM "data" ORDER BY id')).all()

    engine.dispose()

    assert inspector.get_pk_constraint("data")["constrained_columns"] == ["id"]
    assert [tuple(row) for row in rows] == [(1, "a", "c"), (2, "b", "d")]


def test_runner_discovers_scenario_dirs_with_metadata(monkeypatch, tmp_path) -> None:
    _set_benchmark_env(monkeypatch)
    runner = KrownBenchmarkRunner(sparql_backend="pyoxigraph")
    runner.scenarios_root = tmp_path
    scenario_root = tmp_path / "RMLMapper" / "postgresql"

    for scenario_name in ["mappings_1_2", "mappings_2_3"]:
        scenario_path = scenario_root / scenario_name
        scenario_path.mkdir(parents=True)
        (scenario_path / "metadata.json").write_text("{}")
    (scenario_root / "no_metadata").mkdir()

    assert runner.find_krown_scenarios() == [
        scenario_root / "mappings_1_2",
        scenario_root / "mappings_2_3",
    ]


def test_expected_outcome_by_scenario_family() -> None:
    assert expected_outcome("mappings_2_3") == "partial"
    assert expected_outcome("mappings_3_5") == "partial"
    assert expected_outcome("mappings_5_8") == "partial"
    with pytest.raises(ValueError):
        expected_outcome("raw_1000_6_50")
    with pytest.raises(ValueError):
        expected_outcome("mappings_3_3")
    with pytest.raises(ValueError):
        expected_outcome("mappings_3_2")
    with pytest.raises(ValueError):
        expected_outcome("duplicates_25")


def test_outcome_matches_expectation_without_validation(monkeypatch) -> None:
    _set_benchmark_env(monkeypatch)
    runner = KrownBenchmarkRunner(sparql_backend="pyoxigraph")

    completed_partial = {"scenario_name": "mappings_2_3", "status": "completed"}
    failed_partial = {
        "scenario_name": "mappings_2_3",
        "status": "failed",
        "failure_kind": "runtime_error",
    }
    non_invertible_partial = {
        "scenario_name": "mappings_2_3",
        "status": "failed",
        "failure_kind": "non_invertible",
    }

    assert runner.outcome_matches_expectation(completed_partial) is True
    assert runner.outcome_matches_expectation(failed_partial) is False
    assert runner.outcome_matches_expectation(non_invertible_partial) is False


def test_outcome_matches_expectation_with_validation(monkeypatch) -> None:
    _set_benchmark_env(monkeypatch)
    runner = KrownBenchmarkRunner(validate=True, sparql_backend="pyoxigraph")

    partial_with_id_loss = {
        "scenario_name": "mappings_2_3",
        "status": "completed",
        "validation_results": {"validation_passed": True, "lost_columns": ["id"]},
    }
    partial_fully_recovered = {
        "scenario_name": "mappings_2_3",
        "status": "completed",
        "validation_results": {"validation_passed": True, "lost_columns": []},
    }
    partial_validation_failed = {
        "scenario_name": "mappings_2_3",
        "status": "completed",
        "validation_results": {"validation_passed": False, "lost_columns": ["id"]},
    }

    assert runner.outcome_matches_expectation(partial_with_id_loss) is True
    assert runner.outcome_matches_expectation(partial_fully_recovered) is False
    assert runner.outcome_matches_expectation(partial_validation_failed) is False


def test_humanize_scenario_name() -> None:
    assert _humanize_scenario_name("mappings_2_3") == "2 TMs × 3 POMs"
    assert _humanize_scenario_name("mappings_8_5") == "8 TMs × 5 POMs"
    assert _humanize_scenario_name("something_else") == "something_else"


def test_benchmark_config_matches_exgentool_schema() -> None:
    with CONFIG_FILE.open() as f:
        config = json.load(f)
    schema_file = DATA_GENERATOR_DIR / "bench_generator" / "data" / "metadata.schema"
    with schema_file.open() as f:
        schema = json.load(f)

    jsonschema.validate(config, schema)

    mappings_instances = [
        instance
        for instance in config["instances"]
        if instance["generator"] == "Mappings"
    ]
    assert len(config["instances"]) == 3
    assert mappings_instances == config["instances"]

    assert [
        (
            instance["parameters"]["number_of_tms"],
            instance["parameters"]["number_of_poms"],
            instance["parameters"]["number_of_members"],
            instance["parameters"]["number_of_properties"],
            instance["parameters"]["value_size"],
        )
        for instance in mappings_instances
    ] == MAPPINGS_PARAMETERS

    assert {instance["parameters"]["engine"] for instance in config["instances"]} == {
        "RMLMapper"
    }
    assert {
        instance["parameters"]["data_format"] for instance in config["instances"]
    } == {"postgresql"}


def test_exgentool_generates_scenario_layout(tmp_path) -> None:
    config = {
        "@id": "http://example.com/test-config",
        "name": "Test config",
        "description": "Tiny config for the generation integration test",
        "instances": [
            {
                "@id": "http://example.com/test-config#mappings",
                "name": "Mappings tiny",
                "generator": "Mappings",
                "parameters": {
                    "number_of_tms": 1,
                    "number_of_poms": 2,
                    "number_of_members": 2,
                    "number_of_properties": 2,
                    "value_size": 0,
                    "data_format": "postgresql",
                    "engine": "RMLMapper",
                },
            },
        ],
    }
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config))
    scenarios_root = tmp_path / "scenarios"

    generate_scenarios(config_file, scenarios_root, DATA_GENERATOR_DIR)

    mappings_dir = scenarios_root / "RMLMapper" / "postgresql" / "mappings_1_2"
    assert sorted(p.parent for p in scenarios_root.rglob("metadata.json")) == [
        mappings_dir
    ]

    with (mappings_dir / "data" / "shared" / "data.csv").open(newline="") as f:
        rows = list(csv.reader(f))
    assert rows == [
        ["id", "p1", "p2"],
        ["1", "V_1-1", "V_2-1"],
        ["2", "V_1-2", "V_2-2"],
    ]

    with (mappings_dir / "metadata.json").open() as f:
        metadata = json.load(f)
    assert [step["command"] for step in metadata["steps"]] == [
        "load",
        "execute_mapping",
    ]
    assert metadata["steps"][0]["parameters"] == {
        "csv_file": "data.csv",
        "table": "data",
    }
    assert metadata["steps"][1]["parameters"]["mapping_file"] == "mapping.r2rml.ttl"
    assert metadata["steps"][1]["parameters"]["output_file"] == "out.nt"


def _set_benchmark_env(monkeypatch) -> None:
    monkeypatch.setenv("BENCHMARK_DB_HOST", "localhost")
    monkeypatch.setenv("BENCHMARK_DB_PORT", "5432")
    monkeypatch.setenv("BENCHMARK_DB_USER", "root")
    monkeypatch.setenv("BENCHMARK_DB_PASSWORD", "root")
    monkeypatch.setenv("BENCHMARK_DB_NAME", "db")

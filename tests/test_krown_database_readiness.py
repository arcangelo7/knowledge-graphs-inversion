# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import json
import subprocess
from pathlib import Path
from unittest.mock import call, patch

import pytest

from benchmarks.krown_campaigns import PHASES
from benchmarks.krown_catalog import KrownScenario
from benchmarks.run_krown_benchmark import (
    ForwardMeasurement,
    KrownBenchmarkRunner,
    ScenarioOperations,
)


def runner(tmp_path: Path) -> KrownBenchmarkRunner:
    benchmark_runner = object.__new__(KrownBenchmarkRunner)
    benchmark_runner.compose_file = tmp_path / "compose.yml"
    benchmark_runner.session_dir = tmp_path
    return benchmark_runner


def scenario() -> KrownScenario:
    return KrownScenario(
        identifier="benchmark-raw-10-1-0",
        display_name="Raw data",
        generator="RawData",
        parameters={
            "number_of_members": 10,
            "number_of_properties": 1,
            "value_size": 0,
        },
        source_config=None,
        original_data_format=None,
    )


def operations(scenario_path: Path) -> ScenarioOperations:
    scenario_operations = object.__new__(ScenarioOperations)
    scenario_operations.scenario_path = scenario_path
    scenario_operations.source_tables = ()
    return scenario_operations


def completed(
    stdout: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


def test_wait_for_database_returns_when_postgresql_is_healthy(tmp_path: Path) -> None:
    benchmark_runner = runner(tmp_path)

    with (
        patch(
            "benchmarks.run_krown_benchmark.subprocess.run",
            side_effect=[completed("starting\n"), completed("healthy\n")],
        ) as run,
        patch("benchmarks.run_krown_benchmark.time.sleep") as sleep,
    ):
        benchmark_runner.wait_for_database("database_setup", scenario())

    assert run.call_count == 2
    assert sleep.call_args_list == [call(2)]


def test_wait_for_database_timeout_saves_diagnostic(tmp_path: Path) -> None:
    benchmark_runner = runner(tmp_path)
    inspection = json.dumps([{"State": {"Health": {"Status": "unhealthy"}}}])
    responses = [
        completed("starting\n"),
        completed(inspection),
        completed("[]\n"),
        completed("postgres log\n"),
    ]

    with (
        patch("benchmarks.run_krown_benchmark.DATABASE_READINESS_TIMEOUT_SECONDS", 1),
        patch("benchmarks.run_krown_benchmark.time.monotonic", side_effect=[0, 0, 1]),
        patch("benchmarks.run_krown_benchmark.time.sleep"),
        patch("benchmarks.run_krown_benchmark.time.time_ns", return_value=42),
        patch("benchmarks.run_krown_benchmark.subprocess.run", side_effect=responses),
    ):
        with pytest.raises(RuntimeError) as raised:
            benchmark_runner.wait_for_database("inversion", scenario())

    diagnostic_file = tmp_path / "database_diagnostic_raw_10_1_0_inversion_42.json"
    assert str(diagnostic_file) in str(raised.value)
    payload = json.loads(diagnostic_file.read_text(encoding="utf-8"))
    assert payload["stage"] == "inversion"
    assert payload["scenario"] == "raw_10_1_0"
    assert payload["health_check_history"] == {"Status": "unhealthy"}
    assert payload["postgresql_logs"]["stdout"] == "postgres log\n"


def test_stage_command_disables_dependency_startup(tmp_path: Path) -> None:
    benchmark_runner = runner(tmp_path)
    benchmark_runner.project_root = tmp_path

    command = benchmark_runner._stage_command(
        "prepare", scenario(), tmp_path / "case", rdf_file=None
    )

    assert command[4:7] == ["run", "--rm", "--no-deps"]


def test_readiness_precedes_inversion_measurement(tmp_path: Path) -> None:
    benchmark_runner = runner(tmp_path)
    events: list[str] = []
    campaign = PHASES[0].campaigns[0]
    forward = ForwardMeasurement(
        iteration=1,
        rdf_file=tmp_path / "out.nq",
        souffle_directory=tmp_path / "souffle",
        duration=1.0,
        rdf_statements=1,
        run_path=tmp_path / "forward",
        metrics_step=1,
    )

    benchmark_runner.iterations = 3
    benchmark_runner.sample_interval = 0.1

    class Collector:
        def __init__(self, **kwargs: object):
            events.append("measure")

        def stop(self) -> None:
            events.append("stop")

    with (
        patch("benchmarks.run_krown_benchmark.SynchronousCollector", Collector),
        patch("benchmarks.run_krown_benchmark.read_step_duration", return_value=1.0),
        patch("benchmarks.run_krown_benchmark.time.sleep"),
        patch.object(
            benchmark_runner,
            "wait_for_database",
            side_effect=lambda stage, value: events.append("ready"),
        ),
        patch.object(
            benchmark_runner,
            "run_stage",
            side_effect=lambda *args: events.append("stage"),
        ),
        patch.object(
            benchmark_runner, "_backward_directory", return_value=tmp_path / "backward"
        ),
        patch.object(benchmark_runner, "_validate_inversion", return_value={}),
        patch.object(
            benchmark_runner, "_build_result", side_effect=RuntimeError("done")
        ),
    ):
        with pytest.raises(RuntimeError, match="done"):
            benchmark_runner._execute_backward_iteration(
                scenario(), campaign, operations(tmp_path / "case"), 1, forward
            )

    assert events == ["ready", "stage", "measure", "stage", "stop"]


def test_stage_failure_saves_diagnostic_before_raising(tmp_path: Path) -> None:
    benchmark_runner = runner(tmp_path)
    benchmark_runner.project_root = tmp_path
    diagnostic_file = tmp_path / "diagnostic.json"

    with (
        patch(
            "benchmarks.run_krown_benchmark.subprocess.run",
            return_value=completed("failed", returncode=2),
        ),
        patch.object(
            benchmark_runner,
            "_save_database_diagnostic",
            return_value=diagnostic_file,
        ) as save,
    ):
        with pytest.raises(RuntimeError, match=str(diagnostic_file)):
            benchmark_runner.run_stage(
                "prepare", "database_setup", scenario(), tmp_path / "case"
            )

    save.assert_called_once_with("database_setup", "raw_10_1_0")


def test_resume_selects_only_unrecorded_campaigns(tmp_path: Path) -> None:
    benchmark_runner = runner(tmp_path)
    benchmark_runner.measured_runs = {
        "sparql": {"raw_10_1_0": [{"status": "completed"}] * 3},
        "souffle_rdf": {},
        "souffle_provenance": {},
        "souffle_hybrid": {},
    }

    pending = benchmark_runner._pending_campaigns(scenario(), PHASES[0])

    assert tuple(campaign.name for campaign in pending) == ("souffle_rdf",)


def test_resume_preserves_recorded_campaign_artifacts(tmp_path: Path) -> None:
    benchmark_runner = runner(tmp_path)
    case = scenario()
    benchmark_runner.scenarios = (case,)
    benchmark_runner.measured_runs = {
        "sparql": {"raw_10_1_0": [{"status": "completed"}] * 3},
        "souffle_rdf": {},
        "souffle_provenance": {"raw_10_1_0": [{"status": "completed"}] * 3},
        "souffle_hybrid": {"raw_10_1_0": [{"status": "completed"}] * 3},
    }
    forward = benchmark_runner._forward_directory(case, "rdf")
    recorded = benchmark_runner._backward_directory(case, PHASES[0].campaigns[0])
    pending = benchmark_runner._backward_directory(case, PHASES[0].campaigns[1])
    for directory in (forward, recorded, pending):
        directory.mkdir(parents=True)

    with patch.object(benchmark_runner, "_restore_resource_summaries") as restore:
        benchmark_runner._remove_uncheckpointed_scenario_artifacts()

    assert forward.exists() is False
    assert recorded.exists() is True
    assert pending.exists() is False
    restore.assert_called_once()

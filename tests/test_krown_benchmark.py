# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import subprocess
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest
from rich.progress import Progress

from benchmarks import krown_metrics, run_krown_benchmark
from benchmarks.krown_catalog import KrownScenario
from benchmarks.krown_metrics import OfficialRunResult
from benchmarks.run_krown_benchmark import (
    KrownBenchmarkRunner,
    ScenarioExecutionFailure,
    ScenarioOperations,
)


def test_krown_docker_wait_returns_container_exit_code(monkeypatch):
    expected_command = ["docker", "wait", "container-id"]

    def run(command, check, capture_output, text):
        assert command == expected_command
        assert check is True
        assert capture_output is True
        assert text is True
        return subprocess.CompletedProcess(command, 0, stdout="1\n", stderr="")

    class Docker:
        pass

    docker_module = ModuleType("bench_executor.docker")
    setattr(docker_module, "Docker", Docker)

    def import_module(name):
        assert name == "bench_executor.docker"
        return docker_module

    monkeypatch.setattr(krown_metrics.subprocess, "run", run)
    monkeypatch.setattr(krown_metrics.importlib, "import_module", import_module)

    krown_metrics._use_container_exit_status()

    wait = getattr(Docker(), "wait")
    assert wait("container-id") == 1


def test_forward_phase_records_mapper_failure_and_stops_remaining_runs(
    monkeypatch,
    tmp_path,
):
    diagnostic = (
        'Exception in thread "main" java.lang.OutOfMemoryError: '
        "Required array length is too large\n"
    )
    executed_runs: list[int] = []

    class FailingExecutor:
        mapping_step = 2

        def __init__(
            self,
            project_root: Path,
            scenario_path: Path,
            rmlmapper_version: str,
        ):
            self.results_path = scenario_path / "results"

        def run(
            self,
            interval: float,
            run: int,
            checkpoint: bool,
        ) -> OfficialRunResult:
            executed_runs.append(run)
            run_path = self.results_path / f"run_{run}"
            run_path.mkdir(parents=True)
            (run_path / "metrics.csv").write_text(
                "step,timestamp\n2,10.0\n2,49.5\n",
                encoding="utf-8",
            )
            (run_path / "log.txt").write_text(diagnostic, encoding="utf-8")
            return OfficialRunResult(
                False,
                "RMLMapper",
                "Execute mapping",
                diagnostic,
            )

    class Operations:
        def __init__(self, scenario_path: Path):
            self.scenario_path = scenario_path

    monkeypatch.setattr(
        run_krown_benchmark,
        "OfficialKrownExecutor",
        FailingExecutor,
    )
    runner = object.__new__(KrownBenchmarkRunner)
    runner.project_root = tmp_path
    runner.sample_interval = 0.1
    runner.session_dir = tmp_path / "session"
    runner.resource_summaries = {}
    scenario = KrownScenario(
        identifier="raw",
        display_name="Raw",
        generator="RawData",
        parameters={
            "number_of_members": 1,
            "number_of_properties": 1,
            "value_size": 0,
        },
        source_config=None,
        original_data_format=None,
    )
    operations = cast(
        ScenarioOperations,
        Operations(tmp_path / "scenario"),
    )
    progress = Progress()
    task = progress.add_task("test", total=3)

    with pytest.raises(ScenarioExecutionFailure) as raised:
        runner._run_forward_phase(
            scenario,
            operations,
            runs=3,
            phase="forward",
            report_statistics=True,
            progress=progress,
            task=task,
        )

    error = raised.value
    assert executed_runs == [1]
    assert error.stage == "forward_mapping"
    assert error.kind == "out_of_memory"
    assert error.outcome == "OUT_OF_MEMORY"
    assert str(error) == "KROWN Executor RMLMapper step failed"
    assert error.diagnostic == diagnostic
    assert error.elapsed_seconds == 39.5
    assert error.iteration == 1
    preserved_log = (
        runner.session_dir
        / scenario.generated_name
        / "forward"
        / "results"
        / "run_1"
        / "log.txt"
    )
    assert preserved_log.read_text(encoding="utf-8") == diagnostic

# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from benchmarks import run_krown_benchmark as benchmark
from benchmarks.krown_metrics import load_souffle_module
from conformance.souffle_artifacts import FACT_FILES


@pytest.mark.parametrize(
    ("kind", "oom_killed", "logs"),
    [
        ("out_of_memory", True, ["Warning: deprecated option"]),
        ("out_of_memory", False, ["java.lang.OutOfMemoryError: Java heap space"]),
        ("timeout", False, ["Warning: deprecated option"]),
        (None, False, ["Warning: deprecated option"]),
    ],
)
def test_backward_resource_failure(tmp_path, monkeypatch, kind, oom_killed, logs):
    project_root = Path(__file__).resolve().parents[1]
    module = load_souffle_module(project_root, "reverse_souffle")
    resource = module.ReverseSouffle.__new__(module.ReverseSouffle)
    resource._logger = Mock()
    resource._container_id = "container-id"
    resource._souffle_tmp = str(tmp_path / "container-data")
    Path(resource._souffle_tmp).mkdir()
    resource.run = Mock(return_value=True)
    resource._docker = Mock()
    events = []
    state = json.dumps(
        {"OOMKilled": oom_killed, "ExitCode": 143 if kind == "timeout" else 1}
    )

    def docker_command(args, **kwargs):
        events.append(args[1])
        if args[1] == "wait":
            assert kwargs["timeout"] == module.TIMEOUT
            if kind == "timeout":
                raise subprocess.TimeoutExpired(args, module.TIMEOUT)
            return subprocess.CompletedProcess(args, 0, "1\n", "")
        if args[1] == "stop":
            return subprocess.CompletedProcess(args, 0, "container-id\n", "")
        assert args == [
            "docker",
            "inspect",
            "--format",
            "{{json .State}}",
            "container-id",
        ]
        return subprocess.CompletedProcess(args, 0, state + "\n", "")

    resource._docker.logs.side_effect = lambda container: events.append("logs") or logs
    resource._docker.wait.side_effect = lambda container: events.append("joined") or 143
    resource._docker.stop.side_effect = lambda container: (
        events.append("removed") or True
    )
    monkeypatch.setattr(module.subprocess, "run", docker_command)
    monkeypatch.setattr(
        benchmark, "reverse_souffle_resource", lambda root: lambda *args: resource
    )
    monkeypatch.setattr(benchmark, "attach_database_to_krown_network", Mock())
    collector = Mock()
    monkeypatch.setattr(benchmark, "SynchronousCollector", collector)
    monkeypatch.setattr(benchmark, "read_step_duration", lambda *args: 2.5)
    monkeypatch.setattr(benchmark.time, "sleep", Mock())
    monkeypatch.setattr(benchmark, "plot_timing_charts", lambda *args: [])

    scenarios = benchmark.load_scenarios(project_root)[:2]
    forward_directory = tmp_path / "forward"
    forward_directory.mkdir()
    for filename in FACT_FILES:
        (forward_directory / filename).write_text("rdf input\n")
    operations = benchmark.ScenarioOperations.__new__(benchmark.ScenarioOperations)
    operations.scenario = scenarios[0]
    operations.scenario_path = tmp_path / "scenario"
    operations.database = benchmark.HOST_DATABASE
    operations.shared_dir = operations.scenario_path / "data" / "shared"
    operations.shared_dir.mkdir(parents=True)
    (operations.shared_dir / "Datalog_rules.rs").write_text("partial program\n")
    operations._mapping_step = Mock(
        return_value={"parameters": {"mapping_file": "mapping.ttl"}}
    )
    resource._data_path = str(operations.scenario_path / "data")
    resource._reverse_script_container_path = "/workspace-tools/reverseR2RML.py"
    monkeypatch.setattr(benchmark, "ScenarioOperations", Mock(return_value=operations))
    monkeypatch.setattr(
        benchmark, "generate_scenario", Mock(return_value=operations.scenario_path)
    )

    runner = benchmark.KrownBenchmarkRunner.__new__(benchmark.KrownBenchmarkRunner)
    runner.mode = "backward"
    runner.inversion_engine = "souffle"
    runner.souffle_mode = "rdf"
    runner.suites = ("raw",)
    runner.scenarios = scenarios
    runner.measured_runs = {}
    runner.iterations = 3
    runner.sample_interval = 0.1
    runner.scenarios_root = tmp_path
    runner.data_generator_dir = tmp_path
    runner.forward_definition = benchmark.FORWARD_ENGINES["souffle"]
    runner.database = benchmark.HOST_DATABASE
    runner.session_dir = tmp_path
    runner._case_directory = Mock(return_value=tmp_path / "case")
    runner._expected_outcome = Mock(return_value="FULL")
    runner._known_forward_failure = Mock(return_value=None)
    runner.prepare_output_directories = Mock()
    runner._prepare_local_source = Mock()
    runner.run_stage = Mock()
    runner.cleanup = Mock()
    runner._generate_backward_statistics = Mock()
    runner.print_aggregated_summary = Mock()
    runner.save_results = Mock(
        return_value=(tmp_path / "raw.json", tmp_path / "stats.json", {})
    )
    forward = benchmark.ForwardMeasurement(
        1, tmp_path / "out.nq", forward_directory, 1.0, 1, tmp_path, 1
    )
    runner._run_forward_phase = Mock(return_value=[forward])
    execute = runner._execute_backward_iteration
    visited = []

    def backward(scenario, ops, iteration, measurement):
        visited.append((scenario.generated_name, iteration))
        if scenario == scenarios[0]:
            return execute(scenario, ops, iteration, measurement)
        return {"status": "completed"}

    monkeypatch.setattr(runner, "_execute_backward_iteration", backward)
    if kind is None:
        with pytest.raises(
            benchmark.SouffleInversionError,
            match=f"^ReverseSouffle failed for {scenarios[0].generated_name}$",
        ):
            runner.run_benchmark()
        assert visited == [(scenarios[0].generated_name, 1)]
        runner.save_results.assert_not_called()
    else:
        assert runner.run_benchmark() == 1
        assert visited == [
            (scenarios[0].generated_name, 1),
            *[(scenarios[1].generated_name, i) for i in range(1, 4)],
        ]
        scenario = scenarios[0]
        diagnostic = f"Docker state: {state}\n" + "\n".join(logs)
        assert runner.save_results.call_args.args[0] == {
            scenario.generated_name: [
                {
                    "status": "failed",
                    "scenario_name": scenario.generated_name,
                    "display_name": scenario.display_name,
                    "suite": scenario.suite,
                    "generator": scenario.generator,
                    "expected_outcome": "FULL",
                    "parameters": scenario.parameters,
                    "source_configuration": {
                        "identifier": scenario.identifier,
                        "file": scenario.source_config,
                        "overrides": scenario.configuration_overrides,
                    },
                    "execution_time": 2.5,
                    "failure": {
                        "stage": "backward",
                        "kind": kind,
                        "outcome": "TIMEOUT" if kind == "timeout" else "OUT_OF_MEMORY",
                        "message": f"ReverseSouffle failed for {scenario.generated_name}",
                        "diagnostic": diagnostic,
                    },
                    "iteration": 1,
                }
            ],
            scenarios[1].generated_name: [
                {"status": "completed", "iteration": i} for i in range(1, 4)
            ],
        }
        artifacts = tmp_path / "case" / "backward" / "results" / "run_1"
        assert {path.name: path.read_text() for path in artifacts.iterdir()} == {
            **{name: "rdf input\n" for name in FACT_FILES},
            "Datalog_rules.rs": "partial program\n",
        }
    assert resource.failure_kind == kind
    assert events == (
        ["wait", "stop", "joined", "inspect", "logs", "removed"]
        if kind == "timeout"
        else ["wait", "inspect", "logs", "removed"]
    )
    assert Path(resource._souffle_tmp).exists() is False
    collector.return_value.stop.assert_called_once_with()
    runner.cleanup.assert_called_once_with()

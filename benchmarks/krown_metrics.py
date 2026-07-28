# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import csv
import importlib
import importlib.util
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from types import ModuleType
from typing import Callable, Protocol, cast

from benchmarks.forward_engines import ForwardEngineDefinition

MetricValue = int | float | str
KrownCase = dict[str, object]

FRAMEWORK_DIRECTORY = Path("KROWN") / "execution-framework"
FORK_FRAMEWORK_DIRECTORY = Path("KROWN_Extended") / "execution-framework"
RESOURCE_PACKAGE = "bench_executor"


class CollectorProtocol(Protocol):
    _thread: Thread

    def stop(self) -> None: ...


class CollectorFactory(Protocol):
    def __call__(
        self,
        case_name: str,
        results_run_path: str,
        sample_interval: float,
        number_of_steps: int,
        run_id: int,
        directory: str,
        verbose: bool,
    ) -> CollectorProtocol: ...


class StatsProtocol(Protocol):
    def statistics(self) -> bool: ...

    def aggregate(self) -> bool: ...


class StatsFactory(Protocol):
    def __call__(
        self,
        results_path: str,
        number_of_steps: int,
        directory: str,
        verbose: bool,
    ) -> StatsProtocol: ...


class MappingResource(Protocol):
    def execute_mapping(
        self,
        mapping_file: str,
        output_file: str,
        serialization: str,
        rdb_username: str,
        rdb_password: str,
        rdb_host: str,
        rdb_port: int,
        rdb_name: str,
        rdb_type: str,
    ) -> bool: ...


class MappingResourceFactory(Protocol):
    def __call__(
        self,
        data_path: str,
        config_path: str,
        directory: str,
        verbose: bool,
    ) -> MappingResource: ...


class ExecutorProtocol(Protocol):
    def list(self) -> list[KrownCase]: ...

    def run(
        self,
        case: KrownCase,
        interval: float,
        run: int,
        checkpoint: bool,
    ) -> bool: ...

    def stats(self, case: KrownCase) -> bool: ...


class ExecutorFactory(Protocol):
    def __call__(
        self,
        main_directory: str,
        verbose: bool,
        progress_cb: Callable[[str, str, bool], None],
    ) -> ExecutorProtocol: ...


def _add_framework_path(project_root: Path) -> None:
    framework_path = str(project_root / FRAMEWORK_DIRECTORY)
    if framework_path not in sys.path:
        sys.path.insert(0, framework_path)


def resource_config_directory(project_root: Path) -> Path:
    return project_root / FRAMEWORK_DIRECTORY / RESOURCE_PACKAGE / "config"


def load_fork_module(project_root: Path, module_name: str) -> ModuleType:
    _add_framework_path(project_root)
    importlib.import_module(RESOURCE_PACKAGE)
    qualified_name = f"{RESOURCE_PACKAGE}.{module_name}"
    if qualified_name in sys.modules:
        return sys.modules[qualified_name]

    module_file = (
        project_root / FORK_FRAMEWORK_DIRECTORY / RESOURCE_PACKAGE / f"{module_name}.py"
    )
    spec = importlib.util.spec_from_file_location(qualified_name, module_file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified_name] = module
    spec.loader.exec_module(module)
    return module


def load_resource_module(
    project_root: Path,
    definition: ForwardEngineDefinition,
) -> ModuleType:
    if definition.forked:
        module = load_fork_module(project_root, definition.module_name)
    else:
        _add_framework_path(project_root)
        module = importlib.import_module(
            f"{RESOURCE_PACKAGE}.{definition.module_name}",
        )
    setattr(module, "VERSION", definition.version)
    return module


def load_mapping_resource(
    project_root: Path,
    definition: ForwardEngineDefinition,
) -> MappingResourceFactory:
    module = load_resource_module(project_root, definition)
    return cast(MappingResourceFactory, getattr(module, definition.resource))


def _wait_for_container_exit(_docker: object, container_id: str) -> int:
    process = subprocess.run(
        ["docker", "wait", container_id],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(process.stdout.strip())


def _use_container_exit_status() -> None:
    docker_module = importlib.import_module("bench_executor.docker")
    docker_class = getattr(docker_module, "Docker")
    setattr(docker_class, "wait", _wait_for_container_exit)


class SynchronousCollector:
    def __init__(
        self,
        project_root: Path,
        case_name: str,
        run_path: Path,
        sample_interval: float,
        number_of_steps: int,
        run_id: int,
        case_directory: Path,
    ):
        _add_framework_path(project_root)
        collector_module = importlib.import_module("bench_executor.collector")
        collector_class = cast(CollectorFactory, getattr(collector_module, "Collector"))
        self._collector = collector_class(
            case_name,
            str(run_path),
            sample_interval,
            number_of_steps,
            run_id,
            str(case_directory),
            False,
        )

    def stop(self) -> None:
        self._collector.stop()
        self._collector._thread.join()


def read_official_statistics(
    results_path: Path,
    number_of_steps: int,
) -> list[dict[str, MetricValue]]:
    with (results_path / "summary.csv").open(newline="", encoding="utf-8") as file:
        rows = cast(list[dict[str, MetricValue]], list(csv.DictReader(file)))
    if len(rows) != number_of_steps:
        raise RuntimeError(
            f"KROWN summary contains {len(rows)} steps instead of {number_of_steps}"
        )
    return rows


def generate_official_statistics(
    project_root: Path,
    results_path: Path,
    number_of_steps: int,
    case_directory: Path,
) -> list[dict[str, MetricValue]]:
    _add_framework_path(project_root)
    stats_module = importlib.import_module("bench_executor.stats")
    stats_class = cast(StatsFactory, getattr(stats_module, "Stats"))
    statistics = stats_class(
        str(results_path),
        number_of_steps,
        str(case_directory),
        False,
    )
    if not statistics.statistics() or not statistics.aggregate():
        raise RuntimeError("KROWN statistics generation failed")
    return read_official_statistics(results_path, number_of_steps)


def read_step_duration(metrics_file: Path, step: int) -> float:
    with metrics_file.open(newline="", encoding="utf-8") as file:
        timestamps = [
            float(row["timestamp"])
            for row in csv.DictReader(file)
            if int(row["step"]) == step
        ]
    if not timestamps:
        raise RuntimeError(f"KROWN metrics contain no samples for step {step}")
    return timestamps[-1] - timestamps[0]


@dataclass(frozen=True)
class OfficialRunResult:
    success: bool
    failed_resource: str | None
    failed_step: str | None
    diagnostic: str


class OfficialKrownExecutor:
    def __init__(
        self,
        project_root: Path,
        scenario_path: Path,
        definition: ForwardEngineDefinition,
    ):
        _add_framework_path(project_root)
        _use_container_exit_status()
        load_resource_module(project_root, definition)
        executor_module = importlib.import_module("bench_executor.executor")
        executor_class = cast(ExecutorFactory, getattr(executor_module, "Executor"))
        self._failed_resource: str | None = None
        self._failed_step: str | None = None
        self._executor = executor_class(
            str(scenario_path),
            False,
            self._record_progress,
        )
        cases = self._executor.list()
        if len(cases) != 1:
            raise RuntimeError(f"KROWN discovered {len(cases)} cases instead of one")
        self.case = cases[0]
        self.scenario_path = scenario_path
        self.definition = definition

    def _record_progress(
        self,
        resource: str,
        step: str,
        success: bool,
    ) -> None:
        if not success:
            self._failed_resource = resource
            self._failed_step = step

    @property
    def resource(self) -> str:
        return self.definition.resource

    @property
    def steps(self) -> list[dict[str, object]]:
        data = cast(dict[str, object], self.case["data"])
        return cast(list[dict[str, object]], data["steps"])

    @property
    def mapping_step(self) -> int:
        matching = [
            index
            for index, step in enumerate(self.steps, start=1)
            if step["command"] == "execute_mapping"
        ]
        if len(matching) != 1:
            raise RuntimeError("KROWN case must have one execute_mapping step")
        return matching[0]

    @property
    def output_file(self) -> str:
        step = self.steps[self.mapping_step - 1]
        parameters = cast(dict[str, str], step["parameters"])
        return parameters["output_file"]

    @property
    def results_path(self) -> Path:
        return self.scenario_path / "results"

    def run(
        self,
        interval: float,
        run: int,
        checkpoint: bool,
    ) -> OfficialRunResult:
        self._failed_resource = None
        self._failed_step = None
        success = self._executor.run(self.case, interval, run, checkpoint)
        log_file = self.results_path / f"run_{run}" / "log.txt"
        diagnostic = log_file.read_text(encoding="utf-8")
        return OfficialRunResult(
            success,
            self._failed_resource,
            self._failed_step,
            diagnostic,
        )

    def statistics(self) -> list[dict[str, MetricValue]]:
        if not self._executor.stats(self.case):
            raise RuntimeError("KROWN Executor statistics generation failed")
        return read_official_statistics(self.results_path, len(self.steps))

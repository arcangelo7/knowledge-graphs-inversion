# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import csv
import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from typing import Callable, Protocol, cast

MetricValue = int | float | str
KrownCase = dict[str, object]


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
    framework_path = str(project_root / "KROWN" / "execution-framework")
    if framework_path not in sys.path:
        sys.path.insert(0, framework_path)


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


def _convert_csv_value(value: str) -> int | float | str:
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def read_official_statistics(
    results_path: Path,
    number_of_steps: int,
) -> list[dict[str, MetricValue]]:
    expected_files = (
        results_path / "stats.csv",
        results_path / "summary.csv",
        results_path / "aggregated.csv",
    )
    missing_files = [str(path) for path in expected_files if not path.is_file()]
    if missing_files:
        raise RuntimeError(f"KROWN statistics files are missing: {missing_files}")

    with (results_path / "summary.csv").open(newline="", encoding="utf-8") as file:
        rows = [
            {name: _convert_csv_value(value) for name, value in row.items()}
            for row in csv.DictReader(file)
        ]
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
        rmlmapper_version: str,
    ):
        _add_framework_path(project_root)
        rmlmapper_module = importlib.import_module("bench_executor.rmlmapper")
        setattr(rmlmapper_module, "VERSION", rmlmapper_version)
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
    def steps(self) -> list[dict[str, object]]:
        data = self.case["data"]
        if not isinstance(data, dict):
            raise TypeError("Invalid KROWN case data")
        steps = data["steps"]
        if not isinstance(steps, list) or not all(
            isinstance(step, dict) for step in steps
        ):
            raise TypeError("Invalid KROWN case steps")
        return steps

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
        parameters = step["parameters"]
        if not isinstance(parameters, dict):
            raise TypeError("Invalid KROWN mapping parameters")
        return str(parameters["output_file"])

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
        diagnostic = log_file.read_text(encoding="utf-8") if log_file.is_file() else ""
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

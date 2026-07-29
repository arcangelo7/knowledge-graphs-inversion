# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import json
from pathlib import Path

import pytest

from benchmarks.run_krown_benchmark import KrownBenchmarkRunner

MEASURED_SCENARIO = "raw_10000_20_0"


def _write_partial_session(session_dir: Path, **overrides: object) -> Path:
    payload: dict[str, object] = {
        "timestamp": 1785061943,
        "mode": "roundtrip",
        "iterations": 3,
        "sample_interval_seconds": 0.1,
        "forward_engine": "rmlmapper",
        "inversion_engine": "kgi",
        "scenarios": {
            MEASURED_SCENARIO: [
                {
                    "status": "completed",
                    "iteration": iteration,
                    "metrics": {
                        "stages": {
                            "forward": {"step": 2},
                            "backward": {"step": 1},
                        }
                    },
                }
                for iteration in (1, 2, 3)
            ]
        },
        **overrides,
    }
    session_dir.mkdir(parents=True, exist_ok=True)
    partial_file = session_dir / "krown_benchmark_results_partial_1785061943.json"
    partial_file.write_text(json.dumps(payload), encoding="utf-8")
    return partial_file


def _runner(session_dir: Path) -> KrownBenchmarkRunner:
    return KrownBenchmarkRunner(
        mode="roundtrip",
        iterations=3,
        sample_interval=0.1,
        suites=("raw", "mappings", "named-graphs", "joins"),
        scenario_name=None,
        resume_session=session_dir,
    )


def test_resume_adopts_the_session_and_its_measured_runs(tmp_path) -> None:
    session_dir = tmp_path / "krown_1785061943_roundtrip_rmlmapper_kgi"
    _write_partial_session(session_dir)

    runner = _runner(session_dir)

    assert runner.session_dir == session_dir
    assert runner.timestamp == 1785061943
    assert list(runner.measured_runs) == [MEASURED_SCENARIO]
    assert len(runner.measured_runs[MEASURED_SCENARIO]) == 3
    assert MEASURED_SCENARIO in {
        scenario.generated_name for scenario in runner.scenarios
    }


def test_resume_reuses_the_resource_summaries_left_on_disk(tmp_path) -> None:
    session_dir = tmp_path / "krown_1785061943_roundtrip_rmlmapper_kgi"
    _write_partial_session(session_dir)
    for stage, rows in (
        ("forward", (("1", "4.6129"), ("2", "7.4227"))),
        ("backward", (("1", "9.9827"),)),
    ):
        results = session_dir / MEASURED_SCENARIO / stage / "results"
        results.mkdir(parents=True)
        (results / "summary.csv").write_text(
            "name,step,duration\n"
            + "".join(
                f"{MEASURED_SCENARIO},{step},{duration}\n" for step, duration in rows
            ),
            encoding="utf-8",
        )

    runner = _runner(session_dir)
    scenario = next(
        candidate
        for candidate in runner.scenarios
        if candidate.generated_name == MEASURED_SCENARIO
    )
    runner._restore_resource_summaries(
        scenario, runner.measured_runs[MEASURED_SCENARIO]
    )

    assert runner.resource_summaries[MEASURED_SCENARIO] == [
        {"name": MEASURED_SCENARIO, "step": "2", "duration": "7.4227"}
        | {"stage_name": "forward"},
        {"name": MEASURED_SCENARIO, "step": "1", "duration": "9.9827"}
        | {"stage_name": "backward"},
    ]


def test_resume_rejects_a_session_measured_with_other_settings(tmp_path) -> None:
    session_dir = tmp_path / "krown_1785061943_roundtrip_rmlmapper_kgi"
    _write_partial_session(session_dir, iterations=5)

    with pytest.raises(ValueError) as error:
        _runner(session_dir)

    assert str(error.value) == (
        "krown_benchmark_results_partial_1785061943.json was measured with a "
        "different configuration: {'iterations': '5 instead of 3'}"
    )


def test_resume_rejects_measured_scenarios_the_selected_suites_would_drop(
    tmp_path,
) -> None:
    session_dir = tmp_path / "krown_1785061943_roundtrip_rmlmapper_kgi"
    _write_partial_session(session_dir)

    with pytest.raises(ValueError) as error:
        KrownBenchmarkRunner(
            mode="roundtrip",
            iterations=3,
            sample_interval=0.1,
            suites=("joins",),
            scenario_name=None,
            resume_session=session_dir,
        )

    assert str(error.value) == (
        "The session to continue holds scenarios outside the selected suites, "
        f"which the results would drop: {MEASURED_SCENARIO}"
    )

#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import argparse
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def _dictionary(value: object) -> dict:
    if not isinstance(value, dict):
        raise TypeError("Expected a dictionary")
    return value


def _number(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value))


def _timing_points(
    stats_data: dict,
    series: dict,
    metric_name: str,
) -> tuple[list[float], list[float], list[float], list[float]]:
    scenarios = _dictionary(stats_data["scenarios"])
    parameter_name = str(series["parameter"])
    parameter_values = []
    means = []
    lower_errors = []
    upper_errors = []

    for scenario_name in series["scenarios"]:
        scenario = _dictionary(scenarios[scenario_name])
        parameters = _dictionary(scenario["parameters"])
        statistics = _dictionary(scenario["statistics"])
        timing = _dictionary(statistics[metric_name])
        mean = _number(timing["mean"])
        lower = _number(timing["ci_95_lower"])
        upper = _number(timing["ci_95_upper"])
        parameter_values.append(_number(parameters[parameter_name]))
        means.append(mean)
        lower_errors.append(mean - lower)
        upper_errors.append(upper - mean)

    return parameter_values, means, lower_errors, upper_errors


def plot_timing_charts(stats_data: dict, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    plot_files = []
    for series_value in stats_data["series"]:
        series = _dictionary(series_value)
        figure, axis = plt.subplots(figsize=(8, 5))

        for metric_name, label, color, marker in (
            ("rmlmapper_time", "RMLMapper", "#1f77b4", "o"),
            ("inversion_time", "Inversion", "#d62728", "s"),
        ):
            x_values, means, lower_errors, upper_errors = _timing_points(
                stats_data, series, metric_name
            )
            axis.errorbar(
                x_values,
                means,
                yerr=np.array([lower_errors, upper_errors]),
                label=label,
                color=color,
                marker=marker,
                linewidth=2,
                capsize=5,
            )

        axis.set_title(f"KROWN {series['title']}")
        axis.set_xlabel(str(series["parameter_label"]))
        axis.set_ylabel("Time (s), mean with 95% CI")
        axis.grid(axis="y", alpha=0.25)
        axis.legend(loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0)
        figure.tight_layout()

        output_file = output_dir / f"{series['name']}_timing.png"
        figure.savefig(output_file, dpi=300, bbox_inches="tight")
        plt.close(figure)
        plot_files.append(output_file)

    return plot_files


def main() -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Generate KROWN RawData timing plots")
    parser.add_argument("stats_file", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    stats_data = json.loads(args.stats_file.read_text(encoding="utf-8"))
    output_dir = args.output_dir
    if output_dir is None:
        output_dir = args.stats_file.parent
    plot_timing_charts(stats_data, output_dir)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

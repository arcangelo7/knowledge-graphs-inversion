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
) -> tuple[
    list[float | str],
    list[float],
    list[float],
    list[float],
    list[str | None],
]:
    scenarios = _dictionary(stats_data["scenarios"])
    parameter_values = []
    means = []
    lower_errors = []
    upper_errors = []
    failure_labels = []

    for point_data in series["points"]:
        point = _dictionary(point_data)
        scenario_name = str(point["scenario"])
        scenario = _dictionary(scenarios[scenario_name])
        parameter_value = point["value"]
        if isinstance(parameter_value, (int, float)):
            parameter_values.append(float(parameter_value))
        else:
            parameter_values.append(str(parameter_value))

        if scenario["status"] == "failed":
            failure = _dictionary(scenario["failure"])
            means.append(float("nan"))
            lower_errors.append(float("nan"))
            upper_errors.append(float("nan"))
            failure_labels.append(str(failure["outcome"]))
            continue

        statistics = _dictionary(scenario["statistics"])
        timing = _dictionary(statistics[metric_name])
        mean = _number(timing["mean"])
        lower = _number(timing["ci_95_lower"])
        upper = _number(timing["ci_95_upper"])
        means.append(mean)
        lower_errors.append(mean - lower)
        upper_errors.append(upper - mean)
        failure_labels.append(None)

    return parameter_values, means, lower_errors, upper_errors, failure_labels


def plot_timing_charts(stats_data: dict, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    plot_files = []
    for series_value in stats_data["series"]:
        series = _dictionary(series_value)
        figure, axis = plt.subplots(figsize=(8, 5))
        parameter_values: list[float | str] = []
        failure_labels: list[str | None] = []
        x_values: list[float] = []

        for metric_name, label, color, marker in (
            ("rmlmapper_time", "RMLMapper", "#1f77b4", "o"),
            ("inversion_time", "Inversion", "#d62728", "s"),
        ):
            (
                parameter_values,
                means,
                lower_errors,
                upper_errors,
                failure_labels,
            ) = _timing_points(stats_data, series, metric_name)
            x_values = [float(index) for index in range(len(parameter_values))]
            if all(np.isnan(value) for value in means):
                continue
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

        for x_value, failure_label in zip(x_values, failure_labels, strict=True):
            if failure_label is not None:
                axis.text(
                    x_value,
                    0.02,
                    failure_label,
                    color="#b22222",
                    ha="center",
                    va="bottom",
                    rotation=90,
                    transform=axis.get_xaxis_transform(),
                )

        axis.set_title(f"KROWN {series['title']}")
        axis.set_xlabel(str(series["parameter_label"]))
        axis.set_ylabel("Time (s), mean with 95% CI")
        axis.set_xticks(
            x_values,
            [
                f"{value:,.0f}" if isinstance(value, float) else str(value)
                for value in parameter_values
            ],
        )
        axis.grid(axis="y", alpha=0.25)
        handles, labels = axis.get_legend_handles_labels()
        if handles:
            axis.legend(
                handles,
                labels,
                loc="upper left",
                bbox_to_anchor=(1.02, 1),
                borderaxespad=0,
            )
        figure.tight_layout()

        output_file = output_dir / f"{series['name']}_timing.png"
        figure.savefig(output_file, dpi=300, bbox_inches="tight")
        plt.close(figure)
        plot_files.append(output_file)

    return plot_files


def main() -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Generate KROWN timing plots")
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

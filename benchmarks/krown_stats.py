# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from typing import NotRequired, TypedDict

import numpy as np
from scipy import stats


class TimingStatistics(TypedDict):
    mean: float
    median: float
    std: float
    min: float
    max: float
    q1: float
    q3: float
    iqr: float
    ci_95_lower: float
    ci_95_upper: float
    outliers: list[float]
    n: int


class ScenarioMetadata(TypedDict):
    triples_maps_count: int
    predicate_object_maps_count: int
    join_conditions_count: int
    graph_maps_count: int
    mapping_size_bytes: int
    data_size_bytes: int
    rdf_statements: int
    source_rows: int
    source_cells: int


class ScenarioStatistics(TypedDict):
    execution_time: TimingStatistics
    n_runs: int
    metadata: ScenarioMetadata
    forward_time: NotRequired[TimingStatistics]
    inversion_time: NotRequired[TimingStatistics]
    inversion_overhead_percentage: NotRequired[TimingStatistics]
    rows_per_second: NotRequired[TimingStatistics]
    cells_per_second: NotRequired[TimingStatistics]


def calculate_mean_confidence_interval(
    data: np.ndarray, confidence: float = 0.95
) -> tuple[float, float, float]:
    if len(data) == 0:
        raise ValueError("At least one observation is required")

    mean = float(np.mean(data))
    if len(data) == 1:
        return mean, mean, mean

    standard_error = float(stats.sem(data))
    if standard_error == 0 or np.isnan(standard_error):
        return mean, mean, mean

    lower, upper = stats.t.interval(
        confidence,
        len(data) - 1,
        loc=mean,
        scale=standard_error,
    )
    return mean, float(lower), float(upper)


def detect_outliers_iqr(data: np.ndarray) -> list[float]:
    first_quartile = np.percentile(data, 25)
    third_quartile = np.percentile(data, 75)
    interquartile_range = third_quartile - first_quartile
    lower_bound = first_quartile - 1.5 * interquartile_range
    upper_bound = third_quartile + 1.5 * interquartile_range
    return [
        float(value) for value in data if value < lower_bound or value > upper_bound
    ]


def calculate_timing_statistics(values: list[float]) -> TimingStatistics:
    data = np.array(values, dtype=float)
    mean, confidence_low, confidence_high = calculate_mean_confidence_interval(data)
    first_quartile = float(np.percentile(data, 25))
    third_quartile = float(np.percentile(data, 75))

    return {
        "mean": mean,
        "median": float(np.median(data)),
        "std": float(np.std(data, ddof=1)) if len(data) > 1 else 0.0,
        "min": float(np.min(data)),
        "max": float(np.max(data)),
        "q1": first_quartile,
        "q3": third_quartile,
        "iqr": third_quartile - first_quartile,
        "ci_95_lower": confidence_low,
        "ci_95_upper": confidence_high,
        "outliers": detect_outliers_iqr(data),
        "n": len(data),
    }


def aggregate_scenario_statistics(
    runs: list[dict],
) -> ScenarioStatistics:
    timing_breakdowns = [run["timing_breakdown"] for run in runs]
    first_run = runs[0]

    statistics: ScenarioStatistics = {
        "execution_time": calculate_timing_statistics(
            [run["execution_time"] for run in runs]
        ),
        "n_runs": len(runs),
        "metadata": {
            "triples_maps_count": first_run["triples_maps_count"],
            "predicate_object_maps_count": first_run["predicate_object_maps_count"],
            "join_conditions_count": first_run["join_conditions_count"],
            "graph_maps_count": first_run["graph_maps_count"],
            "mapping_size_bytes": first_run["mapping_size_bytes"],
            "data_size_bytes": first_run["data_size_bytes"],
            "rdf_statements": first_run["rdf_statements"],
            "source_rows": first_run["source_rows"],
            "source_cells": first_run["source_cells"],
        },
    }
    for metric_name in (
        "forward_time",
        "inversion_time",
        "inversion_overhead_percentage",
    ):
        if all(metric_name in timing for timing in timing_breakdowns):
            statistics[metric_name] = calculate_timing_statistics(
                [timing[metric_name] for timing in timing_breakdowns]
            )

    return statistics

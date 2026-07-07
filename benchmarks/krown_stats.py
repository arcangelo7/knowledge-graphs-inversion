# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

"""Statistical analysis module for KROWN benchmark results.

This module provides functions to calculate statistical metrics from benchmark runs,
including confidence intervals, quartiles, and outlier detection.
"""

import numpy as np
from scipy import stats
from typing import Dict, List, Tuple, Any


def calculate_mean_confidence_interval(
    data: np.ndarray, confidence: float = 0.95
) -> Tuple[float, float, float]:
    """Calculate mean and its confidence interval using t-Student distribution.

    Args:
        data: Array of numeric values (must have at least 2 elements)
        confidence: Confidence level (default 0.95 for 95% CI)

    Returns:
        Tuple of (mean, ci_lower, ci_upper)
    """
    n = len(data)
    mean_val = float(np.mean(data))

    if n == 1:
        # Single observation: no confidence interval
        return (mean_val, mean_val, mean_val)

    # Calculate 95% CI using t-Student distribution
    ci = stats.t.interval(confidence, n - 1, loc=mean_val, scale=stats.sem(data))
    ci_lower = float(ci[0])
    ci_upper = float(ci[1])

    return (mean_val, ci_lower, ci_upper)


def detect_outliers_iqr(data: np.ndarray) -> List[float]:
    """Detect outliers using the IQR (Interquartile Range) method.

    Outliers are values outside [Q1 - 1.5*IQR, Q3 + 1.5*IQR].

    Args:
        data: Array of numeric values (must have at least 4 elements)

    Returns:
        List of outlier values
    """
    q1 = np.percentile(data, 25)
    q3 = np.percentile(data, 75)
    iqr = q3 - q1

    lower_bound = q1 - 1.5 * iqr
    upper_bound = q3 + 1.5 * iqr

    outliers = [float(x) for x in data if x < lower_bound or x > upper_bound]
    return outliers


def calculate_timing_statistics(values: List[float]) -> Dict[str, Any]:
    """Calculate comprehensive statistics for timing measurements.

    Args:
        values: List of timing values from multiple benchmark runs (must not be empty)

    Returns:
        Dictionary containing:
            - mean: Average value
            - median: Middle value
            - std: Standard deviation
            - min: Minimum value
            - max: Maximum value
            - q1: 25th percentile (first quartile)
            - q3: 75th percentile (third quartile)
            - iqr: Interquartile range (Q3 - Q1)
            - ci_95_lower: Lower bound of 95% confidence interval
            - ci_95_upper: Upper bound of 95% confidence interval
            - outliers: List of outlier values
            - n: Number of observations
    """
    data = np.array(values, dtype=float)
    mean_val, ci_lower, ci_upper = calculate_mean_confidence_interval(data)

    q1 = float(np.percentile(data, 25))
    q3 = float(np.percentile(data, 75))
    iqr = q3 - q1

    outliers = detect_outliers_iqr(data)

    return {
        "mean": mean_val,
        "median": float(np.median(data)),
        "std": float(np.std(data, ddof=1)) if len(data) > 1 else 0.0,
        "min": float(np.min(data)),
        "max": float(np.max(data)),
        "q1": q1,
        "q3": q3,
        "iqr": iqr,
        "ci_95_lower": ci_lower,
        "ci_95_upper": ci_upper,
        "outliers": outliers,
        "n": len(data),
    }


def aggregate_scenario_statistics(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate statistics for a single scenario across multiple runs.

    Args:
        runs: List of result dictionaries from multiple benchmark runs

    Returns:
        Dictionary containing statistics for all timing metrics
    """
    # Extract timing values from all runs
    execution_times = [r["execution_time"] for r in runs]

    timing_breakdown = [r["timing_breakdown"] for r in runs]
    rmlmapper_times = [tb["rmlmapper_time"] for tb in timing_breakdown]
    inversion_times = [tb["inversion_time"] for tb in timing_breakdown]
    overhead_percentages = [
        tb["inversion_overhead_percentage"] for tb in timing_breakdown
    ]

    # Calculate statistics for each metric
    stats = {
        "execution_time": calculate_timing_statistics(execution_times),
        "rmlmapper_time": calculate_timing_statistics(rmlmapper_times),
        "inversion_time": calculate_timing_statistics(inversion_times),
        "inversion_overhead_percentage": calculate_timing_statistics(
            overhead_percentages
        ),
        "n_runs": len(runs),
        "completed_runs": len([r for r in runs if r["status"] == "completed"]),
        "failed_runs": len([r for r in runs if r["status"] == "failed"]),
    }

    # Include metadata from first successful run
    first_success = next((r for r in runs if r["status"] == "completed"), None)
    if first_success:
        stats["metadata"] = {
            "triples_maps_count": first_success["triples_maps_count"],
            "predicate_object_maps_count": first_success["predicate_object_maps_count"],
            "mapping_size_bytes": first_success["mapping_size_bytes"],
            "data_size_bytes": first_success["data_size_bytes"],
        }

    return stats


def get_boxplot_legend_text() -> str:
    """Get standardized box plot elements description text.

    Returns:
        Formatted text explaining box plot elements with line wrapping
    """
    return (
        "Box plot elements:\n"
        "• Box edges: 25th (Q1) and 75th (Q3)\n"
        "  percentiles (interquartile range, IQR)\n"
        "• Blue line: median (50th percentile)\n"
        "• Red diamond: mean\n"
        "• Green bars: 95% confidence interval\n"
        "  for the mean (t-Student)\n"
        "• Whiskers: extend to the most extreme\n"
        "  data point within 1.5×IQR from box edges\n"
        "• Circles: outliers (values beyond\n"
        "  1.5×IQR from box edges)"
    )

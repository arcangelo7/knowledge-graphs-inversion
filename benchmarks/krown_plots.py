#!/usr/bin/env python3
"""Visualization module for KROWN benchmark results.

This script generates publication-quality plots from benchmark statistics,
including box plots with confidence intervals and outlier detection.
"""

import argparse
import json
import re
from pathlib import Path
from typing import Dict, Any
from collections import Counter
import matplotlib.pyplot as plt
import numpy as np


def load_statistics(stats_file: Path) -> Dict[str, Any]:
    """Load aggregated statistics from JSON file.

    Args:
        stats_file: Path to statistics JSON file

    Returns:
        Dictionary with benchmark statistics
    """
    with open(stats_file, 'r') as f:
        return json.load(f)


def _humanize_scenario_name(scenario: str) -> str:
    """Convert scenario name to human-readable format.

    Examples:
        mappings_3_2 -> 3×2
        mappings_5_3 -> 5×3
        mappings_8_5 -> 8×5
    """
    match = re.search(r'(\d+)_(\d+)', scenario)
    if match:
        return f"{match.group(1)}×{match.group(2)}"
    return scenario


def plot_timing_bar_charts(
    stats_data: Dict[str, Any],
    output_dir: Path
):
    """Generate separate box plots with statistics for each timing metric.

    Args:
        stats_data: Dictionary with aggregated statistics
        output_dir: Directory where to save the plots
    """
    scenarios_data = stats_data["scenarios"]
    iterations = stats_data["iterations"]

    # Use actual scenario names from data, sorted alphabetically
    scenarios = sorted(scenarios_data.keys())

    metrics = [
        ("execution_time", "Total execution time", "s"),
        ("morph_kgc_time", "Morph-KGC time", "s"),
        ("inversion_time", "Inversion time", "s"),
        ("inversion_overhead_percentage", "Inversion overhead", "%")
    ]

    for metric_key, metric_label, unit in metrics:
        # Create figure with plot + stats panel layout
        fig = plt.figure(figsize=(16, 6))
        gs = fig.add_gridspec(1, 2, width_ratios=[2, 1], wspace=0.3)

        ax_plot = fig.add_subplot(gs[0, 0])
        ax_stats = fig.add_subplot(gs[0, 1])

        box_data = []
        labels = []
        means = []
        ci_lowers = []
        ci_uppers = []
        stats_lines = [f"{metric_label} statistics", ""]

        for scenario in scenarios:
            scenario_stats = scenarios_data[scenario]
            metric_stats = scenario_stats["statistics"][metric_key]

            if metric_stats["n"] == 0:
                continue

            # Get raw data from completed runs for box plot
            raw_runs = scenario_stats["raw_runs"]
            completed_runs = [r for r in raw_runs if r["status"] == "completed"]
            values = []
            for run in completed_runs:
                if metric_key == "execution_time":
                    values.append(run["execution_time"])
                else:
                    timing = run["timing_breakdown"]
                    values.append(timing[metric_key])

            box_data.append(values)
            human_label = _humanize_scenario_name(scenario)
            labels.append(human_label)
            means.append(metric_stats["mean"])
            ci_lowers.append(metric_stats["ci_95_lower"])
            ci_uppers.append(metric_stats["ci_95_upper"])

            # Add statistics text
            stats_lines.append(f"{human_label}:")
            stats_lines.append(f"  N: {metric_stats['n']}")
            stats_lines.append(f"  Mean: {metric_stats['mean']:.1f} {unit}")
            stats_lines.append(f"  Median: {metric_stats['median']:.1f} {unit}")
            stats_lines.append(f"  Std Dev: {metric_stats['std']:.1f} {unit}")
            stats_lines.append(f"  95% CI: [{metric_stats['ci_95_lower']:.1f}, "
                             f"{metric_stats['ci_95_upper']:.1f}]")
            stats_lines.append(f"  Q1: {metric_stats['q1']:.1f} {unit}")
            stats_lines.append(f"  Q3: {metric_stats['q3']:.1f} {unit}")
            stats_lines.append(f"  Min: {metric_stats['min']:.1f} {unit}")
            stats_lines.append(f"  Max: {metric_stats['max']:.1f} {unit}")
            outliers = metric_stats['outliers']
            if outliers:
                # Group outliers by rounded value, reducing precision if too many unique values
                max_unique = 10

                # Try different precision levels until we get few enough unique values
                for decimals in [1, 0]:
                    rounded_outliers = [round(o, decimals) for o in outliers]
                    outlier_counts = Counter(rounded_outliers)
                    if len(outlier_counts) <= max_unique:
                        break
                else:
                    # If still too many, round to nearest 10
                    rounded_outliers = [round(o / 10) * 10 for o in outliers]
                    outlier_counts = Counter(rounded_outliers)
                    decimals = 0

                # Format output based on precision used
                if decimals == 1:
                    outliers_str = ", ".join([
                        f"{val:.1f} (×{count})" if count > 1 else f"{val:.1f}"
                        for val, count in sorted(outlier_counts.items())
                    ])
                else:
                    outliers_str = ", ".join([
                        f"{int(val)} (×{count})" if count > 1 else f"{int(val)}"
                        for val, count in sorted(outlier_counts.items())
                    ])

                stats_lines.append(f"  Outliers: {outliers_str}")
            stats_lines.append("")

        # Create box plot
        ax_plot.boxplot(
            box_data,
            tick_labels=labels,
            patch_artist=True,
            showmeans=True,
            meanline=False,
            medianprops=dict(color='darkblue', linewidth=2.5),
            meanprops=dict(marker='D', markerfacecolor='red',
                          markeredgecolor='black', markersize=8),
            boxprops=dict(facecolor='lightblue', alpha=0.7),
            whiskerprops=dict(linewidth=1.5),
            capprops=dict(linewidth=1.5),
            flierprops=dict(marker='o', markerfacecolor='none',
                           markeredgecolor='black', markersize=5)
        )

        # Add confidence interval error bars
        x_positions = np.arange(1, len(means) + 1)
        ci_errors = [
            np.array(means) - np.array(ci_lowers),
            np.array(ci_uppers) - np.array(means)
        ]

        ax_plot.errorbar(
            x_positions, means, yerr=ci_errors,
            fmt='none', ecolor='darkgreen', capsize=8, capthick=2,
            linewidth=2.5, alpha=0.8, label='95% CI'
        )

        # Add legend
        ax_plot.plot([], [], color='darkblue', linewidth=2.5, label='Median')
        ax_plot.plot([], [], marker='D', color='red', linestyle='None',
                    markeredgecolor='black', markersize=8, label='Mean')
        ax_plot.plot([], [], marker='o', markerfacecolor='none',
                    markeredgecolor='black', linestyle='None',
                    markersize=5, label='Outliers')
        ax_plot.legend(loc='upper left', frameon=True, fontsize=10)

        ax_plot.set_ylabel(f'{metric_label} ({unit})', fontsize=12)
        ax_plot.set_title(f'{metric_label} distribution', fontsize=13, fontweight='bold')
        ax_plot.set_xlabel('Scenario', fontsize=11)
        ax_plot.grid(True, alpha=0.3, axis='y')
        ax_plot.set_xticklabels(labels, rotation=45, ha='right')

        # Add statistics panel
        ax_stats.axis('off')
        stats_text = "\n".join(stats_lines)
        ax_stats.text(0.05, 0.95, stats_text, transform=ax_stats.transAxes,
                     fontsize=9, verticalalignment='top', fontfamily='monospace')

        # Add overall title
        fig.suptitle(
            f'{metric_label} (N={iterations} iterations)',
            fontsize=15, fontweight='bold'
        )

        fig.tight_layout(rect=[0, 0, 1, 0.96])

        # Save individual plot
        filename = f"{metric_key}_boxplot.png"
        out = output_dir / filename
        fig.savefig(out, dpi=300, bbox_inches='tight', pad_inches=0.3)
        plt.close(fig)
        print(f"Saved: {out}")




def main():
    """Main entry point for plot generation."""
    parser = argparse.ArgumentParser(
        description="Generate plots from KROWN benchmark statistics",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "stats_file",
        type=Path,
        help="Path to statistics JSON file (e.g., krown_benchmark_stats_*.json)"
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for plots (default: same as stats file)"
    )

    args = parser.parse_args()

    if not args.stats_file.exists():
        print(f"Error: Statistics file not found: {args.stats_file}")
        return 1

    output_dir = args.output_dir or args.stats_file.parent
    output_dir.mkdir(exist_ok=True, parents=True)

    # Load statistics
    print(f"Loading statistics from {args.stats_file}")
    stats_data = load_statistics(args.stats_file)

    # Generate plots
    print("Generating timing box plots...")
    plot_timing_bar_charts(stats_data, output_dir)

    print(f"\nPlots saved to {output_dir}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

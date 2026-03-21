#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn, TimeElapsedColumn
from rich.table import Table
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).parent.parent))

# Suppress all console logging - Rich handles console output
logging.disable(logging.CRITICAL)

import rmlmapper
from benchmarks.krown_validator import KrownValidator
from benchmarks.krown_stats import aggregate_scenario_statistics
from kgi.core import reconstruct

from benchmarks.krown_plots import plot_timing_bar_charts

console = Console()


class KrownBenchmarkRunner:

    def __init__(self, use_virtuoso: bool = True, validate: bool = False,
                 cleanup_tables: bool = True, iterations: int = 1):
        self.project_root = Path(__file__).parent.parent
        self.krown_dir = self.project_root / "KROWN"
        self.use_virtuoso = use_virtuoso
        self.validate = validate
        self.cleanup_tables = cleanup_tables
        self.iterations = iterations
        self.validator: KrownValidator | None = None

        self.db_config = {
            'host': os.environ['BENCHMARK_DB_HOST'],
            'port': os.environ['BENCHMARK_DB_PORT'],
            'user': os.environ['BENCHMARK_DB_USER'],
            'password': os.environ['BENCHMARK_DB_PASSWORD'],
            'database': os.environ['BENCHMARK_DB_NAME']
        }

        self.virtuoso_config = {
            'host': 'localhost',
            'port': '8890'
        }

    def get_connection_string(self) -> str:
        return (f"postgresql://{self.db_config['user']}:{self.db_config['password']}@"
                f"{self.db_config['host']}:{self.db_config['port']}/{self.db_config['database']}")

    def run_krown_data_generation(self) -> bool:
        data_generator_dir = self.krown_dir / "data-generator"
        config_file = Path(__file__).parent / "krown" / "config" / "kg-inversion-benchmark.json"

        if not data_generator_dir.exists():
            console.print("[red]KROWN data generator not found[/red]")
            return False

        exgentool_path = data_generator_dir / "exgentool"
        if not exgentool_path.exists():
            try:
                subprocess.run(
                    ["make", "build"],
                    cwd=data_generator_dir,
                    capture_output=True,
                    text=True
                )
            except Exception:
                pass

        cmd = [str(exgentool_path), "generate", f"--scenario={config_file}"]

        try:
            subprocess.run(cmd, cwd=data_generator_dir, capture_output=True, text=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def find_krown_scenarios(self) -> list[Path]:
        krown_data_dir = self.krown_dir / "data-generator" / "Custom" / "postgresql"

        if not krown_data_dir.exists():
            return []

        scenarios = []
        for item in krown_data_dir.iterdir():
            if item.is_dir() and (item / "metadata.json").exists():
                scenarios.append(item)

        return scenarios

    def execute_krown_scenario(self, scenario_path: Path) -> dict:
        scenario_name = scenario_path.name

        metadata_file = scenario_path / "metadata.json"
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)

        shared_dir = scenario_path / "data" / "shared"
        start_time = time.time()

        try:
            self.execute_load_rdb_step(metadata, shared_dir, scenario_name)
            morph_kgc_time = self.execute_forward_mapping_step(metadata, shared_dir)
            inversion_results, inversion_time = self.execute_inversion_step(metadata, shared_dir, scenario_name)

            validation_results = None
            if self.validate:
                validation_results = self.validate_scenario(scenario_name)

            end_time = time.time()
            total_time = end_time - start_time

            inversion_overhead_percentage = (inversion_time / morph_kgc_time * 100) if morph_kgc_time > 0 else 0

            mapping_file = shared_dir / "mapping.r2rml.ttl"
            data_file = shared_dir / "data.csv"

            with open(mapping_file, 'r') as f:
                mapping_content = f.read()

            tm_count = mapping_content.count('rr:TriplesMap')
            pom_count = mapping_content.count('rr:predicateObjectMap')
            inv_count = len(inversion_results) if isinstance(inversion_results, dict) else 0

            return {
                "status": "completed",
                "scenario_name": scenario_name,
                "execution_time": total_time,
                "timing_breakdown": {
                    "morph_kgc_time": morph_kgc_time,
                    "inversion_time": inversion_time,
                    "inversion_overhead_percentage": inversion_overhead_percentage,
                    "total_time": total_time
                },
                "mapping_file": str(mapping_file),
                "data_file": str(data_file),
                "mapping_size_bytes": mapping_file.stat().st_size,
                "data_size_bytes": data_file.stat().st_size,
                "triples_maps_count": tm_count,
                "predicate_object_maps_count": pom_count,
                "inversion_count": inv_count,
                "validation_results": validation_results
            }

        except Exception as e:
            end_time = time.time()
            return {
                "status": "failed",
                "scenario_name": scenario_name,
                "execution_time": end_time - start_time,
                "error": str(e)
            }

    def execute_load_rdb_step(self, metadata: dict, shared_dir: Path,
                              scenario_name: str | None = None) -> None:
        load_step = None
        for step in metadata["steps"]:
            if step["command"] == "load":
                load_step = step
                break

        if not load_step:
            raise ValueError("No load step found in metadata")

        csv_file = shared_dir / load_step["parameters"]["csv_file"]
        table_name = load_step["parameters"]["table"]

        if not csv_file.exists():
            raise FileNotFoundError(f"CSV file not found: {csv_file}")

        conn_string = self.get_connection_string()
        df = pd.read_csv(csv_file)
        engine = create_engine(conn_string)

        if self.validate and scenario_name:
            original_table_name = f"{scenario_name}_original_{table_name}"
            df.to_sql(original_table_name, engine, if_exists='replace', index=False)

        df.to_sql(table_name, engine, if_exists='replace', index=False)
        engine.dispose()

    def execute_forward_mapping_step(self, metadata: dict, shared_dir: Path) -> float:
        start_time = time.time()

        mapping_step = None
        for step in metadata["steps"]:
            if step["command"] == "execute_mapping":
                mapping_step = step
                break

        if not mapping_step:
            raise ValueError("No mapping step found in metadata")

        mapping_file = str(shared_dir / mapping_step["parameters"]["mapping_file"])
        output_file = str(shared_dir / mapping_step["parameters"]["output_file"])

        conn_string = self.get_connection_string()
        sa_url = conn_string.replace("postgresql://", "postgresql+psycopg2://")
        jdbc_dsn, username, password = rmlmapper.sqlalchemy_to_jdbc(sa_url)

        rc = rmlmapper.run(
            mapping_file, output_file,
            dsn=jdbc_dsn, username=username, password=password,
        )

        if rc != 0:
            raise RuntimeError(f"RMLMapper failed with exit code {rc}")

        if not Path(output_file).exists():
            raise FileNotFoundError(f"Expected output file not found: {output_file}")

        return time.time() - start_time

    def execute_inversion_step(self, metadata: dict, shared_dir: Path,
                               scenario_name: str) -> tuple[dict, float]:
        start_time = time.time()

        mapping_step = None
        for step in metadata["steps"]:
            if step["command"] == "execute_mapping":
                mapping_step = step
                break

        if not mapping_step:
            raise ValueError("No mapping step found in metadata for fallback")

        endpoint_url = None
        if self.use_virtuoso:
            endpoint_url = f"http://{self.virtuoso_config['host']}:{self.virtuoso_config['port']}/sparql"

        mapping_file = shared_dir / "mapping.r2rml.ttl"
        rdf_file = shared_dir / "out.nt"
        conn_string = self.get_connection_string()
        source_db_url = conn_string.replace('postgresql://', 'postgresql+psycopg2://')

        original_cwd = os.getcwd()
        try:
            os.chdir(shared_dir)
            inversion_results = reconstruct(
                mapping=str(mapping_file),
                rdf_graph=str(rdf_file),
                source_db_url=source_db_url,
                sparql_endpoint=endpoint_url,
                use_virtuoso=self.use_virtuoso,
                virtuoso_container='kgi-virtuoso',
            )
            return inversion_results, time.time() - start_time
        finally:
            os.chdir(original_cwd)

    def save_results(self, results: list[dict]) -> Path:
        results_dir = Path(__file__).parent / "krown" / "results"
        results_dir.mkdir(exist_ok=True, parents=True)

        timestamp = int(time.time())
        results_file = results_dir / f"krown_benchmark_results_{timestamp}.json"

        benchmark_data = {
            "timestamp": timestamp,
            "benchmark_type": "KROWN",
            "framework": "Knowledge Graph Inversion",
            "environment": "Docker",
            "iterations": self.iterations,
            "total_scenarios": len(results),
            "completed_scenarios": len([r for r in results if r["status"] == "completed"]),
            "failed_scenarios": len([r for r in results if r["status"] == "failed"]),
            "results": results
        }

        with open(results_file, 'w') as f:
            json.dump(benchmark_data, f, indent=2)

        return results_file

    def save_aggregated_results(self, scenario_runs: dict[str, list[dict]]) -> tuple[Path, Path]:
        results_dir = Path(__file__).parent / "krown" / "results"
        results_dir.mkdir(exist_ok=True, parents=True)

        timestamp = int(time.time())

        raw_file = results_dir / f"krown_benchmark_results_raw_{timestamp}.json"
        raw_data = {
            "timestamp": timestamp,
            "benchmark_type": "KROWN",
            "framework": "Knowledge Graph Inversion",
            "environment": "Docker",
            "iterations": self.iterations,
            "scenarios": {
                name: runs for name, runs in scenario_runs.items()
            }
        }

        with open(raw_file, 'w') as f:
            json.dump(raw_data, f, indent=2)

        stats_file = results_dir / f"krown_benchmark_results_stats_{timestamp}.json"
        stats_data = {
            "timestamp": timestamp,
            "benchmark_type": "KROWN",
            "framework": "Knowledge Graph Inversion",
            "environment": "Docker",
            "iterations": self.iterations,
            "scenarios": {}
        }

        for scenario_name, runs in scenario_runs.items():
            completed_runs = [r for r in runs if r["status"] == "completed"]
            if completed_runs:
                stats_data["scenarios"][scenario_name] = {
                    "raw_runs": runs,
                    "statistics": aggregate_scenario_statistics(completed_runs)
                }

        with open(stats_file, 'w') as f:
            json.dump(stats_data, f, indent=2)

        return raw_file, stats_file

    def generate_validation_summary(self, results: list[dict]) -> dict | None:
        if not self.validate:
            return None

        validation_summary: dict = {
            "total_scenarios": 0,
            "passed": 0,
            "failed": 0,
            "scenario_results": []
        }

        for result in results:
            if result["status"] == "completed" and "validation_results" in result:
                validation_summary["total_scenarios"] += 1
                val_result = result["validation_results"]
                validation_summary["scenario_results"].append(val_result)

                if val_result["validation_passed"]:
                    validation_summary["passed"] += 1
                else:
                    validation_summary["failed"] += 1

        if validation_summary["total_scenarios"] > 0:
            validation_summary["validation_rate"] = (
                validation_summary["passed"] / validation_summary["total_scenarios"] * 100
            )
        else:
            validation_summary["validation_rate"] = 0

        return validation_summary

    def print_summary(self, results: list[dict]) -> None:
        completed = [r for r in results if r["status"] == "completed"]
        failed = [r for r in results if r["status"] == "failed"]

        table = Table(title="Benchmark results")
        table.add_column("Scenario")
        table.add_column("Time", justify="right")
        table.add_column("Morph-KGC", justify="right")
        table.add_column("Inversion", justify="right")
        table.add_column("Overhead", justify="right")
        table.add_column("TM/POM/INV", justify="right")
        if self.validate:
            table.add_column("Valid")

        for r in completed:
            t = r["timing_breakdown"]
            val_str = ""
            if self.validate and "validation_results" in r:
                val_str = "PASS" if r["validation_results"]["validation_passed"] else "FAIL"
            row = [
                r["scenario_name"],
                f"{r['execution_time']:.2f}s",
                f"{t['morph_kgc_time']:.2f}s",
                f"{t['inversion_time']:.2f}s",
                f"{t['inversion_overhead_percentage']:.1f}%",
                f"{r['triples_maps_count']}/{r['predicate_object_maps_count']}/{r['inversion_count']}",
            ]
            if self.validate:
                row.append(val_str)
            table.add_row(*row)

        for r in failed:
            row = [r["scenario_name"], f"{r['execution_time']:.2f}s", "[red]FAILED[/red]",
                   "", "", ""]
            if self.validate:
                row.append("")
            table.add_row(*row)

        console.print(table)
        console.print(f"Completed: {len(completed)}/{len(results)}, Failed: {len(failed)}")

    def print_aggregated_summary(self, scenario_runs: dict[str, list[dict]]) -> None:
        table = Table(title=f"Benchmark results ({self.iterations} iterations)")
        table.add_column("Scenario")
        table.add_column("Runs", justify="right")
        table.add_column("Exec time", justify="right")
        table.add_column("Morph-KGC", justify="right")
        table.add_column("Inversion", justify="right")
        table.add_column("Overhead", justify="right")

        for scenario_name, runs in sorted(scenario_runs.items()):
            completed_runs = [r for r in runs if r["status"] == "completed"]

            if completed_runs:
                stats = aggregate_scenario_statistics(completed_runs)
                exec_stats = stats["execution_time"]
                morph_stats = stats["morph_kgc_time"]
                inv_stats = stats["inversion_time"]
                overhead_stats = stats["inversion_overhead_percentage"]

                table.add_row(
                    scenario_name,
                    f"{len(completed_runs)}/{len(runs)}",
                    f"{exec_stats['mean']:.2f}s +/- {exec_stats['std']:.2f}s",
                    f"{morph_stats['mean']:.2f}s +/- {morph_stats['std']:.2f}s",
                    f"{inv_stats['mean']:.2f}s +/- {inv_stats['std']:.2f}s",
                    f"{overhead_stats['mean']:.1f}% +/- {overhead_stats['std']:.1f}%",
                )
            else:
                table.add_row(scenario_name, f"0/{len(runs)}", "[red]ALL FAILED[/red]",
                              "", "", "")

        console.print(table)

    def validate_scenario(self, scenario_name: str) -> dict:
        try:
            original_table = f"{scenario_name}_original_data"
            inverted_table = f"{scenario_name}_data"

            assert self.validator is not None
            return self.validator.validate_inversion(
                original_table=original_table,
                inverted_table=inverted_table,
                scenario_name=scenario_name
            )

        except Exception as e:
            return {
                "validation_passed": False,
                "error": str(e)
            }

    def cleanup(self) -> None:
        if self.cleanup_tables:
            if self.validator:
                self.validator.cleanup_tables()
            self.cleanup_database_tables()

    def cleanup_database_tables(self) -> None:
        conn_string = self.get_connection_string()
        engine = create_engine(conn_string)

        try:
            with engine.connect() as conn:
                result = conn.execute(text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                ))
                tables = [row[0] for row in result]

                for table_name in tables:
                    conn.execute(text(f"DROP TABLE IF EXISTS {table_name} CASCADE"))

                conn.commit()
        except Exception:
            pass
        finally:
            engine.dispose()

    def generate_plots(self, stats_file: Path) -> None:
        with open(stats_file, 'r') as f:
            stats_data = json.load(f)

        plot_timing_bar_charts(stats_data, stats_file.parent)

    def run_benchmark(self) -> int:
        console.print("Starting KROWN benchmark")

        try:
            if self.validate:
                conn_string = self.get_connection_string()
                self.validator = KrownValidator(conn_string)

            if not self.run_krown_data_generation():
                console.print("[yellow]Data generation failed, continuing with existing data[/yellow]")

            scenarios = self.find_krown_scenarios()
            if not scenarios:
                console.print("[red]No scenarios found[/red]")
                return 1

            if self.iterations == 1:
                results = []
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    MofNCompleteColumn(),
                    TimeElapsedColumn(),
                    console=console,
                ) as progress:
                    task = progress.add_task("Scenarios", total=len(scenarios))
                    for scenario_path in scenarios:
                        progress.update(task, description=scenario_path.name)
                        result = self.execute_krown_scenario(scenario_path)
                        results.append(result)
                        progress.advance(task)

                results_file = self.save_results(results)
                self.print_summary(results)

                if self.validate and self.validator:
                    validation_summary = self.generate_validation_summary(results)
                    if validation_summary:
                        validation_file = results_file.parent / f"validation_{results_file.name}"
                        self.validator.save_validation_report(validation_summary, validation_file)
                        self.validator.print_summary(validation_summary)

                console.print(f"Results saved to {results_file}")

            else:
                scenario_runs: dict[str, list[dict]] = {scenario.name: [] for scenario in scenarios}

                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    MofNCompleteColumn(),
                    TimeElapsedColumn(),
                    console=console,
                ) as progress:
                    iter_task = progress.add_task("Iterations", total=self.iterations)

                    for iteration in range(1, self.iterations + 1):
                        progress.update(iter_task, description=f"Iteration {iteration}/{self.iterations}")
                        scenario_task = progress.add_task(
                            f"  Scenarios", total=len(scenarios)
                        )

                        for scenario_path in scenarios:
                            progress.update(scenario_task, description=f"  {scenario_path.name}")
                            result = self.execute_krown_scenario(scenario_path)
                            scenario_runs[scenario_path.name].append(result)
                            progress.advance(scenario_task)

                        progress.remove_task(scenario_task)
                        progress.advance(iter_task)

                raw_file, stats_file = self.save_aggregated_results(scenario_runs)
                self.print_aggregated_summary(scenario_runs)

                console.print(f"Raw results saved to {raw_file}")
                console.print(f"Statistics saved to {stats_file}")

                try:
                    self.generate_plots(stats_file)
                    console.print(f"Plots saved to {stats_file.parent}")
                except Exception:
                    console.print(f"[yellow]Failed to generate plots. Try manually: "
                                  f"uv run python -m benchmarks.krown_plots {stats_file}[/yellow]")

            console.print("Benchmark completed")
            return 0

        except KeyboardInterrupt:
            console.print("[yellow]Benchmark interrupted[/yellow]")
            return 1
        except Exception as e:
            console.print(f"[red]Benchmark failed: {e}[/red]")
            return 1
        finally:
            self.cleanup()


def main():  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="KROWN Benchmark Runner for Knowledge Graph Inversion (Docker)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Docker Usage:
  # Start all services and run benchmark
  docker compose -f docker-compose.benchmark.yml up

  # Run in detached mode
  docker compose -f docker-compose.benchmark.yml up -d

  # View logs
  docker compose -f docker-compose.benchmark.yml logs -f benchmark

  # Stop all services
  docker compose -f docker-compose.benchmark.yml down

  # Clean volumes
  docker compose -f docker-compose.benchmark.yml down -v

Command Line Options (passed to container):
  docker compose -f docker-compose.benchmark.yml run benchmark --no-virtuoso
  docker compose -f docker-compose.benchmark.yml run benchmark --iterations 10

Notes:
  # When using --iterations > 1, plots are automatically generated
  # Results and plots are saved to benchmarks/krown/results/
        """
    )

    parser.add_argument(
        "--no-virtuoso",
        action="store_true",
        help="Disable Virtuoso and use in-memory RDF processing"
    )

    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate that inverted data matches original input data"
    )

    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Keep database tables after benchmark for manual inspection (default: cleanup all tables)"
    )

    parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Number of times to run each scenario (default: 1). Use multiple iterations for statistical analysis."
    )

    args = parser.parse_args()

    runner = KrownBenchmarkRunner(
        use_virtuoso=not args.no_virtuoso,
        validate=args.validate,
        cleanup_tables=not args.no_cleanup,
        iterations=args.iterations
    )
    return runner.run_benchmark()


if __name__ == "__main__":
    sys.exit(main())

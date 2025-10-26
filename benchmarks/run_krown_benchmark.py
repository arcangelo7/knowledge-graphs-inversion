#!/usr/bin/env python3

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from configparser import ConfigParser
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s | %(asctime)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

kgi_logger = logging.getLogger('kgi')
kgi_logger.setLevel(logging.INFO)

from benchmarks.krown_validator import KrownValidator
from benchmarks.krown_stats import aggregate_scenario_statistics
from kgi.core import inversion

from benchmarks.krown_plots import plot_timing_bar_charts


class KrownBenchmarkRunner:
    """KROWN Benchmark Runner for Docker environment."""

    def __init__(self, use_virtuoso=True, validate=False, cleanup_tables=True, iterations=1):
        self.project_root = Path(__file__).parent.parent
        self.krown_dir = self.project_root / "KROWN"
        self.use_virtuoso = use_virtuoso
        self.validate = validate
        self.cleanup_tables = cleanup_tables
        self.iterations = iterations
        self.validator = None
        
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
        
    def get_connection_string(self):
        """Get PostgreSQL connection string."""
        return (f"postgresql://{self.db_config['user']}:{self.db_config['password']}@"
                f"{self.db_config['host']}:{self.db_config['port']}/{self.db_config['database']}")
    
    
    def run_krown_data_generation(self):
        """Generate KROWN benchmark data."""
        data_generator_dir = self.krown_dir / "data-generator"
        config_file = Path(__file__).parent / "krown" / "config" / "kg-inversion-benchmark.json"
        
        if not data_generator_dir.exists():
            logger.error(f"KROWN data generator not found at {data_generator_dir}")
            return False
        
        exgentool_path = data_generator_dir / "exgentool"
        if not exgentool_path.exists():
            try:
                build_result = subprocess.run(
                    ["make", "build"], 
                    cwd=data_generator_dir, 
                    capture_output=True, 
                    text=True
                )
                if build_result.returncode != 0:
                    logger.warning(f"Build failed: {build_result.stderr}")
            except Exception as e:
                logger.warning(f"Build error: {e}")
        
        cmd = [str(exgentool_path), "generate", f"--scenario={config_file}"]
        
        try:
            subprocess.run(cmd, cwd=data_generator_dir, capture_output=True, text=True, check=True)
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Data generation failed: {e}")
            return False
        except FileNotFoundError:
            logger.error(f"exgentool not found at {exgentool_path}")
            return False
    
    def find_krown_scenarios(self):
        """Find generated KROWN scenarios."""
        krown_data_dir = self.krown_dir / "data-generator" / "Custom" / "postgresql"
        
        if not krown_data_dir.exists():
            logger.error(f"KROWN data directory not found: {krown_data_dir}")
            return []
        
        scenarios = []
        for item in krown_data_dir.iterdir():
            if item.is_dir():
                metadata_file = item / "metadata.json"
                if metadata_file.exists():
                    scenarios.append(item)
        
        return scenarios
    
    def execute_krown_scenario(self, scenario_path):
        """Execute a single KROWN scenario using KROWN's execution framework."""
        scenario_name = scenario_path.name

        metadata_file = scenario_path / "metadata.json"
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)

        shared_dir = scenario_path / "data" / "shared"
        start_time = time.time()

        try:
            self.execute_load_rdb_step(metadata, shared_dir, scenario_name)
            morph_kgc_time = self.execute_morph_kgc_step(metadata, shared_dir)
            inversion_results, inversion_time = self.execute_inversion_step(metadata, shared_dir, scenario_name)

            validation_results = None
            if self.validate:
                validation_results = self.validate_scenario(scenario_name)

            end_time = time.time()
            total_time = end_time - start_time

            # Calculate overhead percentage
            inversion_overhead_percentage = (inversion_time / morph_kgc_time * 100) if morph_kgc_time > 0 else 0

            mapping_file = shared_dir / "mapping.r2rml.ttl"
            data_file = shared_dir / "data.csv"

            with open(mapping_file, 'r') as f:
                mapping_content = f.read()

            tm_count = mapping_content.count('rr:TriplesMap')
            pom_count = mapping_content.count('rr:predicateObjectMap')

            # Count inversions instead of saving full results (reduces JSON noise)
            inv_count = len(inversion_results) if isinstance(inversion_results, dict) else 0

            result = {
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

            return result

        except Exception as e:
            end_time = time.time()
            execution_time = end_time - start_time

            logger.error(f"Error executing {scenario_name}: {e}")
            return {
                "status": "failed",
                "scenario_name": scenario_name,
                "execution_time": execution_time,
                "error": str(e)
            }
    
    def execute_load_rdb_step(self, metadata, shared_dir, scenario_name=None):
        """Execute the Load RDB step - load CSV into PostgreSQL."""
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
    
    def execute_morph_kgc_step(self, metadata, shared_dir):
        """Execute Morph-KGC mapping step and return execution time."""
        start_time = time.time()

        mapping_step = None
        for step in metadata["steps"]:
            if step["command"] == "execute_mapping":
                mapping_step = step
                break

        if not mapping_step:
            raise ValueError("No mapping step found in metadata")

        config = ConfigParser()

        config.add_section('CONFIGURATION')
        config.set('CONFIGURATION', 'na_values', ',#N/A,N/A,#N/A N/A,n/a,NA,<NA>,#NA,NULL,null,NaN,nan,None')
        config.set('CONFIGURATION', 'output_file', str(shared_dir / mapping_step["parameters"]["output_file"]))
        config.set('CONFIGURATION', 'output_format', 'N-TRIPLES')
        config.set('CONFIGURATION', 'only_printable_characters', 'no')
        config.set('CONFIGURATION', 'safe_percent_encoding', '')
        config.set('CONFIGURATION', 'mapping_partitioning', 'PARTIAL-AGGREGATIONS')
        config.set('CONFIGURATION', 'infer_sql_datatypes', 'no')
        config.set('CONFIGURATION', 'number_of_processes', '')
        config.set('CONFIGURATION', 'logging_level', 'ERROR')
        config.set('CONFIGURATION', 'logs_file', '')

        config.add_section('DataSource1')
        config.set('DataSource1', 'mappings', str(shared_dir / mapping_step["parameters"]["mapping_file"]))

        conn_string = self.get_connection_string()
        morph_conn = conn_string.replace('postgresql://', 'postgresql+psycopg2://')
        config.set('DataSource1', 'db_url', morph_conn)

        config_file = shared_dir / "morph_kgc_config.ini"
        with open(config_file, 'w') as f:
            config.write(f)

        morph_cmd = ["python3", "-m", "morph_kgc", str(config_file)]

        morph_result = subprocess.run(
            morph_cmd,
            cwd=shared_dir,
            capture_output=True,
            text=True
        )

        if morph_result.returncode != 0:
            raise RuntimeError(f"Morph-KGC failed: {morph_result.stderr}")

        output_file = shared_dir / mapping_step["parameters"]["output_file"]
        if not output_file.exists():
            raise FileNotFoundError(f"Expected output file not found: {output_file}")

        end_time = time.time()
        return end_time - start_time
    
    def execute_inversion_step(self, metadata, shared_dir, scenario_name):
        """Execute our Knowledge Graph Inversion step and return results with execution time."""
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

        inversion_step = {
            "command": "execute_inversion",
            "parameters": {
                "rdf_file": "out.nt",
                "mapping_file": "mapping.r2rml.ttl",
                "rdb_host": self.db_config['host'],
                "rdb_port": self.db_config['port'],
                "rdb_username": self.db_config['user'],
                "rdb_password": self.db_config['password'],
                "rdb_type": "postgresql",
                "rdb_name": self.db_config['database']
            }
        }

        config = ConfigParser()

        config.add_section('CONFIGURATION')
        config.set('CONFIGURATION', 'output_file', str(shared_dir / inversion_step["parameters"]["rdf_file"]))
        config.set('CONFIGURATION', 'output_format', 'N-TRIPLES')

        config.add_section('DataSource1')
        config.set('DataSource1', 'mappings', str(shared_dir / inversion_step["parameters"]["mapping_file"]))

        conn_string = self.get_connection_string()
        morph_conn = conn_string.replace('postgresql://', 'postgresql+psycopg2://')
        config.set('DataSource1', 'db_url', morph_conn)

        inversion_config_file = shared_dir / "inversion_config.ini"
        with open(inversion_config_file, 'w') as f:
            config.write(f)

        original_cwd = os.getcwd()
        try:
            os.chdir(shared_dir)
            inversion_results = inversion(
                inversion_config_file,
                test_id=scenario_name,
                sparql_endpoint=endpoint_url,
                use_virtuoso=self.use_virtuoso,
                virtuoso_container='kgi-virtuoso'
            )
            end_time = time.time()
            return inversion_results, end_time - start_time
        finally:
            os.chdir(original_cwd)
    
    def save_results(self, results):
        """Save benchmark results (raw data only for single iteration)."""
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

    def save_aggregated_results(self, scenario_runs):
        """Save aggregated results with statistics for multiple iterations.

        Args:
            scenario_runs: Dictionary mapping scenario names to list of run results

        Returns:
            Tuple of (raw_file, stats_file) paths
        """
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
    
    def generate_validation_summary(self, results):
        """Generate validation summary from benchmark results."""
        if not self.validate:
            return None
        
        validation_summary = {
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
    
    def print_summary(self, results):
        """Print benchmark summary for single iteration."""
        total = len(results)
        completed = len([r for r in results if r["status"] == "completed"])
        failed = total - completed

        logger.info(f"Total: {total}, Completed: {completed}, Failed: {failed}")

        if completed > 0:
            execution_times = [r["execution_time"] for r in results if r["status"] == "completed"]
            avg_time = sum(execution_times) / len(execution_times)

            # Calculate average timing breakdown
            morph_times = [r["timing_breakdown"]["morph_kgc_time"]
                          for r in results if r["status"] == "completed"]
            inversion_times = [r["timing_breakdown"]["inversion_time"]
                              for r in results if r["status"] == "completed"]
            overhead_percentages = [r["timing_breakdown"]["inversion_overhead_percentage"]
                                   for r in results if r["status"] == "completed"]

            avg_morph = sum(morph_times) / len(morph_times) if morph_times else 0
            avg_inversion = sum(inversion_times) / len(inversion_times) if inversion_times else 0
            avg_overhead = sum(overhead_percentages) / len(overhead_percentages) if overhead_percentages else 0

            logger.info(f"Avg time: {avg_time:.2f}s, Min: {min(execution_times):.2f}s, Max: {max(execution_times):.2f}s")
            logger.info(f"Avg Morph-KGC: {avg_morph:.2f}s, Avg Inversion: {avg_inversion:.2f}s, Avg Overhead: {avg_overhead:.1f}%")

            for result in results:
                if result["status"] == "completed":
                    tm_count = result["triples_maps_count"]
                    pom_count = result["predicate_object_maps_count"]
                    inv_count = result["inversion_count"]
                    val_status = ""
                    if self.validate and "validation_results" in result:
                        val_passed = result["validation_results"]["validation_passed"]
                        val_status = " [PASS]" if val_passed else " [FAIL]"

                    timing = result["timing_breakdown"]
                    morph_time = timing["morph_kgc_time"]
                    inv_time = timing["inversion_time"]
                    overhead = timing["inversion_overhead_percentage"]

                    logger.info(f"  {result['scenario_name']}: {result['execution_time']:.2f}s "
                          f"[TM:{tm_count}, POM:{pom_count}, INV:{inv_count}] "
                          f"Morph:{morph_time:.2f}s, Inv:{inv_time:.2f}s, OH:{overhead:.1f}%{val_status}")

        if failed > 0:
            for result in results:
                if result["status"] == "failed":
                    error = result["error"]
                    logger.error(f"  {result['scenario_name']}: {error}")

    def print_aggregated_summary(self, scenario_runs):
        """Print summary for multiple iterations with statistics."""
        logger.info(f"Benchmark completed with {self.iterations} iterations per scenario")
        logger.info("")

        for scenario_name, runs in sorted(scenario_runs.items()):
            completed_runs = [r for r in runs if r["status"] == "completed"]
            failed_runs = [r for r in runs if r["status"] == "failed"]

            logger.info(f"Scenario: {scenario_name}")
            logger.info(f"  Completed: {len(completed_runs)}/{len(runs)}, Failed: {len(failed_runs)}")

            if completed_runs:
                stats = aggregate_scenario_statistics(completed_runs)

                # Print execution time statistics
                exec_stats = stats["execution_time"]
                logger.info(f"  Execution time: {exec_stats['mean']:.2f}s ± {exec_stats['std']:.2f}s "
                          f"(median: {exec_stats['median']:.2f}s, "
                          f"95% CI: [{exec_stats['ci_95_lower']:.2f}, {exec_stats['ci_95_upper']:.2f}])")

                # Print timing breakdown
                morph_stats = stats["morph_kgc_time"]
                inv_stats = stats["inversion_time"]
                overhead_stats = stats["inversion_overhead_percentage"]

                logger.info(f"  Morph-KGC: {morph_stats['mean']:.2f}s ± {morph_stats['std']:.2f}s")
                logger.info(f"  Inversion: {inv_stats['mean']:.2f}s ± {inv_stats['std']:.2f}s")
                logger.info(f"  Overhead: {overhead_stats['mean']:.1f}% ± {overhead_stats['std']:.1f}%")

                # Print outliers if any
                outliers = exec_stats['outliers']
                if outliers:
                    outliers_str = ", ".join([f"{o:.2f}s" for o in outliers])
                    logger.info(f"  Outliers detected: {outliers_str}")

            logger.info("")
    
    def validate_scenario(self, scenario_name: str) -> dict:
        """Validate inversion results for a scenario."""
        try:
            original_table = f"{scenario_name}_original_data"
            inverted_table = f"{scenario_name}_data"
            
            result = self.validator.validate_inversion(
                original_table=original_table,
                inverted_table=inverted_table,
                scenario_name=scenario_name
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Validation error: {e}")
            return {
                "validation_passed": False,
                "error": str(e)
            }
    
    def cleanup(self):
        """Cleanup resources."""
        if self.cleanup_tables:
            if self.validator:
                self.validator.cleanup_tables()
            self.cleanup_database_tables()
        else:
            logger.info("Keeping tables for inspection")
    
    def cleanup_database_tables(self):
        """Cleanup all database tables."""
        conn_string = self.get_connection_string()
        engine = create_engine(conn_string)

        try:
            with engine.connect() as conn:
                result = conn.execute(text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                ))
                tables = [row[0] for row in result]

                for table in tables:
                    conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))

                conn.commit()
        except Exception as e:
            logger.error(f"Error cleaning up tables: {e}")
        finally:
            engine.dispose()

    def generate_plots(self, stats_file: Path):
        """Generate plots from statistics file.

        Args:
            stats_file: Path to statistics JSON file
        """
        with open(stats_file, 'r') as f:
            stats_data = json.load(f)

        output_dir = stats_file.parent

        plot_timing_bar_charts(stats_data, output_dir)

    def run_benchmark(self):
        """Run the complete KROWN benchmark."""
        logger.info("Starting KROWN benchmark")
        logger.info(f"Running {self.iterations} iteration(s) per scenario")

        try:
            if self.validate:
                conn_string = self.get_connection_string()
                self.validator = KrownValidator(conn_string, verbose=False)

            if not self.run_krown_data_generation():
                logger.warning("Data generation failed, continuing with existing data")

            scenarios = self.find_krown_scenarios()
            if not scenarios:
                logger.error("No scenarios found")
                return 1

            if self.iterations == 1:
                # Single iteration: use original behavior
                results = []
                for i, scenario_path in enumerate(scenarios, 1):
                    logger.info(f"[{i}/{len(scenarios)}] {scenario_path.name}")
                    result = self.execute_krown_scenario(scenario_path)
                    results.append(result)

                results_file = self.save_results(results)
                self.print_summary(results)

                if self.validate:
                    validation_summary = self.generate_validation_summary(results)
                    if validation_summary:
                        validation_file = results_file.parent / f"validation_{results_file.name}"
                        self.validator.save_validation_report(validation_summary, validation_file)
                        self.validator.print_summary(validation_summary)

                logger.info(f"Results saved to {results_file}")

            else:
                # Multiple iterations: collect all runs per scenario
                scenario_runs = {scenario.name: [] for scenario in scenarios}

                for iteration in range(1, self.iterations + 1):
                    logger.info(f"\n{'='*60}")
                    logger.info(f"Iteration {iteration}/{self.iterations}")
                    logger.info(f"{'='*60}")

                    for i, scenario_path in enumerate(scenarios, 1):
                        logger.info(f"[{i}/{len(scenarios)}] {scenario_path.name}")
                        result = self.execute_krown_scenario(scenario_path)
                        scenario_runs[scenario_path.name].append(result)

                # Save aggregated results with statistics
                raw_file, stats_file = self.save_aggregated_results(scenario_runs)
                self.print_aggregated_summary(scenario_runs)

                logger.info(f"\nRaw results saved to {raw_file}")
                logger.info(f"Statistics saved to {stats_file}")

                logger.info("\nGenerating plots...")
                
                try:
                    self.generate_plots(stats_file)
                    logger.info(f"Plots saved to {stats_file.parent}")
                except Exception as e:
                    logger.error(f"Failed to generate plots: {e}")
                    logger.info(f"You can try manually: uv run python -m benchmarks.krown_plots {stats_file}")

            logger.info("Benchmark completed")
            return 0

        except KeyboardInterrupt:
            logger.warning("Benchmark interrupted")
            return 1
        except Exception as e:
            logger.error(f"Benchmark failed: {e}")
            import traceback
            traceback.print_exc()
            return 1
        finally:
            self.cleanup()

def main():
    """Main entry point."""
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

    use_virtuoso = not args.no_virtuoso

    runner = KrownBenchmarkRunner(
        use_virtuoso=use_virtuoso,
        validate=args.validate,
        cleanup_tables=not args.no_cleanup,
        iterations=args.iterations
    )
    return runner.run_benchmark()

if __name__ == "__main__":
    sys.exit(main())
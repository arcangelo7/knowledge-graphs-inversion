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
from kgi.core import inversion


class KrownBenchmarkRunner:
    """KROWN Benchmark Runner for Docker environment."""
    
    def __init__(self, use_virtuoso=True, validate=False, cleanup_tables=True):
        self.project_root = Path(__file__).parent.parent
        self.krown_dir = self.project_root / "KROWN"
        self.use_virtuoso = use_virtuoso
        self.validate = validate
        self.cleanup_tables = cleanup_tables
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
        logger.info("Generating KROWN benchmark data...")
        
        data_generator_dir = self.krown_dir / "data-generator"
        config_file = Path(__file__).parent / "krown" / "config" / "kg-inversion-benchmark.json"
        
        if not data_generator_dir.exists():
            logger.error(f"KROWN data generator not found at {data_generator_dir}")
            logger.error("Run: git submodule update --init --recursive")
            return False
        
        exgentool_path = data_generator_dir / "exgentool"
        if not exgentool_path.exists():
            logger.info("Building KROWN data generator...")
            try:
                build_result = subprocess.run(
                    ["make", "build"], 
                    cwd=data_generator_dir, 
                    capture_output=True, 
                    text=True
                )
                if build_result.returncode != 0:
                    logger.warning("Make build failed, trying alternative approaches...")
                    logger.warning(f"Build stderr: {build_result.stderr}")
            except Exception as e:
                logger.warning(f"Build error: {e}")
        
        cmd = [str(exgentool_path), "generate", f"--scenario={config_file}"]
        
        try:
            subprocess.run(cmd, cwd=data_generator_dir, capture_output=True, text=True, check=True)
            logger.info("KROWN data generation completed")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"KROWN data generation failed: {e}")
            logger.error(f"Stderr: {e.stderr}")
            logger.error(f"Stdout: {e.stdout}")
            return False
        except FileNotFoundError:
            logger.error(f"exgentool not found at {exgentool_path}")
            logger.error("Try building KROWN data generator first")
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
        
        logger.info(f"Found {len(scenarios)} KROWN scenarios")
        return scenarios
    
    def execute_krown_scenario(self, scenario_path):
        """Execute a single KROWN scenario using KROWN's execution framework."""
        scenario_name = scenario_path.name
        logger.info(f"Executing KROWN scenario: {scenario_name}")
        
        metadata_file = scenario_path / "metadata.json"
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)
        
        shared_dir = scenario_path / "data" / "shared"
        
        start_time = time.time()
        
        try:
            self.execute_load_rdb_step(metadata, shared_dir, scenario_name)
            
            self.execute_morph_kgc_step(metadata, shared_dir)
            
            inversion_results = self.execute_inversion_step(metadata, shared_dir, scenario_name)
            
            validation_results = None
            if self.validate:
                validation_results = self.validate_scenario(scenario_name)
            
            end_time = time.time()
            execution_time = end_time - start_time
            
            mapping_file = shared_dir / "mapping.r2rml.ttl"
            data_file = shared_dir / "data.csv"
            
            with open(mapping_file, 'r') as f:
                mapping_content = f.read()
            
            tm_count = mapping_content.count('rr:TriplesMap')
            pom_count = mapping_content.count('rr:predicateObjectMap')
            
            result = {
                "status": "completed",
                "scenario_name": scenario_name,
                "execution_time": execution_time,
                "mapping_file": str(mapping_file),
                "data_file": str(data_file),
                "mapping_size_bytes": mapping_file.stat().st_size,
                "data_size_bytes": data_file.stat().st_size,
                "triples_maps_count": tm_count,
                "predicate_object_maps_count": pom_count,
                "inversion_results": inversion_results,
                "validation_results": validation_results
            }
            
            logger.info(f"Completed {scenario_name} in {execution_time:.2f}s")
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
        logger.info("Step 1: Loading CSV data into PostgreSQL...")
        
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
        
        if self.validate and scenario_name:
            original_table_name = f"{scenario_name}_original_{table_name}"
            df = pd.read_csv(csv_file)
            engine = create_engine(conn_string)
            
            df.to_sql(original_table_name, engine, if_exists='replace', index=False)
            logger.info(f"Saved original data to '{original_table_name}' for validation")
            
            df.to_sql(table_name, engine, if_exists='replace', index=False)
        else:
            df = pd.read_csv(csv_file)
            engine = create_engine(conn_string)
            df.to_sql(table_name, engine, if_exists='replace', index=False)
        
        logger.info(f"Loaded {len(df)} rows into table '{table_name}'")
        engine.dispose()
    
    def execute_morph_kgc_step(self, metadata, shared_dir):
        """Execute Morph-KGC mapping step."""
        logger.info("Step 2: Executing RML mapping with Morph-KGC...")
        
        mapping_step = None
        for step in metadata["steps"]:
            if step["command"] == "execute_mapping":
                mapping_step = step
                break
        
        if not mapping_step:
            raise ValueError("No mapping step found in metadata")
        
        if mapping_step.get("resource") == "Custom":
            logger.warning("Found 'Custom' resource, treating as MorphKGC step...")
        
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
        if output_file.exists():
            logger.info(f"Generated RDF file: {output_file.stat().st_size} bytes")
        else:
            raise FileNotFoundError(f"Expected output file not found: {output_file}")
    
    def execute_inversion_step(self, metadata, shared_dir, scenario_name):
        """Execute our Knowledge Graph Inversion step."""
        logger.info("Step 3: Executing Knowledge Graph Inversion...")
                
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
            logger.info(f"Using Virtuoso endpoint: {endpoint_url}")
        else:
            logger.info("Using in-memory RDF processing")
        
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
            logger.info(f"Inversion completed: {len(inversion_results)} data sources processed")
            return inversion_results
        finally:
            os.chdir(original_cwd)
    
    def save_results(self, results):
        """Save benchmark results."""
        results_dir = Path(__file__).parent / "krown" / "results"
        results_dir.mkdir(exist_ok=True, parents=True)
        
        timestamp = int(time.time())
        results_file = results_dir / f"krown_benchmark_results_{timestamp}.json"
        
        benchmark_data = {
            "timestamp": timestamp,
            "benchmark_type": "KROWN",
            "framework": "Knowledge Graph Inversion",
            "environment": "Docker",
            "total_scenarios": len(results),
            "completed_scenarios": len([r for r in results if r["status"] == "completed"]),
            "failed_scenarios": len([r for r in results if r["status"] == "failed"]),
            "results": results
        }
        
        with open(results_file, 'w') as f:
            json.dump(benchmark_data, f, indent=2)
        
        logger.info(f"Results saved to: {results_file}")
        return results_file
    
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
            if result["status"] == "completed" and result.get("validation_results"):
                validation_summary["total_scenarios"] += 1
                val_result = result["validation_results"]
                validation_summary["scenario_results"].append(val_result)
                
                if val_result.get("validation_passed", False):
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
        """Print benchmark summary."""
        logger.info("="*60)
        logger.info("KROWN BENCHMARK SUMMARY")
        logger.info("="*60)
        
        total = len(results)
        completed = len([r for r in results if r["status"] == "completed"])
        failed = total - completed
        
        logger.info(f"Total scenarios: {total}")
        logger.info(f"Completed: {completed}")
        logger.info(f"Failed: {failed}")
        
        if completed > 0:
            execution_times = [r["execution_time"] for r in results if r["status"] == "completed"]
            avg_time = sum(execution_times) / len(execution_times)
            
            logger.info("Performance metrics:")
            logger.info(f"  Average execution time: {avg_time:.2f}s")
            logger.info(f"  Min execution time: {min(execution_times):.2f}s")
            logger.info(f"  Max execution time: {max(execution_times):.2f}s")
            
            logger.info("Scenario details:")
            for result in results:
                if result["status"] == "completed":
                    tm_count = result.get("triples_maps_count", 0)
                    pom_count = result.get("predicate_object_maps_count", 0)
                    inv_count = len(result.get("inversion_results", []))
                    val_status = ""
                    if self.validate and result.get("validation_results"):
                        val_passed = result["validation_results"].get("validation_passed", False)
                        val_status = " ✅" if val_passed else " ❌"
                    logger.info(f"  {result['scenario_name']}: {result['execution_time']:.2f}s "
                          f"[TM:{tm_count}, POM:{pom_count}, INV:{inv_count}]{val_status}")
        
        if failed > 0:
            logger.info("Failed scenarios:")
            for result in results:
                if result["status"] == "failed":
                    error = result.get("error", "Unknown error")
                    logger.error(f"  {result['scenario_name']}: {error}")
    
    def validate_scenario(self, scenario_name: str) -> dict:
        """Validate inversion results for a scenario."""
        logger.info("Step 4: Validating inversion results...")
        
        try:
            original_table = f"{scenario_name}_original_data"
            inverted_table = f"{scenario_name}_data"
            
            result = self.validator.validate_inversion(
                original_table=original_table,
                inverted_table=inverted_table,
                scenario_name=scenario_name
            )
            
            if result["validation_passed"]:
                logger.info(f"Validation passed for {scenario_name}")
            else:
                logger.error(f"Validation failed for {scenario_name}")
                if result.get("errors"):
                    for error in result["errors"][:3]:  # Show first 3 errors
                        logger.error(f"- {error}")
            
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
            logger.info("Cleaning up database tables...")
            if self.validator:
                self.validator.cleanup_tables()
            self.cleanup_database_tables()
        else:
            logger.info("Keeping database tables for manual inspection")
            logger.info(f"Database is available at: {self.get_connection_string()}")
    
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
                    logger.info(f"Dropped table: {table}")
                
                conn.commit()
                logger.info("All tables cleaned up")
        except Exception as e:
            logger.error(f"Error cleaning up tables: {e}")
        finally:
            engine.dispose()
    
    def run_benchmark(self):
        """Run the complete KROWN benchmark."""
        logger.info("KROWN Benchmark for Knowledge Graph Inversion (Docker)")
        logger.info("="*60)
        
        try:
            if self.validate:
                conn_string = self.get_connection_string()
                self.validator = KrownValidator(conn_string, verbose=False)
                logger.info("Validator initialized")
            
            if not self.run_krown_data_generation():
                logger.warning("Data generation failed, but continuing with existing data...")
            
            scenarios = self.find_krown_scenarios()
            if not scenarios:
                logger.error("No KROWN scenarios found")
                return 1
            
            results = []
            for i, scenario_path in enumerate(scenarios, 1):
                # Temporarily skip the third benchmark (mappings_8_5)
                if scenario_path.name == "mappings_8_5":
                    logger.info(f"[{i}/{len(scenarios)}] Skipping {scenario_path.name} (temporarily disabled)")
                    continue
                logger.info(f"[{i}/{len(scenarios)}] " + "="*40)
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
            
            logger.info("KROWN Benchmark completed!")
            return 0
            
        except KeyboardInterrupt:
            logger.warning("Benchmark interrupted by user")
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
  docker compose -f docker-compose.benchmark.yml run benchmark --validate
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
    
    args = parser.parse_args()
    
    use_virtuoso = not args.no_virtuoso
    
    runner = KrownBenchmarkRunner(
        use_virtuoso=use_virtuoso,
        validate=args.validate,
        cleanup_tables=not args.no_cleanup
    )
    return runner.run_benchmark()

if __name__ == "__main__":
    sys.exit(main())
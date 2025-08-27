"""
KROWN Benchmark Validator for Knowledge Graph Inversion.

This module validates that the inversion process successfully recreates
the original data by comparing input tables with inverted output tables.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import pandas as pd
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.engine import Engine


class KrownValidator:
    """Validates KROWN benchmark inversion results."""
    
    def __init__(self, connection_string: str, verbose: bool = False):
        """
        Initialize the validator.
        
        Args:
            connection_string: PostgreSQL connection string
            verbose: Enable verbose logging
        """
        self.connection_string = connection_string
        self.engine = create_engine(connection_string)
        self.verbose = verbose
        
        if verbose:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)
        
        self.logger = logging.getLogger(__name__)
    
    def get_all_tables(self) -> List[str]:
        """Get all tables in the database."""
        inspector = inspect(self.engine)
        return inspector.get_table_names()
    
    def validate_inversion(
        self, 
        original_table: str, 
        inverted_table: str,
        scenario_name: str
    ) -> Dict:
        """
        Validate that the inverted table matches the original.
        
        Args:
            original_table: Name of the original input table
            inverted_table: Name of the inverted output table
            scenario_name: Name of the scenario being validated
            
        Returns:
            Dictionary with validation results
        """
        result = {
            "scenario": scenario_name,
            "original_table": original_table,
            "inverted_table": inverted_table,
            "validation_passed": False,
            "errors": [],
            "warnings": [],
            "metrics": {}
        }
        
        try:
            # Load both tables
            with self.engine.connect() as conn:
                original_df = pd.read_sql(f"SELECT * FROM {original_table}", conn)
                inverted_df = pd.read_sql(f"SELECT * FROM {inverted_table}", conn)
            
            # Store metrics
            result["metrics"]["original_rows"] = len(original_df)
            result["metrics"]["inverted_rows"] = len(inverted_df)
            result["metrics"]["original_columns"] = list(original_df.columns)
            result["metrics"]["inverted_columns"] = list(inverted_df.columns)
            
            # Check row count
            if len(original_df) != len(inverted_df):
                result["errors"].append(
                    f"Row count mismatch: original={len(original_df)}, inverted={len(inverted_df)}"
                )
            
            # Check column names (order may differ)
            original_cols = set(original_df.columns)
            inverted_cols = set(inverted_df.columns)
            
            missing_cols = original_cols - inverted_cols
            extra_cols = inverted_cols - original_cols
            
            if missing_cols:
                result["errors"].append(f"Missing columns in inverted table: {missing_cols}")
            
            if extra_cols:
                result["errors"].append(f"Extra columns in inverted table: {extra_cols}")
            
            # If columns match, check data content
            if not missing_cols and not extra_cols:
                # Reorder inverted dataframe to match original column order
                inverted_df = inverted_df[original_df.columns]
                
                # Sort both dataframes for comparison (since row order may differ)
                original_sorted = original_df.sort_values(
                    by=list(original_df.columns)
                ).reset_index(drop=True)
                
                inverted_sorted = inverted_df.sort_values(
                    by=list(inverted_df.columns)
                ).reset_index(drop=True)
                
                # Convert to comparable types
                for col in original_sorted.columns:
                    # Handle numeric columns
                    if pd.api.types.is_numeric_dtype(original_sorted[col]):
                        original_sorted[col] = pd.to_numeric(original_sorted[col], errors='coerce')
                        inverted_sorted[col] = pd.to_numeric(inverted_sorted[col], errors='coerce')
                
                # Compare data
                try:
                    # Use pandas testing function for detailed comparison
                    pd.testing.assert_frame_equal(
                        original_sorted, 
                        inverted_sorted,
                        check_dtype=False,  # Allow type differences (e.g., int vs float)
                        check_exact=False,  # Allow small numeric differences
                        rtol=1e-5,  # Relative tolerance for floats
                        atol=1e-8   # Absolute tolerance for floats
                    )
                    result["validation_passed"] = True
                    self.logger.info(f"✅ Validation passed for {scenario_name}")
                    
                except AssertionError as e:
                    # Find specific differences
                    differences = self._find_differences(original_sorted, inverted_sorted)
                    result["errors"].append(f"Data mismatch: {str(e)}")
                    result["differences"] = differences
                    
                    if self.verbose:
                        self.logger.debug(f"Original data sample:\n{original_sorted.head()}")
                        self.logger.debug(f"Inverted data sample:\n{inverted_sorted.head()}")
                
            else:
                self.logger.error(f"❌ Cannot compare data due to column mismatches")
                
        except Exception as e:
            result["errors"].append(f"Validation error: {str(e)}")
            self.logger.error(f"❌ Validation failed for {scenario_name}: {e}")
        
        return result
    
    def _find_differences(self, df1: pd.DataFrame, df2: pd.DataFrame) -> List[Dict]:
        """Find specific differences between two dataframes."""
        differences = []
        
        # Compare values cell by cell
        for idx in range(min(len(df1), len(df2))):
            for col in df1.columns:
                val1 = df1.loc[idx, col]
                val2 = df2.loc[idx, col]
                
                # Handle NaN values
                if pd.isna(val1) and pd.isna(val2):
                    continue
                    
                if pd.isna(val1) or pd.isna(val2):
                    differences.append({
                        "row": idx,
                        "column": col,
                        "original": val1,
                        "inverted": val2
                    })
                elif val1 != val2:
                    # For numeric values, check if they're close enough
                    try:
                        if abs(float(val1) - float(val2)) > 1e-5:
                            differences.append({
                                "row": idx,
                                "column": col,
                                "original": val1,
                                "inverted": val2
                            })
                    except (TypeError, ValueError):
                        # Non-numeric values
                        differences.append({
                            "row": idx,
                            "column": col,
                            "original": val1,
                            "inverted": val2
                        })
        
        # Limit to first 10 differences for readability
        return differences[:10]
    
    def validate_scenario_tables(self, scenario_name: str) -> Dict:
        """
        Validate tables for a specific scenario.
        Assumes naming convention: original table is 'data', inverted is '{scenario_name}_data'
        """
        original_table = "data"
        inverted_table = f"{scenario_name}_data"
        
        # Check if tables exist
        all_tables = self.get_all_tables()
        
        if original_table not in all_tables:
            return {
                "scenario": scenario_name,
                "validation_passed": False,
                "errors": [f"Original table '{original_table}' not found"]
            }
        
        if inverted_table not in all_tables:
            return {
                "scenario": scenario_name,
                "validation_passed": False,
                "errors": [f"Inverted table '{inverted_table}' not found"]
            }
        
        return self.validate_inversion(original_table, inverted_table, scenario_name)
    
    def validate_all_scenarios(self, scenarios: List[str]) -> Dict:
        """
        Validate all scenarios.
        
        Args:
            scenarios: List of scenario names
            
        Returns:
            Dictionary with validation results for all scenarios
        """
        results = {
            "total_scenarios": len(scenarios),
            "passed": 0,
            "failed": 0,
            "scenario_results": []
        }
        
        for scenario in scenarios:
            self.logger.info(f"Validating scenario: {scenario}")
            result = self.validate_scenario_tables(scenario)
            results["scenario_results"].append(result)
            
            if result.get("validation_passed", False):
                results["passed"] += 1
            else:
                results["failed"] += 1
        
        results["validation_rate"] = (
            results["passed"] / results["total_scenarios"] * 100 
            if results["total_scenarios"] > 0 else 0
        )
        
        return results
    
    def save_validation_report(self, results: Dict, output_path: Path):
        """Save validation results to a JSON file."""
        with open(output_path, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        
        self.logger.info(f"📊 Validation report saved to: {output_path}")
    
    def print_summary(self, results: Dict):
        """Print validation summary."""
        print("\n" + "="*60)
        print("📋 VALIDATION SUMMARY")
        print("="*60)
        print(f"Total scenarios: {results['total_scenarios']}")
        print(f"Passed: {results['passed']} ✅")
        print(f"Failed: {results['failed']} ❌")
        print(f"Validation rate: {results['validation_rate']:.2f}%")
        
        if results['failed'] > 0:
            print("\n❌ Failed scenarios:")
            for result in results['scenario_results']:
                if not result.get('validation_passed', False):
                    print(f"  - {result['scenario']}:")
                    for error in result.get('errors', []):
                        print(f"    • {error}")
    
    def cleanup_tables(self, keep_tables: List[str] = None):
        """
        Clean up database tables.
        
        Args:
            keep_tables: List of table names to keep (optional)
        """
        keep_tables = keep_tables or []
        
        with self.engine.connect() as conn:
            all_tables = self.get_all_tables()
            
            for table in all_tables:
                if table not in keep_tables:
                    try:
                        conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
                        conn.commit()
                        self.logger.info(f"Dropped table: {table}")
                    except Exception as e:
                        self.logger.warning(f"Could not drop table {table}: {e}")
        
        self.logger.info("Database cleanup completed")
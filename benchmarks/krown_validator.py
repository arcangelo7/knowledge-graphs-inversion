import json
from pathlib import Path

import pandas as pd
from rich.console import Console
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

console = Console()


class KrownValidator:

    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        self.engine: Engine = create_engine(connection_string)

    def get_all_tables(self) -> list[str]:
        insp = inspect(self.engine)
        return insp.get_table_names()

    def validate_inversion(
        self,
        original_table: str,
        inverted_table: str,
        scenario_name: str
    ) -> dict:
        result: dict = {
            "scenario": scenario_name,
            "original_table": original_table,
            "inverted_table": inverted_table,
            "validation_passed": False,
            "errors": [],
            "warnings": [],
            "metrics": {}
        }

        try:
            with self.engine.connect() as conn:
                original_df = pd.read_sql(f"SELECT * FROM {original_table}", conn)
                inverted_df = pd.read_sql(f"SELECT * FROM {inverted_table}", conn)

            result["metrics"]["original_rows"] = len(original_df)
            result["metrics"]["inverted_rows"] = len(inverted_df)
            result["metrics"]["original_columns"] = list(original_df.columns)
            result["metrics"]["inverted_columns"] = list(inverted_df.columns)

            if len(original_df) != len(inverted_df):
                result["errors"].append(
                    f"Row count mismatch: original={len(original_df)}, inverted={len(inverted_df)}"
                )

            original_cols = set(original_df.columns)
            inverted_cols = set(inverted_df.columns)

            missing_cols = original_cols - inverted_cols
            extra_cols = inverted_cols - original_cols

            if missing_cols:
                result["errors"].append(f"Missing columns in inverted table: {missing_cols}")

            if extra_cols:
                result["errors"].append(f"Extra columns in inverted table: {extra_cols}")

            if not missing_cols and not extra_cols:
                inverted_df = inverted_df[original_df.columns]

                original_sorted = original_df.sort_values(
                    by=original_df.columns.tolist()
                ).reset_index(drop=True)

                inverted_sorted = inverted_df.sort_values(  # type: ignore[call-overload]
                    by=inverted_df.columns.tolist()
                ).reset_index(drop=True)

                for col in original_sorted.columns:
                    if pd.api.types.is_numeric_dtype(original_sorted[col]):
                        original_sorted[col] = pd.to_numeric(original_sorted[col], errors='coerce')
                        inverted_sorted[col] = pd.to_numeric(inverted_sorted[col], errors='coerce')

                try:
                    pd.testing.assert_frame_equal(
                        original_sorted,
                        inverted_sorted,
                        check_dtype=False,
                        check_exact=False,
                        rtol=1e-5,
                        atol=1e-8
                    )
                    result["validation_passed"] = True

                except AssertionError as e:
                    differences = self._find_differences(original_sorted, inverted_sorted)
                    result["errors"].append(f"Data mismatch: {str(e)}")
                    result["differences"] = differences

        except Exception as e:
            result["errors"].append(f"Validation error: {str(e)}")

        return result

    def _find_differences(self, df1: pd.DataFrame, df2: pd.DataFrame) -> list[dict]:
        differences = []

        for idx in range(min(len(df1), len(df2))):
            for col in df1.columns:
                val1 = df1.loc[idx, col]
                val2 = df2.loc[idx, col]

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
                    try:
                        if abs(float(val1) - float(val2)) > 1e-5:
                            differences.append({
                                "row": idx,
                                "column": col,
                                "original": val1,
                                "inverted": val2
                            })
                    except (TypeError, ValueError):
                        differences.append({
                            "row": idx,
                            "column": col,
                            "original": val1,
                            "inverted": val2
                        })

        return differences[:10]

    def validate_scenario_tables(self, scenario_name: str) -> dict:
        original_table = "data"
        inverted_table = f"{scenario_name}_data"

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

    def validate_all_scenarios(self, scenarios: list[str]) -> dict:
        results: dict = {
            "total_scenarios": len(scenarios),
            "passed": 0,
            "failed": 0,
            "scenario_results": []
        }

        for scenario in scenarios:
            result = self.validate_scenario_tables(scenario)
            results["scenario_results"].append(result)

            if result["validation_passed"]:
                results["passed"] += 1
            else:
                results["failed"] += 1

        results["validation_rate"] = (
            results["passed"] / results["total_scenarios"] * 100
            if results["total_scenarios"] > 0 else 0
        )

        return results

    def save_validation_report(self, results: dict, output_path: Path) -> None:
        with open(output_path, 'w') as f:
            json.dump(results, f, indent=2, default=str)

    def print_summary(self, results: dict) -> None:
        console.print(
            f"Validation: {results['passed']}/{results['total_scenarios']} "
            f"passed ({results['validation_rate']:.2f}%)"
        )

        if results['failed'] > 0:
            console.print("Failed scenarios:")
            for result in results['scenario_results']:
                if not result['validation_passed']:
                    console.print(f"  - {result['scenario']}")
                    for error in result['errors'][:1]:
                        console.print(f"    {error}")

    def cleanup_tables(self, keep_tables: list[str] | None = None) -> None:
        tables_to_keep = keep_tables if keep_tables is not None else []

        with self.engine.connect() as conn:
            all_tables = self.get_all_tables()

            for table_name in all_tables:
                if table_name not in tables_to_keep:
                    conn.execute(text(f"DROP TABLE IF EXISTS {table_name} CASCADE"))
                    conn.commit()

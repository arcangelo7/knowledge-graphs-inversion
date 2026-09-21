#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import gspread
from gspread.utils import ValueInputOption

from benchmarks.forward_engines import SOUFFLE_RELEASE
from benchmarks.krown_campaigns import CAMPAIGNS, campaign_payloads
from benchmarks.krown_catalog import EXCLUDED_SERIES

CellValue = str | int | float | bool
Table = list[list[CellValue]]
Record = tuple[str, dict[str, object]]

OVERVIEW_TAB = "Overview"
SCENARIO_STATISTICS_TAB = "Scenario statistics"
RAW_RUNS_TAB = "Raw runs"
VALIDATIONS_TAB = "Validations"
RESOURCES_TAB = "Resources"
FAILURES_TAB = "Failures"

CAMPAIGN_COLUMN = "Campaign"
FORWARD_STAGE = "forward_mapping"

SOUFFLE_SOFTWARE = f"Soufflé {SOUFFLE_RELEASE}"
CREDENTIALS_VARIABLE = "KROWN_SHEETS_CREDENTIALS"
DEFAULT_CREDENTIALS = Path.home() / ".config" / "krown-sheets" / "sa.json"
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SCENARIO_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Scenario ID", "scenario_id"),
    ("Suite", "suite"),
    ("Status", "status"),
    ("Expected outcome", "expected_outcome"),
    ("Recorded runs", "recorded_runs"),
    ("Completed runs", "statistics.execution_time.n"),
    ("Forward mean (s)", "statistics.forward_time.mean"),
    ("Forward 95% CI lower (s)", "statistics.forward_time.ci_95_lower"),
    ("Forward 95% CI upper (s)", "statistics.forward_time.ci_95_upper"),
    ("Reverse mean (s)", "statistics.inversion_time.mean"),
    ("Reverse 95% CI lower (s)", "statistics.inversion_time.ci_95_lower"),
    ("Reverse 95% CI upper (s)", "statistics.inversion_time.ci_95_upper"),
    ("Display name", "display_name"),
    ("Generator", "generator"),
    ("Members", "parameters.number_of_members"),
    ("Properties", "parameters.number_of_properties"),
    ("Value size (characters)", "parameters.value_size"),
    ("Data format", "parameters.data_format"),
    ("Scenario percentage (%)", "parameters.percentage"),
    ("Triples Maps parameter", "parameters.number_of_tms"),
    ("Predicate-Object Maps parameter", "parameters.number_of_poms"),
    ("Named graphs per predicate-object map", "parameters.number_of_ng_pom"),
    ("Named graphs per subject map", "parameters.number_of_ng_s"),
    ("Static named graphs", "parameters.static"),
    ("Join relation N", "parameters.n"),
    ("Join relation M", "parameters.m"),
    ("Join conditions parameter", "parameters.jc"),
    ("Duplicates parameter", "parameters.number_of_duplicates"),
    ("Source configuration: identifier", "source_configuration.identifier"),
    (
        "Source configuration: overrides: data format: source",
        "source_configuration.overrides.data_format.source",
    ),
    (
        "Source configuration: overrides: data format: benchmark",
        "source_configuration.overrides.data_format.benchmark",
    ),
    (
        "Source configuration: overrides: scenario",
        "source_configuration.overrides.scenario",
    ),
    ("Execution time mean (s)", "statistics.execution_time.mean"),
    ("Execution time median (s)", "statistics.execution_time.median"),
    ("Execution time sample SD (s)", "statistics.execution_time.std"),
    ("Execution time minimum (s)", "statistics.execution_time.min"),
    ("Execution time maximum (s)", "statistics.execution_time.max"),
    ("Execution time Q1 (s)", "statistics.execution_time.q1"),
    ("Execution time Q3 (s)", "statistics.execution_time.q3"),
    ("Execution time IQR (s)", "statistics.execution_time.iqr"),
    ("Execution time 95% CI lower (s)", "statistics.execution_time.ci_95_lower"),
    ("Execution time 95% CI upper (s)", "statistics.execution_time.ci_95_upper"),
    ("Execution time outliers (s)", "statistics.execution_time.outliers"),
    ("Execution time N", "statistics.execution_time.n"),
    ("Triples maps count", "statistics.metadata.triples_maps_count"),
    ("Predicate object maps count", "statistics.metadata.predicate_object_maps_count"),
    ("Join conditions count", "statistics.metadata.join_conditions_count"),
    ("Graph maps count", "statistics.metadata.graph_maps_count"),
    ("Mapping size (bytes)", "statistics.metadata.mapping_size_bytes"),
    ("Data size (bytes)", "statistics.metadata.data_size_bytes"),
    ("RDF statements", "statistics.metadata.rdf_statements"),
    ("Source rows", "statistics.metadata.source_rows"),
    ("Source cells", "statistics.metadata.source_cells"),
    ("Forward median (s)", "statistics.forward_time.median"),
    ("Forward sample SD (s)", "statistics.forward_time.std"),
    ("Forward minimum (s)", "statistics.forward_time.min"),
    ("Forward maximum (s)", "statistics.forward_time.max"),
    ("Forward Q1 (s)", "statistics.forward_time.q1"),
    ("Forward Q3 (s)", "statistics.forward_time.q3"),
    ("Forward IQR (s)", "statistics.forward_time.iqr"),
    ("Forward outliers (s)", "statistics.forward_time.outliers"),
    ("Forward N", "statistics.forward_time.n"),
    ("Reverse median (s)", "statistics.inversion_time.median"),
    ("Reverse sample SD (s)", "statistics.inversion_time.std"),
    ("Reverse minimum (s)", "statistics.inversion_time.min"),
    ("Reverse maximum (s)", "statistics.inversion_time.max"),
    ("Reverse Q1 (s)", "statistics.inversion_time.q1"),
    ("Reverse Q3 (s)", "statistics.inversion_time.q3"),
    ("Reverse IQR (s)", "statistics.inversion_time.iqr"),
    ("Reverse outliers (s)", "statistics.inversion_time.outliers"),
    ("Reverse N", "statistics.inversion_time.n"),
    ("Reverse / forward mean (%)", "statistics.inversion_overhead_percentage.mean"),
    (
        "Reverse / forward median (%)",
        "statistics.inversion_overhead_percentage.median",
    ),
    (
        "Reverse / forward sample SD (%)",
        "statistics.inversion_overhead_percentage.std",
    ),
    ("Reverse / forward minimum (%)", "statistics.inversion_overhead_percentage.min"),
    ("Reverse / forward maximum (%)", "statistics.inversion_overhead_percentage.max"),
    ("Reverse / forward Q1 (%)", "statistics.inversion_overhead_percentage.q1"),
    ("Reverse / forward Q3 (%)", "statistics.inversion_overhead_percentage.q3"),
    ("Reverse / forward IQR (%)", "statistics.inversion_overhead_percentage.iqr"),
    (
        "Reverse / forward 95% CI lower (%)",
        "statistics.inversion_overhead_percentage.ci_95_lower",
    ),
    (
        "Reverse / forward 95% CI upper (%)",
        "statistics.inversion_overhead_percentage.ci_95_upper",
    ),
    (
        "Reverse / forward outliers (%)",
        "statistics.inversion_overhead_percentage.outliers",
    ),
    ("Reverse / forward N", "statistics.inversion_overhead_percentage.n"),
    ("Rows throughput mean (rows/s)", "statistics.rows_per_second.mean"),
    ("Rows throughput median (rows/s)", "statistics.rows_per_second.median"),
    ("Rows throughput sample SD (rows/s)", "statistics.rows_per_second.std"),
    ("Rows throughput minimum (rows/s)", "statistics.rows_per_second.min"),
    ("Rows throughput maximum (rows/s)", "statistics.rows_per_second.max"),
    ("Rows throughput Q1 (rows/s)", "statistics.rows_per_second.q1"),
    ("Rows throughput Q3 (rows/s)", "statistics.rows_per_second.q3"),
    ("Rows throughput IQR (rows/s)", "statistics.rows_per_second.iqr"),
    ("Rows throughput 95% CI lower (rows/s)", "statistics.rows_per_second.ci_95_lower"),
    ("Rows throughput 95% CI upper (rows/s)", "statistics.rows_per_second.ci_95_upper"),
    ("Rows throughput outliers (rows/s)", "statistics.rows_per_second.outliers"),
    ("Rows throughput N", "statistics.rows_per_second.n"),
    ("Cells throughput mean (cells/s)", "statistics.cells_per_second.mean"),
    ("Cells throughput median (cells/s)", "statistics.cells_per_second.median"),
    ("Cells throughput sample SD (cells/s)", "statistics.cells_per_second.std"),
    ("Cells throughput minimum (cells/s)", "statistics.cells_per_second.min"),
    ("Cells throughput maximum (cells/s)", "statistics.cells_per_second.max"),
    ("Cells throughput Q1 (cells/s)", "statistics.cells_per_second.q1"),
    ("Cells throughput Q3 (cells/s)", "statistics.cells_per_second.q3"),
    ("Cells throughput IQR (cells/s)", "statistics.cells_per_second.iqr"),
    (
        "Cells throughput 95% CI lower (cells/s)",
        "statistics.cells_per_second.ci_95_lower",
    ),
    (
        "Cells throughput 95% CI upper (cells/s)",
        "statistics.cells_per_second.ci_95_upper",
    ),
    ("Cells throughput outliers (cells/s)", "statistics.cells_per_second.outliers"),
    ("Cells throughput N", "statistics.cells_per_second.n"),
    ("Failure outcome", "failure.outcome"),
)

RAW_RUN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Scenario ID", "scenario_name"),
    ("Iteration", "iteration"),
    ("Suite", "suite"),
    ("Status", "status"),
    ("Execution time (s)", "execution_time"),
    ("Forward time (s)", "timing_breakdown.forward_time"),
    ("Reverse time (s)", "timing_breakdown.inversion_time"),
    ("Total measured time (s)", "timing_breakdown.total_time"),
    ("Validation results: outcome", "validation_results.outcome"),
    ("Expected outcome", "expected_outcome"),
    ("Display name", "display_name"),
    ("Generator", "generator"),
    ("Members", "parameters.number_of_members"),
    ("Properties", "parameters.number_of_properties"),
    ("Value size (characters)", "parameters.value_size"),
    ("Data format", "parameters.data_format"),
    ("Source configuration: identifier", "source_configuration.identifier"),
    (
        "Source configuration: overrides: data format: source",
        "source_configuration.overrides.data_format.source",
    ),
    (
        "Source configuration: overrides: data format: benchmark",
        "source_configuration.overrides.data_format.benchmark",
    ),
    (
        "Source configuration: overrides: scenario",
        "source_configuration.overrides.scenario",
    ),
    ("Reverse / forward (%)", "timing_breakdown.inversion_overhead_percentage"),
    ("Reverse throughput (rows/s)", "throughput.rows_per_second"),
    ("Reverse throughput (cells/s)", "throughput.cells_per_second"),
    ("Mapping size (bytes)", "mapping_size_bytes"),
    ("Data size (bytes)", "data_size_bytes"),
    ("RDF statements", "rdf_statements"),
    ("Source rows", "source_rows"),
    ("Source cells", "source_cells"),
    ("Triples maps count", "triples_maps_count"),
    ("Predicate object maps count", "predicate_object_maps_count"),
    ("Join conditions count", "join_conditions_count"),
    ("Graph maps count", "graph_maps_count"),
    ("Reverse count", "inversion_count"),
    ("Metrics: scope", "metrics.scope"),
    ("Metrics: stages: forward: executor", "metrics.stages.forward.executor"),
    ("Metrics: stages: forward: step", "metrics.stages.forward.step"),
    ("Metrics: stages: backward: executor", "metrics.stages.backward.executor"),
    ("Metrics: stages: backward: step", "metrics.stages.backward.step"),
    ("Validation results: scenario", "validation_results.scenario"),
    ("Validation results: validation passed", "validation_results.validation_passed"),
    ("Validation results: expected outcome", "validation_results.expected_outcome"),
    ("Validation results: checks: tables", "validation_results.checks.tables"),
    (
        "Validation results: missing mapped columns",
        "validation_results.missing_mapped_columns",
    ),
    ("Validation results: source tables", "validation_results.source_tables"),
    ("Validation results: destination tables", "validation_results.destination_tables"),
    ("Validation results: errors", "validation_results.errors"),
    ("Failure: stage", "failure.stage"),
    ("Failure: kind", "failure.kind"),
    ("Failure: outcome", "failure.outcome"),
    ("Failure: message", "failure.message"),
    ("Scenario percentage (%)", "parameters.percentage"),
    (
        "Validation results: missing mapped columns: data",
        "validation_results.missing_mapped_columns.data",
    ),
    ("Triples Maps parameter", "parameters.number_of_tms"),
    ("Predicate-Object Maps parameter", "parameters.number_of_poms"),
    ("Named graphs per predicate-object map", "parameters.number_of_ng_pom"),
    ("Named graphs per subject map", "parameters.number_of_ng_s"),
    ("Static named graphs", "parameters.static"),
    ("Join relation N", "parameters.n"),
    ("Join relation M", "parameters.m"),
    (
        "Validation results: missing mapped columns: data1",
        "validation_results.missing_mapped_columns.data1",
    ),
    ("Join conditions parameter", "parameters.jc"),
    ("Duplicates parameter", "parameters.number_of_duplicates"),
)

VALIDATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Scenario ID", "scenario_id"),
    ("Iteration", "iteration"),
    ("Suite", "suite"),
    ("Table name", "table_name"),
    ("Expected outcome", "expected_outcome"),
    ("Outcome", "outcome"),
    ("Validation passed", "validation_passed"),
    ("Exact", "exact"),
    ("Partial valid", "partial_valid"),
    ("Metrics: original rows", "metrics.original_rows"),
    ("Metrics: reconstructed rows", "metrics.reconstructed_rows"),
    ("Checks: column subset", "checks.column_subset"),
    ("Checks: no foreign values", "checks.no_foreign_values"),
    ("Checks: columns", "checks.columns"),
    ("Checks: rows", "checks.rows"),
    ("Checks: values", "checks.values"),
    ("Checks: multiplicities", "checks.multiplicities"),
    ("Losses: columns", "losses.columns"),
    ("Losses: rows", "losses.rows"),
    ("Losses: additional rows", "losses.additional_rows"),
    ("Losses: values", "losses.values"),
    ("Losses: multiplicities", "losses.multiplicities"),
    ("Metrics: original columns", "metrics.original_columns"),
    ("Metrics: destination columns", "metrics.destination_columns"),
    ("Metrics: reconstructed columns", "metrics.reconstructed_columns"),
)

RESOURCE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Scenario ID", "scenario_id"),
    ("Stage", "stage_name"),
    ("Suite", "suite"),
    ("Selected run", "run"),
    ("Duration (s)", "duration"),
    ("Samples", "number_of_samples"),
    ("Memory RAM max (bytes)", "memory_ram_max"),
    ("CPU user system change (s)", "cpu_user_system_diff"),
    ("Resource name", "name"),
    ("Step", "step"),
    ("Metrics version", "version"),
    ("CPU user change (s)", "cpu_user_diff"),
    ("CPU system change (s)", "cpu_system_diff"),
    ("CPU idle change (s)", "cpu_idle_diff"),
    ("CPU iowait change (s)", "cpu_iowait_diff"),
    ("Memory swap max (bytes)", "memory_swap_max"),
    ("Memory RAM swap max (bytes)", "memory_ram_swap_max"),
    ("Memory RAM min (bytes)", "memory_ram_min"),
    ("Memory swap min (bytes)", "memory_swap_min"),
    ("Memory RAM swap min (bytes)", "memory_ram_swap_min"),
    ("Disk read count diff", "disk_read_count_diff"),
    ("Disk write count diff", "disk_write_count_diff"),
    ("Disk read (bytes) diff", "disk_read_bytes_diff"),
    ("Disk write (bytes) diff", "disk_write_bytes_diff"),
    ("Disk read time change (ms)", "disk_read_time_diff"),
    ("Disk write time change (ms)", "disk_write_time_diff"),
    ("Disk busy time change (ms)", "disk_busy_time_diff"),
    ("Network received count diff", "network_received_count_diff"),
    ("Network sent count diff", "network_sent_count_diff"),
    ("Network received (bytes) diff", "network_received_bytes_diff"),
    ("Network sent (bytes) diff", "network_sent_bytes_diff"),
    ("Network received error diff", "network_received_error_diff"),
    ("Network sent error diff", "network_sent_error_diff"),
    ("Network received drop diff", "network_received_drop_diff"),
    ("Network sent drop diff", "network_sent_drop_diff"),
)

FAILURE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Scenario ID", "scenario_id"),
    ("Suite", "suite"),
    ("Iteration", "iteration"),
    ("Status", "status"),
    ("Stage", "failure.stage"),
    ("Kind", "failure.kind"),
    ("Outcome", "failure.outcome"),
    ("Execution time (s)", "execution_time"),
    ("Message", "failure.message"),
    ("Complete diagnostic", "failure.diagnostic"),
)


@dataclass(frozen=True)
class Campaign:
    label: str
    source: Path
    data: dict[str, object]

    @property
    def scenarios(self) -> dict[str, dict[str, object]]:
        return cast(dict[str, dict[str, object]], self.data["scenarios"])


def load_campaigns(stats_file: Path) -> tuple[Campaign, ...]:
    payloads = campaign_payloads(
        cast(dict[str, object], json.loads(stats_file.read_text(encoding="utf-8")))
    )
    return tuple(
        Campaign(label=campaign.label, source=stats_file, data=payloads[campaign.name])
        for campaign in CAMPAIGNS
    )


def _flatten(record: dict[str, object]) -> dict[str, object]:
    flat: dict[str, object] = {}

    def walk(value: object, prefix: str) -> None:
        if isinstance(value, dict) and value:
            for key, item in cast(dict[str, object], value).items():
                walk(item, f"{prefix}.{key}" if prefix else key)
        else:
            flat[prefix] = value

    walk(record, "")
    return flat


CELL_CHARACTER_LIMIT = 50000
TRUNCATION_NOTICE = "\n[truncated]"


def _within_cell_limit(text: str) -> str:
    if len(text) <= CELL_CHARACTER_LIMIT:
        return text
    keep = CELL_CHARACTER_LIMIT - len(TRUNCATION_NOTICE)
    return text[:keep] + TRUNCATION_NOTICE


def _cell(value: object) -> CellValue:
    if value is None:
        return ""
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (list, dict)):
        return _within_cell_limit(json.dumps(value, separators=(",", ":")))
    text = str(value)
    try:
        number = float(text)
    except ValueError:
        return _within_cell_limit(text)
    return int(number) if number.is_integer() else number


def _table(columns: Sequence[tuple[str, str]], records: Sequence[Record]) -> Table:
    header: list[CellValue] = [CAMPAIGN_COLUMN]
    header.extend(name for name, _ in columns)
    table: Table = [header]
    for label, record in records:
        flat = _flatten(record)
        row: list[CellValue] = [label]
        row.extend(_cell(flat[path]) if path in flat else "" for _, path in columns)
        table.append(row)
    return table


def _ordered_scenarios(campaigns: Sequence[Campaign]) -> list[tuple[str, Campaign]]:
    names: dict[str, None] = {}
    for campaign in campaigns:
        names.update(dict.fromkeys(campaign.scenarios))
    return [
        (name, campaign)
        for name in names
        for campaign in campaigns
        if name in campaign.scenarios
    ]


def scenario_records(campaigns: Sequence[Campaign]) -> list[Record]:
    records = []
    for name, campaign in _ordered_scenarios(campaigns):
        scenario = campaign.scenarios[name]
        raw_runs = cast(list[dict[str, object]], scenario["raw_runs"])
        records.append(
            (
                campaign.label,
                dict(scenario, scenario_id=name, recorded_runs=len(raw_runs)),
            )
        )
    return records


def raw_run_records(campaigns: Sequence[Campaign]) -> list[Record]:
    records = []
    for name, campaign in _ordered_scenarios(campaigns):
        scenario = campaign.scenarios[name]
        for run in cast(list[dict[str, object]], scenario["raw_runs"]):
            records.append((campaign.label, dict(run, suite=scenario["suite"])))
    return records


def validation_records(campaigns: Sequence[Campaign]) -> list[Record]:
    records = []
    for name, campaign in _ordered_scenarios(campaigns):
        scenario = campaign.scenarios[name]
        for run in cast(list[dict[str, object]], scenario["raw_runs"]):
            if run["status"] != "completed":
                continue
            validation = cast(dict[str, object], run["validation_results"])
            tables = cast(dict[str, dict[str, object]], validation["tables"])
            for table_name, result in tables.items():
                records.append(
                    (
                        campaign.label,
                        dict(
                            result,
                            scenario_id=name,
                            suite=scenario["suite"],
                            iteration=run["iteration"],
                            table_name=table_name,
                            outcome=validation["outcome"],
                            expected_outcome=validation["expected_outcome"],
                            validation_passed=validation["validation_passed"],
                        ),
                    )
                )
    return records


def resource_records(campaigns: Sequence[Campaign]) -> list[Record]:
    records = []
    for name, campaign in _ordered_scenarios(campaigns):
        scenario = campaign.scenarios[name]
        if scenario["status"] != "completed":
            continue
        for row in cast(list[dict[str, str]], scenario["resource_summary"]):
            records.append(
                (
                    campaign.label,
                    dict(row, scenario_id=name, suite=scenario["suite"]),
                )
            )
    return records


def failure_records(campaigns: Sequence[Campaign]) -> list[Record]:
    records = []
    for name, campaign in _ordered_scenarios(campaigns):
        scenario = campaign.scenarios[name]
        if scenario["status"] == "completed":
            continue
        last_run = cast(list[dict[str, object]], scenario["raw_runs"])[-1]
        records.append(
            (
                campaign.label,
                dict(
                    scenario,
                    scenario_id=name,
                    iteration=last_run["iteration"],
                    execution_time=last_run["execution_time"],
                ),
            )
        )
    return records


OVERVIEW_TITLE = "KROWN benchmark campaign"
STAGE_HEADER = (
    "Stage",
    "Scenarios started",
    "Completed scenarios",
    "Failed scenarios",
    "Not run",
    "Out of memory",
    "Timeout",
)
CONFIGURATION_ROWS: tuple[tuple[str, str], ...] = (
    ("Campaign timestamp (UTC)", "timestamp"),
    ("Forward software", "forward_engine_version"),
    ("Reverse software", "reverse_software"),
    ("Forward RML reader", "forward_rml_reader"),
    ("Soufflé mode", "souffle_mode"),
    ("Iterations per completed scenario", "iterations"),
    ("Measurement scope", "measurement_scope"),
    ("Sampling interval (s)", "sample_interval_seconds"),
)


def _failures(campaign: Campaign, forward: bool) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], scenario["failure"])
        for scenario in campaign.scenarios.values()
        if scenario["status"] != "completed"
        and (cast(dict[str, object], scenario["failure"])["stage"] == FORWARD_STAGE)
        is forward
    ]


def _stage_rows(campaign: Campaign) -> list[list[CellValue]]:
    total = len(campaign.scenarios)
    completed = sum(
        1
        for scenario in campaign.scenarios.values()
        if scenario["status"] == "completed"
    )
    forward_failures = _failures(campaign, forward=True)
    inversion_failures = _failures(campaign, forward=False)
    started = total - len(forward_failures)
    return [
        [
            campaign.label,
            "Forward realization",
            total,
            started,
            len(forward_failures),
            0,
            sum(
                1 for failure in forward_failures if failure["kind"] == "out_of_memory"
            ),
            sum(1 for failure in forward_failures if failure["kind"] == "timeout"),
        ],
        [
            campaign.label,
            "Reverse realization",
            started,
            completed,
            len(inversion_failures),
            len(forward_failures),
            sum(
                1
                for failure in inversion_failures
                if failure["kind"] == "out_of_memory"
            ),
            sum(1 for failure in inversion_failures if failure["kind"] == "timeout"),
        ],
    ]


def _configuration_value(campaign: Campaign, key: str) -> CellValue:
    data = campaign.data
    if key == "timestamp":
        moment = datetime.fromtimestamp(cast(int, data["timestamp"]), UTC)
        return moment.isoformat(" ", "seconds").removesuffix("+00:00")
    if key == "souffle_mode":
        if data["inversion_engine"] != "souffle":
            return ""
        return cast(str, data["souffle_mode"])
    if key == "forward_engine_version":
        return SOUFFLE_SOFTWARE
    if key == "reverse_software":
        if data["inversion_engine"] == "souffle":
            return SOUFFLE_SOFTWARE
        return "SPARQL-based"
    if key == "forward_rml_reader":
        provenance = cast(dict[str, object], data["provenance"])
        return f"RMLMapper {cast(str, provenance['forward_rml_reader_version'])}"
    return _cell(data[key])


def overview_table(campaigns: Sequence[Campaign]) -> Table:
    width = max(len(STAGE_HEADER) + 1, len(campaigns) + 1)
    blank: list[CellValue] = [""] * width

    def padded(values: Sequence[CellValue]) -> list[CellValue]:
        return list(values) + [""] * (width - len(values))

    table: Table = [
        list(blank),
        padded([OVERVIEW_TITLE]),
        list(blank),
        list(blank),
        padded([CAMPAIGN_COLUMN, *STAGE_HEADER]),
    ]
    for campaign in campaigns:
        table.extend(padded(row) for row in _stage_rows(campaign))
    table.extend([list(blank), list(blank)])
    table.append(
        padded(["Campaign configuration", *(campaign.label for campaign in campaigns)])
    )
    for label, key in CONFIGURATION_ROWS:
        table.append(
            padded(
                [
                    label,
                    *(_configuration_value(campaign, key) for campaign in campaigns),
                ]
            )
        )
    return table


def build_tables(campaigns: Sequence[Campaign]) -> dict[str, Table]:
    return {
        OVERVIEW_TAB: overview_table(campaigns),
        SCENARIO_STATISTICS_TAB: _table(SCENARIO_COLUMNS, scenario_records(campaigns)),
        RAW_RUNS_TAB: _table(RAW_RUN_COLUMNS, raw_run_records(campaigns)),
        VALIDATIONS_TAB: _table(VALIDATION_COLUMNS, validation_records(campaigns)),
        RESOURCES_TAB: _table(RESOURCE_COLUMNS, resource_records(campaigns)),
        FAILURES_TAB: _table(FAILURE_COLUMNS, failure_records(campaigns)),
    }


def resolve_credentials(override: Path | None) -> Path:
    if override is not None:
        credentials = override
    elif CREDENTIALS_VARIABLE in os.environ:
        credentials = Path(os.environ[CREDENTIALS_VARIABLE])
    else:
        credentials = DEFAULT_CREDENTIALS
    if not credentials.is_file():
        raise FileNotFoundError(
            f"Service account key not found at {credentials}; "
            f"set {CREDENTIALS_VARIABLE} or pass --credentials"
        )
    return credentials


def open_spreadsheet(spreadsheet_id: str, credentials: Path) -> gspread.Spreadsheet:
    client = gspread.service_account(filename=credentials, scopes=SHEETS_SCOPES)
    return client.open_by_key(spreadsheet_id)


def _grid_request(sheet_id: int, rows: int, columns: int) -> dict[str, object]:
    return {
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {"rowCount": rows, "columnCount": columns},
            },
            "fields": "gridProperties.rowCount,gridProperties.columnCount",
        }
    }


def _block_extent(table: Table, start_row: int, start_column: int) -> tuple[int, int]:
    """End of the filled block that a table object anchored here should cover."""
    end_row = start_row
    while end_row < len(table) and any(
        value != "" for value in table[end_row][start_column:]
    ):
        end_row += 1
    header = table[start_row]
    end_column = len(header)
    while end_column > start_column and header[end_column - 1] == "":
        end_column -= 1
    return end_row, end_column


def _resize_requests(sheet: dict[str, object], table: Table) -> list[dict[str, object]]:
    """Fit the grid, and every table object on it, to the values just written.

    A table object may neither exceed its grid nor keep an obsolete range, and
    Sheets refuses to move one to a different start, so each object keeps its
    anchor while the grid is widened first and narrowed only at the end.
    """
    properties = cast(dict[str, object], sheet["properties"])
    sheet_id = cast(int, properties["sheetId"])
    grid = cast(dict[str, int], properties["gridProperties"])
    rows, columns = len(table), len(table[0])
    objects = (
        cast(list[dict[str, object]], sheet["tables"]) if "tables" in sheet else []
    )
    if not objects:
        return [_grid_request(sheet_id, rows, columns)]

    requests = [
        _grid_request(
            sheet_id,
            max(rows, grid["rowCount"]),
            max(columns, grid["columnCount"]),
        )
    ]
    for table_object in objects:
        anchor = cast(dict[str, int], table_object["range"])
        start_row = anchor["startRowIndex"] if "startRowIndex" in anchor else 0
        start_column = anchor["startColumnIndex"] if "startColumnIndex" in anchor else 0
        end_row, end_column = _block_extent(table, start_row, start_column)
        requests.append(
            {
                "updateTable": {
                    "table": {
                        "tableId": table_object["tableId"],
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": start_row,
                            "endRowIndex": end_row,
                            "startColumnIndex": start_column,
                            "endColumnIndex": end_column,
                        },
                    },
                    "fields": "range",
                }
            }
        )
    requests.append(_grid_request(sheet_id, rows, columns))
    return requests


def write_tables(spreadsheet: gspread.Spreadsheet, tables: dict[str, Table]) -> None:
    worksheets = {worksheet.title: worksheet for worksheet in spreadsheet.worksheets()}
    for title in tables:
        if title not in worksheets:
            worksheets[title] = spreadsheet.add_worksheet(title, rows=1, cols=1)
    metadata = spreadsheet.fetch_sheet_metadata()
    sheets = {
        cast(str, cast(dict[str, object], sheet["properties"])["title"]): sheet
        for sheet in cast(list[dict[str, object]], metadata["sheets"])
    }

    requests: list[dict[str, object]] = []
    for title, table in tables.items():
        requests.append(
            {
                "updateCells": {
                    "range": {"sheetId": worksheets[title].id},
                    "fields": "userEnteredValue",
                }
            }
        )
        requests.extend(_resize_requests(sheets[title], table))
    spreadsheet.batch_update({"requests": requests})

    for title, table in tables.items():
        worksheets[title].update(table, "A1", value_input_option=ValueInputOption.raw)


CHART_ROW_STEP = 25
CHART_WIDTH_PIXELS = 580
CHART_HEIGHT_PIXELS = 533
CHART_BLOCK_COLUMN = 8
CHART_FIRST_ROW = 5
CHART_TITLE_OFFSET = 2  # the table toolbar hides the row right above the header
CHART_TAB_INDEX = 1
SERIES_COLOURS = (
    (0.12156863, 0.46666667, 0.7058824),
    (0.8392157, 0.15294118, 0.15686275),
    (0.17254902, 0.627451, 0.17254902),
    (1.0, 0.49803922, 0.05490196),
    (0.58039216, 0.40392157, 0.7411765),
    (0.54901963, 0.3372549, 0.29411766),
    (0.8901961, 0.46666667, 0.76078431),
    (0.49803922, 0.49803922, 0.49803922),
)


@dataclass(frozen=True)
class ChartMeasure:
    label: str
    column: str
    stage: str | None


@dataclass(frozen=True)
class ChartTab:
    title: str
    axis: str
    source: str
    measures: tuple[ChartMeasure, ...]


CHART_TABS: tuple[ChartTab, ...] = (
    ChartTab(
        "Charts timing",
        "Mean time (s)",
        SCENARIO_STATISTICS_TAB,
        (
            ChartMeasure("Forward", "Forward mean (s)", None),
            ChartMeasure("Reverse", "Reverse mean (s)", None),
        ),
    ),
    ChartTab(
        "Charts memory",
        "Peak RAM (bytes)",
        RESOURCES_TAB,
        (
            ChartMeasure("Forward", "Memory RAM max (bytes)", "forward"),
            ChartMeasure("Reverse", "Memory RAM max (bytes)", "backward"),
        ),
    ),
    ChartTab(
        "Charts disk",
        "Bytes written",
        RESOURCES_TAB,
        (
            ChartMeasure("Forward", "Disk write (bytes) diff", "forward"),
            ChartMeasure("Reverse", "Disk write (bytes) diff", "backward"),
        ),
    ),
)

SOURCE_COLUMNS = {
    SCENARIO_STATISTICS_TAB: SCENARIO_COLUMNS,
    RESOURCES_TAB: RESOURCE_COLUMNS,
}


def _column_letter(index: int) -> str:
    letters = ""
    position = index + 1
    while position:
        position, rest = divmod(position - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def _source_letter(source: str, header: str) -> str:
    columns = SOURCE_COLUMNS[source]
    index = next(
        position for position, (name, _) in enumerate(columns) if name == header
    )
    return _column_letter(index + 1)


def _chart_formula(
    chart_tab: ChartTab,
    measure: ChartMeasure,
    campaign: Campaign,
    key_cell: str,
) -> str:
    source = f"'{chart_tab.source}'!"
    value = f"${_source_letter(chart_tab.source, measure.column)}"
    scenario = f"${_source_letter(chart_tab.source, 'Scenario ID')}"
    conditions = [
        f"{source}{scenario}$2:{scenario}={key_cell}",
        f'{source}$A$2:$A="{campaign.label}"',
    ]
    if measure.stage is not None:
        stage = f"${_source_letter(chart_tab.source, 'Stage')}"
        conditions.append(f'{source}{stage}$2:{stage}="{measure.stage}"')
    return (
        f"=IFERROR(LET(v;FILTER({source}{value}$2:{value};"
        + ";".join(conditions)
        + ');IF(v="";"";v));"")'
    )


def _sweep_value(value: CellValue) -> CellValue:
    # Chart tabs are written as user input, so text such as "1-5" needs the
    # leading apostrophe that stops Sheets from reading it as a date.
    return f"'{value}" if isinstance(value, str) else value


def _chart_sweeps(campaigns: Sequence[Campaign]) -> list[dict[str, object]]:
    """Reported sweeps, limited to points that at least one campaign completed."""
    sweeps = []
    for sweep in cast(list[dict[str, object]], campaigns[0].data["series"]):
        if sweep["name"] in EXCLUDED_SERIES:
            continue
        points = [
            point
            for point in cast(list[dict[str, object]], sweep["points"])
            if any(
                campaign.scenarios[cast(str, point["scenario"])]["status"]
                == "completed"
                for campaign in campaigns
            )
        ]
        sweeps.append({**sweep, "points": points})
    return sweeps


def chart_tab_values(chart_tab: ChartTab, campaigns: Sequence[Campaign]) -> Table:
    """Lookup block feeding one chart per parameter sweep, stacked vertically."""
    series = _chart_sweeps(campaigns)
    width = CHART_BLOCK_COLUMN + 2 + len(chart_tab.measures) * len(campaigns)
    table: Table = []
    for position, sweep in enumerate(series):
        header_row = CHART_FIRST_ROW + position * CHART_ROW_STEP
        while len(table) < header_row:
            table.append([""] * width)
        title: list[CellValue] = [""] * width
        title[CHART_BLOCK_COLUMN] = cast(str, sweep["title"])
        table[header_row - CHART_TITLE_OFFSET] = title
        header: list[CellValue] = [""] * width
        header[CHART_BLOCK_COLUMN] = cast(str, sweep["parameter_label"])
        labels = [
            f"{measure.label} {campaign.label}"
            for measure in chart_tab.measures
            for campaign in campaigns
        ]
        for index, label in enumerate(labels):
            header[CHART_BLOCK_COLUMN + 1 + index] = label
        header[width - 1] = "Scenario ID"
        table.append(header)
        for point in cast(list[dict[str, object]], sweep["points"]):
            row: list[CellValue] = [""] * width
            row[CHART_BLOCK_COLUMN] = _sweep_value(cast(CellValue, point["value"]))
            key_cell = f"${_column_letter(width - 1)}{len(table) + 1}"
            for index, (measure, campaign) in enumerate(
                (measure, campaign)
                for measure in chart_tab.measures
                for campaign in campaigns
            ):
                row[CHART_BLOCK_COLUMN + 1 + index] = _chart_formula(
                    chart_tab, measure, campaign, key_cell
                )
            row[width - 1] = cast(str, point["scenario"])
            table.append(row)
    # The last chart needs a full slot of rows, or Sheets moves it over the previous one.
    while len(table) < CHART_FIRST_ROW + len(series) * CHART_ROW_STEP:
        table.append([""] * width)
    return table


def _table_name(chart_tab: ChartTab, position: int) -> str:
    slug = chart_tab.title.removeprefix("Charts ").capitalize()
    return f"{slug}Data{position + 1}"


def _chart_requests(
    chart_tab: ChartTab,
    campaigns: Sequence[Campaign],
    sheet_id: int,
) -> list[dict[str, object]]:
    series_count = len(chart_tab.measures) * len(campaigns)
    width = CHART_BLOCK_COLUMN + 2 + series_count
    requests: list[dict[str, object]] = []
    for position, sweep in enumerate(_chart_sweeps(campaigns)):
        header_row = CHART_FIRST_ROW + position * CHART_ROW_STEP
        points = len(cast(list[dict[str, object]], sweep["points"]))
        source = {
            "sheetId": sheet_id,
            "startRowIndex": header_row,
            "endRowIndex": header_row + points + 1,
        }
        requests.append(
            {
                "addTable": {
                    "table": {
                        "name": _table_name(chart_tab, position),
                        "range": {
                            **source,
                            "startColumnIndex": CHART_BLOCK_COLUMN,
                            "endColumnIndex": width,
                        },
                    }
                }
            }
        )
        requests.append(
            {
                "addChart": {
                    "chart": {
                        "spec": {
                            "title": cast(str, sweep["title"]),
                            "basicChart": {
                                "chartType": "LINE",
                                "legendPosition": "TOP_LEGEND",
                                "headerCount": 1,
                                "axis": [
                                    {
                                        "position": "BOTTOM_AXIS",
                                        "title": cast(str, sweep["parameter_label"]),
                                    },
                                    {"position": "LEFT_AXIS", "title": chart_tab.axis},
                                ],
                                "domains": [
                                    {
                                        "domain": {
                                            "sourceRange": {
                                                "sources": [
                                                    {
                                                        **source,
                                                        "startColumnIndex": (
                                                            CHART_BLOCK_COLUMN
                                                        ),
                                                        "endColumnIndex": (
                                                            CHART_BLOCK_COLUMN + 1
                                                        ),
                                                    }
                                                ]
                                            }
                                        }
                                    }
                                ],
                                "series": [
                                    {
                                        "series": {
                                            "sourceRange": {
                                                "sources": [
                                                    {
                                                        **source,
                                                        "startColumnIndex": column,
                                                        "endColumnIndex": column + 1,
                                                    }
                                                ]
                                            }
                                        },
                                        "targetAxis": "LEFT_AXIS",
                                        "lineStyle": {"width": 2},
                                        "pointStyle": {"size": 5},
                                        "colorStyle": {
                                            "rgbColor": {
                                                "red": red,
                                                "green": green,
                                                "blue": blue,
                                            }
                                        },
                                    }
                                    for index in range(series_count)
                                    for column in [CHART_BLOCK_COLUMN + 1 + index]
                                    for red, green, blue in [
                                        SERIES_COLOURS[index % len(SERIES_COLOURS)]
                                    ]
                                ],
                            },
                        },
                        "position": {
                            "overlayPosition": {
                                "anchorCell": {
                                    "sheetId": sheet_id,
                                    "rowIndex": header_row,
                                },
                                "widthPixels": CHART_WIDTH_PIXELS,
                                "heightPixels": CHART_HEIGHT_PIXELS,
                            }
                        },
                    }
                }
            }
        )
    return requests


def write_chart_tabs(
    spreadsheet: gspread.Spreadsheet,
    campaigns: Sequence[Campaign],
) -> dict[str, int]:
    """Rebuild every chart tab: one sweep block and one chart per KROWN series."""
    worksheets = {worksheet.title: worksheet for worksheet in spreadsheet.worksheets()}
    for chart_tab in CHART_TABS:
        if chart_tab.title not in worksheets:
            worksheets[chart_tab.title] = spreadsheet.add_worksheet(
                chart_tab.title, rows=1, cols=1
            )
    metadata = spreadsheet.fetch_sheet_metadata()
    sheets = {
        cast(str, cast(dict[str, object], sheet["properties"])["title"]): sheet
        for sheet in cast(list[dict[str, object]], metadata["sheets"])
    }

    written: dict[str, int] = {}
    requests: list[dict[str, object]] = []
    updates: list[dict[str, object]] = []
    for chart_tab in CHART_TABS:
        sheet = sheets[chart_tab.title]
        sheet_id = worksheets[chart_tab.title].id
        for chart in (
            cast(list[dict[str, object]], sheet["charts"]) if "charts" in sheet else []
        ):
            requests.append({"deleteEmbeddedObject": {"objectId": chart["chartId"]}})
        for table_object in (
            cast(list[dict[str, object]], sheet["tables"]) if "tables" in sheet else []
        ):
            requests.append({"deleteTable": {"tableId": table_object["tableId"]}})
        values = chart_tab_values(chart_tab, campaigns)
        requests.append(
            {"updateCells": {"range": {"sheetId": sheet_id}, "fields": "*"}}
        )
        requests.append(_grid_request(sheet_id, len(values), len(values[0])))
        updates.append({"range": f"'{chart_tab.title}'!A1", "values": values})
        written[chart_tab.title] = len(values)
    spreadsheet.batch_update({"requests": requests})
    spreadsheet.values_batch_update(
        {"valueInputOption": ValueInputOption.user_entered.value, "data": updates}
    )

    requests = []
    for position, chart_tab in enumerate(CHART_TABS):
        requests.append(
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": worksheets[chart_tab.title].id,
                        "index": CHART_TAB_INDEX + position,
                    },
                    "fields": "index",
                }
            }
        )
        requests.extend(
            _chart_requests(chart_tab, campaigns, worksheets[chart_tab.title].id)
        )
    spreadsheet.batch_update({"requests": requests})
    return written


def main() -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Export KROWN campaigns to a Google spreadsheet"
    )
    parser.add_argument("stats_file", type=Path)
    parser.add_argument("--spreadsheet-id")
    parser.add_argument("--credentials", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    campaigns = load_campaigns(args.stats_file)
    tables = build_tables(campaigns)
    for title, table in tables.items():
        print(f"{title}: {len(table)} rows x {len(table[0])} cols")
    if args.dry_run:
        return 0
    if args.spreadsheet_id is None:
        parser.error("--spreadsheet-id is required unless --dry-run is given")
    spreadsheet = open_spreadsheet(
        args.spreadsheet_id, resolve_credentials(args.credentials)
    )
    write_tables(spreadsheet, tables)
    for title, rows in write_chart_tabs(spreadsheet, campaigns).items():
        print(f"{title}: {rows} rows")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

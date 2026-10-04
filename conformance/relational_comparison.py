# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
# SPDX-License-Identifier: ISC

from dataclasses import dataclass, field
from pathlib import Path

from conformance.database import DatabaseConnection
from conformance.morph_ldp import verify_roundtrip
from conformance.outcome import CaseOutcome, InversionOutcome, logical_table_content
from kgi import NonInvertibleError, UnsupportedMappingError, analyze_mapping
from kgi.comparison import compare_databases, databases_identical


@dataclass
class RecoveryComparison:
    outcome: CaseOutcome
    source_equal: bool = False
    rdf_equal: bool | None = None
    validation_ok: bool = False
    errors: list[dict[str, str]] = field(default_factory=list)


def compare_destination(
    execution_errors: list[dict[str, str]],
    mapping_path: str,
    rdf_path: Path,
    source_url: str,
    destination_url: str,
    directory: Path,
) -> RecoveryComparison:
    result = RecoveryComparison(CaseOutcome(InversionOutcome.MISMATCH))
    connection = DatabaseConnection()
    source_content = connection.get_database_content(source_url)
    dest_content = connection.get_database_content(destination_url)
    result.source_equal = databases_identical(source_content, dest_content)
    result.rdf_equal, verification_errors = verify_roundtrip(
        mapping_path, rdf_path, destination_url, directory
    )
    result.errors.extend(verification_errors)
    result.validation_ok = result.rdf_equal is True and not execution_errors
    losses = frozenset()
    message = "Recovered tables differ from the source"
    try:
        analysis = analyze_mapping(mapping_path, rdf_path, source_db_url=source_url)
    except (NonInvertibleError, UnsupportedMappingError) as error:
        message = str(error)
        equal = result.source_equal
    else:
        projection_destination = {
            name: table
            for name, table in logical_table_content(analysis, destination_url).items()
            if name in analysis or table["data"]
        }
        equal, message, losses = compare_databases(
            logical_table_content(analysis, source_url),
            projection_destination,
            analysis,
        )
    if result.source_equal or equal:
        outcome = InversionOutcome.FULLY_INVERTED
        losses = frozenset()
        message = "All source or logical tables were recovered exactly"
    elif losses:
        outcome = InversionOutcome.PARTIALLY_INVERTED
    elif execution_errors:
        outcome = InversionOutcome.ERROR
        message = "; ".join(
            error["reason"] for error in execution_errors + verification_errors
        )
        losses = frozenset()
    else:
        outcome = InversionOutcome.MISMATCH
    result.outcome = CaseOutcome(outcome, losses, message, source_content, dest_content)
    return result

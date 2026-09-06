# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pyoxigraph import BlankNode, Quad, RdfFormat, Store

from conformance.database import DatabaseConnection
from conformance.souffle import (
    Database as SouffleDatabase,
)
from conformance.souffle import (
    InversionMode,
    SouffleConformanceAdapter,
    SouffleConformanceError,
    rdf_datasets_isomorphic,
)
from kgi import (
    MappingAnalysis,
    NoDataError,
    NonInvertibleError,
    TableAnalysis,
    UnsupportedMappingError,
    analyze_mapping,
)
from kgi.comparison import (
    DatabaseContent,
    PartialLoss,
    compare_databases,
    databases_identical,
)
from kgi.core import _check_for_sql_queries, _parse_mapping_store, reconstruct

RDF_FORMATS = {
    "turtle": RdfFormat.TURTLE,
    "nquads": RdfFormat.N_QUADS,
    "ntriples": RdfFormat.N_TRIPLES,
}

FORWARD_STAGES = frozenset({"forward generation", "forward execution"})


class InversionOutcome(StrEnum):
    FULLY_INVERTED = "fully_inverted"
    PARTIALLY_INVERTED = "partially_inverted"
    NON_INVERTIBLE = "non_invertible"
    NOT_SUPPORTED = "not_supported"
    ERROR_TEST_CASE = "error_test_case"
    NOT_TESTED = "not_tested"
    MISMATCH = "mismatch"
    ERROR = "error"


OUTCOME_LABELS = {
    InversionOutcome.FULLY_INVERTED: "Fully inverted",
    InversionOutcome.PARTIALLY_INVERTED: "Partially inverted",
    InversionOutcome.NON_INVERTIBLE: "Non-invertible",
    InversionOutcome.NOT_SUPPORTED: "Not supported",
    InversionOutcome.ERROR_TEST_CASE: "Error test case",
    InversionOutcome.NOT_TESTED: "Not tested",
    InversionOutcome.MISMATCH: "Mismatch",
    InversionOutcome.ERROR: "Execution error",
}

LOSS_LABELS = {
    PartialLoss.COLUMNS_LOST: "Columns lost (unmapped or unassignable columns)",
    PartialLoss.ROWS_LOST: "Rows lost (NULL in subject template)",
    PartialLoss.MULTIPLICITY_LOST: "Multiplicity lost (duplicate rows collapsed)",
    PartialLoss.TABLES_LOST: "Tables lost (unmapped tables)",
}


@dataclass(frozen=True)
class CaseOutcome:
    outcome: InversionOutcome
    losses: frozenset[PartialLoss] = frozenset()
    message: str = field(default="", compare=False)
    source_content: DatabaseContent | None = field(default=None, compare=False)
    dest_content: DatabaseContent | None = field(default=None, compare=False)


def describe_difference(expected: CaseOutcome | None, observed: CaseOutcome) -> str:
    def render(case: CaseOutcome) -> str:
        if not case.losses:
            return str(case.outcome)
        return f"{case.outcome} ({', '.join(sorted(case.losses))})"

    expected_label = render(expected) if expected else "no recorded expectation"
    return f"expected {expected_label}, got {render(observed)}: {observed.message}"


def _normalize_term(term: object) -> str:
    if isinstance(term, BlankNode):
        return "_:BNODE"
    return str(term)


def graphs_isomorphic(expected: Store, actual: Store) -> bool:
    expected_quads = list(expected)
    actual_quads = list(actual)
    if len(expected_quads) != len(actual_quads):
        return False

    has_bnodes = any(
        isinstance(quad.subject, BlankNode) or isinstance(quad.object, BlankNode)
        for quad in expected_quads + actual_quads
    )
    if not has_bnodes:
        return set(expected_quads) == set(actual_quads)

    def signature(quads: list[Quad]) -> set[tuple[str, str, str, str]]:
        return {
            (
                _normalize_term(quad.subject),
                str(quad.predicate),
                _normalize_term(quad.object),
                str(quad.graph_name),
            )
            for quad in quads
        }

    return signature(expected_quads) == signature(actual_quads)


def _load_expected_store(expected_output_path: str) -> Store:
    store = Store()
    if Path(expected_output_path).is_file():
        store.load(path=expected_output_path, format=RdfFormat.N_QUADS)
    return store


def forward_conformance_failed(
    expects_output: bool,
    expected_output_path: str,
    rdf_path: Path,
    output_format: str,
    exit_code: int,
) -> bool:
    if not (rdf_path.is_file() and rdf_path.stat().st_size > 0):
        if not expects_output:
            return False
        return len(_load_expected_store(expected_output_path)) > 0

    if not expects_output:
        return exit_code == 0

    produced = Store()
    try:
        produced.load(path=str(rdf_path), format=RDF_FORMATS[output_format])
    except SyntaxError:
        return True
    return not graphs_isomorphic(_load_expected_store(expected_output_path), produced)


_db_connection = DatabaseConnection()


def _provenance_analysis(analysis: MappingAnalysis) -> MappingAnalysis:
    # Recorded provenance names the source tuple behind each triple, so every column
    # a term map reads comes back with its own value, including those the graph alone
    # leaves unattributed. Columns no term map reads stay outside the references.
    return {
        table_name: TableAnalysis(
            table.references, frozenset(), table.subject_reference_sets
        )
        for table_name, table in analysis.items()
    }


def _compare_reconstruction(
    analysis: MappingAnalysis,
    source_db_url: str,
    dest_db_url: str,
    allow_empty_destination: bool,
) -> CaseOutcome:
    source_content = _db_connection.get_database_content(source_db_url)
    dest_content = _db_connection.get_database_content(dest_db_url)
    databases_equal, message, losses = compare_databases(
        source_content, dest_content, analysis
    )
    if databases_equal:
        return CaseOutcome(
            InversionOutcome.FULLY_INVERTED,
            message=message,
            source_content=source_content,
            dest_content=dest_content,
        )
    if allow_empty_destination and not dest_content:
        return CaseOutcome(
            InversionOutcome.FULLY_INVERTED,
            message=(
                "Inversion correctly not performed due to mapping errors - "
                "destination database appropriately empty"
            ),
            source_content=source_content,
            dest_content=dest_content,
        )
    outcome = (
        InversionOutcome.PARTIALLY_INVERTED if losses else InversionOutcome.MISMATCH
    )
    return CaseOutcome(
        outcome,
        losses,
        message=message,
        source_content=source_content,
        dest_content=dest_content,
    )


def evaluate_kgi_case(
    mapping_path: str,
    rdf_path: Path,
    expects_output: bool,
    forward_failed: bool,
    source_db_url: str,
    dest_db_url: str,
) -> CaseOutcome:
    produced_rdf = rdf_path.is_file() and rdf_path.stat().st_size > 0

    if not expects_output:
        return CaseOutcome(
            InversionOutcome.ERROR_TEST_CASE,
            message=(
                "The forward mapping produced RDF the specification forbids"
                if produced_rdf
                else "The forward mapping stopped, as the specification requires"
            ),
        )

    if not produced_rdf:
        if _check_for_sql_queries(_parse_mapping_store(mapping_path)):
            return CaseOutcome(
                InversionOutcome.NOT_SUPPORTED,
                message=(
                    "Inversion not supported: SQL query as logical table is not "
                    "supported"
                ),
            )
        if forward_failed:
            return CaseOutcome(
                InversionOutcome.ERROR,
                message=(
                    "The forward mapping did not produce the expected graph, so the "
                    "inversion could not be attempted"
                ),
            )
        if not rdf_path.is_file():
            rdf_path.touch()

    try:
        reconstruct(
            mapping=mapping_path,
            rdf_graph=rdf_path,
            source_db_url=source_db_url,
            dest_db_url=dest_db_url,
        )
    except UnsupportedMappingError as error:
        return CaseOutcome(
            InversionOutcome.NOT_SUPPORTED,
            message=f"Inversion not supported: {error}",
        )
    except NonInvertibleError as error:
        return CaseOutcome(
            InversionOutcome.NON_INVERTIBLE,
            message=f"Non-invertible mapping detected: {error}",
        )
    except NoDataError:
        if forward_failed:
            return CaseOutcome(
                InversionOutcome.ERROR,
                message=(
                    "The forward mapping did not produce the expected graph, so the "
                    "inversion could not be attempted"
                ),
            )
        return _compare_reconstruction(
            analyze_mapping(mapping_path, rdf_path, source_db_url=source_db_url),
            source_db_url,
            dest_db_url,
            allow_empty_destination=True,
        )

    return _compare_reconstruction(
        analyze_mapping(mapping_path, rdf_path, source_db_url=source_db_url),
        source_db_url,
        dest_db_url,
        allow_empty_destination=False,
    )


def _compare_recorded_provenance(
    source_db_url: str, dest_db_url: str, error: NonInvertibleError
) -> CaseOutcome:
    source_content = _db_connection.get_database_content(source_db_url)
    dest_content = _db_connection.get_database_content(dest_db_url)
    if databases_identical(source_content, dest_content):
        return CaseOutcome(
            InversionOutcome.FULLY_INVERTED,
            message="All tables in source and destination databases are identical",
            source_content=source_content,
            dest_content=dest_content,
        )
    return CaseOutcome(
        InversionOutcome.NON_INVERTIBLE,
        message=f"Non-invertible mapping detected: {error}",
        source_content=source_content,
        dest_content=dest_content,
    )


def evaluate_souffle_case(
    adapter: SouffleConformanceAdapter,
    mapping_path: Path,
    expected_rdf_path: Path,
    rdf_path: Path,
    shared_directory: Path,
    expects_output: bool,
    database: SouffleDatabase,
    source_db_url: str,
    dest_db_url: str,
    inversion_mode: InversionMode | bool,
) -> CaseOutcome:
    if isinstance(inversion_mode, bool):
        inversion_mode = "provenance" if inversion_mode else "rdf"
    try:
        adapter.run_forward(
            mapping_path, rdf_path, shared_directory, source_db_url, database
        )
    except SouffleConformanceError as error:
        if expects_output or error.stage not in FORWARD_STAGES:
            raise
        return CaseOutcome(
            InversionOutcome.ERROR_TEST_CASE,
            message="The forward mapping stopped, as the specification requires",
        )

    if not expects_output:
        return CaseOutcome(
            InversionOutcome.ERROR_TEST_CASE,
            message=(
                "The forward mapping produced RDF the specification forbids"
                if rdf_path.stat().st_size
                else "The forward mapping stopped, as the specification requires"
            ),
        )

    if not rdf_datasets_isomorphic(expected_rdf_path, rdf_path):
        return CaseOutcome(
            InversionOutcome.MISMATCH,
            message="Forward RDF dataset differs from the expected dataset",
        )

    try:
        analysis = analyze_mapping(mapping_path, rdf_path, source_db_url=source_db_url)
    except UnsupportedMappingError as error:
        return CaseOutcome(
            InversionOutcome.NOT_SUPPORTED,
            message=f"Inversion not supported: {error}",
        )
    except NonInvertibleError as error:
        if inversion_mode == "rdf":
            return CaseOutcome(
                InversionOutcome.NON_INVERTIBLE,
                message=f"Non-invertible mapping detected: {error}",
            )
        adapter.run_backward(
            shared_directory,
            source_db_url,
            dest_db_url,
            inversion_mode,
        )
        return _compare_recorded_provenance(source_db_url, dest_db_url, error)

    adapter.run_backward(
        shared_directory,
        source_db_url,
        dest_db_url,
        inversion_mode,
    )
    if inversion_mode != "rdf":
        analysis = _provenance_analysis(analysis)
    return _compare_reconstruction(
        analysis, source_db_url, dest_db_url, allow_empty_destination=False
    )

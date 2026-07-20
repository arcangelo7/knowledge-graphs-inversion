# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

"""SPARQL query generation and execution."""

from collections.abc import Iterator
from typing import cast

import pandas as pd
from pyoxigraph import BlankNode, Literal, NamedNode, QuerySolutions, Triple

from kgi.base import Endpoint
from kgi.constants import (
    RML_BLANK_NODE,
    RML_CONSTANT,
    RML_PARENT_TRIPLES_MAP,
    RML_REFERENCE,
    RML_TEMPLATE,
)
from kgi.exceptions import UnsupportedMappingError
from kgi.triples import QueryTriple, SubjectTriple, extract_from_iri_template
from kgi.utils import (
    Codex,
    IdGenerator,
    signature_value,
    sparql_to_python_type,
    url_decode,
)

GROUP_SIGNATURE_COLUMNS = [
    "predicate_map_type",
    "predicate_map_value",
    "predicate_references_template",
    "predicate_references",
    "predicate_reference_count",
    "object_map_type",
    "object_map_value",
    "object_references_template",
    "object_references",
    "object_reference_count",
    "object_termtype",
    "lang_datatype",
    "lang_datatype_map_type",
    "lang_datatype_map_value",
    "graph_map_type",
    "graph_map_value",
    "graph_references_template",
    "graph_references",
    "graph_reference_count",
    "object_join_conditions",
]
_QUERY_CHUNK_SIZE = 10_000
RdfTerm = NamedNode | BlankNode | Literal | Triple | None


class Query:
    """Represents a SPARQL query for data inversion."""

    def __init__(self, triples: list[QueryTriple] | None = None):
        self.triples: list[QueryTriple] = triples or []
        self.id_generator = IdGenerator()
        self.codex = Codex()
        self.generated_query = None

    @property
    def references(self) -> list[str]:
        """Get all references used in the query."""
        references = set()
        for triple in self.triples:
            references.update(triple.references)
        return list(references)

    @property
    def template_references(self) -> list[str]:
        """Get references extracted from URI/blank node templates."""
        refs = set()
        for triple in self.triples:
            refs.update(triple.template_extracted_references)
        return list(refs)

    @property
    def literal_references(self) -> list[str]:
        """Get references available from object literals."""
        refs = set()
        for triple in self.triples:
            refs.update(triple.plain_references)
        return list(refs)

    @property
    def template_only_references(self) -> list[str]:
        """Get references only available from template extraction.

        When a reference is available from both a template and a literal,
        the literal value is preferred (no URL decoding needed).
        """
        literal = set(self.literal_references)
        return [ref for ref in self.template_references if ref not in literal]

    def generate(self, all_mapping_rules: pd.DataFrame) -> str | None:
        """Generate SPARQL query string."""
        all_references = self.references

        if not all_references:
            return None

        triple_strings = []
        generated_patterns = set()

        # Separate SubjectTriples from ObjectTriples.
        # SubjectTriples must be processed last so their BINDs are skipped
        # when the reference is already available from a literal.
        object_triples = [t for t in self.triples if not isinstance(t, SubjectTriple)]
        subject_triples = [t for t in self.triples if isinstance(t, SubjectTriple)]

        constant_triples = [
            t for t in object_triples if t.rule["object_map_type"] == RML_CONSTANT
        ]
        reference_triples = [
            t for t in object_triples if t.rule["object_map_type"] == RML_REFERENCE
        ]
        template_triples = [
            t for t in object_triples if t.rule["object_map_type"] == RML_TEMPLATE
        ]
        parent_triples = [
            t
            for t in object_triples
            if t.rule["object_map_type"] == RML_PARENT_TRIPLES_MAP
        ]

        for triple_group in [
            constant_triples,
            template_triples,
            reference_triples,
            parent_triples,
            subject_triples,
        ]:
            for triple in triple_group:
                triple_string = triple.generate(
                    self.id_generator, self.codex, all_mapping_rules
                )
                if (
                    triple_string is not None
                    and triple_string not in generated_patterns
                ):
                    triple_strings.append(triple_string)
                    generated_patterns.add(triple_string)

        graph_info = self._get_exclusive_graph_info()
        graph_binds = ""
        graph_var: str | None = None
        if graph_info:
            graph_var = self.codex.get_id(str(graph_info["graph_map_value"]))
            graph_binds = self._generate_graph_binds(graph_info, graph_var)

        all_vars = [f"?{self.codex.get_id(ref)}" for ref in all_references]
        select_part = "SELECT " + " ".join(all_vars) + " WHERE {"

        if graph_var is not None:
            body = (
                f"GRAPH ?{graph_var} {{\n"
                + "\n".join(triple_strings)
                + "\n}\n"
                + graph_binds
            )
        else:
            body = "\n".join(triple_strings)

        generated_query = select_part + body + "}"
        self.generated_query = generated_query.replace("\\", "\\\\")
        return self.generated_query

    def _get_exclusive_graph_info(self) -> dict[str, object] | None:
        """Return graph map info if there are column references exclusive to the graph map."""
        all_graph_refs: set[str] = set()
        all_other_refs: set[str] = set()
        graph_info: dict[str, object] | None = None

        for triple in self.triples:
            all_other_refs.update(triple.subject_references)
            all_other_refs.update(triple.predicate_references)
            all_other_refs.update(triple.object_references)

            g_refs = triple.graph_references
            if g_refs:
                all_graph_refs.update(g_refs)
                if graph_info is None:
                    graph_info = {
                        "graph_map_type": triple.rule["graph_map_type"],
                        "graph_map_value": triple.rule["graph_map_value"],
                        "graph_references": triple.rule["graph_references"],
                        "graph_references_template": triple.rule[
                            "graph_references_template"
                        ],
                    }

        exclusive = all_graph_refs - all_other_refs
        if not exclusive or graph_info is None:
            return None

        graph_info["exclusive_references"] = exclusive
        return graph_info

    def _generate_graph_binds(
        self, graph_info: dict[str, object], graph_var: str
    ) -> str:
        """Generate SPARQL BINDs for extracting column values from graph IRIs."""
        graph_map_type = str(graph_info["graph_map_type"])
        rule = self.triples[0].rule

        if graph_map_type == RML_REFERENCE:
            ref = list(cast(set[str], graph_info["exclusive_references"]))
            ref_id = str(ref[0])
            ref_var = self.codex.get_id(ref_id)
            return f"BIND(STR(?{graph_var}) AS ?{ref_var})\n"

        if graph_map_type == RML_TEMPLATE:
            return (
                extract_from_iri_template(
                    template_value=str(graph_info["graph_map_value"]),
                    references_template=str(graph_info["graph_references_template"]),
                    references=cast(list[str], graph_info["graph_references"]),
                    rule=rule,
                    codex=self.codex,
                    id_generator=self.id_generator,
                    slice_label="graph",
                )
                + "\n"
            )

        raise UnsupportedMappingError(f"Unsupported graph map type: {graph_map_type}")

    def decode_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Decode query results DataFrame."""
        df = df.copy(deep=True)

        template_only = set(self.template_only_references)
        for reference in self.references:
            column_reference = self.codex.get_id(reference)
            if reference in template_only:
                df[column_reference] = df[column_reference].apply(url_decode)
            df.rename(columns={column_reference: reference}, inplace=True)

        return df


def _term_to_python(term: RdfTerm) -> object:
    if term is None:
        return None
    if isinstance(term, Literal):
        return sparql_to_python_type(term.value, term.datatype.value)
    if isinstance(term, (NamedNode, BlankNode)):
        return term.value
    return str(term)


def _solutions_to_dataframes(
    solutions: QuerySolutions, chunk_size: int = _QUERY_CHUNK_SIZE
) -> Iterator[pd.DataFrame]:
    variables = list(solutions.variables)
    columns = [variable.value for variable in variables]
    column_index = pd.Index(columns)
    rows: list[dict[str, object]] = []
    found_solution = False

    for solution in solutions:
        found_solution = True
        rows.append(
            {
                variable.value: _term_to_python(solution[variable])
                for variable in variables
            }
        )
        if len(rows) == chunk_size:
            yield pd.DataFrame(rows, columns=column_index)
            rows = []

    if rows:
        yield pd.DataFrame(rows, columns=column_index)
    elif not found_solution:
        yield pd.DataFrame(columns=column_index)


def _subject_group_signature(subject_rules: pd.DataFrame) -> frozenset[tuple[str, ...]]:
    signature = []
    for _, rule in subject_rules.iterrows():
        signature.append(
            tuple(signature_value(rule[column]) for column in GROUP_SIGNATURE_COLUMNS)
        )
    return frozenset(signature)


def _subject_group_references(subject_rules: pd.DataFrame) -> tuple[set[str], set[str]]:
    subject_references: set[str] = set()
    non_subject_references: set[str] = set()
    for _, rule in subject_rules.iterrows():
        triple = QueryTriple(rule)
        subject_references.update(triple.subject_references)
        non_subject_references.update(triple.predicate_references)
        non_subject_references.update(triple.object_references)
        non_subject_references.update(triple.graph_references)
    return subject_references, non_subject_references


def _select_query_source_rules(source_rules: pd.DataFrame) -> pd.DataFrame:
    selected_groups = []
    reducible_signatures: set[frozenset[tuple[str, ...]]] = set()

    for _, subject_rules in source_rules.groupby("subject_map_value", dropna=False):
        signature = _subject_group_signature(subject_rules)
        subject_references, non_subject_references = _subject_group_references(
            subject_rules
        )
        is_reducible = subject_references <= non_subject_references

        if is_reducible and signature in reducible_signatures:
            continue

        selected_groups.append(subject_rules)
        if is_reducible:
            reducible_signatures.add(signature)

    if not selected_groups:
        return source_rules.iloc[0:0]

    return pd.concat(selected_groups)


def retrieve_data(
    mapping_rules: pd.DataFrame,
    source_rules: pd.DataFrame,
    endpoint: Endpoint,
    decode_columns: bool = False,
) -> tuple[Iterator[pd.DataFrame] | None, str | None]:
    """Retrieve data from SPARQL endpoint using mapping rules."""
    query_source_rules = _select_query_source_rules(source_rules)
    triples: list[QueryTriple] = [
        QueryTriple(rule)
        for _, rule in query_source_rules.iterrows()
        if rule["object_map_type"] not in [RML_BLANK_NODE]
    ]

    subject_groups = list(query_source_rules.groupby("subject_map_value", dropna=False))
    triples.extend(
        SubjectTriple(subject_rules.iloc[0]) for _, subject_rules in subject_groups
    )
    query = Query(triples)
    generated_query = query.generate(mapping_rules)

    if generated_query is None:
        return None, None

    chunks = _solutions_to_dataframes(endpoint.query(generated_query))
    if decode_columns:
        chunks = (query.decode_dataframe(chunk) for chunk in chunks)
    return chunks, generated_query

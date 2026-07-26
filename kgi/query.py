# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

"""SPARQL query generation and execution."""

from collections.abc import Iterator
from dataclasses import dataclass, field

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


@dataclass
class GraphMapPattern:
    """A graph map to invert, with the triple pattern that identifies its graphs."""

    map_type: str
    map_value: str
    references: list[str]
    references_template: str
    anchor: str | None
    hidden_references: list[str] = field(default_factory=list)

    @property
    def variable_key(self) -> str:
        """Codex key for the graph variable.

        A reference graph map names a column, so the key is suffixed to keep the
        graph variable distinct from the variable holding that column value.
        """
        if self.map_type == RML_REFERENCE:
            return f"{self.map_value}_graph"
        return self.map_value


class Query:
    """Represents a SPARQL query for data inversion."""

    def __init__(
        self,
        triples: list[QueryTriple] | None = None,
        excluded_references: frozenset[str] = frozenset(),
    ):
        self.triples: list[QueryTriple] = triples or []
        self.excluded_references = excluded_references
        self.id_generator = IdGenerator()
        self.codex = Codex()
        self.generated_query = None
        self._dropped_references: set[str] = set()

    @property
    def references(self) -> list[str]:
        """Get the references the query selects, without the unrecoverable ones."""
        references: set[str] = set()
        for triple in self.triples:
            references.update(triple.references)
        return list(references - self.excluded_references - self._dropped_references)

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
        patterns_by_triple: dict[int, str] = {}

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
                if triple_string is None:
                    continue
                if not isinstance(triple, SubjectTriple):
                    patterns_by_triple[id(triple)] = triple_string
                if triple_string not in generated_patterns:
                    triple_strings.append(triple_string)
                    generated_patterns.add(triple_string)

        graph_clauses = [
            self._generate_graph_clause(graph_map)
            for graph_map in self._exclusive_graph_maps(patterns_by_triple)
        ]

        selected_references = self.references
        if not selected_references:
            return None

        # Merging indistinguishable subject groups makes one source row match through
        # several subjects, so the same tuple would be returned once per subject
        select_keyword = (
            "SELECT DISTINCT"
            if self.excluded_references or self._dropped_references
            else "SELECT"
        )
        all_vars = [f"?{self.codex.get_id(ref)}" for ref in selected_references]
        select_part = select_keyword + " " + " ".join(all_vars) + " WHERE {"
        body = "\n".join(triple_strings + graph_clauses)

        generated_query = select_part + body + "}"
        self.generated_query = generated_query.replace("\\", "\\\\")
        return self.generated_query

    def _exclusive_graph_maps(self, patterns: dict[int, str]) -> list[GraphMapPattern]:
        """Collect the graph maps carrying columns no other term map exposes.

        Each one is inverted on its own graph variable, anchored to a triple
        pattern of the predicate-object map it belongs to.
        """
        all_other_refs: set[str] = set()
        groups: dict[str, GraphMapPattern] = {}

        for triple in self.triples:
            all_other_refs.update(triple.subject_references)
            all_other_refs.update(triple.predicate_references)
            all_other_refs.update(triple.object_references)

            graph_refs = triple.graph_references
            if not graph_refs:
                continue
            value = str(triple.rule["graph_map_value"])
            anchor = patterns[id(triple)] if id(triple) in patterns else None
            if value in groups:
                if groups[value].anchor is None:
                    groups[value].anchor = anchor
                continue
            groups[value] = GraphMapPattern(
                map_type=str(triple.rule["graph_map_type"]),
                map_value=value,
                references=[str(item) for item in triple.rule["graph_references"]],
                references_template=signature_value(
                    triple.rule["graph_references_template"]
                ),
                anchor=anchor,
            )

        selected = []
        for graph_map in groups.values():
            hidden = [
                reference
                for reference in graph_map.references
                if reference not in all_other_refs
                and reference not in self.excluded_references
            ]
            if not hidden:
                continue
            has_siblings = any(
                other.map_value != graph_map.map_value
                and other.references_template == graph_map.references_template
                for other in groups.values()
            )
            # Sibling graph maps build indistinguishable IRIs, so no filter can
            # restrict a graph variable to the one that carries these columns, and
            # a graph map whose predicate-object map produced no triple pattern has
            # nothing to match its graphs with
            if has_siblings or graph_map.anchor is None:
                self._dropped_references.update(hidden)
                continue
            graph_map.hidden_references = hidden
            selected.append(graph_map)
        return selected

    def _generate_graph_clause(self, graph_map: GraphMapPattern) -> str:
        """Bind a graph variable to the given graph map and extract its columns."""
        graph_var = self.codex.get_id(graph_map.variable_key)
        binds = self._generate_graph_binds(graph_map, graph_var)
        return f"GRAPH ?{graph_var} {{\n{graph_map.anchor}\n}}\n{binds}"

    def _generate_graph_binds(self, graph_map: GraphMapPattern, graph_var: str) -> str:
        """Generate SPARQL BINDs for extracting column values from graph IRIs."""
        if graph_map.map_type == RML_REFERENCE:
            ref_var = self.codex.get_id(graph_map.hidden_references[0])
            return f"BIND(STR(?{graph_var}) AS ?{ref_var})"

        if graph_map.map_type == RML_TEMPLATE:
            return extract_from_iri_template(
                template_value=graph_map.map_value,
                references_template=graph_map.references_template,
                references=graph_map.references,
                codex=self.codex,
                id_generator=self.id_generator,
                slice_label="graph",
            )

        raise UnsupportedMappingError(
            f"Unsupported graph map type: {graph_map.map_type}"
        )

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


def _select_query_source_rules(
    source_rules: pd.DataFrame, excluded_references: frozenset[str] = frozenset()
) -> pd.DataFrame:
    subject_groups: list[tuple[bool, pd.DataFrame, bool]] = []
    for _, subject_rules in source_rules.groupby("subject_map_value", dropna=False):
        subject_references, non_subject_references = _subject_group_references(
            subject_rules
        )
        # Subject columns left out of the reconstruction do not need a group of their
        # own: this is what collapses indistinguishable subject templates into one
        is_reducible = (
            subject_references - excluded_references
        ) <= non_subject_references
        drops_subject_columns = bool(subject_references & excluded_references)
        subject_groups.append((drops_subject_columns, subject_rules, is_reducible))

    # Among interchangeable groups prefer one whose subject columns are recovered, so
    # that its template is matched against them instead of every subject matching
    subject_groups.sort(key=lambda subject_group: subject_group[0])

    selected_groups = []
    reducible_signatures: set[frozenset[tuple[str, ...]]] = set()
    for _, subject_rules, is_reducible in subject_groups:
        signature = _subject_group_signature(subject_rules)
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
    excluded_references: frozenset[str] = frozenset(),
    decode_columns: bool = False,
) -> tuple[Iterator[pd.DataFrame] | None, str | None]:
    """Retrieve data from SPARQL endpoint using mapping rules."""
    query_source_rules = _select_query_source_rules(source_rules, excluded_references)
    triples: list[QueryTriple] = [
        QueryTriple(rule, excluded_references)
        for _, rule in query_source_rules.iterrows()
        if rule["object_map_type"] not in [RML_BLANK_NODE]
    ]

    subject_groups = list(query_source_rules.groupby("subject_map_value", dropna=False))
    triples.extend(
        SubjectTriple(subject_rules.iloc[0], excluded_references)
        for _, subject_rules in subject_groups
    )
    query = Query(triples, excluded_references)
    generated_query = query.generate(mapping_rules)

    if generated_query is None:
        return None, None

    chunks = _solutions_to_dataframes(endpoint.query(generated_query))
    if decode_columns:
        chunks = (query.decode_dataframe(chunk) for chunk in chunks)
    return chunks, generated_query

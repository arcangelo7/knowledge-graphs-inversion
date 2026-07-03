# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

"""SPARQL query generation and execution."""

import json
import logging

import pandas as pd

from kgi.base import Endpoint
from kgi.constants import (
    RML_BLANK_NODE,
    RML_CONSTANT,
    RML_PARENT_TRIPLES_MAP,
    RML_REFERENCE,
    RML_TEMPLATE,
)
from kgi.triples import QueryTriple, SubjectTriple, extract_from_iri_template
from kgi.utils import Codex, IdGenerator, Identifier, sparql_to_python_type, url_decode


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
            logging.getLogger("kgi").warning("No references found, no query generated")
            return None

        triple_strings = []

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
                if triple_string is not None:
                    triple_strings.append(triple_string)

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
                        "graph_map_type": triple.rule.get("graph_map_type"),
                        "graph_map_value": triple.rule.get("graph_map_value"),
                        "graph_references": triple.rule.get("graph_references", []),
                        "graph_references_template": triple.rule.get(
                            "graph_references_template"
                        ),
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
            ref = list(graph_info["exclusive_references"])  # type: ignore[arg-type]
            ref_id = Identifier.generate_plain_identifier(rule, str(ref[0])) or str(
                ref[0]
            )
            ref_var = self.codex.get_id(ref_id)
            return f"BIND(STR(?{graph_var}) AS ?{ref_var})\n"

        if graph_map_type == RML_TEMPLATE:
            return (
                extract_from_iri_template(
                    template_value=str(graph_info["graph_map_value"]),
                    references_template=str(graph_info["graph_references_template"]),
                    references=list(graph_info["graph_references"]),  # type: ignore[arg-type]
                    rule=rule,
                    codex=self.codex,
                    id_generator=self.id_generator,
                    slice_label="graph",
                )
                + "\n"
            )

        return ""

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

    def execute_on_endpoint(
        self, endpoint: Endpoint, all_mapping_rules: pd.DataFrame
    ) -> pd.DataFrame:
        """Execute query on a SPARQL endpoint."""
        self.generated_query = self.generate(all_mapping_rules)
        assert self.generated_query is not None
        json_result = endpoint.query(self.generated_query)
        df = _json_sparql_to_dataframe(json_result)
        return self.decode_dataframe(df)


def _json_sparql_to_dataframe(json_result: str) -> pd.DataFrame:
    result_data = json.loads(json_result)
    columns = result_data["head"]["vars"]
    data = []
    for binding in result_data["results"]["bindings"]:
        row = {}
        for col in columns:
            if col in binding:
                value = binding[col]["value"]
                datatype = binding[col].get("datatype")
                row[col] = sparql_to_python_type(value, datatype)
            else:
                row[col] = None
        data.append(row)
    return pd.DataFrame(data, columns=columns)


def retrieve_data(
    mapping_rules: pd.DataFrame,
    source_rules: pd.DataFrame,
    endpoint: Endpoint,
    decode_columns: bool = False,
) -> tuple[pd.DataFrame | None, str | None]:
    """Retrieve data from SPARQL endpoint using mapping rules."""
    triples: list[QueryTriple] = [
        QueryTriple(rule)
        for _, rule in source_rules.iterrows()
        if rule["object_map_type"] not in [RML_BLANK_NODE]
    ]

    subject_groups = list(source_rules.groupby("subject_map_value", dropna=False))
    triples.extend(
        SubjectTriple(subject_rules.iloc[0]) for _, subject_rules in subject_groups
    )
    query = Query(triples)
    generated_query = query.generate(mapping_rules)

    if generated_query is None:
        logging.getLogger("kgi").warning("No query generated (no references found)")
        return None, None

    try:
        result = endpoint.query(generated_query)
        if not result.strip():
            return pd.DataFrame(), generated_query

        df = _json_sparql_to_dataframe(result)

        if decode_columns:
            df = query.decode_dataframe(df)

        return df, generated_query

    except Exception as e:
        logging.getLogger("kgi").warning(f"Error while querying endpoint: {e}")
        raise

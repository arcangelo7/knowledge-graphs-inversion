"""SPARQL query generation and execution."""

import json
import logging
from io import StringIO

import pandas as pd

from .base import Endpoint
from .constants import RML_BLANK_NODE, RML_CONSTANT, RML_PARENT_TRIPLES_MAP, RML_REFERENCE, RML_TEMPLATE
from .triples import QueryTriple, SubjectTriple
from .utils import Codex, IdGenerator, sparql_to_python_type, url_decode


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
        return [ref for ref in self.template_references
                if ref not in literal]

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

        constant_triples = [t for t in object_triples if t.rule["object_map_type"] == RML_CONSTANT]
        reference_triples = [t for t in object_triples if t.rule["object_map_type"] == RML_REFERENCE]
        template_triples = [t for t in object_triples if t.rule["object_map_type"] == RML_TEMPLATE]
        parent_triples = [t for t in object_triples if t.rule["object_map_type"] == RML_PARENT_TRIPLES_MAP]

        for triple_group in [constant_triples, template_triples, reference_triples, parent_triples, subject_triples]:
            for triple in triple_group:
                triple_string = triple.generate(
                    self.id_generator, self.codex, all_mapping_rules
                )
                if triple_string is not None:
                    triple_strings.append(triple_string)

        all_vars = [f'?{self.codex.get_id(ref)}' for ref in all_references]
        select_part = "SELECT " + " ".join(all_vars) + " WHERE {"

        generated_query = select_part + "\n".join(triple_strings) + "}"
        self.generated_query = generated_query.replace("\\", "\\\\")
        return self.generated_query

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

    def execute_on_endpoint(self, endpoint: Endpoint, all_mapping_rules: pd.DataFrame) -> pd.DataFrame:
        """Execute query on a SPARQL endpoint."""
        self.generated_query = self.generate(all_mapping_rules)
        assert self.generated_query is not None
        csv_result = endpoint.query(self.generated_query)
        df = pd.read_csv(StringIO(csv_result))
        return self.decode_dataframe(df)


def retrieve_data(
    mapping_rules: pd.DataFrame,
    source_rules: pd.DataFrame,
    endpoint: Endpoint,
    decode_columns: bool = False,
) -> tuple[pd.DataFrame | None, str | None]:
    """Retrieve data from SPARQL endpoint using mapping rules."""
    triples: list[QueryTriple] = [
        QueryTriple(rule) for _, rule in source_rules.iterrows() 
        if rule["object_map_type"] not in [RML_BLANK_NODE]
    ]
    
    subject_groups = list(source_rules.groupby("subject_map_value", dropna=False))
    triples.extend(
        SubjectTriple(subject_rules.iloc[0])
        for _, subject_rules in subject_groups
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

        if hasattr(endpoint, '_graph'):
            result_data = json.loads(result)
            columns = result_data['head']['vars']
            
            data = []
            bindings = result_data['results']['bindings']
            
            for binding in bindings:
                row = {}
                for col in columns:
                    if col in binding:
                        value = binding[col]['value']
                        datatype = binding[col].get('datatype')
                        row[col] = sparql_to_python_type(value, datatype)
                    else:
                        row[col] = None
                data.append(row)
            df = pd.DataFrame(data, columns=columns)
        else:
            df = pd.read_csv(StringIO(result))

        if decode_columns:
            df = query.decode_dataframe(df)

        return df, generated_query

    except Exception as e:
        logging.getLogger("kgi").warning(f"Error while querying endpoint: {e}")
        raise
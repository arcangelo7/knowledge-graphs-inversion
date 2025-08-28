"""SPARQL query generation and execution."""

import json
import logging
from io import StringIO

import pandas as pd

from .base import Endpoint, Triple
from .constants import QUERY_FULL, RML_BLANK_NODE, RML_CONSTANT, RML_PARENT_TRIPLES_MAP, RML_REFERENCE, RML_TEMPLATE
from .selectors import Selector, SelectorFactory
from .triples import QueryTriple, SubjectTriple
from .utils import Codex, IdGenerator, sparql_to_python_type, url_decode


class Query:
    """Represents a SPARQL query for data inversion."""
    
    def __init__(self, triples: list[QueryTriple] = None, selector: Selector | int = QUERY_FULL):
        self.triples: list[QueryTriple] = triples or []
        if isinstance(selector, int):
            self.selector = SelectorFactory.create(selector)
        else:
            self.selector = selector
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
    def uri_encoded_references(self) -> list[str]:
        """Get references that need URI encoding."""
        uri_encoded_references = set()
        for triple in self.triples:
            uri_encoded_references.update(triple.uri_encoded_references)
        return list(uri_encoded_references)
    
    @property
    def plain_references(self) -> list[str]:
        """Get references that don't need URI encoding."""
        plain_references = set()
        for triple in self.triples:
            plain_references.update(triple.plain_references)
        return list(plain_references)

    @property
    def pure_references(self) -> list[str]:
        """Get references that are neither URI encoded nor plain."""
        return [reference for reference in self.references 
                if reference not in self.uri_encoded_references]

    def generate(self, all_mapping_rules: pd.DataFrame) -> str:
        """Generate SPARQL query string."""
        selected_triples: list[Triple] = self.selector.select(self.triples)
        all_references = self.references
        uri_encoded_references = self.uri_encoded_references
        
        if not all_references:
            logging.getLogger("kgi").warning("No references found, no query generated")
            return None

        triple_strings = []
        
        # Group triples by type for better performance
        constant_triples = [t for t in selected_triples if t.rule["object_map_type"] == RML_CONSTANT]
        reference_triples = [t for t in selected_triples if t.rule["object_map_type"] == RML_REFERENCE]
        template_triples = [t for t in selected_triples if t.rule["object_map_type"] == RML_TEMPLATE]
        parent_triples = [t for t in selected_triples if t.rule["object_map_type"] == RML_PARENT_TRIPLES_MAP]
                            
        # Process triples in order for performance
        for triple_group in [constant_triples, parent_triples, template_triples, reference_triples]:
            for triple in triple_group:
                triple_string = triple.generate(
                    uri_encoded_references, self.id_generator, self.codex, all_mapping_rules
                )
                if triple_string is not None:
                    triple_strings.append(triple_string)
                        
        pure_references = [f'?{self.codex.get_id(reference)}' for reference in self.pure_references]
        
        select_part = "SELECT " + " ".join(
            pure_references + [
                f'?{self.codex.get_id(reference)}_encoded ?{self.codex.get_id(reference)}_datatype'
                for reference in uri_encoded_references
            ]
        ) + " WHERE {"
        
        generated_query = select_part + "\n".join(triple_strings) + "}"
        self.generated_query = generated_query.replace("\\", "\\\\")
        return self.generated_query

    def decode_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Decode query results DataFrame."""
        df = df.copy(deep=True)
        
        for reference in self.uri_encoded_references:
            column_reference = self.codex.get_id(reference)
            encoded_column = f"{column_reference}_encoded"
            datatype_column = f"{column_reference}_datatype"
            
            # Decode the encoded column
            df[encoded_column] = df[encoded_column].apply(url_decode)
            
            # Apply datatype to the data
            if datatype_column in df.columns:
                df[encoded_column] = df.apply(
                    lambda row: sparql_to_python_type(row[encoded_column], row[datatype_column]),
                    axis=1
                )
                df.drop(columns=[datatype_column], inplace=True)
            
            # Rename the column
            df.rename(columns={encoded_column: reference}, inplace=True)
            
        for reference in self.pure_references:
            column_reference = self.codex.get_id(reference)
            df.rename(columns={column_reference: reference}, inplace=True)
            
        return df

    def execute_on_endpoint(self, endpoint: Endpoint, all_mapping_rules: pd.DataFrame) -> pd.DataFrame:
        """Execute query on a SPARQL endpoint."""
        self.generated_query = self.generate(all_mapping_rules)
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
    # Create query triples
    triples: list[QueryTriple] = [
        QueryTriple(rule) for _, rule in source_rules.iterrows() 
        if rule["object_map_type"] not in [RML_BLANK_NODE]
    ]
    
    # Add subject triples for template extraction
    triples.extend(
        SubjectTriple(subject_rules.iloc[0])
        for _, subject_rules in source_rules.groupby("subject_map_value", dropna=False)
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

        # Handle different endpoint types
        if hasattr(endpoint, '_graph'):  # LocalSparqlGraphStore
            result_data = json.loads(result)
            columns = result_data['head']['vars']
            data = []
            for binding in result_data['results']['bindings']:
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
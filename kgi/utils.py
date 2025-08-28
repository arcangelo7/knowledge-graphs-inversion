"""Utility functions and classes."""

import functools
import logging
import re
from decimal import Decimal
from datetime import datetime
from typing import Self
from urllib.parse import ParseResult, unquote, urlparse

import jsonpath_ng
import pandas as pd

from .constants import REF_TEMPLATE_REGEX


class IdGenerator:
    """Generates unique IDs."""
    
    def __init__(self):
        self.counter = 0

    def get_id(self):
        self.counter += 1
        return self.counter

    def reset(self):
        self.counter = 0


class Validator:
    """Validation utilities."""
    
    @staticmethod
    def url(x) -> bool:
        """Check if a string is a valid URL."""
        try:
            result: ParseResult = urlparse(x)
            return all([result.scheme, result.netloc])
        except Exception:
            return False

    @staticmethod
    def df_equals(df1: pd.DataFrame, df2: pd.DataFrame) -> bool:
        """Compare two DataFrames for equality."""
        df1 = df1.copy(deep=True)
        df2 = df2.copy(deep=True)
        
        # Sort by columns and rows
        df1.sort_index(axis=1, inplace=True)
        df1.sort_values(by=list(df1.columns), inplace=True)
        df1.drop_duplicates(inplace=True)
        df2.sort_index(axis=1, inplace=True)
        df2.sort_values(by=list(df2.columns), inplace=True)
        df2.drop_duplicates(inplace=True)
        
        if df1.shape != df2.shape:
            return False
            
        # Check if all rows exist in both DataFrames
        for row in df1.itertuples():
            if row not in df2.itertuples():
                return False

        for row in df2.itertuples():
            if row not in df1.itertuples():
                return False

        return True


class Identifier:
    """Identifier generation utilities."""
    
    @staticmethod
    def generate_plain_identifier(rule: pd.Series, value: str) -> str | None:
        """Generate a plain identifier from a rule and value."""
        source_type: str = rule["source_type"]
        
        if source_type == "CSV":
            return value
        elif source_type == "JSON":
            try:
                return JSONPathFunctions.extend_string_path(rule["iterator"], value)
            except Exception:
                return value
        elif source_type == "RDB":
            return value          
        else:
            logging.getLogger("kgi").error(f"Unsupported source type: {source_type}")
            return None


class Codex:
    """Manages ID mapping for variables."""
    
    def __init__(self):
        self.codex: dict[str, str] = {}
        self.subjects: set[str] = set()
        self.idGenerator = IdGenerator()
    
    def get_id(self, key: str) -> str:
        """Get or create an ID for a key."""
        if key in self.codex.keys():
            return self.codex[key]
        else:
            self.codex[key] = str(self.idGenerator.get_id())
            return self.codex[key]
        
    def get_id_and_is_bound(self, key: str) -> tuple[str, bool]:
        """Get ID and check if key was already bound."""
        is_bound = key in self.codex.keys()
        return self.get_id(key), is_bound


class JSONPathFunctions:
    """JSON path manipulation utilities."""
    
    def __init__(self):
        raise NotImplementedError("This class should not be instantiated")
    
    @staticmethod
    def list_path_steps(jsonpath: jsonpath_ng.JSONPath) -> list[jsonpath_ng.JSONPath]:
        """Break down a JSON path into steps."""
        steps = []
        current = jsonpath
        while isinstance(current, jsonpath_ng.Child):
            steps.append(current.right)
            current = current.left
        steps.append(current)
        return steps[::-1]
    
    @staticmethod
    @functools.cache
    def normalize_json_path(path: str) -> str:
        """Normalize a JSON path string."""
        parsed: jsonpath_ng.Child = jsonpath_ng.parse(path)
        return str(parsed)
    
    @staticmethod
    def extend_string_path(path: str, extension: str) -> str:
        """Extend a JSON path with an additional segment."""
        if ' ' in extension:
            new_path = f"{path}['{extension}']"
        else:
            new_path = f"{path}.{extension}"
        return JSONPathFunctions.normalize_json_path(new_path)


def sparql_to_python_type(value, datatype):
    """Convert SPARQL datatype to Python type."""
    datatype = str(datatype)
    try:
        if datatype == 'http://www.w3.org/2001/XMLSchema#integer':
            return int(value)
        elif datatype == 'http://www.w3.org/2001/XMLSchema#decimal':
            return Decimal(value)
        elif datatype == 'http://www.w3.org/2001/XMLSchema#float':
            return float(value)
        elif datatype == 'http://www.w3.org/2001/XMLSchema#double':
            return float(value)
        elif datatype == 'http://www.w3.org/2001/XMLSchema#boolean':
            return value.lower() == 'true'
        elif datatype == 'http://www.w3.org/2001/XMLSchema#dateTime':
            return datetime.fromisoformat(value)
        elif datatype == 'http://www.w3.org/2001/XMLSchema#date':
            return datetime.strptime(value, "%Y-%m-%d").date()
        else:
            return value
    except (ValueError, TypeError) as e:
        logging.getLogger("kgi").warning(f"Type conversion failed for value '{value}' to datatype '{datatype}': {e}. Returning original value.")
        return value


def url_decode(url):
    """URL decode a string."""
    try:
        return unquote(url) if isinstance(url, str) else url
    except Exception:
        return url


def insert_columns(df: pd.DataFrame, pure=False) -> pd.DataFrame:
    """Insert reference columns into mapping rules DataFrame."""
    if pure:
        df = df.copy(deep=True)

    # Add columns at specific positions
    df.insert(
        df.columns.get_loc("subject_map_value") + 1,
        "subject_references",
        [[] for _ in range(df.shape[0])],
    )
    df.insert(
        df.columns.get_loc("subject_map_value") + 1, "subject_references_template", None
    )
    df.insert(
        df.columns.get_loc("subject_references") + 1, "subject_reference_count", 0
    )
    df.insert(
        df.columns.get_loc("predicate_map_value") + 1,
        "predicate_references",
        [[] for _ in range(df.shape[0])],
    )
    df.insert(
        df.columns.get_loc("predicate_map_value") + 1,
        "predicate_references_template",
        None,
    )
    df.insert(
        df.columns.get_loc("predicate_references") + 1, "predicate_reference_count", 0
    )
    df.insert(
        df.columns.get_loc("object_map_value") + 1,
        "object_references",
        [[] for _ in range(df.shape[0])],
    )
    df.insert(
        df.columns.get_loc("object_map_value") + 1, "object_references_template", None
    )
    df.insert(df.columns.get_loc("object_references") + 1, "object_reference_count", 0)

    # Process each mapping rule to extract references
    for index in df.index:
        # Subject references
        match df.at[index, "subject_map_type"]:
            case "http://w3id.org/rml/constant":
                df.at[index, "subject_references"] = []
                df.at[index, "subject_reference_count"] = 0
            case "http://w3id.org/rml/reference":
                df.at[index, "subject_references"] = [df.at[index, "subject_map_value"]]
                df.at[index, "subject_reference_count"] = 1
            case "http://w3id.org/rml/template":
                references_list = re.findall(
                    REF_TEMPLATE_REGEX, df.at[index, "subject_map_value"]
                )
                df.at[index, "subject_references"] = references_list
                df.at[index, "subject_reference_count"] = len(references_list)
                df.at[index, "subject_references_template"] = (
                    re.sub(
                        REF_TEMPLATE_REGEX,
                        r"([^/]*)",
                        df.at[index, "subject_map_value"],
                    )
                )

        # Predicate references
        match df.at[index, "predicate_map_type"]:
            case "http://w3id.org/rml/constant":
                df.at[index, "predicate_references"] = []
                df.at[index, "predicate_reference_count"] = 0
            case "http://w3id.org/rml/reference":
                df.at[index, "predicate_references"] = [
                    df.at[index, "predicate_map_value"]
                ]
                df.at[index, "predicate_reference_count"] = 1
            case "http://w3id.org/rml/template":
                references_list = re.findall(
                    REF_TEMPLATE_REGEX, df.at[index, "predicate_map_value"]
                )
                df.at[index, "predicate_references"] = references_list
                df.at[index, "predicate_reference_count"] = len(references_list)
                df.at[index, "predicate_references_template"] = (
                    re.sub(
                        REF_TEMPLATE_REGEX,
                        r"([^/]*)",
                        df.at[index, "predicate_map_value"],
                    )
                )

        # Object references
        match df.at[index, "object_map_type"]:
            case "http://w3id.org/rml/constant":
                df.at[index, "object_references"] = []
                df.at[index, "object_reference_count"] = 0
            case "http://w3id.org/rml/reference":
                df.at[index, "object_references"] = [df.at[index, "object_map_value"]]
                df.at[index, "object_reference_count"] = 1
            case "http://w3id.org/rml/template":
                references_list = re.findall(
                    REF_TEMPLATE_REGEX, df.at[index, "object_map_value"]
                )
                df.at[index, "object_references"] = references_list
                df.at[index, "object_reference_count"] = len(references_list)
                df.at[index, "object_references_template"] = (
                    re.sub(
                        REF_TEMPLATE_REGEX, r"([^/]*)", df.at[index, "object_map_value"]
                    )
                )
            case "http://w3id.org/rml/parentTriplesMap":
                import json
                df.at[index, "object_references"] = [
                    list(
                        json.loads(
                            df.at[index, "object_join_conditions"].replace("'", '"')
                        ).values()
                    )[0]["child_value"]
                ]
                df.at[index, "object_reference_count"] = 1

    return df
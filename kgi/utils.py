# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

"""Utility functions and classes."""

import json
import re
from decimal import Decimal
from datetime import datetime
from urllib.parse import ParseResult, unquote, urlparse

import pandas as pd

from kgi.constants import REF_TEMPLATE_REGEX
from kgi.exceptions import UnsupportedMappingError


def _is_delimited(identifier: str) -> bool:
    return identifier.startswith('"') and identifier.endswith('"')


def undelimited_sql_identifier(identifier: str) -> str:
    """Drop the delimiters of a SQL identifier, leaving its case untouched."""
    return identifier[1:-1] if _is_delimited(identifier) else identifier


def normalize_sql_identifier(identifier: str) -> str:
    """Resolve a SQL identifier to its stored form: delimited identifiers keep
    their exact case, undelimited ones are folded to lowercase as PostgreSQL does."""
    return identifier[1:-1] if _is_delimited(identifier) else identifier.lower()


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
        result: ParseResult = urlparse(x)
        return all([result.scheme, result.netloc])


class Codex:
    """Manages ID mapping for variables."""

    def __init__(self):
        self.codex: dict[str, str] = {}
        self.subjects: set[str] = set()
        self.idGenerator = IdGenerator()
        self.variable_counters: dict[str, int] = {}

    def _extract_base_from_url(self, url: str) -> str:
        """Extract meaningful base name from a URL or template."""
        # http://example.com/Student/{ID}/{Name} → [..., 'Student', '{ID}', '{Name}']
        parts = url.rstrip("/").split("/")
        base = parts[-1] if parts[-1] else parts[-2] if len(parts) > 1 else "resource"
        # {"Name"} → Name
        base = base.split("#")[-1].strip('{}"')
        # Template URL: Name → Name_uri (preserves Name for the SELECT variable)
        if "{" in url:
            base = f"{base}_uri"
        return base

    def _generate_descriptive_id(self, key: str) -> str:
        """Generate a descriptive variable name from a key.

        The key can be:
        - An RML template like "http://example.com/{Name}"
        - A column/reference name like "Name"
        - A temporary variable like "Name_temp_1" (created in triples.py for intermediate values)
        - A slice variable like "http://example.com/{Name}_slice_subject_2" (for template parsing)
        - A plain variable like "Name_plain_3" (for non-encoded values)
        """
        # Define suffix patterns and their descriptions
        SUFFIXES = [
            ("_temp_", "_temp"),  # Temporary variables for intermediate values
            ("_slice_", "_slice"),  # Slice variables for substring operations
            ("_plain_", "_plain"),  # Plain variables for non-encoded values
        ]

        # Check for special suffixes and extract base name
        suffix_to_add = ""
        for separator, suffix_label in SUFFIXES:
            if separator in key:
                base_name = key.split(separator)[0]
                suffix_to_add = suffix_label
                break
        else:
            # No special suffix found
            base_name = key

        if "http://" in base_name or "https://" in base_name:
            base_name = self._extract_base_from_url(base_name)

        base_name = self._sanitize_variable_name(base_name)

        if not base_name or base_name.isdigit():
            base_name = "var"

        if suffix_to_add:
            base_name = f"{base_name}{suffix_to_add}"

        if base_name in self.variable_counters:
            self.variable_counters[base_name] += 1
            return f"{base_name}_{self.variable_counters[base_name]}"
        else:
            self.variable_counters[base_name] = 1
            return base_name

    def _sanitize_variable_name(self, name: str) -> str:
        """Sanitize a string to be a valid SPARQL variable name."""
        # Keep only alphanumeric characters and underscores
        sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name)
        # Remove leading/trailing underscores
        sanitized = sanitized.strip("_")
        # Ensure it doesn't start with a number
        if sanitized and sanitized[0].isdigit():
            sanitized = "v_" + sanitized
        return sanitized if sanitized else "var"

    def get_id(self, key: str) -> str:
        """Get or create an ID for a key."""
        if key in self.codex.keys():
            return self.codex[key]
        else:
            self.codex[key] = self._generate_descriptive_id(key)
            return self.codex[key]

    def get_id_and_is_bound(self, key: str) -> tuple[str, bool]:
        """Get ID and check if key was already bound."""
        is_bound = key in self.codex.keys()
        return self.get_id(key), is_bound


def sparql_to_python_type(value, datatype):
    """Convert SPARQL datatype to Python type."""
    datatype = str(datatype)
    if datatype == "http://www.w3.org/2001/XMLSchema#integer":
        return int(value)
    if datatype == "http://www.w3.org/2001/XMLSchema#decimal":
        return Decimal(value)
    if datatype == "http://www.w3.org/2001/XMLSchema#float":
        return float(value)
    if datatype == "http://www.w3.org/2001/XMLSchema#double":
        return float(value)
    if datatype == "http://www.w3.org/2001/XMLSchema#boolean":
        return {
            "true": True,
            "1": True,
            "false": False,
            "0": False,
        }[value]
    if datatype == "http://www.w3.org/2001/XMLSchema#dateTime":
        return datetime.fromisoformat(value)
    if datatype == "http://www.w3.org/2001/XMLSchema#date":
        return datetime.strptime(value, "%Y-%m-%d").date()
    return value


def url_decode(url):
    """URL decode a string."""
    return unquote(url) if isinstance(url, str) else url


def signature_value(value: object) -> str:
    if isinstance(value, list):
        return repr(tuple(str(item) for item in value))
    if value is None or value is pd.NA or value is pd.NaT:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value)


def insert_columns(df: pd.DataFrame, pure=False) -> pd.DataFrame:
    """Insert reference columns into mapping rules DataFrame."""
    if pure:
        df = df.copy(deep=True)

    def _col_pos(name: str) -> int:
        loc = df.columns.get_loc(name)
        assert isinstance(loc, int)
        return loc

    def _empty_lists() -> pd.Series:  # type: ignore[type-arg]
        return pd.Series([[] for _ in range(df.shape[0])])

    def _none_col() -> pd.Series:  # type: ignore[type-arg]
        return pd.Series([None] * df.shape[0], dtype="object")

    # Add columns at specific positions
    df.insert(_col_pos("subject_map_value") + 1, "subject_references", _empty_lists())
    df.insert(
        _col_pos("subject_map_value") + 1, "subject_references_template", _none_col()
    )
    df.insert(_col_pos("subject_references") + 1, "subject_reference_count", 0)
    df.insert(
        _col_pos("predicate_map_value") + 1, "predicate_references", _empty_lists()
    )
    df.insert(
        _col_pos("predicate_map_value") + 1,
        "predicate_references_template",
        _none_col(),
    )
    df.insert(_col_pos("predicate_references") + 1, "predicate_reference_count", 0)
    df.insert(_col_pos("object_map_value") + 1, "object_references", _empty_lists())
    df.insert(
        _col_pos("object_map_value") + 1, "object_references_template", _none_col()
    )
    df.insert(_col_pos("object_references") + 1, "object_reference_count", 0)
    df.insert(_col_pos("graph_map_value") + 1, "graph_references", _empty_lists())
    df.insert(_col_pos("graph_map_value") + 1, "graph_references_template", _none_col())
    df.insert(_col_pos("graph_references") + 1, "graph_reference_count", 0)

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
                df.at[index, "subject_references_template"] = re.sub(
                    REF_TEMPLATE_REGEX,
                    r"([^/]*)",
                    df.at[index, "subject_map_value"],
                )
            case subject_map_type:
                raise UnsupportedMappingError(
                    f"Unsupported subject map type: {subject_map_type}"
                )

        # Predicate references
        predicate_map_type = df.at[index, "predicate_map_type"]
        if pd.notna(predicate_map_type):
            match predicate_map_type:
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
                    df.at[index, "predicate_references_template"] = re.sub(
                        REF_TEMPLATE_REGEX,
                        r"([^/]*)",
                        df.at[index, "predicate_map_value"],
                    )
                case unsupported_predicate_map_type:
                    raise UnsupportedMappingError(
                        f"Unsupported predicate map type: "
                        f"{unsupported_predicate_map_type}"
                    )

        # Object references
        object_map_type = df.at[index, "object_map_type"]
        if pd.notna(object_map_type):
            match object_map_type:
                case "http://w3id.org/rml/constant":
                    df.at[index, "object_references"] = []
                    df.at[index, "object_reference_count"] = 0
                case "http://w3id.org/rml/reference":
                    df.at[index, "object_references"] = [
                        df.at[index, "object_map_value"]
                    ]
                    df.at[index, "object_reference_count"] = 1
                case "http://w3id.org/rml/template":
                    references_list = re.findall(
                        REF_TEMPLATE_REGEX, df.at[index, "object_map_value"]
                    )
                    df.at[index, "object_references"] = references_list
                    df.at[index, "object_reference_count"] = len(references_list)
                    df.at[index, "object_references_template"] = re.sub(
                        REF_TEMPLATE_REGEX, r"([^/]*)", df.at[index, "object_map_value"]
                    )
                case "http://w3id.org/rml/parentTriplesMap":
                    join_conditions = df.at[index, "object_join_conditions"]
                    if pd.notna(join_conditions):
                        df.at[index, "object_references"] = [
                            list(
                                json.loads(join_conditions.replace("'", '"')).values()
                            )[0]["child_value"]
                        ]
                        df.at[index, "object_reference_count"] = 1
                    else:
                        df.at[index, "object_references"] = []
                        df.at[index, "object_reference_count"] = 0
                case unsupported_object_map_type:
                    raise UnsupportedMappingError(
                        f"Unsupported object map type: {unsupported_object_map_type}"
                    )

        # Graph references
        graph_map_type = df.at[index, "graph_map_type"]
        if pd.notna(graph_map_type):
            match graph_map_type:
                case "http://w3id.org/rml/constant":
                    df.at[index, "graph_references"] = []
                    df.at[index, "graph_reference_count"] = 0
                case "http://w3id.org/rml/reference":
                    df.at[index, "graph_references"] = [df.at[index, "graph_map_value"]]
                    df.at[index, "graph_reference_count"] = 1
                case "http://w3id.org/rml/template":
                    references_list = re.findall(
                        REF_TEMPLATE_REGEX, df.at[index, "graph_map_value"]
                    )
                    df.at[index, "graph_references"] = references_list
                    df.at[index, "graph_reference_count"] = len(references_list)
                    df.at[index, "graph_references_template"] = re.sub(
                        REF_TEMPLATE_REGEX, r"([^/]*)", df.at[index, "graph_map_value"]
                    )
                case unsupported_graph_map_type:
                    raise UnsupportedMappingError(
                        f"Unsupported graph map type: {unsupported_graph_map_type}"
                    )

    return df

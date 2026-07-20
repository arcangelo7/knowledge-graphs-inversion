# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

"""Triple classes for SPARQL query generation."""

import json
from typing import cast

import pandas as pd

from kgi.base import Triple
from kgi.constants import (
    RML_BLANK_NODE,
    RML_CONSTANT,
    RML_DEFAULT_GRAPH,
    RML_IRI,
    RML_LITERAL,
    RML_PARENT_TRIPLES_MAP,
    RML_REFERENCE,
    RML_TEMPLATE,
)
from kgi.exceptions import UnsupportedMappingError
from kgi.utils import Codex, IdGenerator


def _same_value_filter(left_var: str, right_var: str) -> str:
    return (
        f"FILTER(!BOUND(?{left_var}) || STR(?{left_var}) = STR(?{right_var}) "
        f"|| ENCODE_FOR_URI(STR(?{left_var})) = STR(?{right_var}) "
        f"|| STR(?{left_var}) = ENCODE_FOR_URI(STR(?{right_var})))"
    )


def _has_adjacent_template_captures(references_template: str) -> bool:
    parts = references_template.split("([^/]*)")
    return any(part == "" for part in parts[1:-1])


def extract_from_iri_template(
    template_value: str,
    references_template: str,
    references: list[str],
    rule: pd.Series,
    codex: Codex,
    id_generator: IdGenerator,
    slice_label: str,
) -> str:
    """Generate SPARQL FILTER + BIND patterns to extract column values from a template IRI.

    Shared by SubjectTriple (subject templates) and graph map extraction.
    """
    source_var = codex.get_id(template_value)

    lines = []
    lines.append(f"FILTER(REGEX(STR(?{source_var}), '{references_template}'))")

    evaluated_template = references_template
    current_slice = source_var

    for reference in references:
        current_pre_string = evaluated_template.split("(", 1)[0]
        current_post_string = evaluated_template.split(")", 1)[1]
        ref_str = str(reference)
        reference_identifier = ref_str
        current_reference, already_bound = codex.get_id_and_is_bound(
            reference_identifier
        )

        if current_post_string == "":
            target = (
                current_reference
                if not already_bound
                else codex.get_id(
                    f"{template_value}_slice_{slice_label}_{id_generator.get_id()}"
                )
            )
            lines.append(
                f"BIND(STRAFTER(STR(?{current_slice}), '{current_pre_string}') as ?{target})"
            )
            if already_bound:
                lines.append(_same_value_filter(current_reference, target))
        else:
            next_pre_string = current_post_string.split("(", 1)[0]
            next_slice = codex.get_id(
                f"{template_value}_slice_{slice_label}_{id_generator.get_id()}"
            )
            lines.append(
                f"BIND(STRAFTER(STR(?{current_slice}), '{current_pre_string}') as ?{next_slice})"
            )
            target = (
                current_reference
                if not already_bound
                else codex.get_id(
                    f"{reference_identifier}_temp_{id_generator.get_id()}"
                )
            )
            lines.append(
                f"BIND(STRBEFORE(STR(?{next_slice}), '{next_pre_string}') AS ?{target})"
            )
            if already_bound:
                lines.append(_same_value_filter(current_reference, target))
            current_slice = next_slice

        evaluated_template = current_post_string

    return "\n".join(lines)


class QueryTriple(Triple):
    """Represents a query triple with subject, predicate, and object."""

    def __init__(self, rule: pd.Series):
        self.rule = rule

    @property
    def references(self) -> set[str]:
        """Get all references used in this triple."""
        return set.union(
            self.subject_references,
            self.predicate_references,
            self.object_references,
            self.graph_references,
        )

    @property
    def template_extracted_references(self) -> set[str]:
        """Get references extracted from URI templates (subject, predicate, object, graph template)."""
        refs = set.union(self.subject_references, self.predicate_references)
        if self.rule["object_map_type"] == RML_TEMPLATE:
            refs = refs.union(self.object_references)
        graph_map_type = self.rule["graph_map_type"]
        if graph_map_type == RML_TEMPLATE:
            refs = refs.union(self.graph_references)
        return refs

    @property
    def plain_references(self) -> set[str]:
        """Get references available directly from object literals."""
        refs: set[str] = set()
        if self.rule["object_map_type"] in (RML_REFERENCE, RML_PARENT_TRIPLES_MAP):
            refs = set(self.object_references)
        graph_map_type = self.rule["graph_map_type"]
        if graph_map_type == RML_REFERENCE:
            refs = refs.union(self.graph_references)
        return refs

    @property
    def subject_references(self) -> set[str]:
        """Get subject references."""
        return {str(value) for value in self.rule["subject_references"]}

    @property
    def predicate_references(self) -> set[str]:
        """Get predicate references."""
        return {str(value) for value in self.rule["predicate_references"]}

    @property
    def object_references(self) -> set[str]:
        """Get object references."""
        return {str(value) for value in self.rule["object_references"]}

    @property
    def graph_references(self) -> set[str]:
        """Get graph map references."""
        graph_refs = cast(list[object], self.rule["graph_references"])
        return {str(value) for value in graph_refs}

    def _wrap_in_graph(self, pattern: str) -> str:
        graph_iri = self._graph_iri()
        if graph_iri is not None:
            return f"GRAPH <{graph_iri}> {{\n{pattern}\n}}"
        return pattern

    def _graph_iri(self) -> str | None:
        graph_map_type = self.rule["graph_map_type"]
        if graph_map_type == RML_CONSTANT:
            graph_iri = str(self.rule["graph_map_value"])
            if graph_iri != RML_DEFAULT_GRAPH:
                return graph_iri
        return None

    def generate(
        self, id_generator: IdGenerator, codex: Codex, all_mapping_rules: pd.DataFrame
    ) -> str | None:
        """Generate SPARQL triple pattern, wrapped in GRAPH block if needed."""
        pattern = self._generate_pattern(id_generator, codex, all_mapping_rules)
        if pattern is None:
            return None
        if str(self.rule["object_map_type"]) == RML_PARENT_TRIPLES_MAP:
            return pattern
        return self._wrap_in_graph(pattern)

    def _generate_pattern(
        self, id_generator: IdGenerator, codex: Codex, all_mapping_rules: pd.DataFrame
    ) -> str | None:
        subject_reference = codex.get_id(str(self.rule["subject_map_value"]))
        predicate = f"<{self.rule['predicate_map_value']}>"
        object_map_value = str(self.rule["object_map_value"])
        object_map_type = str(self.rule["object_map_type"])
        object_references_template = str(self.rule["object_references_template"])

        if object_map_type == RML_CONSTANT:
            object_term_type = self.rule["object_termtype"]
            if object_term_type == RML_IRI:
                object_map_value = f"<{object_map_value}>"
            elif object_term_type == RML_BLANK_NODE:
                raise UnsupportedMappingError(
                    "Blank node constant object maps are not supported"
                )
            elif object_term_type == RML_LITERAL:
                object_map_value = f'"{object_map_value}"'
            return f"?{subject_reference} {predicate} {object_map_value} ."

        if object_map_type == RML_REFERENCE:
            object_identifier = object_map_value
            object_reference, already_bound = codex.get_id_and_is_bound(
                object_identifier
            )

            lines = []
            temp_object_reference, already_bound = codex.get_id_and_is_bound(
                f"{object_identifier}_temp_{id_generator.get_id()}"
            )
            if already_bound:
                lines.append(
                    f"?{subject_reference} {predicate} ?{temp_object_reference} ."
                )
                lines.append(f"BIND(?{temp_object_reference} as ?{object_reference})")
                lines.append(
                    f"FILTER(!BOUND(?{object_reference}) || !BOUND(?{temp_object_reference})  || ?{temp_object_reference} = ?{object_reference})"
                )
            else:
                lines.append(f"?{subject_reference} {predicate} ?{object_reference} .")
            return "\n".join(lines)

        elif object_map_type == RML_TEMPLATE:
            object_identifier = object_map_value
            object_reference, already_bound = codex.get_id_and_is_bound(
                object_identifier
            )
            lines = []
            lines.append(f"?{subject_reference} {predicate} ?{object_reference}")

            evaluated_template = object_references_template
            current_slice = object_reference

            for obj in self.rule["object_references"]:
                current_pre_string = evaluated_template.split("(", 1)[0]
                current_post_string = evaluated_template.split(")", 1)[1]
                next_pre_string = current_post_string.split("(", 1)[0]
                obj_str = str(obj)
                object_identifier = obj_str
                object_reference, already_bound = codex.get_id_and_is_bound(
                    object_identifier
                )
                next_slice_identifier = (
                    f"{object_identifier}_slice_{id_generator.get_id()}"
                )
                next_slice = codex.get_id(next_slice_identifier)
                unescaped_current_pre_string = current_pre_string.replace("\\", "")
                unescaped_next_pre_string = next_pre_string.replace("\\", "")

                lines.append(
                    f"BIND(STRAFTER(STR(?{current_slice}), '{unescaped_current_pre_string}') as ?{next_slice})"
                )

                if current_post_string == "":
                    if not already_bound:
                        lines.append(f"BIND(?{next_slice} as ?{object_reference})")
                else:
                    temp_reference_identifier = (
                        f"{object_identifier}_temp_{id_generator.get_id()}"
                    )
                    temp_reference = codex.get_id(temp_reference_identifier)
                    lines.append(
                        f"BIND(STRBEFORE(STR(?{next_slice}), '{unescaped_next_pre_string}') AS ?{temp_reference})"
                    )
                    if not already_bound:
                        lines.append(f"BIND(?{temp_reference} as ?{object_reference})")

                evaluated_template = current_post_string
                current_slice = next_slice

            return "\n".join(lines)

        elif object_map_type == RML_PARENT_TRIPLES_MAP:
            object_parent_triples_map_id = self.rule["object_map_value"]
            object_rule = all_mapping_rules[
                all_mapping_rules["triples_map_id"] == object_parent_triples_map_id
            ].iloc[0]
            object_map_value = object_rule["subject_map_value"]
            object_reference = codex.get_id(object_map_value)
            predicate = f"<{self.rule['predicate_map_value']}>"

            graph_iri = self._graph_iri()
            if graph_iri is not None:
                lines = [
                    f"OPTIONAL {{ GRAPH <{graph_iri}> {{ ?{subject_reference} {predicate} ?{object_reference} ."
                ]
            else:
                lines = [
                    f"OPTIONAL {{ ?{subject_reference} {predicate} ?{object_reference} ."
                ]

            raw_join_value = self.rule["object_join_conditions"]
            join_conditions = json.loads(cast(str, raw_join_value).replace("'", '"'))
            parent_template = object_rule["subject_references_template"]
            parent_references = object_rule["subject_references"]

            for jc in join_conditions.values():
                child_value = str(jc["child_value"])
                parent_value = jc["parent_value"]
                child_identifier = child_value
                child_ref, child_already_bound = codex.get_id_and_is_bound(
                    child_identifier
                )

                evaluated_template = parent_template
                current_slice = object_reference

                for ref in parent_references:
                    pre_string = evaluated_template.split("(", 1)[0]
                    post_string = evaluated_template.split(")", 1)[1]
                    next_slice_id = (
                        f"{object_map_value}_join_slice_{id_generator.get_id()}"
                    )
                    next_slice = codex.get_id(next_slice_id)
                    lines.append(
                        f"BIND(STRAFTER(STR(?{current_slice}), '{pre_string}') as ?{next_slice})"
                    )

                    if ref == parent_value:
                        if post_string == "":
                            if not child_already_bound:
                                lines.append(f"BIND(?{next_slice} as ?{child_ref})")
                        else:
                            next_pre = post_string.split("(", 1)[0]
                            temp_id = f"{child_identifier}_temp_{id_generator.get_id()}"
                            temp_ref = codex.get_id(temp_id)
                            lines.append(
                                f"BIND(STRBEFORE(STR(?{next_slice}), '{next_pre}') AS ?{temp_ref})"
                            )
                            if not child_already_bound:
                                lines.append(f"BIND(?{temp_ref} as ?{child_ref})")
                        break

                    evaluated_template = post_string
                    current_slice = next_slice

            if graph_iri is not None:
                lines.append("} }")
            else:
                lines.append("}")
            return "\n".join(lines)

        raise UnsupportedMappingError(f"Unsupported object map type: {object_map_type}")


class SubjectTriple(QueryTriple):
    """Represents a subject triple for template extraction."""

    def __init__(self, rule: pd.Series):
        super().__init__(rule)

    @property
    def template_extracted_references(self) -> set[str]:
        """Subject references extracted from templates (not column references)."""
        if self.rule["subject_map_type"] == RML_REFERENCE:
            return set()
        return self.subject_references

    @property
    def plain_references(self) -> set[str]:
        """Column-reference subjects are plain references (no URL decoding)."""
        if self.rule["subject_map_type"] == RML_REFERENCE:
            return self.subject_references
        return set()

    def generate(
        self, id_generator: IdGenerator, codex: Codex, all_mapping_rules: pd.DataFrame
    ) -> str | None:  # pyright: ignore[reportUnusedParameter]
        """Generate SPARQL pattern for subject extraction."""
        subject_map_type = self.rule["subject_map_type"]
        subject_term_type = self.rule["subject_termtype"]

        if subject_map_type == RML_REFERENCE:
            # Column-reference subjects: the subject variable already binds
            # to the IRI which IS the column value. No extraction needed.
            return None

        if subject_map_type == RML_TEMPLATE:
            all_already_bound = all(
                str(ref) in codex.codex for ref in self.rule["subject_references"]
            )
            if all_already_bound:
                if subject_term_type == RML_BLANK_NODE:
                    return None
                if _has_adjacent_template_captures(
                    str(self.rule["subject_references_template"])
                ):
                    return None

            if subject_term_type == RML_IRI:
                return self._generate_iri_template(codex, id_generator)
            elif subject_term_type == RML_BLANK_NODE:
                return self._generate_blank_node_template(codex, id_generator)

        raise UnsupportedMappingError(
            f"Unsupported subject map type: {subject_map_type} or "
            f"subject term type: {subject_term_type}"
        )

    def _generate_iri_template(self, codex: Codex, id_generator: IdGenerator):
        """Generate SPARQL for IRI template."""
        return extract_from_iri_template(
            template_value=str(self.rule["subject_map_value"]),
            references_template=str(self.rule["subject_references_template"]),
            references=list(self.rule["subject_references"]),
            rule=self.rule,
            codex=codex,
            id_generator=id_generator,
            slice_label="subject",
        )

    def _generate_blank_node_template(self, codex: Codex, id_generator: IdGenerator):
        """Generate SPARQL for blank node template."""
        subject_map_value = str(self.rule["subject_map_value"])
        subject_references_template = str(self.rule["subject_references_template"])
        subject_reference = codex.get_id(subject_map_value)
        normalized_subject_reference = codex.get_id(
            f"{subject_map_value}_blank_node_label"
        )

        lines = [
            f"BIND(REPLACE(REPLACE(STR(?{subject_reference}), '^urn:bnode:', ''), '^_:', '') AS ?{normalized_subject_reference})"
        ]
        evaluated_template = subject_references_template
        current_slice_reference = normalized_subject_reference

        for reference in self.rule["subject_references"]:
            current_pre_string = evaluated_template.split("(", 1)[0]
            current_post_string = (
                evaluated_template.split(")", 1)[1] if ")" in evaluated_template else ""
            )

            next_slice_reference_identifier = (
                f"{subject_map_value}_slice_{id_generator.get_id()}"
            )
            next_slice_reference = codex.get_id(next_slice_reference_identifier)

            ref_str = str(reference)
            reference_identifier = ref_str
            current_reference, already_bound = codex.get_id_and_is_bound(
                reference_identifier
            )

            unescaped_current_pre_string = current_pre_string.replace("\\", "")
            if current_post_string == "":
                target = (
                    current_reference
                    if not already_bound
                    else codex.get_id(
                        f"{reference_identifier}_temp_{id_generator.get_id()}"
                    )
                )
                lines.append(
                    f"BIND(STRAFTER(STR(?{current_slice_reference}), '{unescaped_current_pre_string}') as ?{target})"
                )
                if already_bound:
                    lines.append(_same_value_filter(current_reference, target))
            else:
                unescaped_next_pre_string = current_post_string.split("(", 1)[
                    0
                ].replace("\\", "")
                temp_reference_identifier = (
                    f"{reference_identifier}_temp_{id_generator.get_id()}"
                )
                temp_reference = codex.get_id(temp_reference_identifier)

                lines.append(
                    f"BIND(STRAFTER(STR(?{current_slice_reference}), '{unescaped_current_pre_string}') as ?{next_slice_reference})"
                )
                lines.append(
                    f"BIND(STRBEFORE(STR(?{next_slice_reference}), '{unescaped_next_pre_string}') AS ?{temp_reference})"
                )
                if not already_bound:
                    lines.append(f"BIND(?{temp_reference} as ?{current_reference})")
                else:
                    lines.append(_same_value_filter(current_reference, temp_reference))
                current_slice_reference = next_slice_reference

            evaluated_template = current_post_string

        return "\n".join(lines)

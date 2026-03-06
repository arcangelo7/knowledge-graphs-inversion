"""Triple classes for SPARQL query generation."""

import json
import logging

import pandas as pd

from .base import Triple
from .constants import (
    RML_BLANK_NODE, RML_CONSTANT, RML_IRI, RML_LITERAL, 
    RML_PARENT_TRIPLES_MAP, RML_REFERENCE, RML_TEMPLATE
)
from .utils import Codex, IdGenerator, Identifier


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
            self.object_references
        )

    @property
    def template_extracted_references(self) -> set[str]:
        """Get references extracted from URI templates (subject, predicate, object template)."""
        refs = set.union(
            self.subject_references,
            self.predicate_references
        )
        if self.rule["object_map_type"] == RML_TEMPLATE:
            refs = refs.union(self.object_references)
        return refs

    @property
    def plain_references(self) -> set[str]:
        """Get references available directly from object literals."""
        if self.rule["object_map_type"] in (RML_REFERENCE, RML_PARENT_TRIPLES_MAP):
            return set(self.object_references)
        return set()

    @property
    def subject_references(self) -> set[str]:
        """Get subject references."""
        return {
            ident for value in self.rule["subject_references"]
            if (ident := Identifier.generate_plain_identifier(self.rule, str(value))) is not None
        }

    @property
    def predicate_references(self) -> set[str]:
        """Get predicate references."""
        return {
            ident for value in self.rule["predicate_references"]
            if (ident := Identifier.generate_plain_identifier(self.rule, str(value))) is not None
        }

    @property
    def object_references(self) -> set[str]:
        """Get object references."""
        return {
            ident for value in self.rule["object_references"]
            if (ident := Identifier.generate_plain_identifier(self.rule, str(value))) is not None
        }

    def generate(self, id_generator: IdGenerator,
                codex: Codex, all_mapping_rules: pd.DataFrame) -> str | None:
        """Generate SPARQL triple pattern."""
        subject_reference = codex.get_id(str(self.rule["subject_map_value"]))
        predicate = f'<{self.rule["predicate_map_value"]}>'
        object_map_value = str(self.rule["object_map_value"])
        object_map_type = str(self.rule["object_map_type"])
        object_references_template = str(self.rule["object_references_template"])

        if object_map_type == RML_CONSTANT:
            object_term_type = self.rule["object_termtype"]
            if object_term_type == RML_IRI:
                object_map_value = f'<{object_map_value}>'
            elif object_term_type == RML_BLANK_NODE:
                return None
            elif object_term_type == RML_LITERAL:
                object_map_value = f'"{object_map_value}"'
            return f"?{subject_reference} {predicate} {object_map_value} ."

        if object_map_type == RML_REFERENCE:
            object_identifier = Identifier.generate_plain_identifier(self.rule, object_map_value) or object_map_value
            object_reference, already_bound = codex.get_id_and_is_bound(object_identifier)

            lines = []
            temp_object_reference, already_bound = codex.get_id_and_is_bound(
                f"{object_identifier}_temp_{id_generator.get_id()}"
            )
            if already_bound:
                lines.append(f"?{subject_reference} {predicate} ?{temp_object_reference} .")
                lines.append(f"BIND(?{temp_object_reference} as ?{object_reference})")
                lines.append(f"FILTER(!BOUND(?{object_reference}) || !BOUND(?{temp_object_reference})  || ?{temp_object_reference} = ?{object_reference})")
            else:
                lines.append(f"?{subject_reference} {predicate} ?{object_reference} .")
            return "\n".join(lines)
            
        elif object_map_type == RML_TEMPLATE:
            object_identifier = Identifier.generate_plain_identifier(self.rule, object_map_value) or object_map_value
            object_reference, already_bound = codex.get_id_and_is_bound(object_identifier)
            lines = []
            lines.append(f"?{subject_reference} {predicate} ?{object_reference}")
            
            evaluated_template = object_references_template
            current_slice = object_reference
            
            for obj in self.rule["object_references"]:
                current_pre_string = evaluated_template.split("(", 1)[0]
                current_post_string = evaluated_template.split(")", 1)[1]
                next_pre_string = current_post_string.split("(", 1)[0]
                obj_str = str(obj)
                object_identifier = Identifier.generate_plain_identifier(self.rule, obj_str) or obj_str
                object_reference, already_bound = codex.get_id_and_is_bound(object_identifier)
                next_slice_identifier = f"{object_identifier}_slice_{id_generator.get_id()}"
                next_slice = codex.get_id(next_slice_identifier)
                unescaped_current_pre_string = current_pre_string.replace('\\', "")
                unescaped_next_pre_string = next_pre_string.replace('\\', "")
                
                lines.append(f"BIND(STRAFTER(STR(?{current_slice}), '{unescaped_current_pre_string}') as ?{next_slice})")
                
                if current_post_string == "":
                    if not already_bound:
                        lines.append(f"BIND(?{next_slice} as ?{object_reference})")
                else:
                    temp_reference_identifier = f"{object_identifier}_temp_{id_generator.get_id()}"
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
            predicate = f'<{self.rule["predicate_map_value"]}>'

            lines = [f"OPTIONAL {{ ?{subject_reference} {predicate} ?{object_reference} ."]

            join_conditions = json.loads(
                str(self.rule["object_join_conditions"]).replace("'", '"')
            )
            parent_template = object_rule["subject_references_template"]
            parent_references = object_rule["subject_references"]

            for jc in join_conditions.values():
                child_value = jc["child_value"]
                parent_value = jc["parent_value"]
                child_identifier = Identifier.generate_plain_identifier(self.rule, child_value) or child_value
                child_ref, child_already_bound = codex.get_id_and_is_bound(child_identifier)

                evaluated_template = parent_template
                current_slice = object_reference

                for ref in parent_references:
                    pre_string = evaluated_template.split("(", 1)[0]
                    post_string = evaluated_template.split(")", 1)[1]
                    next_slice_id = f"{object_map_value}_join_slice_{id_generator.get_id()}"
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

            lines.append("}")
            return "\n".join(lines)
            
        else:
            logging.getLogger("kgi").error(f"Unsupported object map type: {object_map_type}")
            return None


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

    def generate(self, id_generator: IdGenerator,
                codex: Codex, all_mapping_rules: pd.DataFrame) -> str | None:  # pyright: ignore[reportUnusedParameter]
        """Generate SPARQL pattern for subject extraction."""
        all_already_bound = all(
            (Identifier.generate_plain_identifier(self.rule, str(ref)) or str(ref)) in codex.codex
            for ref in self.rule["subject_references"]
        )
        if all_already_bound:
            return None

        subject_map_type = self.rule["subject_map_type"]
        subject_term_type = self.rule["subject_termtype"]

        if subject_map_type == RML_REFERENCE:
            # Column-reference subjects: the subject variable already binds
            # to the IRI which IS the column value. No extraction needed.
            return None

        if subject_map_type == RML_TEMPLATE:
            if subject_term_type == RML_IRI:
                return self._generate_iri_template(codex, id_generator)
            elif subject_term_type == RML_BLANK_NODE:
                return self._generate_blank_node_template(codex, id_generator)

        logging.getLogger("kgi").error(
            f"Unsupported subject map type: {subject_map_type} or subject term type: {subject_term_type}"
        )
        return None
    
    def _generate_iri_template(self, codex: Codex, id_generator: IdGenerator):
        """Generate SPARQL for IRI template.

        Example: template http://example.com/Student/{ID}/{Name}
        with references_template http://example.com/Student/([^/]*)/([^/]*)
        """
        subject_map_value = str(self.rule["subject_map_value"])
        subject_references_template = str(self.rule["subject_references_template"])
        # Subject variable: ?Name_uri
        subject_reference = codex.get_id(subject_map_value)

        lines = []
        # FILTER(REGEX(STR(?Name_uri), 'http://example.com/Student/([^/]*)/([^/]*)'))
        lines.append(f"FILTER(REGEX(STR(?{subject_reference}), '{subject_references_template}'))")

        # evaluated_template: http://example.com/Student/([^/]*)/([^/]*)
        evaluated_template = subject_references_template
        current_slice_reference = subject_reference

        for reference in self.rule["subject_references"]:
            # Iteration 1 (ID): pre=http://example.com/Student/ post=/([^/]*)
            # Iteration 2 (Name): pre=/ post=""
            current_pre_string = evaluated_template.split("(", 1)[0]
            current_post_string = evaluated_template.split(")", 1)[1]
            next_pre_string = current_post_string.split("(", 1)[0]
            ref_str = str(reference)
            reference_identifier = Identifier.generate_plain_identifier(self.rule, ref_str) or ref_str
            current_reference, already_bound = codex.get_id_and_is_bound(reference_identifier)

            if current_post_string == "":
                # Last reference (Name): STRAFTER gives the final value directly
                # ?Name_uri_slice → "10/Venus" ; STRAFTER("10/Venus", "/") → "Venus"
                target = current_reference if not already_bound else codex.get_id(
                    f"{subject_map_value}_slice_subject_{id_generator.get_id()}"
                )
                # BIND(STRAFTER(STR(?Name_uri_slice), '/') as ?Name)
                lines.append(f"BIND(STRAFTER(STR(?{current_slice_reference}), '{current_pre_string}') as ?{target})")
            else:
                # Intermediate reference (ID): need slice for next iteration
                # ?Name_uri → "http://example.com/Student/10/Venus"
                # STRAFTER → "10/Venus"
                next_slice_reference = codex.get_id(
                    f"{subject_map_value}_slice_subject_{id_generator.get_id()}"
                )
                # BIND(STRAFTER(STR(?Name_uri), 'http://example.com/Student/') as ?Name_uri_slice)
                lines.append(f"BIND(STRAFTER(STR(?{current_slice_reference}), '{current_pre_string}') as ?{next_slice_reference})")
                # STRBEFORE("10/Venus", "/") → "10"
                target = current_reference if not already_bound else codex.get_id(
                    f"{reference_identifier}_temp_{id_generator.get_id()}"
                )
                # BIND(STRBEFORE(STR(?Name_uri_slice), '/') AS ?ID)
                lines.append(
                    f"BIND(STRBEFORE(STR(?{next_slice_reference}), '{next_pre_string}') AS ?{target})"
                )
                current_slice_reference = next_slice_reference

            evaluated_template = current_post_string

        return "\n".join(lines)

    def _generate_blank_node_template(self, codex: Codex, id_generator: IdGenerator):
        """Generate SPARQL for blank node template."""
        subject_map_value = str(self.rule["subject_map_value"])
        subject_references_template = str(self.rule["subject_references_template"])
        subject_reference = codex.get_id(subject_map_value)

        lines = []
        evaluated_template = subject_references_template
        current_slice_reference = subject_reference

        for reference in self.rule["subject_references"]:
            current_pre_string = evaluated_template.split("(", 1)[0]
            current_post_string = evaluated_template.split(")", 1)[1] if ')' in evaluated_template else ''

            next_slice_reference_identifier = f"{subject_map_value}_slice_{id_generator.get_id()}"
            next_slice_reference = codex.get_id(next_slice_reference_identifier)

            ref_str = str(reference)
            reference_identifier = Identifier.generate_plain_identifier(self.rule, ref_str) or ref_str
            current_reference, already_bound = codex.get_id_and_is_bound(reference_identifier)

            unescaped_current_pre_string = current_pre_string.replace('\\', "")
            if current_post_string == "":
                if not already_bound:
                    lines.append(f"BIND(STRAFTER(STR(?{current_slice_reference}), '{unescaped_current_pre_string}') as ?{current_reference})")
            else:
                unescaped_next_pre_string = current_post_string.split("(", 1)[0].replace('\\', "")
                temp_reference_identifier = f"{reference_identifier}_temp_{id_generator.get_id()}"
                temp_reference = codex.get_id(temp_reference_identifier)

                lines.append(f"BIND(STRAFTER(STR(?{current_slice_reference}), '{unescaped_current_pre_string}') as ?{next_slice_reference})")
                lines.append(f"BIND(STRBEFORE(STR(?{next_slice_reference}), '{unescaped_next_pre_string}') AS ?{temp_reference})")
                if not already_bound:
                    lines.append(f"BIND(?{temp_reference} as ?{current_reference})")
                current_slice_reference = next_slice_reference

            evaluated_template = current_post_string

        return "\n".join(lines)
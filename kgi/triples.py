"""Triple classes for SPARQL query generation."""

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
    def uri_encoded_references(self) -> set[str]:
        """Get references that need URI encoding."""
        object_type = self.rule["object_map_type"]
        if object_type == RML_TEMPLATE:
            return set.union(
                self.subject_references,
                self.predicate_references,
                self.object_references
            )
        return set.union(
            self.subject_references,
            self.predicate_references
        )

    @property
    def plain_references(self) -> set[str]:
        """Get references that don't need URI encoding."""
        if self.rule["object_map_type"] == RML_REFERENCE:
            return set(self.object_references)
        return set()

    @property
    def subject_references(self) -> set[str]:
        """Get subject references."""
        return set(
            [Identifier.generate_plain_identifier(self.rule, value) 
             for value in self.rule["subject_references"]]
        )

    @property
    def predicate_references(self) -> set[str]:
        """Get predicate references."""
        return set(
            [Identifier.generate_plain_identifier(self.rule, value) 
             for value in self.rule["predicate_references"]]
        )

    @property
    def object_references(self) -> set[str]:
        """Get object references."""
        return set(
            [Identifier.generate_plain_identifier(self.rule, value) 
             for value in self.rule["object_references"]]
        )

    def generate(self, encoded_references: set[str], id_generator: IdGenerator, 
                codex: Codex, all_mapping_rules: pd.DataFrame) -> str | None:
        """Generate SPARQL triple pattern."""
        subject_reference = codex.get_id(self.rule["subject_map_value"])
        predicate = f'<{self.rule["predicate_map_value"]}>'
        object_map_value = self.rule["object_map_value"]
        object_map_type = self.rule["object_map_type"]
        object_references_template = self.rule["object_references_template"]
        
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
            object_identifier = Identifier.generate_plain_identifier(self.rule, object_map_value)
            object_reference, already_bound = codex.get_id_and_is_bound(object_identifier)
            
            if object_identifier in encoded_references:
                lines = []
                plain_object_reference, already_bound = codex.get_id_and_is_bound(
                    f"{object_identifier}_plain_{id_generator.get_id()}"
                )
                if already_bound:
                    lines.append(f"OPTIONAL{{?{subject_reference} {predicate} ?{plain_object_reference}. BIND(DATATYPE(?{plain_object_reference}) AS ?{object_reference}_datatype)}}")
                    lines.append(f"OPTIONAL{{BIND(ENCODE_FOR_URI(STR(?{plain_object_reference})) as ?{object_reference}_encoded)}}")
                    lines.append(f"FILTER(!BOUND(?{object_reference}_encoded) || !BOUND(?{plain_object_reference}) || ENCODE_FOR_URI(STR(?{plain_object_reference})) = ?{object_reference}_encoded)")
                else:
                    lines.append(f"OPTIONAL{{?{subject_reference} {predicate} ?{plain_object_reference}. BIND(DATATYPE(?{plain_object_reference}) AS ?{object_reference}_datatype). BIND(ENCODE_FOR_URI(STR(?{plain_object_reference})) as ?{object_reference}_encoded)}}")
                return "\n".join(lines)
            else:
                lines = []
                temp_object_reference, already_bound = codex.get_id_and_is_bound(
                    f"{object_identifier}_temp_{id_generator.get_id()}"
                )
                if already_bound:
                    lines.append(f"OPTIONAL{{?{subject_reference} {predicate} ?{temp_object_reference}}}")
                    lines.append(f"OPTIONAL{{BIND(?{temp_object_reference} as ?{object_reference})}}")
                    lines.append(f"FILTER(!BOUND(?{object_reference}) || !BOUND(?{temp_object_reference})  || ?{temp_object_reference} = ?{object_reference})")
                else:
                    lines.append(f"OPTIONAL{{?{subject_reference} {predicate} ?{object_reference}}}")
                return "\n".join(lines)
            
        elif object_map_type == RML_TEMPLATE:
            object_identifier = Identifier.generate_plain_identifier(self.rule, object_map_value)
            object_reference, already_bound = codex.get_id_and_is_bound(object_identifier)
            lines = []
            lines.append(f"?{subject_reference} {predicate} ?{object_reference}")
            
            evaluated_template = object_references_template
            current_slice = object_reference
            
            for i, obj in enumerate(self.rule["object_references"]):
                current_pre_string = evaluated_template.split("(", 1)[0]
                current_post_string = evaluated_template.split(")", 1)[1]
                next_pre_string = current_post_string.split("(", 1)[0]
                object_identifier = Identifier.generate_plain_identifier(self.rule, obj)
                object_reference, already_bound = codex.get_id_and_is_bound(object_identifier)
                next_slice_identifier = f"{object_identifier}_slice_{id_generator.get_id()}"
                next_slice = codex.get_id(next_slice_identifier)
                unescaped_current_pre_string = current_pre_string.replace('\\', "")
                unescaped_next_pre_string = next_pre_string.replace('\\', "")
                
                lines.append(f"BIND(STRAFTER(STR(?{current_slice}), '{unescaped_current_pre_string}') as ?{next_slice})")
                
                if current_post_string == "":
                    lines.append(f"BIND(?{next_slice} as ?{object_reference}_encoded)")
                else:
                    temp_reference_identifier = f"{object_identifier}_temp_{id_generator.get_id()}"
                    temp_reference = codex.get_id(temp_reference_identifier)
                    lines.append(
                        f"BIND(STRBEFORE(STR(?{next_slice}), '{unescaped_next_pre_string}') AS ?{temp_reference})"
                    )
                    lines.append(f"BIND(?{temp_reference} as ?{object_reference}_encoded)")

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
            return f"?{subject_reference} {predicate} ?{object_reference} ."
            
        else:
            logging.getLogger("kgi").error(f"Unsupported object map type: {object_map_type}")
            return None


class SubjectTriple(QueryTriple):
    """Represents a subject triple for template extraction."""
    
    def __init__(self, rule: pd.Series):
        super().__init__(rule)

    @property
    def uri_encoded_references(self) -> set[str]:
        """Subject references need URI encoding."""
        return self.subject_references
    
    @property
    def plain_references(self) -> set[str]:
        """Subject triples have no plain references."""
        return set()

    def generate(self, encoded_references: set[str], id_generator: IdGenerator, 
                codex: Codex, all_mapping_rules: pd.DataFrame) -> str | None:
        """Generate SPARQL pattern for subject extraction."""
        subject_map_type = self.rule["subject_map_type"]
        subject_term_type = self.rule["subject_termtype"]
        
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
        """Generate SPARQL for IRI template."""
        subject_map_value = self.rule["subject_map_value"]
        subject_references_template = self.rule["subject_references_template"]
        subject_reference = codex.get_id(subject_map_value)
        
        lines = []
        lines.append(f"FILTER(REGEX(STR(?{subject_reference}), '{subject_references_template}'))")
        
        evaluated_template = subject_references_template
        current_slice_reference = subject_reference
        
        for reference in self.rule["subject_references"]:
            current_pre_string = evaluated_template.split("(", 1)[0]
            current_post_string = evaluated_template.split(")", 1)[1]
            next_pre_string = current_post_string.split("(", 1)[0]
            reference_identifier = Identifier.generate_plain_identifier(self.rule, reference)
            current_reference, already_bound = codex.get_id_and_is_bound(reference_identifier)
            next_slice_reference_identifier = f"{subject_map_value}_slice_subject_{id_generator.get_id()}"
            next_slice_reference = codex.get_id(next_slice_reference_identifier)
            lines.append(f"BIND(STRAFTER(STR(?{current_slice_reference}), '{current_pre_string}') as ?{next_slice_reference})")
            
            if current_post_string == "":
                lines.append(f"BIND(?{next_slice_reference} as ?{current_reference}_encoded)")
            else:
                reference_placeholder = codex.get_id(f"{reference_identifier}_temp_{id_generator.get_id()}")
                lines.append(
                    f"BIND(STRBEFORE(STR(?{next_slice_reference}), '{next_pre_string}') AS ?{reference_placeholder})"
                )
                lines.append(f"BIND(?{reference_placeholder} as ?{current_reference}_encoded)")
                
            evaluated_template = current_post_string
            current_slice_reference = next_slice_reference
            
        return "\n".join(lines)

    def _generate_blank_node_template(self, codex: Codex, id_generator: IdGenerator):
        """Generate SPARQL for blank node template."""
        subject_map_value = self.rule["subject_map_value"]
        subject_references_template = self.rule["subject_references_template"]
        subject_reference = codex.get_id(subject_map_value)
        
        lines = []
        evaluated_template = subject_references_template
        current_slice_reference = subject_reference

        for reference in self.rule["subject_references"]:
            current_pre_string = evaluated_template.split("(", 1)[0]
            current_post_string = evaluated_template.split(")", 1)[1] if ')' in evaluated_template else ''

            next_slice_reference_identifier = f"{subject_map_value}_slice_{id_generator.get_id()}"
            next_slice_reference = codex.get_id(next_slice_reference_identifier)

            reference_identifier = Identifier.generate_plain_identifier(self.rule, reference)
            current_reference = codex.get_id(reference_identifier)

            unescaped_current_pre_string = current_pre_string.replace('\\', "")
            if current_post_string == "":
                lines.append(f"BIND(STRAFTER(STR(?{current_slice_reference}), '{unescaped_current_pre_string}') as ?{current_reference}_encoded)")
            else:
                unescaped_next_pre_string = current_post_string.split("(", 1)[0].replace('\\', "")
                temp_reference_identifier = f"{reference_identifier}_temp_{id_generator.get_id()}"
                temp_reference = codex.get_id(temp_reference_identifier)

                lines.append(f"BIND(STRAFTER(STR(?{current_slice_reference}), '{unescaped_current_pre_string}') as ?{next_slice_reference})")
                lines.append(f"BIND(STRBEFORE(STR(?{next_slice_reference}), '{unescaped_next_pre_string}') AS ?{temp_reference})")
                lines.append(f"BIND(?{temp_reference} as ?{current_reference}_encoded)")
                current_slice_reference = next_slice_reference

            evaluated_template = current_post_string

        return "\n".join(lines)
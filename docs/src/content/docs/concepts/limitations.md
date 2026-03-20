---
title: Limitations
description: What can and cannot be inverted.
---

[R2RML](https://www.w3.org/TR/r2rml/) mapping inversion is feasible in many cases, but certain mapping patterns make it structurally impossible to reconstruct the original data. These are not algorithmic shortcomings: they reflect information loss that occurs during the forward RDF transformation.

## Supported RDF serialization formats

Local RDF files must be in N-Triples or N-Quads format. Other serializations (Turtle, RDF/XML, etc.) are not yet supported.

## SQL query logical sources

Mappings that use `rr:sqlQuery` instead of `rr:tableName` define their logical source as an arbitrary SQL query with joins, aggregations, or subqueries. Inverting the result of an arbitrary SQL expression is a different problem from inverting a table mapping, and the algorithm does not attempt it.

## Constant-only mappings

When every term map in a triples map uses `rr:constant`, the generated triples are identical regardless of the source data. The mapping produces the same RDF output whether the table has one row or a thousand, so there is nothing to reconstruct.

## Partial mappings

If a mapping selects only some columns from a table, the unmapped columns have no representation in the RDF graph. The algorithm reconstructs the mapped columns but cannot recover the rest.

## Non-unique subject templates

When a subject template maps multiple source rows to the same IRI, those rows collapse into a single RDF subject. Duplicate triples merge, and the original row count is lost. For example, a table with two identical rows `(Bob, Smith, 30)` mapped through a template `http://example.com/{fname};{lname}` produces one subject with one set of triples.

## NULL values in subject templates

R2RML specifies that if any column referenced by the subject template contains NULL, the entire row generates no triples. Since the row is absent from the RDF graph, there is nothing to reconstruct it from.

## Concatenated template placeholders

The extraction logic relies on literal separators between placeholders to determine where one value ends and the next begins. Templates like `{FirstName}{LastName}` with no separator between them are ambiguous: given the string `JohnSmith`, there is no way to determine the boundary.

## Blank nodes

R2RML requires templates for blank node generation, so the same string extraction applies. When querying a local RDF file, the algorithm parses it directly and preserves the original blank node identifiers, making template inversion possible. When querying a remote triple store, blank node inversion does not work: triple stores replace blank node identifiers with internal opaque labels during loading.

<!--
SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>

SPDX-License-Identifier: ISC
-->

(limitations)=
# Limitations

[R2RML](https://www.w3.org/TR/r2rml/) and [RML](https://kg-construct.github.io/rml-core/spec/docs/) mapping inversion is feasible in many cases, but certain mapping patterns make it structurally impossible to reconstruct the original data. These are not algorithmic shortcomings: they reflect information loss that occurs during the forward RDF transformation. The limitations below apply equally to both mapping languages.

## Supported RDF serialization formats

Local RDF files must be in N-Triples or N-Quads format. Other serializations (Turtle, RDF/XML, etc.) are not yet supported.

## SQL query logical sources

Mappings that use `rr:sqlQuery` (R2RML) or `rml:query` (RML) instead of `rr:tableName` define their logical source as an arbitrary SQL query with joins, aggregations, or subqueries. Inverting the result of an arbitrary SQL expression is a different problem from inverting a table mapping, and the algorithm does not attempt it.

## Constant-only mappings

When every term map in a triples map uses `rr:constant`, the generated triples are identical regardless of the source data. The mapping produces the same RDF output whether the table has one row or a thousand, so there is nothing to reconstruct.

## Partial mappings

If a mapping selects only some columns from a table, the unmapped columns have no representation in the RDF graph. The algorithm reconstructs the mapped columns but cannot recover the rest.

## Non-unique subject templates

When a subject template maps multiple source rows to the same IRI, those rows collapse into a single RDF subject. Duplicate triples merge, and the original row count is lost. For example, a table with two identical rows `(Bob, Smith, 30)` mapped through a template `http://example.com/{fname};{lname}` produces one subject with one set of triples.

## Unassignable columns

Some mappings put a column's values in the graph without recording which column they came from. The values are there, but nothing says where they belong, so the algorithm leaves those columns out of the reconstruction instead of filling them with values that could be wrong. The other columns are reconstructed as usual. When a table has no column left, inversion stops with `NonInvertibleError`.

Five mapping patterns produce such columns.

*Indistinguishable subject templates.* When several triples maps for the same source table use compatible subject templates and emit the same predicate-object patterns, a subject-only column is unassignable. For example, if one triples map uses `http://example.com/{p4}` as its subject and no triple elsewhere exposes `p4`, the graph contains several subjects with the same observable literals and no RDF-level discriminator that identifies which one represents `p4`.

*Indistinguishable predicate maps.* Two predicate maps that connect the same subject and object forms in the same graph, and that build IRIs of the same shape, are interchangeable. Columns exposed only by those predicate maps are unassignable. A different IRI pattern, object, or graph provides enough evidence to distinguish them.

*Indistinguishable object maps.* Two object maps that hang off the same subject and the same predicate, and that build terms of the same shape, are interchangeable too. For example, `http://example.org/friend/{p2}` and `http://example.org/friend/{p3}` under one predicate produce two IRIs, and the triples they belong to differ in nothing else, so neither IRI can be traced back to `p2` or `p3`. Two column-valued object maps behave the same way, because their literals carry no sign of the column that produced them. Columns that only such object maps expose are unassignable. Object maps that a language tag, a datatype, a graph map or a different template pattern tells apart are inverted normally.

*Indistinguishable graph maps.* Graph maps that build named-graph IRIs from the same pattern, such as `http://example.org/graph{p2}` and `http://example.org/graph{p3}`, are interchangeable: a graph IRI does not say which graph map produced it. Columns that only those graph maps expose are unassignable. Graph maps with distinguishable patterns are inverted normally, one `GRAPH` clause each.

*IRI term types over a bare column.* `rr:column` with `rr:termType rr:IRI`, or a template that is a single placeholder, turns the column value into an IRI resolved against a base IRI. The base cannot be separated from the value afterwards. The column is recovered anyway when another term map exposes it, for example an object map over the same column.

## NULL values in subject templates

R2RML specifies that if any column referenced by the subject template contains NULL, the entire row generates no triples. Since the row is absent from the RDF graph, there is nothing to reconstruct it from.

## Concatenated template placeholders

When a value must be extracted only from a template, the extraction logic relies on literal separators between placeholders to determine where one value ends and the next begins. Templates like `{FirstName}{LastName}` with no separator between them are ambiguous: given the string `JohnSmith`, there is no way to determine the boundary. A separated template can also be ambiguous for a particular term. For example, `{FirstName} {LastName}` maps both `(Maria, De Luca)` and `(Maria De, Luca)` to `Maria De Luca`. KGI checks the observed terms before reconstruction and omits columns exposed only by a template that admits several decompositions. Evidence from another term map still makes a column recoverable.

## Unemitted join keys

A join condition may use columns only to decide which subjects are related. If neither side of the join appears in any RDF term, the graph preserves the relationship but not the original key value. The related rows remain recoverable while the join columns are omitted.

## Triples Maps without predicate-object maps

A [Triples Map](https://www.w3.org/TR/r2rml/#dfn-triples-map) may have no predicate-object map, in which case no RDF is generated. A map with no predicate-object map, subject class, or incoming join is non-invertible.

## Blank nodes

R2RML requires templates for blank node generation, so the same string extraction applies when the blank node label still preserves the generated value. KGI reads local N-Triples and N-Quads files, where it can use the parsed blank node labels. Remote stores are outside this input path, and their blank node labels would not provide stable reconstruction evidence.

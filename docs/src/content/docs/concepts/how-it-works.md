---
# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

title: How inversion works
description: The algorithm behind mapping inversion.
---

The starting point is a relational database table, an [R2RML](https://www.w3.org/TR/r2rml/) or [RML](https://kg-construct.github.io/rml-core/spec/docs/) mapping that was used to transform the table into RDF, and the resulting RDF graph. The goal is to reconstruct the original relational data from the RDF graph using only the mapping document, without access to the original database.

Since the RDF graph is the only data source available for reconstruction, SPARQL provides the extraction mechanism. The mapping document encodes how each column value was transformed into RDF terms during the forward process. The algorithm reads this and generates SPARQL queries that reverse it. RML extends R2RML with support for heterogeneous data sources, but the inversion algorithm targets the relational subset, so the same logic applies to both vocabularies.

The following sections use a running example drawn from the [W3C R2RML test suite, R2RMLTC0007a](https://www.w3.org/2001/sw/rdb2rdf/test-cases/#R2RMLTC0007a). The source table is:

| ID | Name |
|---|---|
| 10 | Venus |

And the corresponding R2RML mapping:

```turtle
@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@base <http://example.com/base/> .

<TriplesMap1>
    a rr:TriplesMap;
    rr:logicalTable [ rr:tableName "\"Student\"" ];
    rr:subjectMap [ rr:template
      "http://example.com/Student/{\"ID\"}/{\"Name\"}" ];
    rr:predicateObjectMap [
      rr:predicate rdf:type ;
      rr:object foaf:Person ] .
```

The forward transformation produces this RDF:

```turtle
<http://example.com/Student/10/Venus> a foaf:Person .
```

## The three stages

The algorithm proceeds in three stages. First, it extracts the source table name from the logical source specification in the mapping. Second, it inspects all term maps to discover which columns the table contains and how their values were transformed into RDF. Third, it assembles a SPARQL query that extracts each column value and executes it against the RDF graph to produce the reconstructed table rows.

## Column discovery

The algorithm identifies which columns need to be reconstructed by inspecting all term maps in the triples map: the subject map, predicate maps, object maps, and graph maps. Each term map that references a column, either through `rr:column` or through a placeholder in `rr:template`, corresponds to a column in the source table. In the running example, the subject template `http://example.com/Student/{ID}/{Name}` references two columns: `ID` and `Name`.

Three types of term maps exist in R2RML, each requiring a different inversion strategy.

### Constant-valued term maps

Constant-valued term maps use `rr:constant` for subjects, predicates, or objects. Since constants do not reference any column, they filter the SPARQL results without contributing to column reconstruction. In the running example, `rr:predicate rdf:type` and `rr:object foaf:Person` are both constants: the generated SPARQL embeds them as a fixed triple pattern `?Name_uri a <.../Person>`, which selects subjects by type without extracting any column value.

### Reference-valued term maps

Reference-valued term maps use `rr:column` to reference a source column directly. The column value appears in the RDF graph as a literal (default) or as an IRI (when `rr:termType rr:IRI` is specified), so the SPARQL query captures it with a variable that binds to the cell value without string manipulation. For example, if a mapping specifies `rr:objectMap [ rr:column "Name" ]` with `rr:predicate foaf:name`, the pattern `?s foaf:name ?Name` binds `?Name` directly to the column value.

### Template-based term maps

Template-based term maps encode column values within IRI structures using `rr:template`. Reconstructing the original values requires string manipulation to reverse the construction.

## Template inversion

The subject template in the running example is `http://example.com/Student/{ID}/{Name}`, which encodes both column values into the IRI. The forward transformation produces `<http://example.com/Student/10/Venus>`. To recover the column values, the algorithm generates the following SPARQL query:

```sparql
SELECT ?Name ?ID WHERE {
  ?Name_uri a <http://xmlns.com/foaf/0.1/Person> .
  FILTER(REGEX(STR(?Name_uri),
    '.../Student/([^/]*)/([^/]*)'))
  BIND(STRAFTER(STR(?Name_uri),
    '.../Student/') AS ?Name_uri_slice)
  BIND(STRBEFORE(STR(?Name_uri_slice),
    '/') AS ?ID)
  BIND(STRAFTER(STR(?Name_uri_slice),
    '/') AS ?Name)
}
```

The `FILTER(REGEX(...))` selects subjects matching the template structure. Sequential `STRAFTER`/`STRBEFORE` calls then extract each column value from left to right: `STRAFTER` removes the prefix up to `.../Student/`, yielding `"10/Venus"`; `STRBEFORE` isolates `"10"` as the ID; `STRAFTER` captures `"Venus"` as the Name. When a column encoded in the subject template also appears as a literal in a predicate-object map, the algorithm uses the literal value directly and skips the template extraction for that column.

This approach generalizes to any template with *n* placeholders separated by literal segments. The extraction relies on these literal segments to determine where one column value ends and the next begins.

## Referencing object maps

Object maps additionally support referencing object maps, which link two triples maps through `rr:parentTriplesMap` and `rr:joinCondition` to represent relationships between tables. Inverting this relationship requires extracting the child column value from the parent subject IRI. Consider this example ([R2RMLTC0009a](https://www.w3.org/2001/sw/rdb2rdf/test-cases/#R2RMLTC0009a)) where the "Student" table references the "Sport" table through a foreign key:

| ID | Name | Sport |
|---|---|---|
| 10 | Venus Williams | 100 |
| 20 | Demi Moore | NULL |

| ID | Name |
|---|---|
| 100 | Tennis |

The mapping links the two triples maps via a join condition matching the child column "Sport" against the parent column "ID":

```turtle
<TriplesMap1>
    rr:logicalTable [ rr:tableName "\"Student\"" ];
    rr:subjectMap [ rr:template
      "http://example.com/resource/student_{\"ID\"}" ];
    rr:predicateObjectMap [
      rr:predicate foaf:name ;
      rr:objectMap [ rr:column "\"Name\"" ] ];
    rr:predicateObjectMap [
      rr:predicate ex:practises ;
      rr:objectMap [
        rr:parentTriplesMap <TriplesMap2>;
        rr:joinCondition [
          rr:child "\"Sport\"" ;
          rr:parent "\"ID\"" ] ] ] .

<TriplesMap2>
    rr:logicalTable [ rr:tableName "\"Sport\"" ];
    rr:subjectMap [ rr:template
      "http://example.com/resource/sport_{\"ID\"}" ];
    rr:predicateObjectMap [
      rr:predicate rdfs:label ;
      rr:objectMap [ rr:column "\"Name\"" ] ] .
```

The forward transformation produces four triples:

```turtle
<http://example.com/resource/student_10> foaf:name "Venus Williams" .
<http://example.com/resource/student_10> ex:practises <http://example.com/resource/sport_100> .
<http://example.com/resource/student_20> foaf:name "Demi Moore" .
<http://example.com/resource/sport_100> rdfs:label "Tennis" .
```

`student_10` has a name and practises `sport_100`, `student_20` has a name but no `practises` triple (the NULL foreign key generates no triple), and `sport_100` has a label. To invert the student table, the algorithm generates:

```sparql
SELECT ?Name ?Sport ?ID WHERE {
  ?student__ID_uri foaf:name ?Name .
  OPTIONAL { ?student__ID_uri
      ex:practises ?sport__ID_uri .
    BIND(STRAFTER(STR(?sport__ID_uri),
      '.../resource/sport_') AS ?join_slice)
    BIND(?join_slice AS ?Sport) }
  FILTER(REGEX(STR(?student__ID_uri),
    '.../resource/student_([^/]*)'))
  BIND(STRAFTER(STR(?student__ID_uri),
    '.../resource/student_') AS ?ID) }
```

The literal name is retrieved directly. The `OPTIONAL` block handles the foreign key: the value "100" is extracted from the parent subject IRI `sport_100` via `STRAFTER`. Since Demi Moore has no `practises` triple, the `?Sport` variable remains unbound, which the algorithm writes back as NULL.

## Graph maps

R2RML allows assigning generated triples to named graphs through `rr:graphMap`, which can use the same constant, reference, and template types as other term maps. A constant graph map (e.g., `rr:graph ex:PersonGraph`) does not reference any column and has no effect on reconstruction: the triples are simply retrieved from the named graph. A template or reference graph map encodes column values in the graph IRI. When these columns also appear in other term maps (subject, predicate, or object), the algorithm recovers them from there without additional extraction. When a column is referenced exclusively by the graph map, the algorithm wraps the triple patterns in a `GRAPH ?g { ... }` clause and applies the same `STRAFTER`/`STRBEFORE` extraction to the graph variable.

## Output generation

Once the SPARQL query returns results, the algorithm passes the resulting data through two optional refinement steps: schema type application (casting columns to their original SQL types when a database schema is available) and column ordering (matching the original column sequence). The data is then materialized in the target database when a destination URL is provided, and the returned result contains the reconstructed rows.

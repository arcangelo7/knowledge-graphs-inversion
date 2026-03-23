## New rml-core repo

The new modular repository (https://github.com/kg-construct/rml-core) currently contains only JSON variants. No database-specific test cases exist yet, so this document captures what is known from the JSON variants for future reference.

### Test cases dropped from the new rml-core repo

The new `rml-core` repo (JSON-only) includes fewer test cases from the 0000-0020 range than the legacy repo.

Most dropped test cases depend on SQL-specific features with no JSON equivalent: RMLTC0002c-d, 0002f, 0002h-j, 0003a-b, 0009c-d, 0011a, 0014d, 0016a-e, and 0018a.

R2RMLTC0014a-c test `rr:inverseExpression`, an R2RML-only optimization construct that RML dropped entirely. R2RMLTC0005b tests "default mapping" generation for tables without a primary key, a concept from R2RML's direct mapping that RML does not define -- every RML mapping must be written explicitly (**right?**).

Whether the SQL-specific tests will reappear as database variants in a future release remains to be seen.

### New test cases in rml-core (JSON only, no R2RML or legacy equivalent)

The new `rml-core` repo introduces test cases RMLTC0021 through RMLTC0031 that have no counterpart in either R2RML or the legacy RML repo. After cross-referencing each test case description (from https://kg-construct.github.io/rml-core/test-cases/docs/) with the KGI inversion code:

| ID           | What it tests                                                                                                                                                                                           | Would our system handle it?                                                                                                                                                                                                                                                              |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| RMLTC0021a   | Self-join: RefObjectMap where `parentTriplesMap` points to the same TriplesMap, with a join on the same field (`Sport = Sport`)                                                                         | Yes, the join extraction in `triples.py` is generic and does not check whether child and parent are the same source                                                                                                                                                                      |
| RMLTC0022a   | Fixed constant datatype on an object map (`rml:datatype xsd:string`)                                                                                                                                    | Yes, morph-kgc parses this; the inversion pipeline ignores datatypes during query generation                                                                                                                                                                                             |
| RMLTC0022b-e | Dynamic datatypes via `rml:datatypeMap`: the datatype IRI is read from the data (reference), built from a template, or set as a constant through a separate map                                         | No, no `datatypeMap` handling exists in the codebase                                                                                                                                                                                                                                     |
| RMLTC0023a-e | Invalid IRI templates (5 error cases expecting failure)                                                                                                                                                 | Unclear, our system has an IRI validator in `utils.py` but it is never called during query generation                                                                                                                                                                                    |
| RMLTC0023f   | Valid IRI template with backslash escape (expects success, not an error)                                                                                                                                | Probably yes                                                                                                                                                                                                                                                                             |
| RMLTC0024a   | Constant term map with conflicting explicit term type (`rml:constant "School"` + `rml:termType rml:BlankNode`)                                                                                          | No, my system does not validate term type consistency                                                                                                                                                                                                                                    |
| RMLTC0025a-c | Array references in JSON (extracting values from nested arrays)                                                                                                                                         | JSON-specific, not applicable to databases                                                                                                                                                                                                                                               |
| RMLTC0026a-d | Base IRI resolution: relative IRIs in templates resolved against `rml:baseIRI` at the TriplesMap level                                                                                                  | No, no base IRI handling in the codebase; all IRIs are treated as absolute                                                                                                                                                                                                               |
| RMLTC0027a-c | Term types `rml:IRI` and `rml:UnsafeIRI` for subjects and objects (UnsafeIRI skips IRI validation)                                                                                                      | Partially, our system generates IRI-based SPARQL patterns but does not distinguish between IRI and UnsafeIRI                                                                                                                                                                             |
| RMLTC0028a   | Constant boolean object (`rml:object true`): the processor must preserve the implicit `xsd:boolean` datatype from Turtle syntax                                                                         | Yes, treated as a constant literal                                                                                                                                                                                                                                                       |
| RMLTC0028b   | Mixed graph assignment within one TriplesMap: `subjectMap` targets a named graph while the `predicateObjectMap` uses `rml:defaultGraph`, so the same triple appears in both the default and named graph | Unclear, needs testing against our graph map handling                                                                                                                                                                                                                                    |
| RMLTC0028c   | Constant object with explicit language tag (`"Venus"@en`)                                                                                                                                               | Yes, treated as a constant literal                                                                                                                                                                                                                                                       |
| RMLTC0029a   | Shortcut syntax `rml:subject ex:example` (equivalent to a constant subject map) with a reference-valued object                                                                                          | No, `SubjectTriple.generate()` has no case for `RML_CONSTANT` subjects. The existing `check_for_constant_only_mappings()` only rejects mappings where every component is a constant, so this mapping would pass validation (the object is a reference) but fail during SPARQL generation |
| RMLTC0030a-f | Joins using `rml:parentMap`/`rml:childMap` (reference-valued, template-valued, and constant-valued variants)                                                                                            | No, the current join handling reads `rml:parent`/`rml:child` as simple field references; `parentMap`/`childMap` with templates or constants is a new construct                                                                                                                           |
| RMLTC0031a-c | Dynamic language tags via `rml:languageMap` (constant, reference, or template-valued)                                                                                                                   | No, no `languageMap` handling in the codebase                                                                                                                                                                                                                                            |

## Vocabulary differences between legacy and new RML

The **legacy** RML mappings use a hybrid vocabulary that mixes R2RML and old RML namespaces. Here is the full mapping for RMLTC0001a-JSON (https://github.com/kg-construct/rml-test-cases/blob/master/test-cases/RMLTC0001a-JSON/mapping.ttl):

```turtle
@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix ex: <http://example.com/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix rml: <http://semweb.mmlab.be/ns/rml#> .
@prefix ql: <http://semweb.mmlab.be/ns/ql#> .

@base <http://example.com/base/> .

<TriplesMap1>
  a rr:TriplesMap;
  rml:logicalSource [
    rml:source "student.json";
    rml:referenceFormulation ql:JSONPath;
    rml:iterator "$.students[*]"
  ] ;
  rr:subjectMap [
    rr:template "http://example.com/{Name}"
  ];
  rr:predicateObjectMap [
    rr:predicate foaf:name ;
    rr:objectMap [
      rml:reference "Name"
    ]
  ].
```

The **new** RML mappings are syntactically very different. The same test case (RMLTC0001a-JSON, https://github.com/kg-construct/rml-core/blob/main/test-cases/RMLTC0001a-JSON/mapping.ttl):

```turtle
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix rml: <http://w3id.org/rml/> .

<http://example.com/base/TriplesMap1> a rml:TriplesMap;
  rml:logicalSource [ a rml:LogicalSource;
      rml:iterator "$.students[*]";
      rml:referenceFormulation rml:JSONPath;
      rml:source [ a rml:RelativePathSource;
          rml:root rml:MappingDirectory;
          rml:path "student.json"
        ]
    ];
  rml:predicateObjectMap [
      rml:objectMap [
          rml:reference "$.Name"
        ];
      rml:predicate foaf:name
    ];
  rml:subjectMap [
      rml:template "http://example.com/{$.Name}"
    ] .
```

| Aspect                                    | Legacy                                                   | New                                                                |
| ----------------------------------------- | -------------------------------------------------------- | ------------------------------------------------------------------ |
| RML namespace                             | `http://semweb.mmlab.be/ns/rml#`                         | `http://w3id.org/rml/`                                             |
| TriplesMap type                           | `rr:TriplesMap`                                          | `rml:TriplesMap`                                                   |
| Subject/predicate/object map properties   | `rr:subjectMap`, `rr:predicateObjectMap`, `rr:objectMap` | `rml:subjectMap`, `rml:predicateObjectMap`, `rml:objectMap`        |
| Template property                         | `rr:template`                                            | `rml:template`                                                     |
| Predicate property                        | `rr:predicate`                                           | `rml:predicate`                                                    |
| Reference formulation vocabulary          | `ql:JSONPath` (separate namespace)                       | `rml:JSONPath` (same namespace)                                    |
| Reference syntax                          | `"Name"` (plain field name)                              | `"$.Name"` (full JSONPath expression)                              |
| Template placeholders                     | `{Name}`                                                 | `{$.Name}`                                                         |
| Source declaration                        | `rml:source "student.json"` (string literal)             | `rml:RelativePathSource` with `rml:root` + `rml:path` (structured) |
| TriplesMap IRI                            | `<TriplesMap1>` (relative to `@base`)                    | `<http://example.com/base/TriplesMap1>` (absolute)                 |

Our system never reads mapping files directly (except for two validation functions discussed below). The mapping parsing is delegated to morph-kgc, which (hopefully) normalizes both the legacy and new vocabularies to the same internal DataFrame representation.

Reviewing the KGI codebase, the inversion pipeline is largely vocabulary-agnostic because morph-kgc handles normalization. There are, however, two functions in `core.py` that parse mapping files directly using the R2RML namespace:

- `check_for_sql_queries()`: looks for `rr:sqlQuery` triples
- `check_for_multiple_subject_maps()`: looks for `rr:TriplesMap` and `rr:subjectMap`

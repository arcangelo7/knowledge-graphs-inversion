## Where the database test cases are

There are two repositories to consider:

1. **Archived repository** (https://github.com/kg-construct/rml-test-cases). This repo is marked as deprecated ("new test cases are published per module") but it remains the only source for database-specific RML test cases.
	- Test case directories spanning RMLTC0000 through RMLTC0020
2. **New modular repository** (https://github.com/kg-construct/rml-core): currently only JSON variants.
	- Contains 76 test cases spanning RMLTC0000 through RMLTC0031. Test cases RMLTC0021 through RMLTC0031 are entirely new and have no equivalent in the archived repo or in R2RML. Compared to the archived repo, the new repo also drops several test cases from the 0000-0020 range (detailed below).

## Comparison between archived RML test cases and R2RML test cases

Our project currently uses 62 R2RML test cases (R2RMLTC0000 through R2RMLTC0020), matching the kg-construct/r2rml-test-cases-support repository. The W3C spec (https://www.w3.org/2001/sw/rdb2rdf/test-cases/) defines 63 test cases, but R2RMLTC0003a ("undefined SQL Version identifier", an error test case) was never implemented in the support repository. The archived RML repo covers the same range (RMLTC0000 through RMLTC0020) with 60 PostgreSQL test cases.

### Direct equivalents

Most RML-PostgreSQL archived test cases map directly to R2RML test cases and test the same features, with only the expected vocabulary and quoting differences (detailed in the "Systematic differences" section below). Test cases marked with * have inconsistencies. Test cases marked with ** are affected by the PostgreSQL case sensitivity bug documented below.

| Feature area                    | R2RML IDs                                   | RML IDs (archived) | Notes                                                                                                          |
| ------------------------------- | ------------------------------------------- | ------------------ | -------------------------------------------------------------------------------------------------------------- |
| Empty table                     | R2RMLTC0000                                 | RMLTC0000          |                                                                                                                |
| One column mapping              | R2RMLTC0001a                                | RMLTC0001a         |                                                                                                                |
| One column mapping              | R2RMLTC0001b                                | RMLTC0001b*        | RML tests SQL query, not blank nodes                                                                           |
| Two column variants             | R2RMLTC0002a-j                              | RMLTC0002a-j       |                                                                                                                |
| Three column mapping            | R2RMLTC0003b-c (0003a missing from our set) | RMLTC0003a-c       |                                                                                                                |
| Multiple triples from one row   | R2RMLTC0004a                                | RMLTC0004a         |                                                                                                                |
| Multiple triples from one row   | R2RMLTC0004b                                | RMLTC0004b*        | Both have invalid `rr:termType rr:Literal` on subject map; R2RML also uses `rr:sqlQuery` RML uses simple table |
| Resource typing                 | R2RMLTC0005a                                | RMLTC0005a**       | Forward mapping fails in RML (case sensitivity bug)                                                            |
| Resource typing                 | R2RMLTC0005b                                | RMLTC0005b**       | Different blank node template separators; forward mapping fails in RML (case sensitivity bug)                  |
| Constants                       | R2RMLTC0006a                                | RMLTC0006a         |                                                                                                                |
| Named graphs and typing         | R2RMLTC0007a-f                              | RMLTC0007a-f*      | Different column names (Name vs FirstName)                                                                     |
| Named graphs and typing         | R2RMLTC0007g-h                              | RMLTC0007g-h*      | Missing rdf:type triple in RML                                                                                 |
| Composite keys, ref object maps | R2RMLTC0008a-c                              | RMLTC0008a-c       |                                                                                                                |
| Foreign keys                    | R2RMLTC0009a-d                              | RMLTC0009a-d       | 0009c-d: `rr:sqlQuery` vs `rml:query`                                                                          |
| Special characters              | R2RMLTC0010a-c                              | RMLTC0010a-c       |                                                                                                                |
| Many-to-many                    | R2RMLTC0011a-b                              | RMLTC0011a-b       |                                                                                                                |
| Blank nodes                     | R2RMLTC0012a                                | RMLTC0012a**       | Different blank node template separators; forward mapping fails in RML (case sensitivity bug)                  |
| Blank nodes                     | R2RMLTC0012b                                | RMLTC0012b*        | Different table names (IOUs/Lives vs persons/lives)                                                            |
| Blank nodes                     | R2RMLTC0012c-d                              | RMLTC0012c-d       |                                                                                                                |
| Blank nodes                     | R2RMLTC0012e                                | RMLTC0012e**       | Forward mapping fails in RML (case sensitivity bug)                                                            |
| Null values                     | R2RMLTC0013a                                | RMLTC0013a**       | Forward mapping fails in RML (case sensitivity bug)                                                            |
| Language tags                   | R2RMLTC0015a-b                              | RMLTC0015a-b       |                                                                                                                |
| SQL datatypes                   | R2RMLTC0016a-e                              | RMLTC0016a-e**     | Forward mapping fails in RML (case sensitivity bug)                                                            |
| CHAR type                       | R2RMLTC0018a                                | RMLTC0018a**       | Forward mapping fails in RML (case sensitivity bug)                                                            |
| IRI values                      | R2RMLTC0019a                                | RMLTC0019a         |                                                                                                                |
| IRI values                      | R2RMLTC0019b                                | RMLTC0019b**       | Forward mapping fails in RML (case sensitivity bug)                                                            |
| IRI errors                      | R2RMLTC0020a-b                              | RMLTC0020a-b**     | Forward mapping fails in RML (case sensitivity bug)                                                            |

The coverage is nearly complete. The legacy RML repo adapted R2RML features to RML vocabulary: for instance, RMLTC0009c-d use `rml:query` instead of R2RML's `rr:sqlQuery` for SQL queries as logical sources (verified in https://github.com/kg-construct/rml-test-cases/blob/master/test-cases/RMLTC0009c-PostgreSQL/mapping.ttl). Similarly, RMLTC0002f-j exist as PostgreSQL variants and test the same SQL identifier scenarios as their R2RML counterparts.

The only R2RML test cases with no equivalent in either the legacy or new RML repos are **R2RMLTC0014a-c**, which test `rr:inverseExpression`, a construct that exists only in R2RML.

Conversely, **RMLTC0003a** exists in the RML legacy repo and corresponds to R2RMLTC0003a, which is defined in the W3C spec but was never added to the r2rml-test-cases-support repository.

The RML legacy repo therefore has 60 PostgreSQL test cases: 62 - 3 (no 0014a-c) + 1 (adds 0003a) = 60.

### PostgreSQL case sensitivity bug in RML test cases

14 RML test cases fail at the forward mapping stage (morph-kgc cannot generate RDF) due to a mismatch between the SQL setup scripts and the mapping files.

The R2RML SQL scripts use quoted identifiers (`CREATE TABLE "IOUs"`), which preserves the original case in PostgreSQL. The RML SQL scripts use unquoted identifiers (`CREATE TABLE IOUs`), which PostgreSQL normalizes to lowercase (`ious`). The mapping files then reference the original mixed-case name (`rr:tableName "IOUs"`), and morph-kgc generates a quoted SQL query (`SELECT ... FROM "IOUs"`). Since the table is stored as `ious`, PostgreSQL raises `relation "IOUs" does not exist`.

---
## New rml-core repo
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


Luckily, our system never reads mapping files directly (except for two validation functions discussed below). The mapping parsing is delegated to morph-kgc, which (hopefully) normalizes both the legacy and new vocabularies to the same internal DataFrame representation

Reviewing the KGI codebase, the inversion pipeline is largely vocabulary-agnostic because morph-kgc handles normalization. There are, however, two functions in `core.py` that parse mapping files directly using the R2RML namespace:

- `check_for_sql_queries()`: looks for `rr:sqlQuery` triples
- `check_for_multiple_subject_maps()`: looks for `rr:TriplesMap` and `rr:subjectMap`

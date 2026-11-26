# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from pathlib import Path

from sqlalchemy import create_engine, text

from conformance.database import DatabaseConnection
from conformance.provenance import generate_provenance
from kgi import Provenance, reconstruct

MAPPING = """
@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix ex: <http://ex.org/> .
@base <http://ex.org/> .

<http://ex.org/mapping#Employee> a rr:TriplesMap ;
    rr:logicalTable [ rr:tableName "\\"Employee\\"" ] ;
    rr:subjectMap [ rr:template "http://ex.org/emp/{id}" ; rr:class ex:Employee ] ;
    rr:predicateObjectMap
        [ rr:predicate ex:name ;
          rr:objectMap [ rr:template "{first_name} {last_name}" ; rr:termType rr:Literal ] ] ,
        [ rr:predicate ex:mailbox ;
          rr:objectMap [ rr:template "mailbox-{email}" ; rr:termType rr:BlankNode ] ] ,
        [ rr:predicate ex:phone ; rr:objectMap [ rr:column "office_phone" ] ] ,
        [ rr:predicate ex:phone ; rr:objectMap [ rr:column "mobile_phone" ] ] ,
        [ rr:predicate ex:profile ;
          rr:objectMap [ rr:column "profile_iri" ; rr:termType rr:IRI ] ] ,
        [ rr:predicate ex:worksIn ;
          rr:objectMap [ rr:parentTriplesMap <http://ex.org/mapping#Department> ;
                         rr:joinCondition [ rr:child "dept_id" ; rr:parent "id" ] ] ] .

<http://ex.org/mapping#Department> a rr:TriplesMap ;
    rr:logicalTable [ rr:tableName "\\"Department\\"" ] ;
    rr:subjectMap [ rr:template "http://ex.org/dept/{code}" ; rr:class ex:Department ] .
"""

SOURCE = """
CREATE TABLE "Employee" (
    id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, email TEXT,
    office_phone TEXT, mobile_phone TEXT, salary INTEGER, dept_id INTEGER,
    profile_iri TEXT);
CREATE TABLE "Department" (id INTEGER PRIMARY KEY, code TEXT);
INSERT INTO "Employee" VALUES
    (10, 'Hal', 'Incandenza', 'hal@ex.org', '202-555-0115', '202-555-0142',
     4800, 1, 'profiles/hal'),
    (20, 'Joelle', 'van Dyne', NULL, '202-555-0137', '202-555-0184',
     5200, 1, 'profiles/joelle'),
    (30, 'Don', 'Gately', 'don@ex.org', '202-555-0126', '202-555-0173',
     4500, 3, 'profiles/don');
INSERT INTO "Department" VALUES (1, 'R&D'), (2, 'HR'), (3, NULL), (4, 'HR')
"""

EMPLOYEE = "http://ex.org/emp/"
GRAPH = f"""\
<{EMPLOYEE}10> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://ex.org/Employee> .
<{EMPLOYEE}10> <http://ex.org/name> "Hal Incandenza" .
<{EMPLOYEE}10> <http://ex.org/mailbox> _:b1 .
<{EMPLOYEE}10> <http://ex.org/phone> "202-555-0115" .
<{EMPLOYEE}10> <http://ex.org/phone> "202-555-0142" .
<{EMPLOYEE}10> <http://ex.org/profile> <http://ex.org/profiles/hal> .
<{EMPLOYEE}10> <http://ex.org/worksIn> <http://ex.org/dept/R%26D> .
<{EMPLOYEE}20> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://ex.org/Employee> .
<{EMPLOYEE}20> <http://ex.org/name> "Joelle van Dyne" .
<{EMPLOYEE}20> <http://ex.org/phone> "202-555-0137" .
<{EMPLOYEE}20> <http://ex.org/phone> "202-555-0184" .
<{EMPLOYEE}20> <http://ex.org/profile> <http://ex.org/profiles/joelle> .
<{EMPLOYEE}20> <http://ex.org/worksIn> <http://ex.org/dept/R%26D> .
<{EMPLOYEE}30> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://ex.org/Employee> .
<{EMPLOYEE}30> <http://ex.org/name> "Don Gately" .
<{EMPLOYEE}30> <http://ex.org/mailbox> _:b2 .
<{EMPLOYEE}30> <http://ex.org/phone> "202-555-0126" .
<{EMPLOYEE}30> <http://ex.org/phone> "202-555-0173" .
<{EMPLOYEE}30> <http://ex.org/profile> <http://ex.org/profiles/don> .
<http://ex.org/dept/R%26D> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://ex.org/Department> .
<http://ex.org/dept/HR> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://ex.org/Department> .
"""


def test_without_the_source_database_the_columns_come_from_the_mapping(
    tmp_path: Path,
) -> None:
    source_url = f"sqlite:///{tmp_path / 'source.sqlite'}"
    engine = create_engine(source_url)
    with engine.begin() as connection:
        for statement in SOURCE.split(";"):
            connection.execute(text(statement))
    engine.dispose()
    mapping = tmp_path / "mapping.ttl"
    mapping.write_text(MAPPING)
    graph = tmp_path / "graph.nq"
    graph.write_text(GRAPH)
    with_provenance = tmp_path / "provenance.nq"
    generate_provenance(mapping, graph, source_url, with_provenance, Provenance.FULL)
    dest_url = f"sqlite:///{tmp_path / 'destination.sqlite'}"

    reconstruct(
        mapping=mapping,
        rdf_graph=with_provenance,
        dest_db_url=dest_url,
        provenance=Provenance.FULL,
    )

    destination = DatabaseConnection().get_database_content(dest_url)
    assert destination["Employee"]["columns"] == [
        "dept_id",
        "email",
        "first_name",
        "id",
        "last_name",
        "mobile_phone",
        "office_phone",
        "profile_iri",
    ]
    assert sorted(destination["Department"]["data"]) == [
        ["HR", "2"],
        ["HR", "4"],
        ["R&D", "1"],
    ]

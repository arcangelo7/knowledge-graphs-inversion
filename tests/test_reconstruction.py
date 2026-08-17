# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import MetaData, Table, create_engine, select

from kgi import UnsupportedMappingError, analyze_mapping, reconstruct

R2RML_MAPPING = """
@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix ex: <http://example.com/> .

<http://example.com/map/TriplesMap1> a rr:TriplesMap ;
    rr:logicalTable [ rr:tableName "patient" ] ;
    rr:subjectMap [ rr:template "http://example.com/patient/{id}" ] ;
    rr:predicateObjectMap [
        rr:predicate ex:birthdate ;
        rr:objectMap [ rr:column "birthdate" ] ] ;
    rr:predicateObjectMap [
        rr:predicate ex:weight ;
        rr:objectMap [ rr:column "weight" ] ] .
"""

GRAPH = """\
<http://example.com/patient/10> <http://example.com/birthdate> \
"1981-10-10"^^<http://www.w3.org/2001/XMLSchema#date> .
<http://example.com/patient/10> <http://example.com/weight> \
"8.025E1"^^<http://www.w3.org/2001/XMLSchema#double> .
"""

JSON_MAPPING = """
@prefix rml: <http://w3id.org/rml/> .
@prefix ex: <http://example.com/> .

<http://example.com/map/TriplesMap1> a rml:TriplesMap ;
    rml:logicalSource [
        rml:referenceFormulation rml:JSONPath ;
        rml:iterator "$.students[*]" ;
        rml:source [ rml:path "student.json" ] ] ;
    rml:subjectMap [ rml:template "http://example.com/student/{id}" ] ;
    rml:predicateObjectMap [
        rml:predicate ex:name ;
        rml:objectMap [ rml:reference "$.name" ] ] .
"""


def _write(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


def test_reconstruction_without_a_source_database(tmp_path) -> None:
    """The graph and the mapping are enough, as the documentation promises."""
    mapping = _write(tmp_path, "mapping.ttl", R2RML_MAPPING)
    graph = _write(tmp_path, "graph.nt", GRAPH)
    database_url = f"sqlite:///{tmp_path / 'destination.sqlite'}"

    reconstruct(mapping=mapping, rdf_graph=graph, dest_db_url=database_url)

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            table = Table("patient", MetaData(), autoload_with=connection)
            rows = pd.read_sql(select(table), connection).to_dict(orient="records")
    finally:
        engine.dispose()

    assert {column.name: str(column.type) for column in table.columns} == {
        "id": "TEXT",
        "birthdate": "DATE",
        "weight": "FLOAT",
    }
    assert rows == [{"id": "10", "birthdate": date(1981, 10, 10), "weight": 80.25}]


def test_a_non_relational_logical_source_is_rejected(tmp_path) -> None:
    mapping = _write(tmp_path, "mapping.ttl", JSON_MAPPING)
    graph = _write(tmp_path, "graph.nt", "")

    with pytest.raises(UnsupportedMappingError) as rejection:
        analyze_mapping(mapping, graph)

    assert str(rejection.value) == "Only relational logical sources are supported"

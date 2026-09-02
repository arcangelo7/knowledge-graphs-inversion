# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import cast

ParameterValue = bool | int | float | str

KROWN_REPOSITORY = "https://github.com/kg-construct/KROWN.git"
SUITES = ("raw", "duplicates-empty", "mappings", "named-graphs", "joins")
GENERATOR_SUITES = {
    "RawData": "raw",
    "Duplicates": "duplicates-empty",
    "EmptyValues": "duplicates-empty",
    "Mappings": "mappings",
    "NamedGraph": "named-graphs",
    "JoinsRelation": "joins",
    "JoinsMultiple": "joins",
    "JoinsDuplicate": "joins",
}
OFFICIAL_CONFIG_FILES = (
    "benchmark-raw-rmlmapper.json",
    "benchmark-duplicates-empty-values-rmlmapper.json",
    "benchmark-mappings-rmlmapper.json",
    "benchmark-namedgraph-rmlmapper.json",
    "benchmark-joins-relation-rmlmapper.json",
    "benchmark-joins-multiple-rmlmapper.json",
    "benchmark-joins-duplicates-rmlmapper.json",
)


@dataclass(frozen=True)
class KrownScenario:
    identifier: str
    display_name: str
    generator: str
    parameters: dict[str, ParameterValue]
    source_config: str | None
    original_data_format: str | None

    @property
    def suite(self) -> str:
        return GENERATOR_SUITES[self.generator]

    @property
    def hidden_graph_columns(self) -> int:
        """Columns the named graphs expose but no predicate-object map does.

        Dynamic graph maps of a KROWN NamedGraph scenario share a template that
        differs only in the referenced column, so their graph IRIs carry values
        without saying which column produced them. Any such column is therefore
        unrecoverable, and the scenario cannot round-trip.
        """
        if cast(bool, self.parameters["static"]):
            return 0
        graph_columns = max(
            cast(int, self.parameters["number_of_ng_s"]),
            cast(int, self.parameters["number_of_ng_pom"]),
        )
        return max(graph_columns - cast(int, self.parameters["number_of_poms"]), 0)

    @property
    def expected_outcome(self) -> str:
        if self.generator == "RawData" or (
            self.generator in ("Duplicates", "EmptyValues")
            and cast(float, self.parameters["percentage"]) == 0
        ):
            return "FULL"
        # Scenarios whose surplus triples maps or graph maps carry columns the graph
        # cannot attribute: those columns are left out, so the mapping can no longer
        # rebuild the graph from the reconstruction
        if self.generator == "Mappings" and cast(
            int, self.parameters["number_of_tms"]
        ) > cast(int, self.parameters["number_of_poms"]):
            return "AMBIGUOUS"
        if self.generator == "NamedGraph" and self.hidden_graph_columns > 0:
            return "AMBIGUOUS"
        # Both joins generators join on p1..pN while the subject templates read
        # id, so no term map emits the join columns
        if self.suite == "joins":
            return "AMBIGUOUS"
        return "PARTIAL"

    @property
    def generated_name(self) -> str:
        parameters = self.parameters
        if self.generator == "RawData":
            return (
                f"raw_{cast(int, parameters['number_of_members'])}_"
                f"{cast(int, parameters['number_of_properties'])}_"
                f"{cast(int, parameters['value_size'])}"
            )
        if self.generator == "Duplicates":
            return f"duplicates_{cast(float, parameters['percentage'])}_percentage"
        if self.generator == "EmptyValues":
            return f"empty_{cast(float, parameters['percentage'])}_percentage"
        if self.generator == "Mappings":
            return (
                f"mappings_{cast(int, parameters['number_of_tms'])}_"
                f"{cast(int, parameters['number_of_poms'])}"
            )
        if self.generator == "NamedGraph":
            return (
                "namedgraph_"
                f"{cast(int, parameters['number_of_ng_s'])}SM-NG_"
                f"{cast(int, parameters['number_of_ng_pom'])}POM-NG_"
                f"{cast(int, parameters['number_of_tms'])}TM_"
                f"{cast(int, parameters['number_of_poms'])}POM_"
                f"{cast(bool, parameters['static'])}"
            )
        if self.generator == "JoinsRelation":
            return (
                "joins_relations_"
                f"{cast(int, parameters['n'])}-"
                f"{cast(int, parameters['m'])}_"
                f"{cast(float, parameters['percentage'])}"
            )
        if self.generator == "JoinsMultiple":
            return (
                "joins_mutiple_"
                f"{cast(int, parameters['n'])}-"
                f"{cast(int, parameters['m'])}_"
                f"{cast(int, parameters['jc'])}jc_"
                f"{cast(float, parameters['percentage'])}"
            )
        if self.generator == "JoinsDuplicate":
            return (
                "joins_duplicates_"
                f"{cast(int, parameters['number_of_duplicates'])}_"
                f"{parameters['percentage']}"
            )
        raise ValueError(f"Unsupported KROWN generator: {self.generator}")

    @property
    def source_table_count(self) -> int:
        return 2 if self.suite == "joins" else 1

    @property
    def source_rows(self) -> int:
        return cast(int, self.parameters["number_of_members"]) * self.source_table_count

    @property
    def source_cells(self) -> int:
        columns = cast(int, self.parameters["number_of_properties"]) + 1
        return self.source_rows * columns

    @property
    def expected_rdf_statements(self) -> int | None:
        rows = cast(int, self.parameters["number_of_members"])
        if self.generator == "RawData":
            return rows * cast(int, self.parameters["number_of_properties"])
        if self.generator == "Duplicates":
            duplicate_rows = int(
                rows * cast(float, self.parameters["percentage"]) / 100
            )
            distinct_rows = rows - duplicate_rows + int(duplicate_rows > 0)
            return distinct_rows * cast(int, self.parameters["number_of_properties"])
        if self.generator == "EmptyValues":
            empty_rows = int(rows * cast(float, self.parameters["percentage"]) / 100)
            return (rows - empty_rows) * cast(
                int, self.parameters["number_of_properties"]
            )
        if self.generator == "Mappings":
            return (
                rows
                * cast(int, self.parameters["number_of_tms"])
                * cast(int, self.parameters["number_of_poms"])
            )
        if self.generator == "NamedGraph":
            graph_count = cast(int, self.parameters["number_of_ng_s"]) + cast(
                int, self.parameters["number_of_ng_pom"]
            )
            return rows * cast(int, self.parameters["number_of_poms"]) * graph_count
        if self.generator == "JoinsDuplicate":
            properties = cast(int, self.parameters["number_of_properties"])
            duplicates = cast(int, self.parameters["number_of_duplicates"])
            duplicate_rows = rows * cast(float, self.parameters["percentage"]) / 100
            sample_size = int(min(duplicate_rows / duplicates * (duplicates + 1), rows))
            randomizer = random.Random(0)
            randomizer.sample(range(rows), sample_size)
            sampled_parent_rows = randomizer.sample(range(rows), sample_size)
            existing_join_rows = rows - properties
            overwritten_join_rows = sum(
                index < existing_join_rows for index in sampled_parent_rows
            )
            return existing_join_rows - overwritten_join_rows + sample_size
        return None

    @property
    def configuration_overrides(self) -> dict[str, object]:
        overrides: dict[str, object] = {}
        if (
            self.original_data_format is not None
            and self.original_data_format != "postgresql"
        ):
            overrides["data_format"] = {
                "source": self.original_data_format,
                "benchmark": "postgresql",
            }
        if self.source_config is None:
            overrides["scenario"] = "documented KROWN scale absent from config"
        return overrides

    def config_instance(self, resource: str) -> dict[str, object]:
        return {
            "@id": self.identifier,
            "name": self.display_name,
            "generator": self.generator,
            "parameters": {**self.parameters, "engine": resource},
        }


@dataclass(frozen=True)
class KrownSeries:
    name: str
    title: str
    suite: str
    parameter_label: str
    points: tuple[tuple[str, ParameterValue], ...]


def _raw_name(members: int, properties: int, value_size: int) -> str:
    return f"raw_{members}_{properties}_{value_size}"


def _mappings_name(triples_maps: int, predicate_object_maps: int) -> str:
    return f"mappings_{triples_maps}_{predicate_object_maps}"


def _duplicates_name(percentage: int) -> str:
    return f"duplicates_{float(percentage)}_percentage"


def _empty_values_name(percentage: int) -> str:
    return f"empty_{float(percentage)}_percentage"


def _named_graph_name(
    subject_graphs: int,
    predicate_object_graphs: int,
    predicate_object_maps: int,
    static: bool,
) -> str:
    return (
        f"namedgraph_{subject_graphs}SM-NG_{predicate_object_graphs}POM-NG_"
        f"1TM_{predicate_object_maps}POM_{static}"
    )


def _join_relation_name(n: int, m: int) -> str:
    return f"joins_relations_{n}-{m}_50.0"


def _join_conditions_name(conditions: int) -> str:
    return f"joins_mutiple_1-1_{conditions}jc_50.0"


def _join_duplicates_name(value: int) -> str:
    if value == 0:
        return "joins_duplicates_10_0"
    return f"joins_duplicates_{value}_50.0"


SERIES = (
    KrownSeries(
        "raw_rows",
        "Raw data: rows",
        "raw",
        "Rows",
        tuple(
            (_raw_name(value, 20, 0), value)
            for value in (10_000, 100_000, 1_000_000, 10_000_000)
        ),
    ),
    KrownSeries(
        "raw_properties",
        "Raw data: properties",
        "raw",
        "Properties",
        tuple((_raw_name(100_000, value, 0), value) for value in (1, 10, 20, 30)),
    ),
    KrownSeries(
        "raw_value_size",
        "Raw data: value size",
        "raw",
        "Value size (characters)",
        tuple(
            (_raw_name(100_000, 20, value), value)
            for value in (500, 1_000, 5_000, 10_000)
        ),
    ),
    KrownSeries(
        "duplicates_percentage",
        "Duplicates",
        "duplicates-empty",
        "Duplicate rows (%)",
        tuple((_duplicates_name(value), value) for value in (0, 25, 50, 75, 100)),
    ),
    KrownSeries(
        "empty_values_percentage",
        "Empty values",
        "duplicates-empty",
        "Rows with empty values (%)",
        tuple((_empty_values_name(value), value) for value in (0, 25, 50, 75, 100)),
    ),
    KrownSeries(
        "mappings_triples_maps",
        "Mappings: Triples Maps",
        "mappings",
        "Triples Maps",
        tuple((_mappings_name(value, 5), value) for value in (1, 10, 20, 30)),
    ),
    KrownSeries(
        "mappings_predicate_object_maps",
        "Mappings: Predicate-Object Maps",
        "mappings",
        "Predicate-Object Maps",
        tuple((_mappings_name(20, value), value) for value in (1, 3, 5, 10)),
    ),
    *tuple(
        KrownSeries(
            f"named_graphs_subject_{'static' if static else 'dynamic'}",
            f"Named graphs in subject map ({'static' if static else 'dynamic'})",
            "named-graphs",
            "Named graphs",
            tuple(
                (_named_graph_name(value, 0, 20, static), value)
                for value in (1, 5, 10, 15)
            ),
        )
        for static in (True, False)
    ),
    *tuple(
        KrownSeries(
            f"named_graphs_pom_{'static' if static else 'dynamic'}",
            (
                "Named graphs in predicate-object map "
                f"({'static' if static else 'dynamic'})"
            ),
            "named-graphs",
            "Named graphs",
            tuple(
                (_named_graph_name(0, value, 1, static), value)
                for value in (1, 5, 10, 15)
            ),
        )
        for static in (True, False)
    ),
    *tuple(
        KrownSeries(
            f"named_graphs_both_{'static' if static else 'dynamic'}",
            (
                "Named graphs in subject and predicate-object maps "
                f"({'static' if static else 'dynamic'})"
            ),
            "named-graphs",
            "Named graphs in each map",
            tuple(
                (_named_graph_name(value, value, 10, static), value)
                for value in (1, 5, 10, 15)
            ),
        )
        for static in (True, False)
    ),
    KrownSeries(
        "joins_one_to_many",
        "Joins: 1-N relations",
        "joins",
        "Relation",
        tuple(
            (_join_relation_name(1, value), f"1-{value}") for value in (1, 5, 10, 15)
        ),
    ),
    KrownSeries(
        "joins_many_to_one",
        "Joins: N-1 relations",
        "joins",
        "Relation",
        tuple(
            (_join_relation_name(value, 1), f"{value}-1") for value in (1, 5, 10, 15)
        ),
    ),
    KrownSeries(
        "joins_many_to_many",
        "Joins: N-M relations",
        "joins",
        "Relation",
        tuple(
            (_join_relation_name(n, m), f"{n}-{m}")
            for n, m in ((3, 3), (3, 5), (5, 3), (10, 5), (5, 10))
        ),
    ),
    KrownSeries(
        "joins_conditions",
        "Joins: conditions",
        "joins",
        "Join conditions",
        tuple((_join_conditions_name(value), value) for value in (1, 5, 10, 15)),
    ),
    KrownSeries(
        "joins_duplicates",
        "Joins: duplicates",
        "joins",
        "Duplicates",
        tuple((_join_duplicates_name(value), value) for value in (0, 5, 10, 15)),
    ),
)


def _load_config(config_file: Path) -> list[KrownScenario]:
    catalog = cast(
        dict[str, list[dict[str, object]]],
        json.loads(config_file.read_text(encoding="utf-8")),
    )

    scenarios = []
    for value in catalog["instances"]:
        parameters = cast(dict[str, ParameterValue], value["parameters"])
        original_data_format = cast(str, parameters["data_format"])
        parameters["data_format"] = "postgresql"
        # The benchmark selects the engine, so the one the configuration was written
        # for is not part of the scenario
        del parameters["engine"]
        scenarios.append(
            KrownScenario(
                identifier=cast(str, value["@id"]),
                display_name=cast(str, value["name"]),
                generator=cast(str, value["generator"]),
                parameters=parameters,
                source_config=f"data-generator/config/{config_file.name}",
                original_data_format=original_data_format,
            )
        )
    return scenarios


def _documented_scenarios() -> tuple[KrownScenario, ...]:
    common_parameters: dict[str, ParameterValue] = {
        "percentage": 50.0,
        "n": 1,
        "m": 1,
        "number_of_members": 100_000,
        "number_of_properties": 20,
        "value_size": 0,
        "data_format": "postgresql",
    }
    return (
        KrownScenario(
            identifier=(
                "http://example.com/kg-inversion-benchmark/postgresql"
                "#joins-relation-1-1"
            ),
            display_name="1-1 relation",
            generator="JoinsRelation",
            parameters=dict(common_parameters),
            source_config=None,
            original_data_format=None,
        ),
        KrownScenario(
            identifier=(
                "http://example.com/kg-inversion-benchmark/postgresql"
                "#joins-conditions-1"
            ),
            display_name="1 join condition",
            generator="JoinsMultiple",
            parameters={**common_parameters, "jc": 1},
            source_config=None,
            original_data_format=None,
        ),
    )


def load_scenarios(project_root: Path) -> tuple[KrownScenario, ...]:
    target_order = tuple(
        dict.fromkeys(
            scenario_name for series in SERIES for scenario_name, _ in series.points
        )
    )
    target_names = set(target_order)
    config_root = project_root / "KROWN" / "data-generator" / "config"
    selected: dict[str, KrownScenario] = {}
    for filename in OFFICIAL_CONFIG_FILES:
        for scenario in _load_config(config_root / filename):
            if scenario.generated_name in target_names:
                selected.setdefault(scenario.generated_name, scenario)

    for scenario in _documented_scenarios():
        selected[scenario.generated_name] = scenario

    selected_names = set(selected)
    if selected_names != target_names:
        missing = sorted(target_names - selected_names)
        unexpected = sorted(selected_names - target_names)
        raise ValueError(
            f"KROWN configurations and documented series differ: "
            f"missing={missing}, unexpected={unexpected}"
        )
    return tuple(selected[name] for name in target_order)

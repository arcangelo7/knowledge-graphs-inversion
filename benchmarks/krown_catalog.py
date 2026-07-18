# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import json
from dataclasses import dataclass
from pathlib import Path

ParameterValue = bool | int | float | str

KROWN_REPOSITORY = "https://github.com/kg-construct/KROWN.git"
SUITES = ("raw", "mappings", "named-graphs", "joins")
GENERATOR_SUITES = {
    "RawData": "raw",
    "Mappings": "mappings",
    "NamedGraph": "named-graphs",
    "JoinsRelation": "joins",
    "JoinsMultiple": "joins",
}
OFFICIAL_CONFIG_FILES = (
    "benchmark-raw-rmlmapper.json",
    "benchmark-mappings-rmlmapper.json",
    "benchmark-namedgraph-rmlmapper.json",
    "benchmark-joins-relation-rmlmapper.json",
    "benchmark-joins-multiple-rmlmapper.json",
)


def _integer_parameter(parameters: dict[str, ParameterValue], name: str) -> int:
    value = parameters[name]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"KROWN parameter {name} must be an integer")
    return value


def _float_parameter(parameters: dict[str, ParameterValue], name: str) -> float:
    value = parameters[name]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"KROWN parameter {name} must be numeric")
    return float(value)


def _boolean_parameter(parameters: dict[str, ParameterValue], name: str) -> bool:
    value = parameters[name]
    if not isinstance(value, bool):
        raise TypeError(f"KROWN parameter {name} must be boolean")
    return value


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
    def expected_outcome(self) -> str:
        if self.generator == "RawData":
            return "FULL"
        if self.generator == "Mappings" and _integer_parameter(
            self.parameters, "number_of_tms"
        ) > _integer_parameter(self.parameters, "number_of_poms"):
            return "NON_INVERTIBLE"
        return "PARTIAL"

    @property
    def generated_name(self) -> str:
        parameters = self.parameters
        if self.generator == "RawData":
            return (
                f"raw_{_integer_parameter(parameters, 'number_of_members')}_"
                f"{_integer_parameter(parameters, 'number_of_properties')}_"
                f"{_integer_parameter(parameters, 'value_size')}"
            )
        if self.generator == "Mappings":
            return (
                f"mappings_{_integer_parameter(parameters, 'number_of_tms')}_"
                f"{_integer_parameter(parameters, 'number_of_poms')}"
            )
        if self.generator == "NamedGraph":
            return (
                "namedgraph_"
                f"{_integer_parameter(parameters, 'number_of_ng_s')}SM-NG_"
                f"{_integer_parameter(parameters, 'number_of_ng_pom')}POM-NG_"
                f"{_integer_parameter(parameters, 'number_of_tms')}TM_"
                f"{_integer_parameter(parameters, 'number_of_poms')}POM_"
                f"{_boolean_parameter(parameters, 'static')}"
            )
        if self.generator == "JoinsRelation":
            return (
                "joins_relations_"
                f"{_integer_parameter(parameters, 'n')}-"
                f"{_integer_parameter(parameters, 'm')}_"
                f"{_float_parameter(parameters, 'percentage')}"
            )
        if self.generator == "JoinsMultiple":
            return (
                "joins_mutiple_"
                f"{_integer_parameter(parameters, 'n')}-"
                f"{_integer_parameter(parameters, 'm')}_"
                f"{_integer_parameter(parameters, 'jc')}jc_"
                f"{_float_parameter(parameters, 'percentage')}"
            )
        raise ValueError(f"Unsupported KROWN generator: {self.generator}")

    @property
    def source_table_count(self) -> int:
        return 2 if self.suite == "joins" else 1

    @property
    def source_rows(self) -> int:
        return (
            _integer_parameter(self.parameters, "number_of_members")
            * self.source_table_count
        )

    @property
    def source_cells(self) -> int:
        columns = _integer_parameter(self.parameters, "number_of_properties") + 1
        return self.source_rows * columns

    @property
    def expected_rdf_statements(self) -> int | None:
        rows = _integer_parameter(self.parameters, "number_of_members")
        if self.generator == "RawData":
            return rows * _integer_parameter(self.parameters, "number_of_properties")
        if self.generator == "Mappings":
            return (
                rows
                * _integer_parameter(self.parameters, "number_of_tms")
                * _integer_parameter(self.parameters, "number_of_poms")
            )
        if self.generator == "NamedGraph":
            graph_count = _integer_parameter(
                self.parameters, "number_of_ng_s"
            ) + _integer_parameter(self.parameters, "number_of_ng_pom")
            return (
                rows
                * _integer_parameter(self.parameters, "number_of_poms")
                * graph_count
            )
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

    def config_instance(self) -> dict[str, object]:
        return {
            "@id": self.identifier,
            "name": self.display_name,
            "generator": self.generator,
            "parameters": self.parameters,
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
)


def _parse_parameters(value: object) -> dict[str, ParameterValue]:
    if not isinstance(value, dict):
        raise TypeError("Invalid KROWN scenario parameters")
    parameters: dict[str, ParameterValue] = {}
    for name, parameter_value in value.items():
        if not isinstance(name, str) or not isinstance(
            parameter_value, (bool, int, float, str)
        ):
            raise TypeError("Invalid KROWN scenario parameter")
        parameters[name] = parameter_value
    return parameters


def _load_config(config_file: Path) -> list[KrownScenario]:
    catalog = json.loads(config_file.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict) or not isinstance(catalog["instances"], list):
        raise TypeError(f"Invalid KROWN configuration: {config_file}")

    scenarios = []
    for value in catalog["instances"]:
        if not isinstance(value, dict):
            raise TypeError(f"Invalid KROWN scenario: {config_file}")
        parameters = _parse_parameters(value["parameters"])
        original_data_format = str(parameters["data_format"])
        parameters["data_format"] = "postgresql"
        parameters["engine"] = "RMLMapper"
        scenarios.append(
            KrownScenario(
                identifier=str(value["@id"]),
                display_name=str(value["name"]),
                generator=str(value["generator"]),
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
        "engine": "RMLMapper",
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

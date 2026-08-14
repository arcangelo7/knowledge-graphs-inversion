# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from dataclasses import dataclass
from typing import Literal, cast

EnginePair = Literal["rmlmapper_kgi", "souffle_souffle"]

DEFAULT_ENGINE_PAIR: EnginePair = "rmlmapper_kgi"
ENGINE_PAIRS: dict[EnginePair, dict[str, str | tuple[str, ...]]] = {
    "rmlmapper_kgi": {
        "label": "RMLMapper → KGI",
        "forward": "RMLMapper",
        "inversion": "KGI",
        "suite_ids": ("r2rml", "rml"),
    },
    "souffle_souffle": {
        "label": "Soufflé → Soufflé",
        "forward": "Soufflé",
        "inversion": "Soufflé",
        "suite_ids": ("r2rml",),
    },
}

RML_MYSQL_UNAVAILABLE = (
    "RML is unavailable for MySQL because the RML Core RDB test suite does not "
    "yet provide MySQL variants."
)
SOUFFLE_RML_UNAVAILABLE = "Soufflé/Soufflé is available only for R2RML."
R2RML_POSTGRESQL_ONLY_CASES = frozenset({"R2RMLTC0002f", "R2RMLTC0018a"})

SUITE_LABELS = {
    "r2rml": "R2RML",
    "rml": "RML",
}


@dataclass(frozen=True)
class DatabaseHosts:
    source: str
    destination: str


@dataclass(frozen=True)
class DatabaseConfig:
    label: str
    sqlalchemy_driver: str
    port: int
    username: str
    password: str
    database: str
    suite_hosts: dict[str, DatabaseHosts]
    jdbc_properties: tuple[tuple[str, str], ...] = ()

    def connection_urls(self, suite_id: str) -> tuple[str, str]:
        hosts = self.suite_hosts[suite_id]
        return self._connection_url(hosts.source), self._connection_url(
            hosts.destination
        )

    def _connection_url(self, host: str) -> str:
        return (
            f"{self.sqlalchemy_driver}://{self.username}:{self.password}@"
            f"{host}:{self.port}/{self.database}"
        )


DATABASE_CONFIGS: dict[str, DatabaseConfig] = {
    "postgresql": DatabaseConfig(
        label="PostgreSQL",
        sqlalchemy_driver="postgresql+psycopg2",
        port=5432,
        username="r2rml",
        password="r2rml",
        database="r2rml",
        suite_hosts={
            "r2rml": DatabaseHosts(
                source="postgresql_r2rml",
                destination="dest_postgresql_r2rml",
            ),
            "rml": DatabaseHosts(
                source="postgresql_rml",
                destination="dest_postgresql_rml",
            ),
        },
    ),
    "mysql": DatabaseConfig(
        label="MySQL",
        sqlalchemy_driver="mysql+pymysql",
        port=3306,
        username="r2rml",
        password="r2rml",
        database="r2rml",
        suite_hosts={
            "r2rml": DatabaseHosts(
                source="mysql_r2rml",
                destination="dest_mysql_r2rml",
            ),
        },
        jdbc_properties=(("padCharsWithSpace", "true"),),
    ),
}


def get_database_config(database_system: str) -> DatabaseConfig:
    try:
        return DATABASE_CONFIGS[database_system]
    except KeyError:
        raise ValueError(f"Unsupported database system: {database_system}") from None


def validate_database_suite(database_system: str, suite_id: str) -> DatabaseConfig:
    database = get_database_config(database_system)
    if suite_id not in SUITE_LABELS:
        raise ValueError(f"Unsupported test suite: {suite_id}")
    if suite_id not in database.suite_hosts:
        raise ValueError(RML_MYSQL_UNAVAILABLE)
    return database


def validate_engine_pair(engine_pair: str, suite_id: str) -> EnginePair:
    if not engine_pair:
        raise ValueError("Engine pair is required")
    if engine_pair not in ENGINE_PAIRS:
        raise ValueError(f"Unsupported engine pair: {engine_pair}")
    selected_pair = cast(EnginePair, engine_pair)
    suite_ids = ENGINE_PAIRS[selected_pair]["suite_ids"]
    assert isinstance(suite_ids, tuple)
    if suite_id not in suite_ids:
        raise ValueError(SOUFFLE_RML_UNAVAILABLE)
    return selected_pair


def is_r2rml_case_available(test_id: str, database_system: str) -> bool:
    return database_system == "postgresql" or test_id not in R2RML_POSTGRESQL_ONLY_CASES

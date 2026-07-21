# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from dataclasses import dataclass

RML_MYSQL_UNAVAILABLE = (
    "RML is unavailable for MySQL because the RML Core RDB test suite does not "
    "yet provide MySQL variants."
)

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

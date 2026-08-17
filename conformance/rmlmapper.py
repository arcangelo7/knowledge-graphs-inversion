# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import os
import re
import subprocess
import sys
import urllib.request
from urllib.parse import urlencode

from sqlalchemy.engine import make_url

RMLMAPPER_VERSION = "8.1.0"
JAR_FILENAME = "rmlmapper-8.1.0-r380-all.jar"
JAR_URL = f"https://github.com/RMLio/rmlmapper-java/releases/download/v{RMLMAPPER_VERSION}/{JAR_FILENAME}"
JAR_DIRECTORY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build"
)


def sqlalchemy_to_jdbc(
    url: str, connection_properties: tuple[tuple[str, str], ...] = ()
) -> tuple[str, str, str]:
    parsed = make_url(url)
    dialect = parsed.get_backend_name()
    host = parsed.host
    port = parsed.port
    database = parsed.database
    jdbc_dsn = f"jdbc:{dialect}://{host}:{port}/{database}"
    if connection_properties:
        jdbc_dsn = f"{jdbc_dsn}?{urlencode(connection_properties)}"
    username = str(parsed.username)
    password = str(parsed.password)
    return jdbc_dsn, username, password


def prepare_rml_mapping(
    mapping_path: str,
    jdbc_dsn: str,
    username: str,
    password: str,
    output_dir: str,
) -> str:
    with open(mapping_path, "r", encoding="utf-8") as f:
        content = f.read()
    content = content.replace("CONNECTIONDSN", jdbc_dsn)
    content = re.sub(
        r'(d2rq:username\s+)"[^"]*"',
        rf'\1"{username}"',
        content,
    )
    content = re.sub(
        r'(d2rq:password\s+)"[^"]*"',
        rf'\1"{password}"',
        content,
    )
    prepared_path = os.path.join(output_dir, "mapping_prepared.ttl")
    with open(prepared_path, "w", encoding="utf-8") as f:
        f.write(content)
    return prepared_path


def _managed_jar_path() -> str:
    return os.path.join(JAR_DIRECTORY, JAR_FILENAME)


def _ensure_managed_jar() -> str:
    jar_path = _managed_jar_path()
    if not os.path.isfile(jar_path):
        os.makedirs(JAR_DIRECTORY, exist_ok=True)
        urllib.request.urlretrieve(JAR_URL, jar_path)
    return jar_path


def _get_jar_path() -> str:
    env_path = os.environ.get("RMLMAPPER_JAR")
    if env_path and os.path.isfile(env_path):
        return env_path
    return _ensure_managed_jar()


def _build_args(
    mapping_path: str,
    output_path: str,
    serialization: str,
    dsn: str | None,
    username: str | None,
    password: str | None,
) -> list[str]:
    args = ["-m", mapping_path, "-s", serialization, "-o", output_path]
    if dsn:
        args.extend(["-dsn", dsn])
    if username:
        args.extend(["-u", username])
    if password:
        args.extend(["-p", password])
    return args


def run(
    mapping_path: str,
    output_path: str,
    serialization: str = "nquads",
    dsn: str | None = None,
    username: str | None = None,
    password: str | None = None,
    timeout: int = 180,
    java_options: tuple[str, ...] = (),
) -> int:
    result = execute(
        mapping_path,
        output_path,
        serialization,
        dsn,
        username,
        password,
        timeout,
        java_options,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
    return result.returncode


def execute(
    mapping_path: str,
    output_path: str,
    serialization: str = "nquads",
    dsn: str | None = None,
    username: str | None = None,
    password: str | None = None,
    timeout: int = 180,
    java_options: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    jar_path = _get_jar_path()
    cmd = ["java", *java_options, "-jar", jar_path]
    cmd.extend(
        _build_args(
            os.path.abspath(mapping_path),
            os.path.abspath(output_path),
            serialization,
            dsn,
            username,
            password,
        )
    )

    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

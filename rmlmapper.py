# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import os
import re
import subprocess
from urllib.parse import urlparse

DOCKER_IMAGE = "rmlio/rmlmapper-java:v8.0.1"
JAR_URL = "https://github.com/RMLio/rmlmapper-java/releases/download/v8.1.0/rmlmapper-8.1.0-r380-all.jar"


def sqlalchemy_to_jdbc(url: str) -> tuple[str, str, str]:
    parsed = urlparse(url.split("+")[0] + url[url.index("://"):])
    host = parsed.hostname
    port = parsed.port
    database = parsed.path.lstrip("/")
    jdbc_dsn = f"jdbc:postgresql://{host}:{port}/{database}"
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


def _get_jar_path() -> str | None:
    env_path = os.environ.get("RMLMAPPER_JAR")
    if env_path and os.path.isfile(env_path):
        return env_path
    default_path = os.path.join(os.path.dirname(__file__), "rmlmapper.jar")
    if os.path.isfile(default_path):
        return default_path
    return None


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
) -> int:
    jar_path = _get_jar_path()
    if jar_path:
        cmd = ["java", "-jar", jar_path]
        cmd.extend(_build_args(
            os.path.abspath(mapping_path), os.path.abspath(output_path),
            serialization, dsn, username, password,
        ))
    else:
        output_dir = os.path.dirname(os.path.abspath(output_path))
        output_filename = os.path.basename(output_path)
        mapping_abs = os.path.abspath(mapping_path)
        cmd = [
            "docker", "run", "--rm", "--network", "host",
            "-v", f"{mapping_abs}:/data/mapping.ttl:ro",
            "-v", f"{output_dir}:/data/output",
            DOCKER_IMAGE,
        ]
        cmd.extend(_build_args(
            "/data/mapping.ttl", f"/data/output/{output_filename}",
            serialization, dsn, username, password,
        ))

    result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return result.returncode

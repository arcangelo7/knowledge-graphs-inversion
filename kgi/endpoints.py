# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

"""SPARQL endpoint implementations."""

import gzip
import json
import logging
import os
import re
import signal
import shutil
import subprocess
import tempfile
import time

from io import BytesIO
from typing import TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from pyoxigraph import (
    BlankNode,
    DefaultGraph,
    Literal,
    NamedNode,
    Quad,
    QueryResultsFormat,
    QuerySolutions,
    Store,
)
from sparqlite import SPARQLClient

from kgi.base import Endpoint
from kgi.utils import Validator


def _qlever_setting(name: str, default: str) -> str:
    return os.environ[name] if name in os.environ else default


def _virtuoso_setting(name: str, default: str) -> str:
    return os.environ[name] if name in os.environ else default


class RemoteEndpoint(Endpoint):
    """Remote SPARQL endpoint implementation."""

    def __init__(self, url: str, rdf_file_to_load: str | None = None):
        self._client = SPARQLClient(url)
        self.endpoint_url = url
        self.rdf_file_path = rdf_file_to_load
        self._graph_uri = None

        if rdf_file_to_load:
            self._graph_uri = f"http://temp/graph/{os.path.basename(rdf_file_to_load)}"
            self._load_data()

    def _load_data(self):
        """Load RDF data into the SPARQL endpoint using INSERT DATA."""
        assert self.rdf_file_path is not None
        self._client.update(f"CLEAR GRAPH <{self._graph_uri}>")

        with open(self.rdf_file_path, "r", encoding="utf-8") as f:
            chunk_size = 1000
            triples = []

            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    triples.append(line)

                    if len(triples) >= chunk_size:
                        self._insert_triples(triples)
                        triples = []

            if triples:
                self._insert_triples(triples)

    def _insert_triples(self, triples):
        """Insert a batch of triples into the SPARQL endpoint."""
        insert_query = f"INSERT DATA {{\n  GRAPH <{self._graph_uri}> {{\n"
        for triple in triples:
            if triple.endswith("."):
                triple = triple[:-1].strip()
            insert_query += f"    {triple} .\n"
        insert_query += "  }\n}"

        self._client.update(insert_query)

    def query(self, query: str):
        """Execute a SPARQL query and return JSON string."""
        if self._graph_uri:
            modified_query = query.replace(
                "WHERE {", f"WHERE {{ GRAPH <{self._graph_uri}> {{"
            )
            bracket_count = modified_query.count("{") - modified_query.count("}")
            if bracket_count > 0:
                modified_query += "}" * bracket_count
            query = modified_query

        result = self._client.query(query, method="POST")
        return json.dumps(result)

    def __repr__(self):
        return f"RemoteEndpoint({self.endpoint_url})"

    def close(self) -> None:
        try:
            if self._graph_uri is not None:
                self._client.update(f"CLEAR GRAPH <{self._graph_uri}>")
                self._graph_uri = None
        finally:
            self._client.close()

    def __del__(self):
        if hasattr(self, "_client"):
            try:
                self.close()
            except Exception:
                pass


class VirtuosoEndpoint(RemoteEndpoint):
    """Virtuoso-specific endpoint that uses bulk loading for better performance."""

    def __init__(
        self,
        url: str,
        rdf_file_to_load: str | None = None,
        container_name: str = "virtuoso-kgi",
    ):
        self.container_name = container_name
        self.endpoint_url = url
        self.rdf_file_path = rdf_file_to_load
        self._graph_uri = None
        self._client: SPARQLClient | None = None
        self._work_dir = tempfile.TemporaryDirectory(prefix="kgi_virtuoso_")
        self._server_process: subprocess.Popen[str] | None = None
        self._server_log: TextIO | None = None
        self._closed = False

        parsed_url = urlparse(url)
        if parsed_url.port is None:
            raise ValueError("Virtuoso endpoint URL must include a port")

        self.http_port = parsed_url.port
        self.sql_port = int(_virtuoso_setting("VIRTUOSO_SQL_PORT", "1111"))
        self.dba_password = _virtuoso_setting("DBA_PASSWORD", "dba")
        self.max_query_mem = _virtuoso_setting("VIRTUOSO_MAX_QUERY_MEM", "16G")
        self.threads_per_query = _virtuoso_setting("VIRTUOSO_THREADS_PER_QUERY", "4")
        self.async_queue_max_threads = _virtuoso_setting(
            "VIRTUOSO_ASYNC_QUEUE_MAX_THREADS", "10"
        )
        self.number_of_buffers = _virtuoso_setting(
            "VIRTUOSO_NUMBER_OF_BUFFERS", "10000"
        )
        self.max_dirty_buffers = _virtuoso_setting("VIRTUOSO_MAX_DIRTY_BUFFERS", "7500")
        self.startup_timeout = int(_virtuoso_setting("VIRTUOSO_STARTUP_TIMEOUT", "60"))

        if rdf_file_to_load:
            self.rdf_file_path = rdf_file_to_load
            self._graph_uri = f"http://temp/graph/{os.path.basename(rdf_file_to_load)}"

        self._write_config()
        self._start_server()
        self._client = SPARQLClient(url)

        if rdf_file_to_load:
            self._bulk_load_data()

    @property
    def _work_path(self) -> str:
        return self._work_dir.name

    def _write_config(self) -> None:
        config = f"""
[Database]
DatabaseFile = virtuoso.db
ErrorLogFile = virtuoso.log
LockFile = virtuoso.lck
TransactionFile = virtuoso.trx
xa_persistent_file = virtuoso.pxa
ErrorLogLevel = 7
FileExtend = 200
MaxCheckpointRemap = 2000
Striping = 0
TempStorage = TempDatabase

[TempDatabase]
DatabaseFile = virtuoso-temp.db
TransactionFile = virtuoso-temp.trx
MaxCheckpointRemap = 2000
Striping = 0

[Parameters]
ServerPort = {self.sql_port}
LiteMode = 0
DisableUnixSocket = 1
DisableTcpSocket = 0
MaxClientConnections = 10
CheckpointInterval = 60
O_DIRECT = 0
CaseMode = 2
MaxStaticCursorRows = 5000
CheckpointAuditTrail = 0
AllowOSCalls = 0
SchedulerInterval = 10
DirsAllowed = ., {self._work_path}
ThreadCleanupInterval = 1
ThreadThreshold = 10
ResourcesCleanupInterval = 1
FreeTextBatchSize = 100000
SingleCPU = 0
VADInstallDir = /opt/virtuoso-opensource/share/virtuoso/vad/
PrefixResultNames = 0
RdfFreeTextRulesSize = 100
IndexTreeMaps = 64
MaxMemPoolSize = 200000000
MaxQueryMem = {self.max_query_mem}
VectorSize = 1000
MaxVectorSize = 1000000
AdjustVectorSize = 0
ThreadsPerQuery = {self.threads_per_query}
AsyncQueueMaxThreads = {self.async_queue_max_threads}
NumberOfBuffers = {self.number_of_buffers}
MaxDirtyBuffers = {self.max_dirty_buffers}

[HTTPServer]
ServerPort = {self.http_port}
ServerRoot = /opt/virtuoso-opensource/share/virtuoso/vsp
MaxClientConnections = 10
DavRoot = DAV
EnabledDavVSP = 0
HTTPProxyEnabled = 0
TempASPXDir = 0
DefaultMailServer = localhost:25
MaxKeepAlives = 10
KeepAliveTimeout = 10
MaxCachedProxyConnections = 10
ProxyConnectionCacheTimeout = 15
HTTPThreadSize = 280000
HttpPrintWarningsInOutput = 0
Charset = UTF-8
MaintenancePage = atomic.html
EnabledGzipContent = 1

[Client]
SQL_PREFETCH_ROWS = 100
SQL_PREFETCH_BYTES = 16000
SQL_QUERY_TIMEOUT = 0
SQL_TXN_TIMEOUT = 0

[SPARQL]
MaxConstructTriples = 10000
DefaultQuery = SELECT (COUNT(*) AS ?triples) WHERE {{?s ?p ?o}}
DeferInferenceRulesInit = 0
MaxMemInUse = 0

[Plugins]
LoadPath = /opt/virtuoso-opensource/lib/virtuoso/hosting
""".lstrip()
        with open(
            os.path.join(self._work_path, "virtuoso.ini"), "w", encoding="utf-8"
        ) as f:
            f.write(config)

    def _start_server(self) -> None:
        virtuoso_path = "/opt/virtuoso-opensource/bin/virtuoso-t"
        if not os.path.exists(virtuoso_path):
            raise RuntimeError(f"virtuoso-t not found at {virtuoso_path}")

        log_path = os.path.join(self._work_path, "virtuoso.log")
        self._server_log = open(log_path, "w", encoding="utf-8")
        self._server_process = subprocess.Popen(
            [
                virtuoso_path,
                "+foreground",
                "+wait",
                "+configfile",
                os.path.join(self._work_path, "virtuoso.ini"),
            ],
            cwd=self._work_path,
            stdout=self._server_log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        self._wait_until_ready()
        self._configure_permissions()

    def _wait_until_ready(self) -> None:
        assert self._server_process is not None
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self._server_process.poll() is not None:
                raise RuntimeError(
                    f"Virtuoso server exited during startup\n{self._virtuoso_log_tail()}"
                )
            try:
                with urlopen(self.endpoint_url, timeout=1):
                    return
            except HTTPError:
                return
            except (TimeoutError, URLError):
                time.sleep(0.5)

        raise RuntimeError(
            f"Virtuoso server did not start within {self.startup_timeout}s\n"
            f"{self._virtuoso_log_tail()}"
        )

    def _configure_permissions(self) -> None:
        self._execute_sql("DB.DBA.RDF_DEFAULT_USER_PERMS_SET('nobody', 7)")

    def _virtuoso_log_tail(self, max_chars: int = 4000) -> str:
        if self._server_log is not None:
            self._server_log.flush()
        log_path = os.path.join(self._work_path, "virtuoso.log")
        if not os.path.exists(log_path):
            return ""
        size = os.path.getsize(log_path)
        with open(log_path, encoding="utf-8") as f:
            f.seek(max(size - max_chars, 0))
            return f.read()

    def _bulk_load_data(self):
        """Load RDF data using Virtuoso bulk loading instead of INSERT queries."""
        assert self.rdf_file_path is not None
        assert self._client is not None

        temp_nq_file = None
        temp_nq_gz_file = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".nq", delete=False, encoding="utf-8"
            ) as temp_nq:
                temp_nq_file = temp_nq.name

                with open(self.rdf_file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            if line.endswith("."):
                                line = line[:-1].strip()
                            temp_nq.write(f"{line} <{self._graph_uri}> .\n")

            temp_nq_gz_file = temp_nq_file + ".gz"
            with open(temp_nq_file, "rb") as f_in:
                with gzip.open(temp_nq_gz_file, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)

            bulk_load_file = os.path.join(self._work_path, "temp_bulk_load.nq.gz")
            shutil.copy2(temp_nq_gz_file, bulk_load_file)

            register_sql = (
                f"ld_dir('{self._work_path}', 'temp_bulk_load.nq.gz', "
                f"'http://localhost:{self.http_port}/DAV/ignored')"
            )
            self._execute_sql(register_sql)

            self._execute_sql("rdf_loader_run()")

            count_query = (
                f"SELECT COUNT(*) WHERE {{ GRAPH <{self._graph_uri}> {{ ?s ?p ?o }} }}"
            )
            result = self._client.query(count_query, method="POST")
            bindings = result["results"]["bindings"]
            triple_count_in_graph = (
                int(bindings[0][list(bindings[0].keys())[0]]["value"])
                if bindings
                else 0
            )
            if triple_count_in_graph == 0:
                raise RuntimeError("No triples were loaded into the graph")

        finally:
            for temp_file in [
                temp_nq_file,
                temp_nq_gz_file,
                os.path.join(self._work_path, "temp_bulk_load.nq.gz"),
            ]:
                if temp_file and os.path.exists(temp_file):
                    os.remove(temp_file)

    def _execute_sql(self, sql_command):
        """Execute SQL command using local isql."""
        # Use local isql command
        isql_path = "/opt/virtuoso-opensource/bin/isql"

        if not os.path.exists(isql_path):
            logging.getLogger("kgi").error(f"isql not found at {isql_path}")
            raise RuntimeError(f"isql not found at {isql_path}")

        cmd = [
            isql_path,
            f"localhost:{self.sql_port}",
            "dba",
            self.dba_password,
            f"exec={sql_command}",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"SQL execution failed: {result.stderr}")
        return result.stdout

    def close(self) -> None:
        if self._closed:
            return
        if self._client is not None:
            self._client.close()
        if self._server_process is not None and self._server_process.poll() is None:
            process_group_id = os.getpgid(self._server_process.pid)
            os.killpg(process_group_id, signal.SIGTERM)
            try:
                self._server_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process_group_id, signal.SIGKILL)
                self._server_process.wait()
        if self._server_log is not None:
            self._server_log.close()
        self._closed = True
        self._work_dir.cleanup()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class QLeverEndpoint(RemoteEndpoint):
    def __init__(
        self,
        url: str,
        rdf_file_to_load: str,
        name: str = "kgi_qlever",
    ):
        self.name = name
        self.endpoint_url = url
        self.rdf_file_path = rdf_file_to_load
        self._graph_uri = None
        self._client = SPARQLClient(url)
        self._work_dir = tempfile.TemporaryDirectory(prefix="kgi_qlever_")
        self._server_process: subprocess.Popen[str] | None = None
        self._server_log: TextIO | None = None
        self._closed = False

        parsed_url = urlparse(url)
        if parsed_url.port is None:
            raise ValueError("QLever endpoint URL must include a port")

        self.port = parsed_url.port
        self.memory_for_queries = _qlever_setting("QLEVER_MEMORY_FOR_QUERIES", "1G")
        self.cache_max_size = _qlever_setting("QLEVER_CACHE_MAX_SIZE", "256M")
        self.cache_max_size_single_entry = _qlever_setting(
            "QLEVER_CACHE_MAX_SIZE_SINGLE_ENTRY", "64M"
        )
        self.num_threads = _qlever_setting("QLEVER_NUM_THREADS", "1")
        self.query_timeout = _qlever_setting("QLEVER_QUERY_TIMEOUT", "120s")
        self.index_stxxl_memory = _qlever_setting("QLEVER_INDEX_STXXL_MEMORY", "512M")
        self.parser_buffer_size = _qlever_setting("QLEVER_PARSER_BUFFER_SIZE", "64M")
        self.startup_timeout = int(_qlever_setting("QLEVER_STARTUP_TIMEOUT", "30"))

        self._prepare_work_dir()
        self._write_qleverfile()
        self._run_qlever(["qlever", "index", "--overwrite-existing"])
        self._start_server()

    @property
    def _work_path(self) -> str:
        return self._work_dir.name

    def _prepare_work_dir(self) -> None:
        shutil.copy2(self.rdf_file_path, os.path.join(self._work_path, "data.nt"))

    def _write_qleverfile(self) -> None:
        qleverfile = f"""
[data]
NAME = {self.name}
FORMAT = nt
DESCRIPTION = Knowledge Graphs Inversion benchmark data

[index]
INPUT_FILES = data.nt
CAT_INPUT_FILES = cat ${{INPUT_FILES}}
SETTINGS_JSON = {{ "ascii-prefixes-only": false, "num-triples-per-batch": 100000 }}
STXXL_MEMORY = {self.index_stxxl_memory}
PARSER_BUFFER_SIZE = {self.parser_buffer_size}

[server]
HOST_NAME = localhost
PORT = {self.port}
ACCESS_TOKEN = {self.name}
MEMORY_FOR_QUERIES = {self.memory_for_queries}
CACHE_MAX_SIZE = {self.cache_max_size}
CACHE_MAX_SIZE_SINGLE_ENTRY = {self.cache_max_size_single_entry}
TIMEOUT = {self.query_timeout}
NUM_THREADS = {self.num_threads}

[runtime]
SYSTEM = native
""".lstrip()

        with open(
            os.path.join(self._work_path, "Qleverfile"), "w", encoding="utf-8"
        ) as f:
            f.write(qleverfile)

    def _run_qlever(self, cmd: list[str]) -> None:
        try:
            subprocess.run(
                cmd,
                cwd=self._work_path,
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                "QLever is not installed or not available in PATH"
            ) from e
        except subprocess.CalledProcessError as e:
            output = "\n".join(part for part in [e.stdout, e.stderr] if part)
            raise RuntimeError(
                f"QLever command failed: {' '.join(cmd)}\n{output}"
            ) from e

    def _start_server(self) -> None:
        log_path = os.path.join(self._work_path, "qlever-server.log")
        self._server_log = open(log_path, "w", encoding="utf-8")
        try:
            self._server_process = subprocess.Popen(
                [
                    "qlever",
                    "start",
                    "--kill-existing-with-same-port",
                    "--no-warmup",
                    "--run-in-foreground",
                ],
                cwd=self._work_path,
                stdout=self._server_log,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                "QLever is not installed or not available in PATH"
            ) from e

        self._wait_until_ready()

    def _wait_until_ready(self) -> None:
        assert self._server_process is not None
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self._server_process.poll() is not None:
                raise RuntimeError(
                    f"QLever server exited during startup\n{self._qlever_log_tail()}"
                )
            try:
                with urlopen(self.endpoint_url, timeout=1):
                    return
            except HTTPError:
                return
            except (TimeoutError, URLError):
                time.sleep(0.2)

        raise RuntimeError(
            f"QLever server did not start within {self.startup_timeout}s\n"
            f"{self._qlever_log_tail()}"
        )

    def _qlever_log_tail(self, max_chars: int = 4000) -> str:
        if self._server_log is not None:
            self._server_log.flush()
        log_path = os.path.join(self._work_path, "qlever-server.log")
        if not os.path.exists(log_path):
            return ""
        size = os.path.getsize(log_path)
        with open(log_path, encoding="utf-8") as f:
            f.seek(max(size - max_chars, 0))
            return f.read()

    def _qlever_error_summary(self) -> str:
        relevant_parts = [
            "ERROR",
            "WARNING",
            "large connected component",
        ]
        lines = [
            line
            for line in self._qlever_log_tail().splitlines()
            if any(part in line for part in relevant_parts)
        ]
        if lines:
            return "\n".join(lines[-12:])
        return self._qlever_log_tail()

    def query(self, query: str):
        try:
            return super().query(query)
        except Exception as e:
            if (
                self._server_process is not None
                and self._server_process.poll() is not None
            ):
                raise RuntimeError(
                    f"QLever server exited with code {self._server_process.returncode}\n"
                    f"{self._qlever_error_summary()}"
                ) from e
            raise RuntimeError(
                f"QLever query failed: {e}\n{self._qlever_error_summary()}"
            ) from e

    def close(self) -> None:
        if self._closed:
            return
        self._client.close()
        if self._server_process is not None and self._server_process.poll() is None:
            process_group_id = os.getpgid(self._server_process.pid)
            os.killpg(process_group_id, signal.SIGTERM)
            try:
                self._server_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process_group_id, signal.SIGKILL)
                self._server_process.wait()
        if self._server_log is not None:
            self._server_log.close()
        self._closed = True
        self._work_dir.cleanup()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


_NT_LINE = re.compile(
    r"(<[^>]*>|_:\S+)\s+(<[^>]*>)\s+"
    r'(<[^>]*>|_:\S+|"(?:[^"\\]|\\.)*"(?:@[a-z]+(?:-[a-z0-9]+)*)?(?:\^\^<[^>]*>)?)'
    r"(?:\s+(<[^>]*>))?\s*\."
)


_BNODE_IRI_PREFIX = "urn:bnode:"


def _parse_term_subject(raw: str) -> NamedNode | BlankNode:
    if raw.startswith("<"):
        return NamedNode(raw[1:-1])
    return NamedNode(f"{_BNODE_IRI_PREFIX}{raw[2:]}")


def _parse_term_object(raw: str) -> NamedNode | BlankNode | Literal:
    if raw.startswith("<"):
        return NamedNode(raw[1:-1])
    if raw.startswith("_:"):
        return NamedNode(f"{_BNODE_IRI_PREFIX}{raw[2:]}")
    match = re.match(
        r'^"((?:[^"\\]|\\.)*)"(@([a-z]+(?:-[a-z0-9]+)*))?(\^\^<([^>]*)>)?$', raw
    )
    if not match:
        return Literal(raw)
    value, _, lang, _, datatype = match.groups()
    if datatype:
        return Literal(value, datatype=NamedNode(datatype))
    if lang:
        return Literal(value, language=lang)
    return Literal(value)


def _parse_ntriples_preserve_bnodes(store: Store, data: str) -> None:
    for line in data.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _NT_LINE.match(line)
        if not m:
            continue
        s = _parse_term_subject(m.group(1))
        p = NamedNode(m.group(2)[1:-1])
        o = _parse_term_object(m.group(3))
        g = NamedNode(m.group(4)[1:-1]) if m.group(4) else DefaultGraph()
        store.add(Quad(s, p, o, g))


class LocalSparqlGraphStore(Endpoint):
    """Local pyoxigraph-based SPARQL endpoint."""

    def __init__(self, url: str, delete_after_use: bool = False):
        self.delete_after_use = delete_after_use
        self._store: Store | None = Store()

        with open(url, "r", encoding="utf-8") as f:
            data = f.read()

        _parse_ntriples_preserve_bnodes(self._store, data)

    def query(self, query: str):
        """Execute a SPARQL query on the local store and return SPARQL JSON."""
        assert self._store is not None
        try:
            results = self._store.query(query, use_default_graph_as_union=True)
            assert isinstance(results, QuerySolutions)
            buf = BytesIO()
            results.serialize(buf, QueryResultsFormat.JSON)
            return buf.getvalue().decode()
        except Exception as e:
            logging.getLogger("kgi").error(f"Query execution error: {e}")
            logging.getLogger("kgi").error(f"Failed query: {query}")
            raise

    def __del__(self):
        """Clean up resources."""
        if self.delete_after_use:
            self._store = None


class EndpointFactory:
    """Factory for creating SPARQL endpoints."""

    @classmethod
    def create_from_url(cls, url: str):
        """Create an endpoint from a URL or file path."""
        if Validator.url(url):
            return RemoteEndpoint(url)
        else:
            return LocalSparqlGraphStore(url)

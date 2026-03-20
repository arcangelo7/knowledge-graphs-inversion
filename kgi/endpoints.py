"""SPARQL endpoint implementations."""

import gzip
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile

from io import BytesIO

from pyoxigraph import BlankNode, DefaultGraph, Literal, NamedNode, Quad, QueryResultsFormat, QuerySolutions, Store
from sparqlite import SPARQLClient

from .base import Endpoint
from .utils import Validator


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

    def close(self):
        self._client.close()

    def __del__(self):
        """Clean up by removing the graph from the endpoint."""
        if hasattr(self, "_graph_uri") and self._graph_uri:
            try:
                self._client.update(f"CLEAR GRAPH <{self._graph_uri}>")
            except Exception:
                pass
        if hasattr(self, "_client"):
            self._client.close()


class VirtuosoEndpoint(RemoteEndpoint):
    """Virtuoso-specific endpoint that uses bulk loading for better performance."""

    def __init__(
        self,
        url: str,
        rdf_file_to_load: str | None = None,
        container_name: str = "virtuoso-kgi",
    ):
        self.container_name = container_name
        self.host_bulk_load_dir = os.environ["VIRTUOSO_BULK_DIR"]

        self._client = SPARQLClient(url)
        self.endpoint_url = url
        self.rdf_file_path = rdf_file_to_load
        self._graph_uri = None

        if rdf_file_to_load:
            self.rdf_file_path = rdf_file_to_load
            self._graph_uri = f"http://temp/graph/{os.path.basename(rdf_file_to_load)}"
            self._bulk_load_data()

    def _bulk_load_data(self):
        """Load RDF data using Virtuoso bulk loading instead of INSERT queries."""
        assert self.rdf_file_path is not None

        self._client.update(f"CLEAR GRAPH <{self._graph_uri}>")

        # Convert N-Triples to N-Quads with target graph
        temp_nq_file = None
        temp_nq_gz_file = None

        try:
            # Create temporary N-Quads file
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".nq", delete=False, encoding="utf-8"
            ) as temp_nq:
                temp_nq_file = temp_nq.name

                triple_count = 0
                with open(self.rdf_file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            if line.endswith("."):
                                line = line[:-1].strip()
                            # Add graph URI to make it an N-Quad
                            temp_nq.write(f"{line} <{self._graph_uri}> .\n")
                            triple_count += 1

            # Compress the N-Quads file
            temp_nq_gz_file = temp_nq_file + ".gz"
            with open(temp_nq_file, "rb") as f_in:
                with gzip.open(temp_nq_gz_file, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)

            # Copy the gzipped file to the bulk load directory
            bulk_load_file = f"{self.host_bulk_load_dir}/temp_bulk_load.nq.gz"
            shutil.copy2(temp_nq_gz_file, bulk_load_file)

            # Step 1: Clear any existing entries for this file from load_list
            clear_sql = f"DELETE FROM DB.DBA.load_list WHERE ll_file = '{self.host_bulk_load_dir}/temp_bulk_load.nq.gz'"
            try:
                self._execute_sql(clear_sql)
            except Exception as e:
                logging.getLogger("kgi").error(f"Exception running clear command: {e}")
                raise

            # Step 2: Register the file for bulk loading
            register_sql = f"ld_dir('{self.host_bulk_load_dir}', 'temp_bulk_load.nq.gz', 'http://localhost:8890/DAV/ignored')"
            try:
                self._execute_sql(register_sql)
            except Exception as e:
                logging.getLogger("kgi").error(
                    f"Exception running register command: {e}"
                )
                raise

            # Step 3: Run the bulk loader
            load_sql = "rdf_loader_run()"
            try:
                self._execute_sql(load_sql)
            except Exception as e:
                logging.getLogger("kgi").error(f"Exception running bulk load: {e}")
                raise

            # Step 4: Verify data was loaded
            count_query = (
                f"SELECT COUNT(*) WHERE {{ GRAPH <{self._graph_uri}> {{ ?s ?p ?o }} }}"
            )
            try:
                result = self._client.query(count_query, method="POST")
                bindings = result["results"]["bindings"]
                triple_count_in_graph = int(bindings[0][list(bindings[0].keys())[0]]["value"]) if bindings else 0
                if triple_count_in_graph == 0:
                    logging.getLogger("kgi").error(
                        "WARNING: No triples were loaded into the graph!"
                    )
            except Exception as e:
                logging.getLogger("kgi").error(f"Could not verify loaded data: {e}")

        finally:
            # Clean up temporary files
            for temp_file in [
                temp_nq_file,
                temp_nq_gz_file,
                f"{self.host_bulk_load_dir}/temp_bulk_load.nq.gz",
            ]:
                if temp_file and os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except Exception:
                        pass

    def _execute_sql(self, sql_command):
        """Execute SQL command using local isql."""
        # Use local isql command
        isql_path = "/opt/virtuoso-opensource/bin/isql"

        if not os.path.exists(isql_path):
            logging.getLogger("kgi").error(f"isql not found at {isql_path}")
            raise RuntimeError(f"isql not found at {isql_path}")

        # Execute the SQL command using isql
        cmd = [isql_path, "localhost:1111", "dba", "dba", f"exec={sql_command}"]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode != 0:
                logging.getLogger("kgi").error(f"SQL execution failed: {result.stderr}")
                raise RuntimeError(f"SQL execution failed: {result.stderr}")

            return result.stdout

        except subprocess.TimeoutExpired:
            logging.getLogger("kgi").error("SQL command timed out")
            raise RuntimeError("SQL command timed out")
        except Exception as e:
            logging.getLogger("kgi").error(f"Failed to execute SQL command: {e}")
            raise RuntimeError(f"Failed to execute SQL command: {e}")


_NT_LINE = re.compile(
    r'(<[^>]*>|_:\S+)\s+(<[^>]*>)\s+'
    r'(<[^>]*>|_:\S+|"(?:[^"\\]|\\.)*"(?:@[a-z]+(?:-[a-z0-9]+)*)?(?:\^\^<[^>]*>)?)'
    r'(?:\s+(<[^>]*>))?\s*\.'
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
    match = re.match(r'^"((?:[^"\\]|\\.)*)"(@([a-z]+(?:-[a-z0-9]+)*))?(\^\^<([^>]*)>)?$', raw)
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

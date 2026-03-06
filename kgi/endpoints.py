"""SPARQL endpoint implementations."""

import gzip
import logging
import os
import re
import shutil
import subprocess
import tempfile

from rdflib import BNode, Dataset, Literal, URIRef
from SPARQLWrapper import CSV, SPARQLWrapper, POST

from .base import Endpoint
from .utils import Validator


class RemoteEndpoint(Endpoint):
    """Remote SPARQL endpoint implementation."""
    
    def __init__(self, url: str, rdf_file_to_load: str | None = None):
        self._sparql = SPARQLWrapper(url)
        self._sparql.setReturnFormat(CSV)
        self.endpoint_url = url
        self.rdf_file_path = rdf_file_to_load
        self._graph_uri = None
        
        if rdf_file_to_load:
            self._graph_uri = f"http://temp/graph/{os.path.basename(rdf_file_to_load)}"
            self._load_data()

    def _load_data(self):
        """Load RDF data into the SPARQL endpoint using INSERT DATA."""
        assert self.rdf_file_path is not None
        clear_query = f"CLEAR GRAPH <{self._graph_uri}>"
        self._sparql.setQuery(clear_query)
        self._sparql.setMethod(POST)
        self._sparql.query()

        with open(self.rdf_file_path, 'r', encoding='utf-8') as f:
            chunk_size = 1000
            triples = []
            
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
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
            if triple.endswith('.'):
                triple = triple[:-1].strip()
            insert_query += f"    {triple} .\n"
        insert_query += "  }\n}"
        
        self._sparql.setQuery(insert_query)
        self._sparql.setMethod(POST)
        self._sparql.query()

    def query(self, query: str):
        """Execute a SPARQL query."""
        if self._graph_uri:
            modified_query = query.replace("WHERE {", f"WHERE {{ GRAPH <{self._graph_uri}> {{")
            bracket_count = modified_query.count("{") - modified_query.count("}")
            if bracket_count > 0:
                modified_query += "}" * bracket_count
            query = modified_query
        
        self._sparql.setQuery(query)
        self._sparql.setMethod(POST)
        result = self._sparql.query().convert()
        
        if isinstance(result, bytes):
            return result.decode("utf-8")
        return result

    def __repr__(self):
        return f"RemoteEndpoint({self.endpoint_url})"
    
    def __del__(self):
        """Clean up by removing the graph from the endpoint."""
        if hasattr(self, '_graph_uri') and self._graph_uri:
            try:
                clear_query = f"CLEAR GRAPH <{self._graph_uri}>"
                self._sparql.setQuery(clear_query)
                self._sparql.setMethod(POST)
                self._sparql.query()
            except:
                pass


class VirtuosoEndpoint(RemoteEndpoint):
    """Virtuoso-specific endpoint that uses bulk loading for better performance."""
    
    def __init__(self, url: str, rdf_file_to_load: str | None = None, container_name: str = 'virtuoso-kgi'):
        self.container_name = container_name
        self.host_bulk_load_dir = os.environ['VIRTUOSO_BULK_DIR']
        
        # Don't auto-load data in parent constructor
        self._sparql = SPARQLWrapper(url)
        self._sparql.setReturnFormat(CSV)
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
        
        # Clear existing graph first
        clear_query = f"CLEAR GRAPH <{self._graph_uri}>"
        self._sparql.setQuery(clear_query)
        self._sparql.setMethod(POST)
        self._sparql.query()
        
        # Convert N-Triples to N-Quads with target graph
        temp_nq_file = None
        temp_nq_gz_file = None
        
        try:
            # Create temporary N-Quads file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.nq', delete=False, encoding='utf-8') as temp_nq:
                temp_nq_file = temp_nq.name
                
                triple_count = 0
                with open(self.rdf_file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            if line.endswith('.'):
                                line = line[:-1].strip()
                            # Add graph URI to make it an N-Quad
                            temp_nq.write(f"{line} <{self._graph_uri}> .\n")
                            triple_count += 1
            
            # Compress the N-Quads file
            temp_nq_gz_file = temp_nq_file + '.gz'
            with open(temp_nq_file, 'rb') as f_in:
                with gzip.open(temp_nq_gz_file, 'wb') as f_out:
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
                register_result = self._execute_sql(register_sql)
            except Exception as e:
                logging.getLogger("kgi").error(f"Exception running register command: {e}")
                raise
            
            # Step 3: Run the bulk loader
            load_sql = "rdf_loader_run()"
            try:
                load_result = self._execute_sql(load_sql)
            except Exception as e:
                logging.getLogger("kgi").error(f"Exception running bulk load: {e}")
                raise
            
            # Step 4: Verify data was loaded
            count_query = f"SELECT COUNT(*) WHERE {{ GRAPH <{self._graph_uri}> {{ ?s ?p ?o }} }}"
            self._sparql.setQuery(count_query)
            self._sparql.setMethod(POST)
            try:
                raw_results = self._sparql.query().convert()
                results_str = raw_results.decode('utf-8') if isinstance(raw_results, bytes) else str(raw_results)
                triple_count_in_graph = 0
                for line in results_str.split('\n'):
                    if line and not line.startswith('"'):
                        try:
                            triple_count_in_graph = int(line)
                            break
                        except:
                            pass
                if triple_count_in_graph == 0:
                    logging.getLogger("kgi").error("WARNING: No triples were loaded into the graph!")
            except Exception as e:
                logging.getLogger("kgi").error(f"Could not verify loaded data: {e}")
            
        finally:
            # Clean up temporary files
            for temp_file in [temp_nq_file, temp_nq_gz_file, f"{self.host_bulk_load_dir}/temp_bulk_load.nq.gz"]:
                if temp_file and os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except Exception:
                        pass

    def _execute_sql(self, sql_command):
        """Execute SQL command using local isql."""
        logger = logging.getLogger("kgi")
        
        # Use local isql command
        isql_path = '/opt/virtuoso-opensource/bin/isql'
        
        if not os.path.exists(isql_path):
            logging.getLogger("kgi").error(f"isql not found at {isql_path}")
            raise RuntimeError(f"isql not found at {isql_path}")
        
        # Execute the SQL command using isql
        cmd = [
            isql_path, 'localhost:1111', 'dba', 'dba',
            f"exec={sql_command}"
        ]
        
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


class LocalSparqlGraphStore(Endpoint):
    """Local RDFLib-based SPARQL endpoint."""
    
    def __init__(self, url: str, delete_after_use: bool = False):
        logger = logging.getLogger("kgi")
        
        self.delete_after_use = delete_after_use
        with open(url, "r", encoding="utf-8") as f:
            data = f.read()
        
        self._graph: Dataset | None = Dataset(default_union=True)
        try:
            self._parse_ntriples_preserve_bnode_ids(data)
        except Exception as e:
            logging.getLogger("kgi").error(f"Invalid RDF data: {e}")
            raise

    def _parse_ntriples_preserve_bnode_ids(self, data: str):
        """Parse N-Triples data while preserving blank node IDs."""
        for line in data.strip().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
                
            # Remove the final dot
            if line.endswith('.'):
                line = line[:-1].strip()

            # Regex pattern for N-Triples (angle-bracket aware to handle IRIs with spaces)
            pattern = r'(<[^>]*>|_:\S+)\s+(<[^>]*>)\s+(.*)'
            match = re.match(pattern, line)
            if not match:
                continue

            s_str, p_str, o_str = match.groups()

            # Parse subject
            if s_str.startswith('<') and s_str.endswith('>'):
                s_node = URIRef(s_str[1:-1])
            elif s_str.startswith('_:'):
                s_node = BNode(s_str[2:])
            else:
                continue

            # Parse predicate
            if p_str.startswith('<') and p_str.endswith('>'):
                p_node = URIRef(p_str[1:-1])
            else:
                continue

            # Parse object
            if o_str.startswith('<') and o_str.endswith('>'):
                o_node = URIRef(o_str[1:-1])
            elif o_str.startswith('_:'):
                o_node = BNode(o_str[2:])
            elif o_str.startswith('"'):
                # Literal
                literal_pattern = r'^"([^"]*)"(@[a-z]+(-[a-z0-9]+)*)?(\^\^<([^>]*)>)?$'
                lit_match = re.match(literal_pattern, o_str)
                if not lit_match:
                    continue
                lit_value, lang, _, _, datatype = lit_match.groups()
                if datatype:
                    o_node = Literal(lit_value, datatype=URIRef(datatype))
                elif lang:
                    o_node = Literal(lit_value, lang=lang[1:])
                else:
                    o_node = Literal(lit_value)
            else:
                continue

            assert self._graph is not None
            self._graph.add((s_node, p_node, o_node))

    def query(self, query: str):
        """Execute a SPARQL query on the local graph."""
        assert self._graph is not None
        try:
            results = self._graph.query(query)
            if results.type == 'SELECT':
                return results.serialize(format='json')
            elif results.type == 'CONSTRUCT' or results.type == 'DESCRIBE':
                return results.serialize(format='nt')
            elif results.type == 'ASK':
                return str(results.boolean)
            else:
                return ""
        except Exception as e:
            logging.getLogger("kgi").error(f"Query execution error: {e}")
            logging.getLogger("kgi").error(f"Failed query: {query}")
            raise

    def __del__(self):
        """Clean up resources."""
        if self.delete_after_use:
            self._graph = None


class EndpointFactory:
    """Factory for creating SPARQL endpoints."""
    
    @classmethod
    def create_from_url(cls, url: str):
        """Create an endpoint from a URL or file path."""
        if Validator.url(url):
            return RemoteEndpoint(url)
        else:
            return LocalSparqlGraphStore(url)
// SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
// SPDX-License-Identifier: ISC

import java.io.PrintStream;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.aksw.sparqlmap.core.SparqlMap;
import org.aksw.sparqlmap.core.errors.ImplementationException;
import org.aksw.sparqlmap.core.SparqlMapBuilder;
import org.apache.jena.graph.Node;
import org.apache.jena.query.Dataset;
import org.apache.jena.riot.RDFDataMgr;
import org.apache.jena.sparql.core.Quad;
import org.apache.jena.riot.out.NodeFmtLib;
import org.apache.metamodel.jdbc.JdbcDataContext;
import org.apache.metamodel.MetaModelException;

import com.zaxxer.hikari.HikariDataSource;

public class InsertDataset {

  public static void main(String[] args) throws Exception {
    String jdbcUrl = args[0];
    String user = args[1];
    String password = args[2];
    String mapping = args[3];
    String data = args[4];
    PrintStream out = System.out;

    HikariDataSource pool = new HikariDataSource();
    pool.setJdbcUrl(jdbcUrl);
    pool.setUsername(user);
    pool.setPassword(password);
    JdbcDataContext context = new JdbcDataContext(pool);
    SparqlMap sparqlMap = SparqlMapBuilder.newSparqlMap(null).connectTo(context).mappedBy(mapping).create();

    Dataset dataset = RDFDataMgr.loadDataset(data);
    Map<Node, List<Quad>> bySubject = new LinkedHashMap<>();
    dataset.asDatasetGraph().find().forEachRemaining(quad ->
        bySubject.computeIfAbsent(quad.getSubject(), key -> new ArrayList<>()).add(quad));

    int requests = 0;
    int failures = 0;
    for (Map.Entry<Node, List<Quad>> entry : bySubject.entrySet()) {
      StringBuilder query = new StringBuilder("INSERT DATA {\n");
      for (Quad quad : entry.getValue()) {
        String triple = NodeFmtLib.str(quad.getSubject()) + " "
            + NodeFmtLib.str(quad.getPredicate()) + " "
            + NodeFmtLib.str(quad.getObject()) + " .";
        if (quad.isDefaultGraph()) {
          query.append("  ").append(triple).append("\n");
        } else {
          query.append("  GRAPH ").append(NodeFmtLib.str(quad.getGraph()))
              .append(" { ").append(triple).append(" }\n");
        }
      }
      query.append("}");
      requests++;
      out.println("SPARQLMAP_REQUEST " + entry.getKey());
      out.println(query);
      try {
        sparqlMap.update(query.toString()).execute();
        out.println("SPARQLMAP_REQUEST_DONE " + entry.getKey());
      } catch (ImplementationException | MetaModelException error) {
        failures++;
        out.println("SPARQLMAP_REQUEST_FAILED " + entry.getKey() + " " + error.getClass().getName() + ": " + error.getMessage());
        error.printStackTrace(out);
      }
    }
    out.println("SPARQLMAP_SUMMARY requests=" + requests + " failures=" + failures);
    dataset.close();
    sparqlMap.close();
    pool.close();
  }
}

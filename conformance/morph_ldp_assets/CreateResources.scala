// SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
// SPDX-License-Identifier: ISC

import java.io.{FileInputStream, PrintWriter}
import java.util.Properties
import org.apache.log4j.{AppenderSkeleton, BasicConfigurator, Logger}
import org.apache.log4j.spi.LoggingEvent
import com.hp.hpl.jena.tdb.TDBFactory
import es.upm.fi.dia.oeg.morph.base.MorphProperties
import es.upm.fi.dia.oeg.morph.r2rml.ldp.engine.{MorphLDPRunner, MorphLDPRunnerFactory}

object CreateResources {
  def main(args: Array[String]): Unit = {
    Logger.getRootLogger.removeAllAppenders()
    BasicConfigurator.configure()
    val sql = new PrintWriter(args(3))
    Logger.getRootLogger.addAppender(new AppenderSkeleton {
      def requiresLayout() = false
      def close(): Unit = sql.close()
      def append(event: LoggingEvent): Unit = {
        val message = event.getRenderedMessage()
        if (message.startsWith("zInsert = \n")) {
          sql.println(message.substring("zInsert = \n".length) + ";")
          sql.flush()
        }
      }
    })
    val input = new Properties()
    val stream = new FileInputStream(args(0))
    try input.load(stream) finally stream.close()
    val props = new MorphProperties()
    props.noOfDatabase = 1
    props.databaseType = input.getProperty("database.type")
    props.databaseDriver = input.getProperty("database.driver")
    props.databaseURL = input.getProperty("database.url")
    props.databaseName = input.getProperty("database.name")
    props.databaseUser = input.getProperty("database.user")
    props.databasePassword = input.getProperty("database.password")
    props.mappingDocumentFilePath = input.getProperty("mapping")
    props.rdfLanguage = "N-TRIPLE"
    props.jenaMode = "memory"
    props.queryEvaluatorClassName = "es.upm.fi.dia.oeg.morph.r2rml.rdb.engine.MorphRDBDataSourceReader"
    props.queryTranslatorFactoryClassName = "ConfiguredQueryTranslatorFactory"
    val dataset = TDBFactory.createDataset(args(2))
    try {
      val model = dataset.getDefaultModel()
      val rdf = new FileInputStream(args(1))
      try model.read(rdf, null, "N-TRIPLE") finally rdf.close()
      val runner = new MorphLDPRunnerFactory().createRunner(props).asInstanceOf[MorphLDPRunner]
      val subjects = model.listSubjects()
      try {
        while (subjects.hasNext()) {
          val subject = subjects.nextResource()
          println("MORPH_CREATE_RESOURCE " + subject)
          try runner.createResource(subject) catch {
            case error: Exception if error.getClass == classOf[Exception] &&
                error.getMessage == "Only STG pattern is supported for insert operation!" =>
              System.err.println("MORPH_RESOURCE_REJECTED " + subject + ": " + error.getMessage)
          }
        }
      } finally subjects.close()
    } finally {
      dataset.close()
      sql.close()
    }
  }
}

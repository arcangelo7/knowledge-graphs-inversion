// SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
// SPDX-License-Identifier: ISC

import java.sql.Connection
import es.upm.dia.fi.oeg.morph.r2rml.model.R2RMLMappingDocument
import es.upm.fi.dia.oeg.morph.base.engine.{IQueryTranslator, IQueryTranslatorFactory}
import es.upm.fi.dia.oeg.morph.base.model.MorphBaseMappingDocument
import es.upm.fi.dia.oeg.morph.base.querytranslator.NameGenerator
import es.upm.fi.dia.oeg.morph.r2rml.rdb.engine.MorphRDBUnfolder
import es.upm.fi.dia.oeg.morph.rdb.querytranslator._

class ConfiguredQueryTranslatorFactory extends IQueryTranslatorFactory {
  def createQueryTranslator(mapping: MorphBaseMappingDocument): IQueryTranslator =
    createQueryTranslator(mapping, null)

  def createQueryTranslator(mapping: MorphBaseMappingDocument, connection: Connection): IQueryTranslator = {
    val document = mapping.asInstanceOf[R2RMLMappingDocument]
    val dialect = document.dbMetaData.get.dbType
    val unfolder = new MorphRDBUnfolder(document)
    unfolder.dbType = dialect
    val translator = new MorphRDBQueryTranslator(
      new NameGenerator(),
      new MorphRDBAlphaGenerator(document, unfolder),
      new MorphRDBBetaGenerator(document, unfolder),
      new MorphRDBCondSQLGenerator(document, unfolder),
      new MorphRDBPRSQLGenerator(document, unfolder))
    translator.connection = connection
    translator.mappingDocument = document
    translator.setDatabaseType(dialect)
    translator
  }
}

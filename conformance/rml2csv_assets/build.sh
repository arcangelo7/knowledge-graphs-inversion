#!/bin/sh
# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
# SPDX-License-Identifier: ISC
set -eu
mkdir -p /opt/rml2csv/lib /opt/rml2csv/classes
cp /source/rml2csv/Libs/RMLMapper-0.1.jar /opt/rml2csv/lib/
find /source/rml2csv/src/main/java -name '*.java' > /tmp/sources
javac -encoding UTF-8 -cp '/opt/rml2csv/lib/*' -d /opt/rml2csv/classes @/tmp/sources /source/adapter/ReverseDataset.java
cp -a /opt/java/openjdk /opt/rml2csv/java

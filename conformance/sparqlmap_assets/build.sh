#!/bin/sh
# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
# SPDX-License-Identifier: ISC
set -eu
cd /source/sparqlmap
./gradlew --no-daemon :sparqlmap-client:installDist -x test
mkdir -p /opt/sparqlmap/lib /opt/sparqlmap/classes
cp sparqlmap-client/build/install/*/lib/*.jar /opt/sparqlmap/lib/
cd /opt/sparqlmap/lib
curl -fsSL https://repo.maven.apache.org/maven2/mysql/mysql-connector-java/5.1.49/mysql-connector-java-5.1.49.jar -o mysql-connector-java-5.1.49.jar
curl -fsSL https://repo.maven.apache.org/maven2/org/postgresql/postgresql/42.2.29/postgresql-42.2.29.jar -o postgresql-42.2.29.jar
printf '%s\n' \
  '5bba9ff50e5e637a0996a730619dee19ccae274883a4d28c890d945252bb0e12  mysql-connector-java-5.1.49.jar' \
  '4c5d4528527354b2e594f458f0807e710f4d5683491179d2580f3d5ae5e7477c  postgresql-42.2.29.jar' | sha256sum -c -
javac -cp '/opt/sparqlmap/lib/*' -d /opt/sparqlmap/classes /source/adapter/InsertDataset.java
cp -a /opt/java/openjdk /opt/sparqlmap/java

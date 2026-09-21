#!/bin/sh
# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
# SPDX-License-Identifier: ISC
set -eu
mkdir -p /opt/morph-ldp/classes
find /source/morph/morph-base/src/main /source/morph/morph-base-querytranslator/src/main \
    /source/morph/morph-r2rml/src/main /source/morph/morph-r2rml-rdb/src/main \
    /source/morph/morph-rdb-querytranslator/src/main /source/ldp/src/main \
    /source/adapter -name '*.scala' -print | sort > /source/sources.txt
java -cp '/opt/morph-ldp/lib/*' scala.tools.nsc.Main \
    -classpath '/opt/morph-ldp/lib/*' -d /opt/morph-ldp/classes @/source/sources.txt
cp -a /usr/lib/jvm/zulu7-ca-amd64 /opt/morph-ldp/java

#!/bin/sh
# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
# SPDX-License-Identifier: ISC
set -eu
mkdir -p /source /rmlmapper
cd /source
curl -fsSL https://github.com/RMLio/rmlmapper-java/releases/download/v8.1.0/rmlmapper-8.1.0-r380-all.jar -o original.jar
curl -fsSL https://repo.maven.apache.org/maven2/be/ugent/idlab/knows/dataio/2.2.0/dataio-2.2.0-sources.jar -o sources.jar
printf '%s\n' \
  '819371d49ca47d8ffddae0f34e95f38e8eaaf588ee023e3c2c7527a14d302f58  original.jar' \
  'eb4e2a4b7c96732b61d58e783ff596988338ccef8f2bcc165f48faa249f0b22b  sources.jar' | sha256sum -c -
jar xf sources.jar be/ugent/idlab/knows/dataio/access/RDBAccess.java
git apply --no-index /assets/binary_null.patch
javac --release 17 -cp original.jar -d classes be/ugent/idlab/knows/dataio/access/RDBAccess.java
cp original.jar /rmlmapper/rmlmapper.jar
jar --update --file /rmlmapper/rmlmapper.jar --date=2026-09-10T00:00:00Z -C classes .
cp /assets/binary_null.patch /rmlmapper/
sha256sum /rmlmapper/rmlmapper.jar > /rmlmapper/sha256.txt

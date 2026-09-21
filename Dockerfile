# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

FROM maven:3.9.11-eclipse-temurin-17@sha256:e4a7ace3dc0d645ed97f8d9ad0b0d3f0b14fa8d150138f27f116d7105a639b82 AS rmlmapper-assets
COPY conformance/rmlmapper_assets /assets
RUN sh /assets/build.sh

FROM maven:3.9.11-eclipse-temurin-17@sha256:e4a7ace3dc0d645ed97f8d9ad0b0d3f0b14fa8d150138f27f116d7105a639b82 AS morph-dependencies

WORKDIR /source
RUN git clone https://github.com/fpriyatna/morph.git morph && \
    git -C morph checkout --detach 130af1ab3b7944953bb95572f8658910c107002a && \
    git clone https://github.com/fpriyatna/external-libs.git external-libs && \
    git -C external-libs checkout --detach da3038427da33f8e2a247fe2119027a44ad96ca6
COPY conformance/morph_ldp_assets/pom.xml ./pom.xml
RUN mvn -B org.apache.maven.plugins:maven-dependency-plugin:3.8.1:copy-dependencies \
    -DoutputDirectory=/opt/morph-ldp/lib

FROM azul/zulu-openjdk:7@sha256:ba31b92fba2cfa84844e187d4d70343c5a0a1886f30131a66afc5908d81a0ca5 AS morph-ldp-assets
COPY --from=morph-dependencies /source/morph /source/morph
COPY --from=morph-dependencies /opt/morph-ldp/lib /opt/morph-ldp/lib
COPY morph-LDP/src /source/ldp/src
COPY conformance/morph_ldp_assets /source/adapter
RUN sh /source/adapter/build.sh

FROM maven:3.9.11-eclipse-temurin-17 AS translator-build

WORKDIR /source

COPY R2RML2Datalog-Translator/translator/pom.xml ./pom.xml
COPY R2RML2Datalog-Translator/translator/src ./src

RUN mvn --quiet -Dproject.build.outputTimestamp=1980-01-01T00:00:02Z package

FROM alloka/souffle:v1.0.0@sha256:0e9288ca6f7a63faf93f4358f210de0ffcab6e3e2405d88c365391da6d54fe89 AS souffle-assets

COPY R2RML2Datalog-Translator/functors.cpp /tmp/functors.cpp
COPY --from=translator-build /source/target/rulegen.jar /opt/kgi/souffle/rulegen.jar

RUN g++ -std=c++17 -I/souffle/include -DRAM_DOMAIN_SIZE=32 \
        -shared -fPIC /tmp/functors.cpp \
        -o /opt/kgi/souffle/libfunctors.so

FROM souffle-assets AS krown-souffle

COPY ReverseR2RML/reverseR2RML.py /souffle/reverseR2RML.py

RUN mkdir -p /souffle/lib && \
    cp /opt/kgi/souffle/rulegen.jar /souffle/rulegen.jar && \
    cp /opt/kgi/souffle/libfunctors.so /souffle/lib/libfunctors.so

ENV LD_LIBRARY_PATH=/souffle/lib
ENV PATH=/souffle/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

CMD ["tail", "-f", "/dev/null"]

FROM python:3.12-slim AS krown-rmlmapper

RUN apt-get update && apt-get install -y \
    ca-certificates \
    git \
    vim-tiny \
    wget \
    postgresql-client \
    openjdk-21-jre-headless \
    && rm -rf /var/lib/apt/lists/*

COPY --from=rmlmapper-assets /rmlmapper /rmlmapper

CMD ["tail", "-f", "/dev/null"]

FROM krown-rmlmapper AS benchmark

RUN mkdir -p /scripts

RUN pip install uv

ENV RMLMAPPER_JAR=/rmlmapper/rmlmapper.jar

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen

COPY . .

COPY scripts/entrypoint.sh /scripts/entrypoint.sh
RUN chmod +x /scripts/entrypoint.sh

EXPOSE 5000

ENTRYPOINT ["/scripts/entrypoint.sh"]
CMD ["app"]

FROM benchmark AS app

RUN apt-get update && apt-get install -y \
    libffi8 \
    libgomp1 \
    libncurses6 \
    libsqlite3-0 \
    libtinfo6 \
    mcpp \
    zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY --from=souffle-assets /souffle/bin/souffle /usr/local/bin/souffle
COPY --from=souffle-assets /opt/kgi/souffle /opt/kgi/souffle

COPY --from=morph-ldp-assets /opt/morph-ldp /opt/morph-ldp
ENV MORPH_LDP_HOME=/opt/morph-ldp

ENV SOUFFLE_TRANSLATOR_JAR=/opt/kgi/souffle/rulegen.jar
ENV SOUFFLE_FUNCTOR_LIBRARY=/opt/kgi/souffle/libfunctors.so
ENV SOUFFLE_EXECUTABLE=/usr/local/bin/souffle

FROM app AS app-souffle

COPY ReverseR2RML/reverseR2RML.py /opt/kgi/souffle/reverseR2RML.py

ENV SOUFFLE_REVERSE_SCRIPT=/opt/kgi/souffle/reverseR2RML.py

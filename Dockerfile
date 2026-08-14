# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

FROM maven:3.9.11-eclipse-temurin-17 AS translator-build

WORKDIR /source

COPY R2RML2Datalog-Translator/translator/pom.xml ./pom.xml
COPY R2RML2Datalog-Translator/translator/src ./src

RUN mvn --quiet -Dproject.build.outputTimestamp=1980-01-01T00:00:02Z package

FROM alloka/souffle:v1.0.0@sha256:0e9288ca6f7a63faf93f4358f210de0ffcab6e3e2405d88c365391da6d54fe89 AS souffle-assets

COPY R2RML2Datalog-Translator/functors.cpp /tmp/functors.cpp
COPY --from=translator-build /source/target/rulegen.jar /opt/kgi/souffle/rulegen.jar
COPY KROWN_Extended/execution-framework/dockers/Souffle/reverseR2RML.py /opt/kgi/souffle/reverseR2RML.py

RUN g++ -std=c++17 -shared -fPIC /tmp/functors.cpp \
        -o /opt/kgi/souffle/libfunctors.so

FROM souffle-assets AS krown-souffle

RUN mkdir -p /souffle/lib && \
    cp /opt/kgi/souffle/rulegen.jar /souffle/rulegen.jar && \
    cp /opt/kgi/souffle/reverseR2RML.py /souffle/reverseR2RML.py && \
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

RUN mkdir -p /rmlmapper && wget -q -O /rmlmapper/rmlmapper.jar \
    https://github.com/RMLio/rmlmapper-java/releases/download/v8.1.0/rmlmapper-8.1.0-r380-all.jar

CMD ["tail", "-f", "/dev/null"]

FROM krown-rmlmapper AS benchmark

RUN mkdir -p /scripts

RUN pip install uv

ENV RMLMAPPER_JAR=/rmlmapper/rmlmapper.jar

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen

COPY . .

COPY entrypoint.sh /scripts/entrypoint.sh
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

ENV SOUFFLE_TRANSLATOR_JAR=/opt/kgi/souffle/rulegen.jar
ENV SOUFFLE_REVERSE_SCRIPT=/opt/kgi/souffle/reverseR2RML.py
ENV SOUFFLE_FUNCTOR_LIBRARY=/opt/kgi/souffle/libfunctors.so
ENV SOUFFLE_EXECUTABLE=/usr/local/bin/souffle

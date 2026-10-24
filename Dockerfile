# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

FROM maven:3.9.11-eclipse-temurin-17@sha256:e4a7ace3dc0d645ed97f8d9ad0b0d3f0b14fa8d150138f27f116d7105a639b82 AS rmlmapper-assets
COPY conformance/rmlmapper_assets /assets
RUN sh /assets/build.sh

FROM python:3.12-slim AS app

RUN apt-get update && apt-get install -y \
    ca-certificates \
    git \
    vim-tiny \
    wget \
    postgresql-client \
    openjdk-21-jre-headless \
    && rm -rf /var/lib/apt/lists/*

COPY --from=rmlmapper-assets /rmlmapper /rmlmapper

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

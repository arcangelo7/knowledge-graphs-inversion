# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    ca-certificates \
    git \
    vim-tiny \
    wget \
    postgresql-client \
    openjdk-21-jre-headless \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /scripts

RUN pip install uv

RUN wget -q -O /opt/rmlmapper.jar \
    https://github.com/RMLio/rmlmapper-java/releases/download/v8.1.0/rmlmapper-8.1.0-r380-all.jar

ENV RMLMAPPER_JAR=/opt/rmlmapper.jar

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen

COPY . .

RUN git submodule update --init --recursive || true

COPY entrypoint.sh /scripts/entrypoint.sh
RUN chmod +x /scripts/entrypoint.sh

EXPOSE 5000

ENTRYPOINT ["/scripts/entrypoint.sh"]
CMD ["app"]

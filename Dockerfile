# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

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

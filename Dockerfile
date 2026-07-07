# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    ca-certificates \
    git \
    gpg \
    wget \
    postgresql-client \
    openjdk-21-jre-headless \
    && rm -rf /var/lib/apt/lists/*

RUN wget -qO - https://packages.qlever.dev/pub.asc | \
    gpg --dearmor -o /usr/share/keyrings/qlever.gpg && \
    . /etc/os-release && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/qlever.gpg] https://packages.qlever.dev/ ${UBUNTU_CODENAME:-$VERSION_CODENAME} main" > /etc/apt/sources.list.d/qlever.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends curl qlever-bin qlever-control && \
    rm -rf /var/lib/apt/lists/*

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

EXPOSE 5000 7019

ENTRYPOINT ["/scripts/entrypoint.sh"]
CMD ["app"]

FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    git \
    wget \
    postgresql-client \
    openjdk-21-jre-headless \
    libncurses6 \
    libtinfo6 \
    && rm -rf /var/lib/apt/lists/*

RUN cd /tmp && \
    wget https://github.com/openlink/virtuoso-opensource/releases/download/v7.2.15/virtuoso-opensource.x86_64-generic_glibc25-linux-gnu.tar.gz && \
    tar -xzf virtuoso-opensource.x86_64-generic_glibc25-linux-gnu.tar.gz && \
    mv virtuoso-opensource /opt/ && \
    rm virtuoso-opensource.x86_64-generic_glibc25-linux-gnu.tar.gz && \
    ln -s /usr/lib/x86_64-linux-gnu/libncurses.so.6 /usr/lib/x86_64-linux-gnu/libncurses.so.5 && \
    ln -s /usr/lib/x86_64-linux-gnu/libtinfo.so.6 /usr/lib/x86_64-linux-gnu/libtinfo.so.5

RUN mkdir -p /opt/virtuoso-data && \
    mkdir -p /scripts

RUN pip install uv

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen

COPY . .

RUN git submodule update --init --recursive || true

COPY entrypoint.sh /scripts/entrypoint.sh
RUN chmod +x /scripts/entrypoint.sh

EXPOSE 5000 8890 1111

ENV EMBEDDED_VIRTUOSO=true
ENV VIRTUOSO_DATA_DIR=/opt/virtuoso-data
ENV VIRTUOSO_BULK_DIR=/opt/virtuoso-data
ENV DBA_PASSWORD=dba
ENV DAV_PASSWORD=dba

ENTRYPOINT ["/scripts/entrypoint.sh"]
CMD ["app"]
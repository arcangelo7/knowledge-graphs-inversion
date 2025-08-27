FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    git \
    curl \
    openjdk-21-jre-headless \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen

COPY . .

RUN git submodule update --init --recursive || true

EXPOSE 5000

CMD ["uv", "run", "python", "app.py"]
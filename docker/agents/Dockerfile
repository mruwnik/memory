FROM python:3.12-slim

WORKDIR /app

# gcc builds wheels that have no binary for this platform (e.g. psutil on arm64).
RUN apt-get update && apt-get install -y gcc python3-dev \
    && rm -rf /var/lib/apt/lists/*

# The runner needs the API package (fastmcp, the in-process MCP server), and
# Celery autodiscovery imports every task module, which needs the worker deps.
COPY requirements ./requirements/
COPY setup.py ./
RUN mkdir src
RUN pip install --no-cache-dir -e ".[api,workers]"

COPY src/ ./src/
RUN pip install --no-cache-dir -e ".[api,workers]"

RUN apt-get purge -y gcc python3-dev && apt-get autoremove -y

RUN mkdir -p /app/memory_files
RUN useradd -m -u 1000 kb && chown -R kb:kb /app

USER kb

ENV PYTHONPATH="/app"
# External MCP servers must never reach the stack's own container networks.
ENV MCP_BLOCK_CONTAINER_NETWORKS=1
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

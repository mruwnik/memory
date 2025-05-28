FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y \
    libpq-dev gcc supervisor && \
    pip install -e ".[workers]" && \
    apt-get purge -y gcc && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY requirements-*.txt ./
RUN pip install --no-cache-dir -r requirements-common.txt
RUN pip install --no-cache-dir -r requirements-parsers.txt
RUN pip install --no-cache-dir -r requirements-workers.txt

# Copy requirements files and setup
COPY setup.py ./
COPY src/ ./src/

# Create and copy entrypoint script
COPY docker/workers/entry.sh ./entry.sh
RUN chmod +x entry.sh

# Create storage directory
RUN mkdir -p /app/memory_files

COPY docker/ingest_hub/supervisor.conf /etc/supervisor/conf.d/supervisor.conf

# Create required tmpfs directories for supervisor
RUN mkdir -p /var/log/supervisor /var/run/supervisor

# Create user and set permissions
RUN useradd -m kb && chown -R kb /app /var/log/supervisor /var/run/supervisor /app/memory_files
USER kb

ENV QUEUES="maintenance"
ENV PYTHONPATH="/app"

ENTRYPOINT ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisor.conf"]
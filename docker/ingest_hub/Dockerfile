FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y \
    libpq-dev gcc supervisor && \
    apt-get purge -y gcc && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Copy requirements files and setup
COPY requirements ./requirements/
COPY setup.py ./
RUN mkdir src
RUN pip install -e ".[common]"

COPY src/ ./src/
RUN pip install -e ".[common]"

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
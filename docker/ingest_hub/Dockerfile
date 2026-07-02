FROM python:3.12-slim

WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y \
    libpq-dev gcc supervisor && \
    apt-get purge -y gcc && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Copy requirements files and setup
COPY requirements ./requirements/
COPY setup.py ./
RUN mkdir src
RUN pip install -e ".[ingesters]"

COPY src/ ./src/
RUN pip install -e ".[ingesters]"

# Create and copy entrypoint script
COPY docker/workers/entry.sh ./entry.sh
RUN chmod +x entry.sh

# Create storage directory
RUN mkdir -p /app/memory_files

COPY docker/ingest_hub/supervisor.conf /etc/supervisor/conf.d/supervisor.conf
COPY docker/ingest_hub/entry.sh ./supervisord_entry.sh
RUN chmod +x supervisord_entry.sh

# Create required tmpfs directories for supervisor
RUN mkdir -p /var/log/supervisor /var/run/supervisor

# Create user and set permissions
RUN useradd -m kb && chown -R kb /app /var/log/supervisor /var/run/supervisor /app/memory_files
USER kb

ENV PYTHONPATH="/app"

# Validate-then-exec wrapper that runs supervisord with fd 2 on /dev/null so
# a dead conmon pipe can't kill it — rationale in the script itself.
ENTRYPOINT ["/app/supervisord_entry.sh"]
FROM python:3.11-slim

WORKDIR /app

# Copy requirements files and setup
COPY requirements-*.txt ./
COPY setup.py ./
COPY src/ ./src/

# Install dependencies
RUN apt-get update && apt-get install -y \
        libpq-dev gcc && \
    pip install -e ".[workers]" && \
    apt-get purge -y gcc && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Create and copy entrypoint script
COPY docker/workers/entry.sh ./entry.sh
RUN chmod +x entry.sh

# Create user and set permissions
RUN useradd -m kb && chown -R kb /app
USER kb

# Default queues to process
ENV QUEUES="medium_embed,photo_embed,low_ocr,git_summary,rss,docs"
ENV PYTHONPATH="/app"

ENTRYPOINT ["./entry.sh"] 
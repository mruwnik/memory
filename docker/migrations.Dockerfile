FROM python:3.11-slim

WORKDIR /app

# Copy requirements files and setup
COPY requirements ./requirements/
COPY setup.py ./
RUN mkdir src
RUN pip install -e ".[common]"

# Install the package with common dependencies
COPY src/ ./src/
RUN pip install -e ".[common]"

# Run as non-root user
RUN useradd -m appuser
RUN mkdir -p /app/memory_files
ENV PYTHONPATH="/app"

# Create user and set permissions
RUN useradd -m kb
RUN mkdir -p /var/cache/fontconfig /home/kb/.cache/fontconfig && \
    chown -R kb:kb /var/cache/fontconfig /home/kb/.cache/fontconfig /app

USER kb

# Run the migrations
CMD ["alembic", "-c", "/app/db/migrations/alembic.ini", "upgrade", "head"] 
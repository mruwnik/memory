FROM python:3.12-slim

WORKDIR /app

# Copy requirements files and setup
COPY requirements ./requirements/
COPY setup.py ./
RUN mkdir src
RUN pip install -e ".[common]"

# Install the package with common dependencies
COPY src/ ./src/
RUN pip install -e ".[common]"

RUN mkdir -p /app/memory_files
ENV PYTHONPATH="/app"

# Run the migrations (as root - this is a one-shot container)
CMD ["alembic", "-c", "/app/db/migrations/alembic.ini", "upgrade", "head"] 
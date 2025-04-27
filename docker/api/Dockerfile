FROM python:3.10-slim

WORKDIR /app

# Copy requirements files and setup
COPY requirements-*.txt ./
COPY setup.py ./
COPY src/ ./src/

# Install the package with API dependencies
RUN pip install -e ".[api]"

# Run as non-root user
RUN useradd -m appuser
USER appuser

# Set environment variables
ENV PORT=8000
ENV PYTHONPATH="/app"

EXPOSE 8000

# Run the API
CMD ["uvicorn", "memory.api.app:app", "--host", "0.0.0.0", "--port", "8000"] 
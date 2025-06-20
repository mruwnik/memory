# Frontend build stage
FROM node:18-alpine AS frontend-builder

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Backend build stage
FROM python:3.11-slim

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements files and setup
COPY requirements ./requirements/
RUN mkdir src
COPY setup.py ./
# Do an initial install to get the dependencies cached
RUN pip install -e ".[api]"

# Install the package with common dependencies
COPY src/ ./src/
RUN pip install -e ".[api]"

# Copy frontend build output from previous stage
COPY --from=frontend-builder /frontend/dist ./static/

# Run as non-root user
RUN useradd -m appuser
RUN mkdir -p /app/memory_files
ENV PYTHONPATH="/app"

# Create user and set permissions
RUN useradd -m kb
RUN mkdir -p /var/cache/fontconfig /home/kb/.cache/fontconfig && \
    chown -R kb:kb /var/cache/fontconfig /home/kb/.cache/fontconfig /app

USER kb

# Set environment variables
ENV PORT=8000
EXPOSE 8000

CMD ["uvicorn", "memory.api.app:app", "--host", "0.0.0.0", "--port", "8000"] 
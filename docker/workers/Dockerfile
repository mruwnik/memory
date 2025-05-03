FROM python:3.11-slim

WORKDIR /app

# Copy requirements files and setup
COPY requirements-*.txt ./
COPY setup.py ./
COPY src/ ./src/

# Install dependencies
RUN apt-get update && apt-get install -y \
    libpq-dev gcc pandoc \
    texlive-xetex texlive-fonts-recommended texlive-plain-generic \
    texlive-lang-greek texlive-lang-cyrillic texlive-lang-european \
    texlive-luatex texlive-latex-extra texlive-latex-recommended \
    texlive-science texlive-fonts-extra \
    fontconfig \
    # For optional LibreOffice support (uncomment if needed)
    # libreoffice-writer \
    && apt-get purge -y gcc && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
RUN pip install -e ".[workers]"

# Create and copy entrypoint script
COPY docker/workers/entry.sh ./entry.sh
COPY docker/workers/unnest-table.lua ./unnest-table.lua
RUN chmod +x entry.sh

RUN mkdir -p /app/memory_files

# Create user and set permissions
RUN useradd -m kb
RUN mkdir -p /var/cache/fontconfig /home/kb/.cache/fontconfig && \
    chown -R kb:kb /var/cache/fontconfig /home/kb/.cache/fontconfig /app

USER kb

# Default queues to process
ENV QUEUES="medium_embed,photo_embed,low_ocr,git_summary,rss,docs,email"
ENV PYTHONPATH="/app"

ENTRYPOINT ["./entry.sh"] 
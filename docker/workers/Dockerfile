FROM python:3.11-slim

WORKDIR /app

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

COPY requirements-*.txt ./
RUN pip install --no-cache-dir -r requirements-common.txt
RUN pip install --no-cache-dir -r requirements-parsers.txt
RUN pip install --no-cache-dir -r requirements-workers.txt

# Install Python dependencies
COPY setup.py ./
COPY src/ ./src/
RUN pip install -e ".[workers]"

# Copy entrypoint scripts and set permissions
COPY docker/workers/entry.sh ./entry.sh
RUN chmod +x entry.sh

RUN mkdir -p /app/memory_files

COPY docker/workers/unnest-table.lua ./unnest-table.lua

# Create user and set permissions
RUN useradd -m kb
RUN mkdir -p /var/cache/fontconfig /home/kb/.cache/fontconfig && \
    chown -R kb:kb /var/cache/fontconfig /home/kb/.cache/fontconfig /app

USER kb

# Default queues to process
ENV QUEUES="ebooks,email,comic,blogs,forums,photo_embed,maintenance"
ENV PYTHONPATH="/app"

ENTRYPOINT ["./entry.sh"] 
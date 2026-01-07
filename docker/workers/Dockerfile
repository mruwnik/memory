FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libpq-dev gcc pandoc git openssh-client \
    texlive-xetex texlive-fonts-recommended texlive-plain-generic \
    texlive-lang-greek texlive-lang-cyrillic texlive-lang-european \
    texlive-luatex texlive-latex-extra texlive-latex-recommended \
    texlive-science texlive-fonts-extra \
    fontconfig \
    # For optional LibreOffice support (uncomment if needed)
    # libreoffice-writer \
    && apt-get purge -y gcc && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY requirements ./requirements/
COPY setup.py ./
RUN mkdir src
RUN pip install -e ".[workers]"

# Install Python dependencies
COPY src/ ./src/
RUN pip install -e ".[workers]"

# Copy entrypoint scripts and set permissions
COPY docker/workers/entry.sh ./entry.sh
RUN chmod +x entry.sh

RUN mkdir -p /app/memory_files

COPY docker/workers/unnest-table.lua ./unnest-table.lua

# Create user and set permissions
RUN useradd -m -u 1000 kb
RUN mkdir -p /var/cache/fontconfig /home/kb/.cache/fontconfig && \
    chown -R kb:kb /var/cache/fontconfig /home/kb/.cache/fontconfig /app

USER kb

# Git config will be set via environment variables
ENV GIT_USER_EMAIL=${GIT_USER_EMAIL:-me@some.domain}
ENV GIT_USER_NAME=${GIT_USER_NAME:-memory}
RUN git config --global user.email "${GIT_USER_EMAIL}" && \
    git config --global user.name "${GIT_USER_NAME}"

# Default queues to process
ENV QUEUES="backup,blogs,calendar,comic,discord,ebooks,email,forums,github,google,maintenance,meetings,notes,people,photos,scheduler"
ENV PYTHONPATH="/app"

ENTRYPOINT ["./entry.sh"] 
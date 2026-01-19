FROM ubuntu:24.04

# Avoid interactive prompts during installation
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    curl \
    git \
    jq \
    openssl \
    python3 \
    python3-pip \
    openssh-client \
    ca-certificates \
    tmux \
    openjdk-21-jdk-headless \
    rlwrap \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20.x
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/bin/python

# Install Clojure (needed to build differ)
RUN curl -fsSL https://download.clojure.org/install/linux-install-1.11.1.1435.sh | bash

# Create users:
# - claude (UID 10000): runs Claude Code
# - differ (UID 10001): runs differ MCP server (isolated from claude)
# Both share a 'workspace' group for /workspace access
# differ also has its own group for private files (SQLite DB with GitHub token)
#
# Permissions model:
# - /workspace: differ owns (creates repo), claude has group write via workspace group
#   setgid bit (2775) ensures new files inherit workspace group
# - /home/claude: claude-only (mode 700), contains .claude config and credentials
# - differ's home: contains SQLite DB with GitHub token, inaccessible to claude
# - /var/log/claude: mounted from host, UID 10000 matches orchestrator's chown
RUN groupadd -g 10000 workspace \
    && groupadd -g 10001 differ \
    && useradd -m -s /bin/bash -u 10000 -g workspace claude \
    && useradd -m -s /bin/bash -u 10001 -g differ -G workspace differ \
    && mkdir -p /home/claude/.claude /home/claude/.ssh /workspace /var/log/claude \
    && chown -R claude:workspace /home/claude /var/log/claude \
    && chmod 700 /home/claude \
    && chown -R differ:workspace /workspace \
    && chmod 2775 /workspace

# Install Claude Code CLI to default location (/home/claude/.local)
# For snapshot instances: snapshot captures full .local/ including auth state
# For volume instances: entrypoint reinstalls if missing
USER claude
RUN curl -fsSL https://claude.ai/install.sh | bash
ENV PATH="/home/claude/.local/bin:${PATH}"

# SSH known hosts for git
RUN ssh-keyscan github.com gitlab.com bitbucket.org >> /home/claude/.ssh/known_hosts 2>/dev/null

# Git config
RUN git config --global init.defaultBranch main

# Install differ for code review and PR management
# Owned by differ user - claude cannot read differ's database (contains GitHub token)
# NOTE: Differ is cloned at build time. To update differ, you must rebuild the image.
# The orchestrator detects Dockerfile changes and rebuilds automatically on restart.
# Change DIFFER_VERSION to bust the cache and pull fresh code.
ARG DIFFER_VERSION=2025-01-19
USER root
RUN mkdir -p /opt/differ && chown differ:differ /opt/differ

USER differ
WORKDIR /opt/differ
RUN echo "Differ version: $DIFFER_VERSION" && git clone --depth 1 https://github.com/mruwnik/differ.git . \
    && npm install \
    && npm run build

USER root
RUN mkdir -p /var/log/claude && chown claude:workspace /var/log/claude
WORKDIR /

COPY docker/claude-cloud/entrypoint.sh /entrypoint.sh
COPY docker/claude-cloud/verify-setup.sh /verify-setup.sh
RUN chmod 755 /entrypoint.sh /verify-setup.sh

# Entrypoint runs as root to switch between users
WORKDIR /workspace
ENTRYPOINT ["/entrypoint.sh"]

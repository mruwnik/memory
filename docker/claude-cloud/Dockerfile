FROM ubuntu:24.04

# Avoid interactive prompts during installation
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    curl \
    git \
    python3 \
    python3-pip \
    openssh-client \
    ca-certificates \
    tmux \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20.x
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/bin/python

# Create non-root user for Claude Code (required for --dangerously-skip-permissions)
# Using UID 10000 to avoid conflicts with host users (1000 is often taken by ec2-user, etc.)
RUN useradd -m -s /bin/bash -u 10000 claude \
    && mkdir -p /home/claude/.claude /home/claude/.ssh /workspace /var/log/claude \
    && chown -R claude:claude /home/claude /workspace /var/log/claude

# Install Claude Code CLI as claude user
USER claude
ENV PATH="/home/claude/.local/bin:${PATH}"
RUN curl -fsSL https://claude.ai/install.sh | bash

# SSH known hosts for git
RUN ssh-keyscan github.com gitlab.com bitbucket.org >> /home/claude/.ssh/known_hosts 2>/dev/null

# Git config
RUN git config --global init.defaultBranch main

USER root
COPY docker/claude-cloud/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh

USER claude
WORKDIR /workspace
ENTRYPOINT ["/entrypoint.sh"]

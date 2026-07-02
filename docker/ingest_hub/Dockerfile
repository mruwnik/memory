FROM python:3.12-slim

WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y \
    libpq-dev gcc supervisor && \
    apt-get purge -y gcc && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Copy requirements files and setup
COPY requirements ./requirements/
COPY setup.py ./
RUN mkdir src
RUN pip install -e ".[ingesters]"

COPY src/ ./src/
RUN pip install -e ".[ingesters]"

# Create and copy entrypoint script
COPY docker/workers/entry.sh ./entry.sh
RUN chmod +x entry.sh

# Create storage directory
RUN mkdir -p /app/memory_files

COPY docker/ingest_hub/supervisor.conf /etc/supervisor/conf.d/supervisor.conf

# Create required tmpfs directories for supervisor
RUN mkdir -p /var/log/supervisor /var/run/supervisor

# Create user and set permissions
RUN useradd -m kb && chown -R kb /app /var/log/supervisor /var/run/supervisor /app/memory_files
USER kb

ENV PYTHONPATH="/app"

# supervisord's stderr must always be writable: its logger's handleError()
# prints failed-write tracebacks to fd 2 and raises out of PID 1 if that write
# also fails. With both fds on the conmon pipe, a dead conmon turns the first
# log line into a silent whole-container exit (2026-07-02 zombie incident).
# /dev/null for fd 2 makes log-write failure non-fatal; child stderr still
# reaches `podman logs` via redirect_stderr in supervisor.conf.
#
# But fd 2 -> /dev/null would also swallow supervisord's own pre-logger startup
# errors (options.py usage() writes config errors to sys.stderr), turning a
# config typo into a silent-exit container. So while fd 2 is still live, run
# supervisord's own config validation (ServerOptions.realize: parse, %(ENV_x)s
# expansion, user lookup — no pidfile/setuid side effects) so errors hit real
# stderr and abort with exit 2; only the long-lived supervisord gets fd 2 on
# /dev/null. Debian's supervisor package installs for /usr/bin/python3, not
# the image's /usr/local python — hence the explicit interpreter.
ENTRYPOINT ["/bin/sh", "-c", "/usr/bin/python3 -c \"from supervisor.options import ServerOptions; ServerOptions().realize(args=['-c', '/etc/supervisor/conf.d/supervisor.conf'], progname='supervisord')\" && exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisor.conf 2>/dev/null"]
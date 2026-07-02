#!/bin/sh
# Two-phase start for supervisord (2026-07-02 zombie incident, see
# docs/incidents/2026-07-02-ingest-hub-zombie.md).
#
# Phase 2 runs supervisord with fd 2 on /dev/null: its logger's handleError()
# prints failed-write tracebacks to stderr and raises out of PID 1 if that
# write also fails, so with both fds on the conmon pipe a dead conmon turns
# the first log line into a silent whole-container exit. A writable fd 2
# makes log-write failure non-fatal; child stderr still reaches
# `podman logs` via redirect_stderr in supervisor.conf.
#
# But that same /dev/null would swallow supervisord's own pre-logger startup
# errors (options.py usage() writes config errors to stderr), turning a config
# typo into a silent-exit container. So phase 1 validates the config while
# stderr is still live: ServerOptions.realize does the full parse (including
# %(ENV_x)s expansion and user lookup) with no pidfile/socket/setuid side
# effects — safe to run as the unprivileged kb user — and exits 2 with the
# error on real stderr. Debian's supervisor package installs for
# /usr/bin/python3, not the image's /usr/local python — hence the explicit
# interpreter.
set -eu

CONF=/etc/supervisor/conf.d/supervisor.conf

/usr/bin/python3 -c "from supervisor.options import ServerOptions; ServerOptions().realize(args=['-c', '$CONF'], progname='supervisord')"

# exec so supervisord stays PID 1 and receives container signals directly
exec /usr/bin/supervisord -c "$CONF" 2>/dev/null

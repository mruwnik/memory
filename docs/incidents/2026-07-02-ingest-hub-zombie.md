# Incident: chris-ingest-hub-1 zombie container (Discord tools outage)

**Date:** 2026-07-02
**Impact:** All `discord_*` MCP tools (chris/equistamp servers) failed from 16:19 to 17:43 UTC (~84 min).
**Detected by:** health-check zombie detector at 16:24:02 UTC (first failing run, 5 min after death); user-visible as MCP tool errors.

## Summary

The `chris-ingest-hub-1` container's process tree died silently at **16:19:34 UTC**.
Podman kept reporting it as "Up 6 days" because the conmon process monitoring it
was already dead, so the exit was never observed — a zombie container. Since
`restart: unless-stopped` only fires on an *observed* exit, nothing restarted it.
All Discord MCP tools proxy through `ingest-hub:8003` from chris-api, so every
Discord call failed with `[Errno 111] Connection refused`.

Recovered at **17:43 UTC** by force-removing the zombie and recreating the
container from the existing image (no pull/rebuild).

## Timeline (UTC)

| Time | Event |
|------|-------|
| Jun 26 12:27–12:29 | Messy stack restart: supervisord SIGTERM loops, discord-api killed with SIGKILL, container restarted twice in 2 min. Conmon (or its log pipes) evidently died here — **zero log lines were captured from this container for the next 6 days** despite it actively serving traffic. |
| Jul 2 ~13:00–16:19 | Container serving Discord traffic normally (METR channel-mapping session). |
| Jul 2 16:19:34 | `libpod-67e4cbab….scope: Deactivated successfully` — the whole process tree (supervisord + discord-api + celery-beat) exited. No OOM kill, no kernel event, no signal logged, memory peak 381.5M of a 512M limit. Cause unrecorded — with conmon dead, nothing the processes wrote in their final moments could be captured. |
| Jul 2 16:24:02 | health-check first flags: `Container chris-ingest-hub-1 reports running but its host process is dead (zombie state)`; Discord webhook notifications firing every 5 min thereafter. |
| Jul 2 16:24+ | chris-api logs fill with `HTTPConnectionPool(host='ingest-hub', port=8003) … Connection refused` for every Discord tool call. |
| Jul 2 17:41 | `compose up --force-recreate` fails with "conmon exited prematurely" but flips podman's state to `Exited (-1)`, leaving a renamed `67e4cbabfdd5_chris-ingest-hub-1` in Created state. |
| Jul 2 17:42 | `podman rm -f` both containers; `compose up -d --no-deps ingest-hub` with the existing image. |
| Jul 2 17:43 | supervisord/discord-api/celery-beat RUNNING, uvicorn on :8003, log capture working again. `discord_list_categories` verified end-to-end. |

## Root cause

**Update 2026-07-02 (later session): the death mechanism is now identified and
reproduced.** Supervisord 4.2.5's logger is what killed the container, via an
unguarded error-handler cascade:

1. A log write to `/dev/stdout` (the conmon pipe) raises `BrokenPipeError`
   once the pipe has no reader. `loggers.Handler.emit` catches it and calls
   `handleError()`.
2. `handleError()` prints the traceback **to `sys.stderr` — the same dead
   pipe** — raising a second `BrokenPipeError` that escapes `emit()`.
3. The main loop's `except:` catches that and calls the dispatcher's
   `handle_error()`, which calls `logger.critical(...)` → a third
   `BrokenPipeError`, this time *inside* the `except:` handler → escapes
   `runforever()` → supervisord (PID 1) exits → namespace teardown kills
   celery-beat and discord-api. No dying words possible by construction.

Reproduced locally (supervisord 4.2.5 on a pipe, reader closed): death within
seconds of the first log lines, exit code 120. Breadcrumb instrumentation
confirmed the exact chain above. Note this is an exception cascade, not the
SIGPIPE signal (Python ignores SIGPIPE) — the earlier hypothesis was
directionally right. The stdlib `logging` module guards this exact path
(`except OSError: pass` in `Handler.handleError`, bpo-5971), which is why the
plain uvicorn/celery containers (api, workers) are *not* vulnerable —
supervisord's homegrown logger is missing that guard, making ingest-hub
uniquely fragile.

This also refines the timeline: celery-beat emitted schedule lines every
minute for the whole 6 days, and any one of them would have triggered the
cascade within moments of the pipe losing its reader. So the stdout pipe had
a live, draining reader until exactly 16:19:34 — the Jun 26 restart broke
conmon's log *persistence* (zero captured lines), not its pipe-draining. What
killed the degraded reader at 16:19:34 is still unrecorded (the
`list_role_members` burst is plausible as the volume that tipped it over);
supervisord's death milliseconds later, however, was fully deterministic.

Two stacked failures remain the frame:

1. **Conmon log-capture death (Jun 26), full death (Jul 2 16:19:34).** With
   conmon gone, podman had no way to observe the exit.
2. **Supervisord logger cascade (Jul 2 16:19:34)** — the proven mechanism above.

The zombie state, not the death itself, is what turned a blip into an outage:
a normally-observed exit would have been auto-restarted by podman within seconds.

## Fix (in this repo)

`docker/ingest_hub/` hardening, verified by local repro under ~6MB of
post-death log traffic:

- **Dockerfile**: supervisord now runs with fd 2 → `/dev/null`. `handleError()`
  then always succeeds, `emit()` swallows the `BrokenPipeError`, and the
  cascade is impossible. A dead conmon degrades the container to
  logless-but-serving instead of killing it, and supervisord keeps draining
  child pipes (children never block).
- **supervisor.conf**: `redirect_stderr=true` on both programs (child stderr
  merges into the stdout channel) so pointing fd 2 at `/dev/null` doesn't
  discard uvicorn/celery stderr output.

Needs an image rebuild on deploy (`compose build ingest-hub`), not just a
restart.

## What worked

- The new `/proc`-liveness zombie detector (commit `5424cd5`) caught it on the
  first health-check run after death and alerted via Discord webhook every 5 min.
- Journald retained the systemd scope-deactivation record, giving an exact
  time of death even with container logs dead.

## Follow-ups worth considering

- **Pin conmon's death time**: `journalctl | grep 'libpod-conmon-67e4'` — the
  conmon scope's deactivation timestamp would confirm whether the reader died
  at 16:19:34 (predicted above) and whether the Jun 26 event only broke log
  persistence. (Host-side; not reachable from the sandbox this analysis ran in.)

- **Auto-remediate zombies**: health-check currently alerts only. A zombie has no
  clean fix except recreate; the detector could do (or offer) `rm -f` + compose up.
- **Alert dedup/escalation**: the same FAIL posts every 5 min; a state-change-only
  or escalating notifier would make alerts more attention-worthy. (chris-postgres
  has been co-alerting at 91% of its memory limit in every one of these runs —
  separate issue, still outstanding.)
- **Detect dead log capture**: "container serving traffic but 0 log lines in N
  hours" is a cheap health-check probe that would have caught the June 26 conmon
  death a week early.
- **chris-postgres memory**: at 91% of limit; bump the limit or investigate growth.

## Runbook: recovering a zombie container

```bash
# 1. Confirm zombie: podman says running, /proc disagrees
pid=$(podman --url unix:///run/podman/podman.sock inspect <name> --format '{{.State.Pid}}')
test -d /proc/$pid || echo zombie

# 2. Find exact death time
journalctl | grep 'libpod-<container-id>.scope: Deactivated'

# 3. Recreate (chris example; expect step 3a to error but fix podman's state)
DC="docker-compose -f /var/opt/src/memory/docker-compose.yaml \
    -f /var/opt/services/compose/memory/docker-compose.override.yml \
    -p chris --env-file /var/opt/services/compose/memory/chris.env"
$DC up -d --force-recreate --no-deps ingest-hub   # 3a: errors "conmon exited prematurely"
podman --url unix:///run/podman/podman.sock rm -f chris-ingest-hub-1 <cid12>_chris-ingest-hub-1
$DC up -d --no-deps ingest-hub

# 4. Verify: /proc alive, logs flowing, then one real Discord MCP call
```

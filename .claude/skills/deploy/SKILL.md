---
name: deploy
description: Deploy the memory application to the remote server. Use when deploying code, syncing changes, restarting services, or running commands on the production server.
---

# Memory Deployment

The deployment script is at `tools/deploy.sh`. It manages the remote server `memory` (EC2 instance) at `/home/ec2-user/memory`.

## Commands

### Deploy (most common)
Pull latest code and restart services:
```bash
./tools/deploy.sh deploy          # deploys master branch
./tools/deploy.sh deploy <branch> # deploys specific branch
```

### Sync
Rsync local code directly to server (bypasses git, useful for testing):
```bash
./tools/deploy.sh sync
```
Syncs: `src/`, `tests/`, `tools/`, `db/`, `docker/`, `frontend/`, `requirements/`, config files.
Excludes: `__pycache__`, `.git`, `memory_files`, `secrets`, `.env`, `venv`, etc.

### Pull
Git pull on the server without restarting:
```bash
./tools/deploy.sh pull          # pulls master
./tools/deploy.sh pull <branch> # pulls specific branch
```

### Restart
Restart docker services without pulling new code:
```bash
./tools/deploy.sh restart
```
Runs: `docker compose up --build -d`

### Run
Execute arbitrary commands on the server (with venv activated):
```bash
./tools/deploy.sh run <command>
```
Examples:
```bash
./tools/deploy.sh run "python -c 'print(1)'"
./tools/deploy.sh run "pip list"
./tools/deploy.sh run "alembic upgrade head"
```

## Typical Workflows

**Standard deployment:**
```bash
git push origin master
./tools/deploy.sh deploy
```

**Quick test without committing:**
```bash
./tools/deploy.sh sync
./tools/deploy.sh restart
```

**Run migrations:**
```bash
./tools/deploy.sh run "alembic upgrade head"
```

**Check logs after deploy:**
```bash
./tools/deploy.sh run "docker compose logs -f"
```

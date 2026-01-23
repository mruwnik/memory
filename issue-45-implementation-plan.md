# Implementation Plan: Slack Message Ingestion

I've analyzed the codebase and designed an implementation following the existing Discord integration pattern. Here's the approach:

## Architecture Overview

### Database Models (`src/memory/common/db/models/slack.py`)

| Model | Purpose |
|-------|---------|
| `SlackWorkspace` | OAuth credentials + workspace metadata (team_id as PK, encrypted tokens, sync settings) |
| `SlackChannel` | Channel/DM/group tracking with collection toggle (inherit/on/off) |
| `SlackUser` | User cache for mention resolution, linkable to Person records |
| `SlackMessage` | SourceItem subclass for ingested messages |

**Key design choice:** Using OAuth2 user tokens (not bot tokens) to access the user's own DMs and private channels.

### OAuth2 Scopes (User Token)
- Read: `channels:history`, `groups:history`, `mpim:history`, `im:history`, `channels:read`, `groups:read`, `mpim:read`, `im:read`
- Metadata: `users:read`, `users:read.email`, `reactions:read`, `files:read`
- Write (for MCP tools): `chat:write`, `reactions:write`

### API Endpoints (`src/memory/api/slack.py`)
- `GET/slack/authorize` - Initiate OAuth2 flow
- `GET /slack/callback` - Handle OAuth2 callback
- Workspace CRUD: `GET/PATCH/DELETE /slack/workspaces/{id}`
- Channel management: `GET /slack/workspaces/{id}/channels`, `PATCH /slack/channels/{id}`
- Sync trigger: `POST /slack/workspaces/{id}/sync`
- User linking: `GET /slack/workspaces/{id}/users`, `PATCH /slack/users/{id}`

### Celery Tasks (`src/memory/workers/tasks/slack.py`)
- `SYNC_ALL_SLACK_WORKSPACES` - Beat task (configurable interval, default 60s)
- `SYNC_SLACK_WORKSPACE` - Refresh token, update channels/users, fan out to channel syncs
- `SYNC_SLACK_CHANNEL` - Incremental message fetch via `conversations.history` with cursor
- `ADD_SLACK_MESSAGE` - Process message, resolve mentions (`<@U123>` -> `@name`), create SourceItem

**Deduplication:** Messages identified by `channel_id + message_ts`. Edits detected via `edited_ts` field.

**Rate limiting:** Tier 3 handling (~50 req/min) with exponential backoff.

### MCP Tools (`src/memory/api/MCP/servers/slack.py`)
- `send_slack_message(message, channel_id/name, thread_ts?)` - via `chat.postMessage`
- `add_slack_reaction(channel_id, message_ts, emoji)` - via `reactions.add`
- `list_slack_channels(workspace_id?, include_dms?)`
- `get_slack_channel_history(channel_id/name, limit?, before?, after?)`

### Frontend (`frontend/src/components/sources/panels/SlackPanel.tsx`)
- OAuth "Connect Workspace" button with popup flow
- Workspace cards with sync status and interval configuration
- Channel list with three-state collection toggle (inherit/on/off)
- User linking to Person records

## Implementation Order

1. Database models + Alembic migration
2. Settings + OAuth endpoints
3. Celery tasks for sync
4. REST API endpoints
5. MCP tools
6. Frontend panel

## Environment Variables

```bash
SLACK_CLIENT_ID=...
SLACK_CLIENT_SECRET=...
SLACK_REDIRECT_URI=http://localhost:8000/slack/callback
SLACK_SYNC_INTERVAL=60  # optional
```

## Questions/Decisions

1. **Thread handling:** Plan to fetch threads inline when `reply_count > 0`. Alternative: separate thread sync pass.
2. **File handling:** Initially store metadata only (files accessible via Slack URLs while token valid). Future: download and store locally.
3. **Historical backfill:** On initial connection, sync all accessible history or just from connection time?

Let me know if you'd like any adjustments before I begin implementation.

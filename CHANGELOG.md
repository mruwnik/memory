# Changelog

Each release lists what changed and what an operator must do to update. See
[UPDATING.md](UPDATING.md) for the procedure. Use these sections in every
entry, writing "None" when a section has nothing in it:

- **Changes**: user-visible features, fixes and removals, one line each where
  possible. Never "None": `tools/release.sh` refuses a release without one,
  and the entry becomes the release tag's message.
- **Required env vars**: new settings without which the api/workers refuse to start.
- **Manual steps**: anything `tools/update.sh` doesn't do, e.g. a Postgres
  image change (major versions need a dump and restore, not just a new image).
- **Other changes for operators**: new optional settings, changed defaults,
  removed settings, endpoints or scripts operators call, resource and runtime
  notes, Qdrant changes.
- **Migrations**: new alembic revisions; flag any that are slow or lock large tables.
- **Post-deploy commands**: run after the update, in order. Each must be safe to re-run.

Changes on master collect under `## Unreleased` at the top, in the same
sections. Cutting a release renames it to `## vX.Y.Z (YYYY-MM-DD)` and starts
a new, empty `## Unreleased` above it.

## Unreleased

### Changes
- GitHub syncs and webhook refetches no longer report success when GitHub refuses a query. The repo row shows what the GitHub App or token can't read and which permissions to grant (e.g. Contents, Checks and Commit statuses for commits; organization Projects for projects; adding the repo to the App's installation when it can't see the repo at all), and the job fails with the same message. A repo GitHub answers not-found for is no longer deactivated as deleted: GitHub hides a private repo the App or token lost access to the same way, so the row says the App isn't installed on it (or the token can't see it), or it was renamed or deleted, and syncs keep trying. Refreshing, attaching or promoting a project through MCP no longer deactivates such a repo either. A refused milestone fetch is shown on the repo without stopping issues and PRs from syncing. Listing a GitHub account's available projects answers 502 with that message.
- When GitHub withholds only some fields (check results, the head commit), issues and PRs are still stored without them and the repo row shows "Sync incomplete" (a warning, not "Sync failing") with what to grant. Syncs keep moving their cursor; the first sync that gets every field re-fetches everything updated since the first incomplete one, then clears the repo's error. Fields no permission on the repo can grant (e.g. a PR's head repository on a private fork) are left out without flagging the repo.
- The daily open-items sweep of project-tracking repos also fetches what changed since the last sync, so issues and PRs closed in between are no longer missed.
- Webhook deliveries of deleted or transferred issues no longer queue a refetch, and a refetch of an item GitHub no longer has finishes as `not_found`.
- The webhook log lists a GitHub account's repo sync errors above its deliveries, since refetches fail the same way.
- `people_list_all` search also matches every contact_info value (emails, phone numbers, GitHub login, Slack user id, username and display name, Discord id) and the username and display name of linked Discord accounts the caller can see (admins see all; others, Discord users who wrote to one of their bots), so an email address or chat handle finds its person. `%` and `_` in the search term match only themselves.
- `people_list_all` search and tag filters only match tidbits the caller can read. Before, a match on an unreadable or hidden tidbit returned its person.
- `agent_run` and `agent_upsert` advertise the inline spec's fields in their parameter schema, and a spec with an unknown top-level field is refused with the list of accepted fields.
- `agent_run(notify=true)` notifies you when the run ends, on the same channel `meta_notify_user` picks (Discord, Slack or email); it is refused, with why each channel can't be used, when none can reach you, and runs started by runs can't ask to. `agent_wait(result_only=true)` returns the run's status, result and error without its spec. `agent_list_runs(unseen=true)` lists the runs you started with `agent_run` that ended since `agent_wait` or an unseen listing last handed them to you.
- `meta_notify_user` says why it can't use a channel: for Discord, whether no Discord account is linked to your Person or you have no Discord bot of your own (notifications go out through a bot you registered). Results list `skipped_channels`, why each channel ahead of the one used was passed over. A Discord user id in the Person's `contact_info.discord` counts as a linked account when no Discord account is linked.
- `people_upsert` links only the Discord ids its `contact_info` names, and removes a linked id from the Person it moves from, listing each move in `moved_discord_users`. Editing the `discord_<id>` Person that ingest made for an account no longer takes back the account after an admin linked it to your own Person, which left `meta_notify_user` without a Discord account (#118). Accounts already taken back are fixed by naming the id in your Person's `contact_info.discord` again. `discord_send_message` suggests a Person by the Discord id in their `contact_info.discord`.
- Browse what has been ingested: each content type on the Ingestion overview opens its items, newest first, a page at a time, and the overview page can show each type's latest five items inline. The list can be narrowed to one connection (email account, Discord or Slack channel, Beeper chat, feed, GitHub repo, Google Drive folder, calendar or transcript account, book), filtered by indexing status, and searched by title, subject, sender, author or filename (not the item text). Only items you can access are listed.
- MCP `core_list_items` and `core_count_items` take `item_types`, `embed_statuses` (default still indexed items only), `contains` and `connection_id`. List rows carry `type` and `embed_status`; `include_total=false` skips the total count; pages sort by id within equal timestamps so they never overlap. `core_count_items` adds `by_type` and, for types that share a kind of connection, `by_connection`.
- ERROR logs of the api and workers carry ids instead of document text, mailbox addresses, GitHub titles and responses, feed URLs, calendar names or file paths, and exception text goes only into the traceback the operator log filter cuts to the exception type.
- IMAP syncs and SMTP sends check the mail server's address each time they connect and dial only an address they checked, so a server whose DNS later points at a loopback, private or link-local address is refused instead of only being checked when the account is saved.
- A pushed Slack message that isn't stored yet is read back from Slack with the pushing app's own token and stored as Slack returns it (author, text, thread, files). A push for a message that app can't read is dropped, so one user's Slack app can no longer add messages, under any author, to channels other users collect. This costs one `conversations.history` (or `conversations.replies`) call per new pushed message; while Slack rate-limits or fails that call, the push is retried for about 15 minutes before it is given up to the next poll.
- Discord `send_message` to a channel now posts only with one of your own bots in that channel's server (by default the server's owning bot if it is yours, else another of yours there), and accepts any of your bots that has joined the server. It no longer falls back to another user's bot; with none of yours in the server it says so.
- `POST /meetings` external ids are now per user: another user's identical id no longer returns their job or suppresses your meeting, and ids may start with `push:`. `GET /api/jobs/external/{id}` finds only your own jobs.
- `people_merge` moves deadline ownership, Beeper participant links and GitHub item authorship to the kept person, and reports `deadlines_moved`, `beeper_participants_moved` and `github_items_moved`. Before, these were cleared when the merged-away person was deleted. A transcript account that makes action-item tasks for the merged-away person makes them for the kept person instead (`transcript_task_picks_moved`), rather than losing the pick and, if it was its only one, making tasks for everyone.
- `teams_upsert` without a `slug` folds accented letters into the derived slug (Équipe → `equipe`); a team of that name saved before, under the slug that dropped them (`quipe`), is still updated. It refuses a name with no usable slug characters, and a derived slug that belongs to a team with a different name (e.g. "C++" and "C" both give `c`), instead of overwriting that team. Pass `slug` to choose one.
- Forecast market search and resolved-market results (Manifold, Polymarket) keep a probability of exactly 0 instead of dropping it or showing the pre-resolution probability. ExUrbe blog posts get their published date as a date, read from the visible date when the page has no machine-readable one.
- IMAP folders whose names contain spaces, parentheses or non-ASCII characters (e.g. `AI orgs`, `Ärger`) sync. Before, the server refused to open them and their mail was never fetched.
- An IMAP sync that can't fetch or queue a message no longer moves the account's last sync time past that mail: the account shows how many failed and the next sync fetches them again. Mail still failing after 3 days (6 hours for an account that has never synced cleanly, whose every retry re-downloads the whole mailbox) is given up on, and the account's sync error says so. A folder that no longer exists on the server (renamed or deleted) is shown on the account as a folder-list problem and doesn't hold back the other folders; a folder the server refuses for a passing reason (throttling, a lock) counts as a failure. A clean sync records when it started rather than when it finished, so mail arriving mid-sync isn't skipped, and each sync re-checks the day before the last one (copies already stored are skipped).
- Emails without a Message-ID header are no longer dropped when another such email has the same IMAP UID in a different folder or account. Their generated id now comes from the message's content; such mail received within a day before upgrading may be stored a second time on the first sync.
- `core_search_observations` filters by `subject` (exact) and `observation_types`. Before, a subject returned no observations (it was matched against mail subjects) and observation types were ignored. The generic search filters no longer accept `observation_types`.
- The `author`, `authors`, `min_published` and `max_published` search filters match every item type that has an author or publication date (blog posts, comics, forum posts and comments, GitHub items, book sections). Before, keyword search and `core_list_items`/`core_count_items` matched them on blog posts only, and `authors` matched only forum posts in semantic search.
- Titles recalled by query analysis no longer bring in results that the search filters exclude. Before, they honoured only access control, tags and the item-id filters, so e.g. an `author`, `sender`, `person_id` or `min_confidences` filter could still return a non-matching item.
- Uploading a report (`POST /reports/upload`) into a project you aren't a member of is refused with 403 (admins excepted).
- Only a report's creator or an admin can overwrite it (MCP `reports_upsert` with its filename, or re-uploading it through `POST /reports/upload`) or delete it (`reports_delete`); being able to read it is no longer enough. The report worker writes an MCP report's file only after checking it may overwrite the report stored under that filename.
- Only a photo's creator or an admin can delete it (`DELETE /photos/{id}`); others who can see it get 403.
- MCP `organizer_upcoming` only returns calendar events the caller can access, as `list_tasks` does (admins see all). Before, every user saw everyone's events. Non-admins naming other users in `user_ids` are refused.
- MCP `organizer_upcoming` with a date-only `start_date` or `end_date` (e.g. "2024-01-15") includes recurring events again; dates and times without an offset are read as UTC.
- Recurring calendar events whose rule has `INTERVAL=0`, whitespace or a `:` (which could smuggle in another property) are skipped with a warning; an `INTERVAL=0` rule from a synced calendar hung `organizer_upcoming` forever. Expanding a rule visits at most 50,000 occurrences from the series start, which keeps a dense or decades-old series to about a quarter second, and an event that fails to expand no longer fails the whole query.
- Recurring all-day calendar series that end on a date (a date-only `UNTIL`, as CalDAV and Google send them) show in `organizer_upcoming` again, through the end of that day; a floating `UNTIL` time is read as UTC.
- Calendar syncs remove events deleted or cancelled in CalDAV or Google Calendar. A sync that read every calendar deletes the account's stored events starting inside the sync window (less a day at each end) that the calendar no longer returns; a sync that skipped a calendar or event, or found no calendar, deletes nothing. The sync result reports `events_deleted`.
- Gmail syncs no longer re-download, on every run, messages they never store (a copy the same account already stored, password-reset mail, a message that keeps killing its worker). The account remembers their ids (new column `email_accounts.gmail_declined_ids`) until they leave the mailbox; messages that failed to queue are retried. A copy stored by another account is checked again each sync, so the account stores its own once that copy is deleted.
- Connecting a Google account finishes only in a browser logged in as the memory user who started it: a logged-out browser is asked to log in and reload, another user's authorization link is refused, and a Google account already connected to another memory user is refused instead of having its tokens replaced.
- On https, the login cookies the web app sets carry the Secure flag, so the browser never sends them over plain http.
- Web UI: a slow earlier search, month or Tasks/Polls filter load no longer replaces newer results; the calendar loads a month once, works with browser storage blocked, and shows a tappable event count per day on phones; People says when a person's teams or tidbits failed to load, with Retry, instead of showing none.
- Calendar syncs check once whether each recurring event's rule yields any occurrence at all; `organizer_upcoming` skips the events whose rule no date satisfies (e.g. 30 February), which took seconds each to expand. The expansion runs off the MCP server's event loop, so a slow event no longer holds up other requests.
- A subscription's `max_per_hour` sets its hourly delivery limit, above `AGENT_MAX_RUNS_PER_HOUR` as well as below, up to `AGENT_MAX_DELIVERIES_PER_HOUR`: it was held to that setting, so deliveries over 60 an hour were throttled whatever it said. Runs an agent handler starts are still held to that agent's own hourly limit, which `AGENT_MAX_RUNS_PER_HOUR` caps. Rate refusals name the limit that fired (`max_per_hour`, `max_runs_per_hour`, or `AGENT_MAX_RUNS_PER_HOUR` as default or ceiling). Filter and raw subscriptions are returned with `limits`, their own `max_per_hour` and `backlog_max` in effect (null: no limit).
- Discord messages holding only an image or file are no longer empty to agents (#121): their text names each attachment (`[image attachment: cat.png]`), `discord_*` results, `core_fetch`/search metadata and subscription deliveries list the attachments, image-only messages are indexed with their images embedded, and `core_fetch_file` serves a message's stored images.

- Transcript accounts can stop turning meeting action items into tasks (notes and summaries are still written), or make tasks only for action items assigned to chosen people, e.g. just you: set them in the account's edit form on the Sources page, or with `create_tasks` and `task_person_ids` on `/transcript-accounts`. An assignee matches a chosen person by full display name, alias or email (ignoring case), never by a bare first name: add the short name you go by in meetings ("Dan") as an alias on your person. A name that is also another attendee's full name, first name or alias makes no task, attendees counting the meeting's linked people, its attendee list and its speaker labels, and so does a name two chosen people share. The task is assigned to the chosen person it matched, whoever else in the database goes by that name. With people chosen, unassigned action items make no task. Re-extracting a meeting removes tasks nobody has started that the account no longer wants. Existing accounts keep making tasks for every action item, and meetings sent without an account are unchanged.
- Outgoing requests to user-supplied servers also refuse loopback, link-local and metadata addresses reached through 6to4 or Teredo IPv6 addresses, every local-use NAT64 address (`64:ff9b:1::/48`), plus Azure's WireServer (168.63.129.16), `192.0.0.0/24` (Oracle Cloud metadata) and deprecated IPv6 site-local `fec0::/10`.
- CalDAV syncs send every request, including the calendar and event URLs the server names and any redirect it sends, only to public addresses checked when connecting (30 s timeout, 50 MB per response). A failed CalDAV sync shows one of three fixed messages (credentials refused, server unreachable, response unusable) instead of the server's error text.
- A calendar event shared by several invitees is stored in each invitee's calendar account, and two users submitting the same meeting transcript each keep their own meeting. Before, the second sync of the event or transcript failed and stored nothing.
- Only a Slack workspace's owner (the first user to connect it, whose default key also pays for its LLM work) or an admin can change the workspace's settings (collection, project, sensitivity, sync and sweep intervals, LLM key) or its channels' (collection, project, sensitivity). Other connected users get 403 and see these settings read-only on the Sources page. They are also no longer shown other users' DMs and private channels: the channel listing (`GET /slack/workspaces/{id}/channels`, MCP `slack_list_channels`) and the workspace's channel count show them only the workspace's public channels. Before, any connected user could make another user's DMs public or move them into their own project and read them. Admins can now list and change a workspace's channels without a credential of their own.
- Pushed Slack edits, deletes and reactions are checked with Slack too: an edited message is read back with the pushing app's own token and stored as Slack has it, a delete applies only when the token of the app that stored the message (or the pusher's, when one user owns both) can read the channel and Slack no longer has the message (or only its tombstone), so another user's app that can't see a message, e.g. past a free plan's history limit, can't delete it, and a reaction event refreshes the message's reactions from Slack's own list. Before, the user whose app stored a message first could make it say anything, delete it or add reactions to it by signing pushes with their own app, and no other user's app could repair it; now any connected app's poll or push stores what Slack holds. This costs one `conversations.history` (or `conversations.replies`) call per pushed edit, delete or reaction; while Slack rate-limits or fails it, the event is retried.
- Pushed Slack channel events (created, renamed, archived, unarchived) are read with the pushing app's own token (`conversations.info`) and the channel's name, type and archived state stored as Slack returns them; an event for a channel that token can't read is ignored. Before, a user could push `channel_created` for another workspace's channel id from their own workspace, which then skipped that channel's messages for good, or archive a co-user's channel so it stopped syncing until the next channel listing. Channel rows already squatted this way stay until an admin deletes them.
- Every API request counts against the per-client rate limit `API_RATE_LIMIT_DEFAULT` (100/minute), including routes on included routers, the OAuth endpoints and the login page. Before, only `/files` and `/reports` were limited. `/mcp` has its own budget, `API_RATE_LIMIT_MCP` (1200/minute), and signed webhook pushes from GitHub, Slack, Discord and transcript services share `API_RATE_LIMIT_WEBHOOKS` (1000/minute); `/health` is never limited. Anonymous OAuth client registration (`/register`) is also held to `API_RATE_LIMIT_OAUTH_REGISTER` (20/hour) and authorization requests (`/authorize`) to `API_RATE_LIMIT_OAUTH_AUTHORIZE` (10/minute). Counters are kept in the api process, so they hold while Redis is down. A registration without `client_name` is accepted instead of failing with a 500. The daily maintenance run (04:15) deletes expired sessions, authorization requests that expired before anyone logged in, and OAuth clients that never completed a login `OAUTH_UNUSED_CLIENT_DAYS` (30) days after registering.
- Recurring calendar events with `FREQ=SECONDLY` or `FREQ=MINUTELY` are skipped like other unsupported rules (calendars don't send them; Google refuses them): one such event from a synced calendar cost `organizer_upcoming` minutes of CPU and a calendar sync over 20 seconds. Expanding one event's rule also stops after a quarter second, keeping the occurrences found so far, so a sparse rule on a very old series (e.g. 29 February, daily, since year 1) can't hold up the query.
### Required env vars
None

### Manual steps
None

### Other changes for operators
- `AGENT_MAX_RUNS_PER_HOUR` is the ceiling on runs an agent definition or prebuilt agent starts an hour, and only the default for a subscription's `max_per_hour`.
- New optional `AGENT_MAX_DELIVERIES_PER_HOUR` (default 3600) caps the deliveries one subscription makes an hour; `subscription_upsert` refuses a `max_per_hour` above it, and a subscription saved with more is held to it.
- New beat task `notify-finished-runs` (every 30s, `scheduler` queue) sends `agent_run(notify=true)` notifications.
- A repo synced before this release while GitHub refused queries may have had its `last_sync_at` moved past items it never stored. After granting the missing permissions, run Sync on the repo with `force_full=true` (`POST /github/accounts/{account_id}/repos/{repo_id}/sync?force_full=true`), or clear `last_sync_at` for it. Later incidents catch up by themselves.
- Recurring calendar events synced before this release are checked on their next scheduled sync if it still returns them; until then they expand as before. `POST /calendar-accounts/{id}/sync` checks an account's events now and is safe to repeat.

### Migrations
- `20261220_github_incomplete_since` adds a nullable `github_repos.incomplete_since` column and a `github_repos.sync_incomplete` boolean defaulting to false (instant).
- `20261221_agent_run_notify_seen` adds `agent_runs.notify_on_finish`, `notified_at` and `seen_at` with two partial indexes, and backfills `seen_at = finished_at` for ended runs started through MCP or by hand that aren't sub-runs (an `UPDATE` scanning all of `agent_runs`: slow on a large table, and the indexes are built without `CONCURRENTLY`).
- `20261222_item_inserted_idx` builds two indexes on `source_item`, `(inserted_at, id)` and `(type, inserted_at, id)`, with `CREATE INDEX CONCURRENTLY`: writes continue while they build, but the upgrade's time grows with the number of stored items. If it is interrupted, drop any invalid `source_inserted_at_idx` or `source_type_inserted_at_idx` before re-running it: Postgres keeps a half-built concurrent index, and `IF NOT EXISTS` would skip it.
- `20261223_transcript_task_options` adds `transcript_accounts.create_tasks` (default true) and a `transcript_account_task_people` table (instant).
- `20261223_gmail_declined_ids` adds `email_accounts.gmail_declined_ids`, a text array defaulting to empty (instant).
- `20261224_recurrence_expandable` adds a nullable `calendar_event.recurrence_expandable` boolean (instant).
- `20261223_meeting_api_ext_ids` renames the `external_id` of meetings submitted through `POST /meetings` (no transcript account, not a push, with a creator) to `api:<user id>:<id>`; one UPDATE over `meeting`, and one over `pending_jobs` renaming `params.external_id` of those submissions' meeting jobs so a retry finds its meeting. Meetings of deleted transcript accounts match the same rule and are renamed too. A submission still queued when the migration runs is stored under its bare id; let the `meetings` queue drain before updating to avoid that.

### Post-deploy commands
- Optional, re-runnable: Discord messages stored before this release that hold only an attachment stay unindexed until re-embedded. To catch up, run `UPDATE source_item SET embed_status='RAW' WHERE embed_status='SKIPPED' AND id IN (SELECT id FROM discord_message WHERE cardinality(images) > 0 OR attachments @> '[{}]');` and the hourly `process_raw_items` job embeds them (300 an hour). Re-processing records a change, so `on_edit` subscriptions may receive those messages once more.

## v0.5.2 (2026-11-08)

### Changes
- GitHub repos take a `content_mode`: `full` (the default, as before) or `metadata`. A metadata repo stores titles, state and activity (authors, branch names, labels, assignees, milestone, project fields, commit SHAs, timestamps, review and comment ids and review states) but not issue or PR text, comments, review text or code (diffs, file lists), and indexes nothing; subscriptions still see every change (`new`, `edited`, `commits`, `comments`, `review`, `state`, ...). Set it when adding a repo or change it on the repo row; a change applies to what later syncs store, and content stored earlier is left as it was. Metadata repos don't fetch PR diffs.
- Breaking for queue workers: `agent_work_claim` takes `queues`, a list of queue names or case-sensitive globs (e.g. `["messages", "slack-*"]`), in place of `queue`. It claims from the matching queue whose oldest pending, undeferred delivery is oldest, and picks up queues made while it waits. With `capabilities` also given it claims either runs or deliveries, runs first: deliveries wait while any run is claimable, so latency-sensitive queues want a queue-only worker. Workers passing `queue="x"` must pass `queues=["x"]`; an empty list is refused.
- New queue handlers can't use `*`, `?`, `[` or `]` in their queue name. An existing queue whose name has them is still claimed by escaping them glob-style, e.g. `queues=["jobs[[]1]"]` for `jobs[1]`.

### Required env vars
None

### Manual steps
None

### Other changes for operators
- Python requirements now need `fastapi>=0.140.0` and `sse-starlette>=3` (fresh installs already got these; images rebuild them on update).

### Migrations
- `20261219_github_content_mode`: adds `github_repos.content_mode` (default `full`) and `github_item.body_hash`. Fast: two nullable/defaulted columns.

### Post-deploy commands
None

## v0.5.1 (2026-11-08)

### Changes
- **Breaking for MCP clients:** `core_search`, `core_fetch`, `core_list_items` and `github_pr_diff` return third-party text in untrusted blocks (one per top-level field; lists and dicts as one JSON block, field `<name>.json`) under a per-call random tag named in a top-level `untrusted` preamble. Shapes: search → `{untrusted, results}`, fetch → `{untrusted, ...item}`, fetch of several ids → `{untrusted, items}`, list → `{untrusted, items, total, has_more}`, pr_diff → `{untrusted, diff}`. The web UI and the HTML-report client (`mcp-client.js`) unwrap them.
- HTML reports' `MCP.call`/`MCP.batch` post to `/mcp` again (they posted to `/mcp/<tool>`, which 404s).
- **Breaking for MCP clients:** channel, server and chat names come back in untrusted blocks with a top-level `untrusted` preamble: `discord_list_channels`, `slack_list_channels`, `beeper_list_chats`, the `channel` block of `discord_channel_history`, and the names in `discord_send_message`'s not-found suggestions. `discord_list_channels` no longer returns `server_name` unless asked.
- `discord_channel_history` returns only the messages the caller can read, as search does: for non-admins, a server or channel with no project now has an empty history.
- Discord accounts link to People only by numeric user id (ingest and `people_upsert`'s `contact_info.discord`, which accepts ids as strings or numbers); usernames no longer link, since a rename can hand one to another account. Links already made by username are kept: admins should check any that look wrong.
- Fast typing or a paste into a field with a picker no longer hits React's update-depth limit (the shared Combobox no longer resyncs on every new options array). Action notices pause their timer while hovered or focused and give focus back when they close; a dialog keeps focus inside after a click on blank space; a disabled Create says when the name is taken.
- `slack_get_channel_history` and `discord_channel_history` wrap message text and authors the same way as the thread tools and return the tag as `untrusted`. `discord_channel_history` messages take the thread tools' shape: `id` is the item id, the Discord id is `message_id`, `content` is `text`; `discord_thread` shows authors' display names.
- Untrusted text in agent run inputs, raw-webhook claims and the Slack/Discord thread MCP tools is wrapped in a tag named afresh for each render (`untrusted_item_<random hex>`), named in a one-line preamble, so a fake closing tag can't end a block; names anyone can set (authors, email From/To and Message-ID, GitHub check names, attendees) are wrapped too, and an oversized input clips the item text inside its block instead of cutting the template. The thread tools return the tag as `untrusted`.
- Discord `channel_ids` filters cover the channel's threads; `exclude_own` also skips the owner's Person-linked Discord accounts (on by default for agent handlers, as for Slack) and, like our own bots' posts, matches by author id rather than display name.
- A Slack message is stored even when its workspace's user list can't be fetched (mentions stay unresolved); queue backlogs leave out disabled subscriptions; a re-claimed run whose items were all posted says so.
- Subscriptions page: server errors use the form's labels, each field shows its own problem (cron once you leave it), the dry-run verdict is announced from a standing status region, the item picker opens upward without moving the page, row buttons name their subscription, and a success notice floats in the corner and clears itself (`useActionNotice({floating: true})`).
- Discord pushes can no longer pre-claim, retype or rewrite someone else's channel. A push that would create a server channel or thread is stored only once the bot's token reads it in the pushed server (`GET /channels/{id}`, one 5-second try; `unconfirmed_channel` otherwise), and then as the token lists it. The gateway or a REST listing (server discovery, thread discovery) moves a channel stored under another server to the one they see it in, with default access settings, its content re-resolved and its parent cleared. A push never retypes a stored channel and re-parents only a thread of a server its bot owns. A push its token confirms records the bot as in that server. When a token takes over a server first seen in a push, or moves a channel into its server, the messages that bots whose owner may not push into it delivered there are kept but disowned (no delivering bot, no creator). A bot's own messages in a server stay editable only while its owner may push into it. Moving a Discord channel to another server now triggers the access-control reconciliation of its messages.
- Discord webhook deliveries of the wrong shape (ids that aren't snowflakes, nested objects that aren't objects, names, types or timestamps of the wrong kind) or holding what Postgres can't (NUL, lone surrogates, NaN, Infinity) get a 400 instead of a 500, and the worker refuses the wrongly shaped ones (`malformed`). For a channel the pusher may not push into, or one it names through a stored parent or with no server, a delivery's outcome reads as for a channel, parent and server never stored; its container is always the channel id pushed; and raw subscriptions and the webhook log treat a stored channel as unknown unless the bot's owner holds it as a DM or may push into its server, so neither a push nor its raw subscriptions can tell whether another owner's channel exists or is collected. A push the worker refuses (`guild_mismatch`, `no_server`, `not_owner`, `not_member`, `unconfirmed_dm`, `unconfirmed_channel`, `malformed`) is recorded `rejected` rather than `stored`; one whose task crashes still reads `stored`. Raw events of every source now store NaN and Infinity in payloads as null and drop lone surrogates from their type, delivery id and container instead of failing the request.
- Discord message tasks queued before this upgrade fail once (their `server_id` argument is gone; a message now takes its server from its channel when stored); the daily 2-day catch-up re-reads those messages.
- Subscriptions page: a dry run ("Test on an item") tries the unsaved form on an item and names what fails; `subscription_test` takes an unsaved source and handler. Server errors are plain sentences on the field at fault (cron errors name the bad field), the form checks cron ranges, regexes, batch fields, a filter criterion and past timers before saving, row actions keep focus and announce their result, and a Timer source filter and Clear filters were added.
- Re-enabling a filter subscription sends its waiting deliveries at once; a dry run reports a regex that took too long; trip reasons are plain sentences.
- A task called from inside another keeps its cause, so Discord pushed messages, edits, deletes and reactions now link their items to the webhook delivery (raw subscriptions get their item ids, the Webhook log lists them).
- Raw deliveries routed together are claimed in event order.
- A signed GitHub delivery whose fields have the wrong shape is a 400; a Slack one is recorded `rejected` (Slack retries non-200s). Slack events with no channel (team_join, user_change) are routed as `no_item_type`; pins and file shares are judged by their `channel_id`.
- A raw subscription with an agent or notify handler is refused for the combination before any key check.
- Webhook log: payloads of deliveries that stored nothing open per row, withheld where the viewer's credential couldn't read where they came from (the same rule as raw subscriptions, which now let an account's owner read its own unfiled repo or DM); outcome and type filters; repo and channel names; why a stored delivery lists no item. An admin's read-only key sees only its own accounts there.
- A schedule handoff the agents worker never picks up fails, and a failed timer tells its owner, within about 2.5h instead of turning "late" after 6h; it never pauses the subscription. A cron tick a cap stops is still skipped, but the owner is told once per throttling episode, and an agent timer a cap stops tells its owner it failed. A tripped cron clears its next run time.
- Only the owner holding a Discord server may push into it: a push from a bot that is in the server (a token lists it) but whose owner doesn't hold it is refused (`held_by_other`, recorded `rejected`) for every event (creates, edits, partial edits, deletes, bulk deletes, reactions, threads) with no REST call, so a co-member can't forge authors, text or edit times, take ownership of a real message, or hold back a real edit. The holder's pushes and the gateway are unchanged. Messages disowned when a token takes over a server, moves a channel into its server or claims a DM are checked with that bot's token, 100 per task (new task `verify_discord_messages`, discord queue): deleted if Discord says the message doesn't exist (code 10008), rewritten field by field as Discord reads it (text, author, times, reply, thread, type, pin, images, embeds, attachments; reactions cleared) if it differs, left alone if unreachable (including an unknown channel); rate limits, server errors and dropped connections retry with backoff, and a bot without API access logs that the check was skipped. Thread discovery keeps threads whose object names no guild (they are in their parent's server).
- A Discord message's images are downloaded only when the message is stored or its edit applies, and every streamed download (Discord, Slack, web pages) is written to a temporary file moved into place when complete, so a failed download never removes a file already there: a push naming another owner's message id with a junk image URL could delete that message's image. A pushed Discord message naming no server, in a stored server channel, is judged by that channel's server without asking the token; only unknown channel ids cost a REST call. A stored Discord message is only ever changed by an edit naming its own channel, checked again on the locked row after its images download, so a push racing the gateway with a slow image can no longer overwrite another owner's message; streamed downloads also stop after `max_seconds` in all (default 300), not only per read. The disowned-message check asks once per channel its token can't reach, skips (and logs) on a rejected token rather than retrying, keeps what it read before a rate limit or server error and retries the rest, and alerts only on its last attempt.

### Required env vars
None

### Manual steps
None

### Other changes for operators
- Streamed downloads (Discord, Slack, web pages) have a total time cap scaled to their size limit (at least 300s, enough for the limit at 1 Mbit/s), on top of the per-read timeout.
- New Celery task `verify_discord_messages` on the existing discord queue.

### Migrations
- `20261218_disown_dm_messages`: DM messages delivered by a bot not of the DM's owner lose that bot (`bot_id` NULL). Data only, one UPDATE over DM messages.

### Post-deploy commands
None

## v0.5.0 (2026-11-08)

The event pipeline: everything that reacts to new data is now a subscription (events → subscriptions → handlers). Bullets are in the order the work landed, so later ones supersede earlier ones: the `agent_trigger_*` and `scheduler_*` tools that earlier bullets name are replaced by `subscription_*`, and `agent_triggers` / `agent_trigger_matches` / `trigger_id` are renamed `subscriptions` / `deliveries` / `subscription_id` by `20261217_subscriptions_contract`.

### Changes

- A DM (or group DM) to one of your Discord bots is collected by default when its sender shares a server with the bot (in the gateway's member list for one of its servers, or having posted in one), over the gateway and webhook delivery alike; other DMs stay untracked (a `not_collected` stub, no channel row). Each bot card lists its DMs under **Direct messages** (new endpoint `GET /discord/bots/{bot_id}/dms`), named after the other person, where any can be set to Skip or Collect. The daily catch-up reads a DM's last `DISCORD_CATCH_UP_DAYS`; its older history is crawled only once it is set to Collect. A DM's messages belong to the owner of its bot, and only when that owner's bot delivered them: they're visible to that owner and admins, plus anyone a project given to the DM grants. DM rows already stored without a setting start collecting; DM messages stored before this release get their owner at the next daily access-control sweep. Adding a removed bot again gives it back its owner's DMs, and a new bot lists its servers straight away (new task `discover_bot` on the backfill queue). `discord_list_channels(include_dms=false)` now leaves out group DMs too.
- Discord pushes (webhook deliveries) are untrusted and held to their owner's rows. A push adds or changes messages only in one of the owner's DMs or in a channel of a server one of their bots holds or a token lists it in, judged by the stored channel's server, never the pushed `guild_id`; one naming another server is dropped (`guild_mismatch`), as are pushes into others' DMs (`not_owner`) and servers (`not_member`). A push creates a channel or thread only under such a server (`no_server` otherwise), never moves a stored channel to another server or under another server's channel, and never changes another owner's channel, so a thread can no longer be re-parented under a collect-off channel to delete its messages. A server first seen in a push is closed to other owners' pushes until a token lists it. A DM is stored only from the gateway or once the bot's token reads the channel as its DM (`GET /channels/{id}`, one 5-second try); a pushed thread event never makes one, and a DM stays with the owner holding it even when another user registers the same application. Stored messages take their server from their channel. Events the webhook endpoint queued count as pushes even for a bot since switched to the gateway, and events without an origin are taken for pushes. A channel the gateway or a REST listing shows to be a server's channel, or a DM the gateway or the bot's token shows stored as a server's channel, becomes that, with default access settings.
- Discord MCP tools name the server: `discord_list_channels` returns `server_name` by default, and `discord_channel_history` gives the channel's `server_id` and `server_name`.
- GitHub webhook install no longer overwrites a GitHub App's own webhook. For App and token accounts alike it adds or updates one webhook on each org (finding one already pointing at Memory, such as one added by hand), and one per repo only for personal-account repos. An org whose hook isn't permitted gets "Missing permission" on its repos, never per-repo hooks that would duplicate an org hook; other webhooks are left alone. The dialog says this up front, sums an org webhook up in one row, and names the exact permission an App or token is missing.
- Sources rows show the push channel at a glance: "Webhook active", "No webhook", "Webhook error", "No recent events" or "Missing events", on GitHub accounts, Slack workspaces, Discord webhook bots and transcript accounts. GitHub accounts are spaced apart as separate cards.
- GitHub PR reviews record the commit they were left on (`commit_oid`), and PRs record `base_sha`, `head_ref` and `head_repo` (owner/name of the repo the head branch lives in; empty once a fork is deleted). Ingest trigger templates for `github_pr` can name these and `reviews`. Older reviews get their commit at the PR's next sync.
- New MCP tool `github_pr_diff(id)` fetches a PR's full current diff from GitHub with the repo account's credentials, for callers that can read the PR (the `diff_summary` template field stays clipped).
- Agent triggers have a name (unique per owner, `trigger-<id>` by default) and an optional per-trigger `max_per_hour`: `agent_trigger_upsert` takes both. Runs record their trigger. A trigger moved to another definition now dedupes and rate-limits per trigger, across the move, not per definition.
- Ingest triggers are routed from a new `events` table: ingest writes an item's event in the item's own transaction, at store time (before embedding), and a router matches pending events every 5 seconds. A lost Celery message can no longer lose a match. An event whose routing keeps failing is given up after `EVENT_MAX_ATTEMPTS` (default 5) tries, and a trigger whose config no longer validates is skipped with a warning. Deleting an item records a `removed` event. New setting `EVENT_RETENTION_DAYS` (default 30): routed events older than that are deleted by the nightly run purge. Trigger-matching messages (`match_ingest_triggers`) still in the broker at deploy are dropped, a one-time loss of those matches.
- An ingest trigger's match is its delivery: the router writes it, pending, in the transaction that routes the event, and every delivery ends `done` (linked to its run), `failed` (after `EVENT_MAX_ATTEMPTS` failed fires), `expired` or `skipped:<reason>` (`inaccessible`, `disabled`, `shed:rate`, `shed:budget`, `shed:backlog`). A new `sweep-trigger-deliveries` beat task on the `scheduler` queue (every 30s) resends deliveries still pending 2 minutes after they were sent (then 4, 8, ... minutes after each resend, so a backlogged `agents` queue doesn't fill with copies), so a crash after routing or a lost `fire_trigger` message delays a run but can't lose it; deliveries pending 7 days end `expired` (or `skipped:disabled`).
- Batch triggers collect their matches in Postgres instead of Redis, and a paused trigger keeps them: they go out once it's enabled again. Pausing, editing or deleting a trigger no longer clears a buffer.
- Caps throttle instead of disabling a trigger: over `max_per_hour`, the definition's `max_runs_per_hour` or a daily cost pool, a delivery waits an hour and is shed (`shed:rate` / `shed:budget`) if still over; the owner is notified once per episode, which ends after two hours with nothing held back. `agent_trigger_upsert` takes `backlog_max`: beyond that many waiting deliveries the oldest are shed. Only broken credentials or an invalid definition or config still disable (trip) a trigger, and its deliveries wait for it. `agent_trigger_upsert(enabled=true, resume=true)` re-enables a tripped trigger as if it had never tripped: the router replays to it alone the events routed while it was tripped (those within `EVENT_RETENTION_DAYS`) and its `active_since` stays; without `resume` it starts from now, as before. `resume` without `enabled=true` on a disabled trigger is refused. Re-enabling also clears a throttling episode.
- An item stored before a trigger was set up now matches it once its sync reports news (`commits`, `comments`, `review`, `checks_complete` or `state`; with `on_changes`, one of those it names), so an open PR fires on its next push; its run's changes say `new` plus that news. Labels, assignees, draft, checks and plain edits aren't news. Items older than 30 days still never match as new, events written before the trigger existed never match it, and backfills match nothing: Discord history below what is stored and Beeper's first read and backward crawl mark their items `backfill` if sent more than an hour before the chat was first seen, so a new conversation's first message still fires (the Discord catch-up window and live events don't).
- Ingest trigger `on_changes` can name `labels`, `assignees`, `draft` and `checks_complete` (GitHub) and `title`, `due` and `priority` (meeting tasks, which also report `assignees` and `state`), and an `on_edit` trigger re-fires on a GitHub item whose labels or assignees changed. Each item an `on_edit` trigger matched on GitHub before this release may re-fire once, at its next sync, since its stored version no longer compares equal.
- New `scheduler-worker` service (one process) consumes the `scheduler` queue, which the main `worker` no longer does, so long ingest tasks can't delay event routing, scheduled tasks or due-source syncs. Operators with their own compose file must add it, or add `scheduler` back to a worker's `QUEUES`.
- GitHub syncs report three more change tags: `labels`, `assignees` and `draft` (PRs now record `draft`, which `github_pr` trigger templates can name). They don't re-embed an item by themselves. A derived `checks_complete` tag fires when GitHub's check rollup for the head commit finishes (SUCCESS, FAILURE or ERROR, so checks not created yet still count as pending) with a commit and result not recorded before: a new push fires it again once its checks finish, and so does a re-run that turns FAILURE into SUCCESS on the same commit; a re-run with the same result doesn't. A PR with no checks never fires it. A draft flag seen for the first time is not a change.
- New read-only MCP tools `slack_thread(item_id)` and `discord_thread(item_id)` return a stored message's thread: its parent and latest replies (`CHAT_THREAD_REPLIES` by default, at most 100), only messages the caller can read, text wrapped as untrusted. They need only the `slack` / `discord` scope, so team access is enough. A worker handling a chat trigger delivery calls them with the delivered item's `id`.
- Reprocessing a meeting keeps the task ids of action items it extracts again (matched by meeting and text, ignoring case, spacing and a final full stop). Pending tasks follow the extraction: changed ones get an `item.changed` event tagged `title`, `due`, `priority` and/or `assignees` (edits made while pending are overwritten), and those whose item is gone are deleted; a failed reprocess deletes only the tasks it created. Meeting trigger input lists each action item's `task_id`.
- Changing a task's status with `organizer_update_task` writes an `item.changed` event tagged `state`, so triggers can follow tasks.
- Every signed webhook delivery (GitHub, Slack events, Discord webhook mode, transcript pushes) is recorded as a raw event before it is acknowledged: source, type (e.g. `pull_request.synchronize`), a delivery id deduped per source account (Slack's `event_id`, else the body's SHA-256, which also stops replays of a signed GitHub body under a new `X-GitHub-Delivery`), the account's owner and an outcome (`stored`, `not_collected`, `no_item_type`, `rejected`). Only `no_item_type` deliveries keep their payload (capped at 256 KB). Badly signed, unknown-account and unparseable deliveries write nothing and log a warning with ids only; a delivery whose queueing fails is taken again on retry. Items ingested by a Slack, Discord or generic-transcript delivery link it through `caused_by`. New endpoint `GET /webhook-events/{source}/{account_id}` lists an owner's recent deliveries with the items they led to (payloads only with `?payload=true`). New setting `EVENT_RAW_PAYLOAD_DAYS` (default 7): older raw payloads are cleared by the nightly run purge; the rows follow `EVENT_RETENTION_DAYS`.
- Slack and Discord pushes about a channel whose collection is off are no longer stored or queued: they leave only a payload-less `not_collected` raw event. Deletes still pass, so Memory drops its copy. A channel not stored yet follows its workspace's or server's setting, so channels left at a workspace or server default of off stop receiving pushed messages; turn collection on for them to keep them. A server first seen through a Discord webhook bot is still collected. Discord threads now inherit their parent channel's setting before the server's, in push and backfill alike. A Slack app still goes live on its first pushed message, collected or not.
- Queue handlers: a trigger whose handler is `{"queue": "<name>"}` leaves its deliveries pending for its owner's workers, which claim them with `agent_work_claim(queue="<name>")` (needs the `read` scope) and report on the returned run with `agent_work_submit`. A claim renders the trigger's template from the items as they are now. A delivery whose item the trigger can no longer read ends `skipped:inaccessible`; one only the claiming key can't read stays pending for a worker whose key can. A lapsed lease puts the run back on its queue (while its trigger is live), rendered afresh at the next claim; claims without `queue` never take queue runs. `order_by` (`item`, or `thread`: the Slack thread, the Discord thread or the top-level message that starts it, the Beeper chat) holds a queue delivery while an earlier one with the same key is unanswered. `coalesce_mode: latest` replaces a pending delivery with a newer one for its item (or order key), for every handler: the newer one keeps the oldest one's place in line and deferral, merges the item's change tags (a first sight stays `new`), and the run ref's `coalesced` lists the items folded in. Queue triggers keep the `max_per_hour` deferral and shedding (applied at claim) and `backlog_max`; revoked credentials or an invalid config trip them, and a delivery whose claim keeps failing ends `failed` after `EVENT_MAX_ATTEMPTS`. New tool `agent_work_queues` lists your queues with their pending count and oldest age. There's no MCP tool to create a queue trigger yet.
- Behaviour change: runs an ingest trigger placed on workers are rendered at claim from the items as they are now. A worker credential without `read` no longer gets them (it passes them over), nor runs with an item its key can't read, which stay pending for another worker. Items the trigger can no longer read are left out and their deliveries end `skipped:inaccessible`; a run with none left, or whose trigger is gone or no longer valid, fails with its input cleared.
- Ingest trigger filters: `authors_any` (only these authors, by Discord/Slack user id, Beeper sender id or GitHub login; display names never match), `known_people_only` (authors linked to a Person that is linked to a user or that the owner or an admin made, not the ones ingest makes for everyone), `changed_by_any` / `exclude_changed_by` (who made a change: GitHub syncs now record who wrote new comments and reviews and who authored the head commit; a new item or edit is its author's), `threads_im_in` (Discord/Slack threads the owner or their bots posted in), `dms_to_me` (direct messages that reached the owner's Discord bots or Slack apps) and `exclude_own` (the owner's own Slack messages; on by default, off by default for queue handlers). Email never passes `authors_any` or `known_people_only`, as its From header can be forged. Our bots' posts still never match.
- `agent_work_submit` takes `posted` (`[{source, external_id}]`, at most 50; slack `channel:ts`, discord message id, email Message-ID, beeper message id): once ingested, those items never match the trigger that started the run, checked when they're routed and again when their delivery fires or a worker claims it, lapsed re-claims included (`skipped:posted`).
- A queue delivery whose claim fails waits a minute before its next attempt, instead of using up its attempts on consecutive polls.
- `email_send` returns the sent message's RFC Message-ID header (Gmail: the one Gmail sent it with), not Gmail's API id; SMTP sends now carry a Message-ID.
- Slack workspace syncs link Slack users to People by email address only, no longer by matching names.

- Schedules are cron subscriptions and timers on the event pipeline; scheduled tasks are gone. A recurring schedule is an agent trigger of kind `cron` whose due times write `tick` events; a one-off is a `timer` event with a fire time and its own handler. A new `run-clock` beat task on the `scheduler` queue (every 10s) writes due ticks and hands due ticks and timers to their handler's task, resending after 2, 4, 8, ... minutes until it ends them: a `notify` handler's message (Discord, Slack or email) is sent by `send_time_notify` on the `scheduler` queue and records `sent` or `failed` (marked sending first, so a crash mid-send ends it failed rather than sending twice); an agent handler's run is started on the `agents` queue and records `done` with its `run_id`. After an outage a subscription writes one tick for its latest due time, with the ones it skipped counted in `missed`, and a tick still waiting (not yet handed off) when the next is written is `skipped:coalesced` into it. Ticks and agent timers more than 6 hours late are `skipped:late`; a late reminder is still sent, prefixed with when it was due, and results record `late_sec`. A run's `trigger_ref` gives `time`, `since` (the last delivered tick) and `missed`. A schedule whose credentials, definition or cron stop working, or whose message fails 3 times in a row, disables itself and notifies its owner once; a failed timer notifies its owner. Recurring schedules count toward `AGENT_MAX_TRIGGERS_PER_USER`, and their interval (`MIN_CRON_INTERVAL_MINUTES`) is checked over a full week of occurrences when set.
- The `scheduler_*` MCP tools now manage these: `scheduler_upsert` creates a recurring schedule (`cron_expression`) or a timer (`scheduled_time`) that either sends a message (`notification_channel`, `notification_target`, `message`, `topic`) or runs a definition (`definition_id`, `input`; needs the `agents` scope), and updates a recurring one by `schedule_id`; `scheduler_list_all` lists schedules and waiting timers (optionally for one definition; `include_fired=true` adds the latest fired timers with their results); `scheduler_delete` deletes a schedule or cancels a timer; `scheduler_history` lists a schedule's ticks and results. `scheduler_cancel`, `scheduler_executions` and `agent_schedule`/`agent_schedules`/`agent_unschedule` are removed (pause with `scheduler_upsert(schedule_id=…, enabled=false)`). An API key may change, pause or delete only the schedules it created; one bound to a key that no longer works can be paused by any of its owner's credentials. `meta_notify_user(scheduled_time=…)` sets a timer, whose id it returns, and is refused inside agent runs. Deleting an agent definition cancels its waiting timers. The Scheduled tasks page is removed; an agent's page still lists, adds, pauses and deletes its schedules, and `/mcp-client.js`'s `MCP.scheduler` follows the new tools. `agent_trigger_*` tools no longer see cron subscriptions.
- Subscriptions have one set of MCP tools, replacing `agent_trigger_upsert`, `agent_triggers`, `agent_trigger_delete` and every `scheduler_*` tool: `subscription_upsert(handler, source, ...)` creates or updates one (by `subscription_id`, or again by `name`, which updates in place without re-enabling a tripped one or moving its `active_since`), `subscription_list_all` lists them and waiting timers (by `definition_id` or `queue`; `include_fired`), `subscription_fetch` gives one with its history (deliveries with their status and run, pending ones without; a cron's ticks with their results; and its runs; optionally for one `item_id`), and `subscription_delete` deletes one or cancels a timer. A handler is `{"agent": <definition id or slug>}`, `{"queue": name}` or `{"notify": {channel, target, message, subject}}`; a source is `{"filter": ...}` (the old trigger config), `{"cron": ..., "input"}` or `{"at": ..., "input"}` (a timer). Queue handlers need `agents:work` (or `agents`) and `read`, agent handlers `agents` (plus `schedule:write` on a cron or timer), notify handlers `schedule:write`, which takes only `channel`, `target`, `message` and `subject`; queue handlers default to `order_by="item"`, and agent handlers refuse `order_by`. A partial `source` on update is merged into the stored one, a filter field by field. A filter subscription's handler can't change between agent and queue (its waiting deliveries would be stranded); a cron's can move between agent and notify. Filter subscriptions are listed and fetched only for `agents`, or `agents:work` with `read`; `schedule`-only keys see cron subscriptions and timers. Every edit, pause and delete is refused to an API key that didn't create the subscription. New subscriptions are named `subscription-<id>` by default. The tools are hidden from agent runs, reads included. The agent page and `/mcp-client.js` (`MCP.subscription`, replacing `MCP.scheduler`) use them.
- Agent handlers may name a prebuilt or system agent by slug (`{"agent": "fact-check"}`), on filter, cron and timer sources alike: the run uses the owner's preferred model for it. Runs of a prebuilt or system agent, from a subscription or `agent_run`, are capped per user and agent at `AGENT_MAX_RUNS_PER_HOUR` an hour.
- New Subscriptions page (AI section, and from Agents): every subscription with its source, handler, origin (set up in the app or by which API key), waiting deliveries and the oldest one's age, last delivery, and Tripped/Paused/Throttled state with the reason. A row expands to its waiting deliveries (oldest first, with any hold) apart from the ones that ended (status with reason, changes, and a link to the run with its status), or a schedule's ticks. Create and edit filter, cron and timer subscriptions handed to an agent, a queue or a message; pause, resume (a tripped filter subscription also "from when it tripped"), delete, and cancel waiting timers. It replaces the Triggers and Schedules sections on an agent's page, which now lists that agent's subscriptions and links here. `subscription_list_all` adds each subscription's `api_key_name` and `activity` (`pending`, `oldest_pending_at`, `last`), and `subscription_fetch` takes `pending` (true: only waiting deliveries, oldest first; false: only ended ones) and gives each delivery its `run_status`. An API key sees only its own name in `api_key_name`; a login session sees every key's. `subscription_upsert` takes `create_only` (refuse instead of updating a subscription already so named; the page sends it), `max_per_hour=0` restores the default and `backlog_max=0` removes the limit, and a notify handler's `subject: ""` clears it.
- New Webhook log page (Knowledge & content): pick one of your GitHub accounts, Slack apps, Discord bots or transcript accounts and see its webhook deliveries newest first, with outcome, the items each stored and the deliveries those were routed into (subscription, status, run). A delivery from an untracked channel shows only its time and outcome. New endpoint `GET /webhook-events` lists the accounts; `GET /webhook-events/{source}/{account_id}` adds `triggered` per delivery and blanks `type` and `container` of `not_collected` ones. Admins see every account's log; others their own, only items they can still read, and only their own subscriptions' deliveries. Deliveries record the event they were routed from (`event_id`), so only deliveries routed after this release show in the log.
- A `not_collected` raw event, from any source, no longer records the channel or repo it was about: an untracked channel stays unseen. A Slack or Discord delete for such a channel is now recorded as one of these stubs, and still removes Memory's copy. When a Discord thread turns out to sit under a channel that isn't collected, its server's bots' earlier raw events about it become such stubs too, as its messages are removed.
- `tools/ops/status.sh` has a `[queues]` section: one line per subscription queue with deliveries waiting, naming its owner and up to five subscription ids with their count (never the queue's name), the pending count and oldest age in seconds. A delivery shed at a cap logs a WARNING with the subscription id and count; a timed-out filter regex no longer logs the pattern.
- Raw-event subscriptions: `subscription_upsert(handler={"queue": ...}, source={"raw": {"source": "github"|"slack"|"discord"|"transcripts", "account_id", "types", "outcome"}})` hands webhook deliveries to a worker queue, including ones nothing stores (`no_item_type`, whose payload is kept 7 days). Only the owner of the account a delivery came in on receives it, re-checked at claim; deliveries for channels or repos that aren't collected (checked again at claim), and rejected ones, never reach a subscription. A claimed delivery's ref names the event, its source, type, outcome, account and container and the items it stored that the subscription can read: a stored delivery whose task writes item events (Slack and Discord messages, their deletes and Discord text edits, a transcript push that queued a new meeting) waits up to 2 minutes for them, and any other (a Slack `app_mention`, reactions, channel and thread updates) goes at once, and GitHub's are never linked (its refetches are debounced across deliveries), so for GitHub `item_ids` is empty. Its input is the payload inside `<untrusted_item>`, clipped to fit, or `payload withheld` unless both the subscription's key and the claiming key could read items filed in that channel, repo (or the issue's milestone) or account, and every item it stored; one only the claiming key can't read is left for another worker. Raw subscriptions take queue handlers only (the queue can't change), `max_per_hour` (default `AGENT_MAX_RUNS_PER_HOUR`) and `backlog_max` (default 1000): deliveries over either are shed at once as failed runs (`skipped:shed:rate` / `skipped:shed:backlog`), and the owner is told once per episode. Unclaimed ones end `expired` after 7 days, they count in `agent_work_queues`, and a raw subscription whose key stops working trips. Raw and filter deliveries on one queue are claimed oldest first. Slack `app_mention` deliveries are `stored` (their message is stored from its own event), and a GitHub `ping` for a repo that isn't collected is `not_collected`.
- Filter subscriptions take `on_remove`: an item the subscription was delivered is delivered again when it is deleted, as its id with the change `removed` (rendered `[item #<id>] removed`), to a credential that may still read; coalescing never folds a removal. Each item's newest delivery is kept for such subscriptions, so the removal finds it however long ago the item was delivered. Deliveries of an item no longer vanish when it's deleted: pending ones end `skipped:inaccessible`.
- New MCP tool `subscription_test(subscription_id, item_id)`: a dry run of a filter subscription on an item the caller can read, naming the first check it fails (a filter field, `own_authors`, `readable`, `exclude_own`, `threads_im_in`, `known_people_only`, `posted`, `credentials` or `config`). Trigger regexes now use the `regex` package's timeout instead of SIGALRM, so they can run on any thread (new dependency `regex`). Filter regexes are validated by that engine too, so a pattern only `re` accepts is refused.
- The Subscriptions page creates and edits webhook (raw) subscriptions: source, one of your accounts or all, event types and outcomes, handed to a queue; cards name the account, and a webhook subscription's history names each delivery's event with a link to the webhook log. A delivery handed to a run shows the run's status instead of `done`, and the webhook log and history link a run only once it started. A refused save shows its reason by the buttons. `GET /webhook-events` marks each account `own`. The Vite dev server's api proxy reads `VITE_API_PROXY_TARGET` (default `http://localhost:8000`).
- Subscriptions and deliveries are named so throughout: a run's `subscription_id` (in `agent_fetch`, `agent_list_runs` and `subscription_fetch`) replaces `trigger_id`, and so does its ref's key (`ref.subscription_id` in claimed queue deliveries, ticks and raw deliveries; refs of runs made before this keep `trigger_id`). `agent_list_all` gives each definition's `subscriptions` summary (was `triggers`), `agent_catalog` gives `subscription_source_types` (was `trigger_source_types`), and the default filter template says "New items matched this subscription". Default names left by earlier conversions (`trigger-<id>`, `schedule-<task id>`) become `subscription-<id>`. A run's `trigger_kind` and `trigger_ref` keep their names: they say what started any run (manual, mcp, agent, cron, timer, ingest, raw). The runs pages label timer and webhook-event runs.
- Initial imports no longer fire subscriptions: a polled Slack message last edited (else sent), an email received (by IMAP INTERNALDATE or Gmail `internalDate`, else its `Date`), or a new GitHub issue or PR last updated more than `EVENT_BACKFILL_AGE_DAYS` (2) before it's stored counts as backfill. Slack pushes are always live, and an edit today to an old message is live. After a poll outage longer than `EVENT_BACKFILL_AGE_DAYS`, the items the poll catches up don't fire subscriptions.

### Required env vars

None.

### Manual steps

- If the instance sets `AGENT_MAX_TRIGGERS_PER_USER`, rename it `AGENT_MAX_SUBSCRIPTIONS_PER_USER` before updating; otherwise it falls back to the default (20).

### Other changes for operators

- New optional `EVENT_BACKFILL_AGE_DAYS` (default 2): how much older than its store a Slack message, email or new GitHub item must be to count as backfill.
- The `scheduler` MCP server is now `subscription`; the `20261214_subscription_server` migration renames it in the hidden-servers Server setting.
- Removed settings: `SCHEDULED_CALL_RUN_INTERVAL`, `TASK_EXECUTION_RETENTION_DAYS`, `SCHEDULED_TASK_RETENTION_DAYS`, and `MAX_SCHEDULED_TASKS_PER_USER`, replaced by `MAX_TIMERS_PER_USER` (default 20: timers waiting to fire per user). Tick and timer rows follow `EVENT_RETENTION_DAYS` once delivered. The `run-scheduled-tasks`, `cleanup-old-task-executions` and `cleanup-old-done-oneoff-tasks` beat tasks are gone. Scheduled-task executions in flight at deploy (pending or running) are not run, a one-time loss; `send_notification` and `start_cron_run` messages still in the broker at deploy are dropped.
- New optional `CHAT_THREAD_REPLIES` (default 10): replies the thread tools return by default.
- Discord channels first stored as text channels are corrected to threads by the next thread event or listing.
- `AGENT_MAX_TRIGGERS_PER_USER` is renamed `AGENT_MAX_SUBSCRIPTIONS_PER_USER` (default 20); an instance that set the old name must rename it, or it falls back to the default. Celery tasks `fire_trigger` and `sweep_trigger_deliveries` are renamed `fire_subscription` and `sweep_deliveries` (beat entry `sweep-deliveries`): messages of the old names still in the broker at deploy are dropped, and the delivery sweep resends what they carried. Workers and the api on the previous release fail against the renamed tables until they restart, so restart everything with the migration.

### Migrations

- `20261212_github_pr_draft`: adds `draft` and `checks_complete_key` to `github_item`. GitHub's rollup state wasn't stored before, so for PRs whose stored checks have all finished the migration derives it from the per-check results, so they don't report `checks_complete` on their next sync. Where the derived result differs from GitHub's rollup (more than 100 checks, checks not created yet, or a result the derivation maps differently), the PR reports `checks_complete` once at its next sync. One pass over `github_item`.
- `20261209_github_org_hooks`: clears the retired `app_hook` / `app_settings` repo webhook statuses. Fast.
- `20261209_github_pr_branches`: adds `base_sha`, `head_ref` and `head_repo` to `github_item`. Fast.
- `20261210_trigger_subscriptions`: gives agent triggers an owner, name, handler and shaping columns, links agent runs to their trigger and rebuilds the run dedupe indexes. Holds an exclusive lock on `agent_runs` while it backfills and indexes; fast on today's small tables.
- `20261211_events`: creates the `events` table. Fast.
- `20261212_trigger_deliveries`: adds delivery columns to `agent_trigger_matches`. Existing matches become `done`, except per-item matches from the last hour, which go back to pending so the sweep resends any whose fire was lost (run dedupe returns runs that already started). Batch matches waiting in Redis at deploy are dropped, a one-time loss: the Redis keys `agents:trigger:*:buf` can be deleted. Fast.
- `20261212_raw_events`: adds `source`, `type`, `account_id`, `container`, `delivery_id`, `owner_id`, `outcome`, `payload` and `caused_by` to `events`, with their indexes. Fast.
- `20261213_queue_handlers`: adds `agent_runs.queue`, and `order_key` and `coalesced` to `agent_trigger_matches`, with two partial indexes. Fast.
- `20261213_trigger_loop_filters`: adds nullable `events.actors` and `agent_runs.posted` JSONB columns. Fast.
- `20261213_time_events`: adds `next_at` to `agent_triggers` (kind `cron`, handler `notify` allowed) and tick/timer columns to `events`, then converts scheduled tasks: recurring ones become cron subscriptions named `schedule-<task id>` (paused ones stay paused; one set up through a since-deleted API key converts disabled and tripped), pending one-offs become timers. agent_run tasks whose definition is gone are left out. Converted schedules ignore `AGENT_MAX_TRIGGERS_PER_USER`, so a user with many may end up over it: they keep running, but enabling another trigger is refused until some are paused. Re-running converts only what it hasn't. It leaves `scheduled_tasks` and `task_executions` in place (excluded from autogenerate) until a later release drops them. Fast.
- `20261214_subscription_server`: renames `scheduler` to `subscription` in the stored `disabled_mcp_servers` Server setting, so a hidden scheduler server stays hidden (without it the stored list no longer decodes and every hidden server shows). Re-runnable. Fast.
- `20261215_delivery_events`: adds nullable `event_id` to `agent_trigger_matches`, and builds two indexes on it concurrently (on `event_id`, and on `trigger_id, created_at DESC`), so routing isn't blocked. If interrupted, drop any INVALID `agent_trigger_matches_*_idx` it left and run it again.
- `20261216_raw_subscriptions`: allows `agent_triggers.kind = 'raw'`, drops the foreign key from `agent_trigger_matches.item_id` to `source_item`, so a deleted item's deliveries are kept until the nightly purge, and builds `agent_runs_raw_event_idx` concurrently (on the raw event a raw subscription's run delivers). Re-runnable. If interrupted, drop the index if INVALID and run it again.
- `20261217_subscriptions_contract`: renames `agent_triggers` to `subscriptions`, `agent_trigger_matches` to `deliveries` and `trigger_id` to `subscription_id` on `agent_runs`, `deliveries` and `events`, with their indexes, constraints and id sequence (found by lookup; a name off the usual pattern is kept), and renames default names to `subscription-<id>`. The renames are metadata only but need a brief exclusive lock on each table; the migration gives up after 30s waiting for any one of them, holding the locks it already has meanwhile, so at worst it waits about 2 minutes, which is harmless under `tools/update.sh` (stop the workers and retry if it gives up). It then limits `deliveries.status` to the statuses the pipeline records: added NOT VALID, then validated after the renames commit, which scans `deliveries` without blocking writes. If validation fails, a delivery holds a status nothing writes. Don't roll back to the previous release, since the renames are already committed: find the rows with `SELECT status, count(*) FROM deliveries GROUP BY 1`, fix them, and run the migration again (it is re-runnable).

### Post-deploy commands

None.

## v0.4.11 (2026-11-07)

### Changes

- GitHub webhooks install themselves: **Sources → GitHub → Webhook → Install on all repos** generates a secret if there is none and creates (or PATCHes) one repo hook per active tracked repo, found by its payload URL; other hooks are untouched. The dialog lists each repo's result (installed, missing permission, rejected by GitHub, failed); the manual instructions moved into a collapsed "Set it up by hand".
- The install re-runs on secret generate/rotate, when a repo is added or resumed under an account with a secret, and daily at 03:15 (`ensure-github-webhooks` beat entry) to repair drift. Removing the secret deletes Memory's repo hooks (best effort).
- GitHub App accounts: the installation token can't manage repo hooks, so Memory PATCHes the App's own webhook (`/app/hook/config`, URL + secret) with an app JWT from the stored private key. The App's active flag and subscribed events stay in the App's settings.
- New endpoint `POST /github/accounts/{id}/webhooks/install` (queues; returns `{"status": "queued", "task_id"}`). Repo responses carry `webhook_status` and `webhook_checked_at`.
- GitHub pull requests and issues are separate source types, `github_pr` and `github_issue` (comments and project cards stay `github_item`). Ingest trigger `source_types` can name either; trigger template fields `head_sha`, `checks`, `diff_summary` and `files` are offered for `github_pr` only.
- Every `pull_request` webhook action refetches the PR, so labels, assignees and milestone changes (labeled/unlabeled, assigned/unassigned, milestoned/demilestoned, review_requested, converted_to_draft, ...) are stored within the refetch debounce instead of at the next gap-fill poll.
- Push channels that stop delivering are flagged. When a gap-fill poll stores something push should have delivered (a GitHub change made after the repo's hook was last confirmed installed and more than 10 minutes old; a Slack message, in a channel push has delivered to before, more than 10 minutes old), the GitHub repo or Slack app gets `push_missing_since` (first miss) and `push_last_miss_at` (latest). The next webhook event for that repo, or Slack event for that app, clears `push_missing_since`. Check results don't count, since they don't change an item's `updated_at`.
- The owner is notified once per outage, on their usual notification channel. The message names the repo or app only.
- Sources UI: a "⚠ Missing events" badge on each affected GitHub repo row, its account row and each affected Slack app (on its workspace row). Its tooltip shows since/last miss and likely causes. GitHub accounts also get a **Reinstall webhooks** action. The GitHub/Slack tab shows the red "!" with "N missing push events". A muted "No recent push events" hint shows when push is set up but nothing has arrived within `PUSH_STALE_HOURS`.
- API: GitHub repo responses add `last_event_at`, `push_missing_since`, `push_last_miss_at`, `push_stale`; Slack app and workspace-app responses add `push_missing_since`, `push_last_miss_at` (workspace apps also `push_stale`); `/source-tabs` counts add `push_missing`.
- Editing an ingest trigger keeps the credential it was created with: a login-session edit no longer widens a key-bound trigger to full access, and an API key can only edit triggers it created.
- A trigger bound to an API key without the `read` scope matches and renders no items.
- A GitHub webhook delivery whose refetch couldn't be queued is processed again when GitHub retries it, instead of being answered "duplicate".
- A Slack channel push stops reaching (bot removed from it, `message.groups` unsubscribed) no longer re-flags its app and re-notifies the owner every poll while other channels keep delivering: the miss clears that channel's `push_since` until push delivers there again, and a Slack event ends the app's outage only once `PUSH_STALE_HOURS` have passed since the last miss.

### Required env vars

None.

### Manual steps

None.

### Other changes for operators

- To install hooks, a PAT needs `admin:repo_hook` (classic) or **Webhooks: Read and write** (fine-grained). Without it repos show "Missing permission"; polling and hand-added hooks keep working.
- App accounts: in the App's settings keep the webhook active and subscribe to Pull request, Pull request review, Pull request review comment, Issue comment, Issues, Check run, Check suite and Status (the last three need Checks and Commit statuses read permissions). Several Memory accounts sharing one App overwrite each other's App webhook URL.
- `SERVER_URL` must be publicly reachable or GitHub rejects the hook (status "Rejected by GitHub").
- After deploying, existing accounts with a secret get their hooks at the next 03:15 pass, or immediately via **Install on all repos**.
- Orphan verification batches GitHub PRs and issues separately per repo (`source_type` `github_pr` / `github_issue` in `verify_orphans` results); `verify_orphans(source_types=["github_item"])` now selects nothing.
- `reingest_item` / `update_metadata_for_source_items` take `github_pr` / `github_issue` (or `GithubPR` / `GithubIssue`).
- Ingest triggers bound to an API key that lacks `read` (or `*`) stop matching. Re-create them with a key that has `read`, or from a login session.

### Migrations

- `20261207_github_webhook_status`: adds nullable `github_repos.webhook_status` and `github_repos.webhook_checked_at` (`ADD COLUMN IF NOT EXISTS`). Fast.
- `20261207_github_pr_issue_types`: sets `source_item.type` to `github_pr` / `github_issue` from `github_item.kind` (one UPDATE over the GitHub rows; no schema change), and adds both types to every ingest trigger whose `filter.source_types` names `github_item`. Fast.
- `20261208_push_missed`: adds nullable `push_missing_since`/`push_last_miss_at` to `github_repos` and `slack_apps`, and `push_since` to `slack_channels`. Fast (nullable columns, no rewrite).

### Post-deploy commands

None.

## v0.4.10 (2026-11-06)

### Changes

- Workers only see their own user's runs: `agent_work_claim` hands an
  `agents:work` credential only runs its user owns (triggered and scheduled
  runs belong to the trigger's or schedule's owner), and renewing or
  submitting another user's run is refused as an unknown run. Admins get no
  exception.
- Ingest trigger runs carry what a worker needs to act on each item: its url (GitHub, Slack permalink, Discord jump link), its type's ids (repo/number/state/head sha/checks, Slack channel/ts/thread_ts, Discord guild/channel/message, email headers), meeting summary, notes, attendees and action items, and what changed. Item text arrives inside `<untrusted_item>` delimiters it can't close.
- Trigger templates take `{items}`, `{items_json}` and, with per-item delivery, `{item.<field>}`; saving a trigger whose template names an unknown placeholder or field is refused with the valid fields listed.
- Ingest triggers match every stored item, whether or not it was embedded: short chat messages ("@bot hi") and items whose embedding failed now trigger.
- Ingest trigger filters take `repos` (GitHub "owner/name", case-insensitive) and `mentions_me` (Discord/Slack messages that @-mention one of the owner's Discord bots or Slack app bot users).
- `on_changes` limits an on_edit trigger to edits reporting given changes (edited, commits, comments, review, checks, state); a triggered run's `trigger_ref.changes` lists each item's changes, "new" for a first match.
- Beeper messages match trigger `channel_ids` (their beeper_chat_id) and `exclude_authors` (sender id or name).
- Posts by connected Slack apps' bot users never trigger agents, closing a loop for agents that post with a bot token.
- GitHub webhooks: `POST /github/webhook/<account id>`, signed with a per-account secret generated under **Sources → GitHub → Webhook**. Each event refetches the issue or PR it names (debounced ~15s), and check/status events route to the PRs whose head is that commit. See README § GitHub webhooks.
- GitHub PRs store their head commit and a summary of their checks. A GitHub item's trigger claim version now covers head commit, state, merge, checks and reviews (bare approvals included) as well as title and content, so each of those changes can fire an `on_edit` trigger.
- GitHub syncs (webhook and polling) tell trigger matching what changed: `new`, `edited`, `commits`, `comments`, `review`, `checks`, `state`. Comments and reviews by the account's own login are not changes, and a sync with no such change queues no trigger matching.
- Sources with a working push channel are polled only as a gap-filler: a GitHub repo whose webhook delivered an event within `PUSH_STALE_HOURS` is synced every `max(check_interval, PUSH_GAP_FILL_MINUTES)`; once events stop it returns to its own `check_interval`.
- Slack workspaces switch to their background-sweep frequency only while every polling app has received an event within `PUSH_STALE_HOURS` (previously: whenever the app's setup reached "live", even if delivery later broke).

### Required env vars

None.

### Manual steps

- To use webhooks: generate the secret on each GitHub account and add the hook on GitHub (payload URL, `application/json`, the secret; events Pull requests, Pull request reviews, Pull request review comments, Issue comments, Issues, Check runs, Check suites, Statuses).
- Behind a reverse proxy, let `/github/webhook/` through to the api without a login.

### Other changes for operators

- An existing trigger whose template contains a `{word}` that isn't a placeholder is refused the next time it is saved; its runs keep rendering it literally.
- Slack bot users are recorded when a user next connects Slack; until each user reconnects, their app's bot posts aren't loop-protected and `mentions_me` doesn't see Slack mentions of it.
- New endpoints: `POST`/`DELETE /github/accounts/{id}/webhook-secret` (the POST response is the only place the secret appears).
- New Celery task `memory.workers.tasks.github.refetch_github_item` on the existing `github` queue.
- Existing GitHub items' claim versions change shape (no backfill), but GitHub syncs only queue trigger matching when they report a change, and an item's first recorded head SHA or checks is not one. So `on_edit` triggers don't fire once per open PR after the deploy; an item's next real change fires once, as it should.
- PR syncs (polling too) now also fetch the head commit's check rollup in the same GraphQL query.
- New optional settings: `PUSH_GAP_FILL_MINUTES` (default 720) and `PUSH_STALE_HOURS` (default 24).

### Migrations

- `20261206_slack_bot_user`: adds nullable `slack_user_credentials.bot_user_id`. Fast.
- `20261206_github_webhooks`: adds `github_accounts.webhook_secret_encrypted`, `github_repos.last_event_at`, `github_item.head_sha` and `github_item.checks`, and a partial index on `github_item (repo_id, head_sha)`. Nullable columns and a partial index over no rows yet: fast.

### Post-deploy commands

None.

## v0.4.9 (2026-11-06)

### Changes

- Workers may report a run's cost as zero, as one on a flat-rate subscription
  honestly does.
- An API key whose only scope is `agents:work` (or `*`) gets into MCP instead of
  a 403, and sees only `agent_work_claim` and `agent_work_submit`; it no longer
  needs `read`, which would open the owner's knowledge base to the worker.
  `meta_get_current_time` now needs `read` like every other tool.
- Google Drive folders whose sync takes over a minute (every first sync of a
  large folder) no longer fail with "transaction has been rolled back"; a
  folder's sync errors are recorded on it again.
- A second recording of a call its owner already has (say a colleague's
  Fireflies notetaker and yours both joined) is no longer stored, extracted or
  turned into tasks. Two recordings are one call when at least half of the
  shorter one overlaps the other (start within 5 minutes when a duration is
  unknown) and they share the calendar event, or the title and an attendee
  other than the owner. A recording restarted after a drop is kept.
- A meeting whose worker died mid-extraction is rerun once, when its task is
  redelivered or by the hourly stuck-meeting cleanup; if that run is lost too,
  it is marked failed instead of sitting unextracted. Meetings an earlier
  cleanup left `pending` get the same single rerun.
- Text containing NUL characters (some emails, Claude session transcripts,
  JSON metadata) is stored with them removed instead of failing the
  `process_message` or `index_session` job. Failed jobs can be retried.
- Comic sync works again: since v0.2.3 every page it fetched read as empty, so
  no new xkcd or SMBC comics were found. SMBC comic pages, which the site
  currently serves with status 500, are read too. Comics that have dropped out
  of the feeds since then are not fetched back.
- A task that keeps killing its worker is dropped after 3 starts instead of
  being redelivered forever; an email that kills its worker is not re-queued
  by later syncs.
- Slack history sync no longer loses messages whose store failed: it stores
  each message itself, oldest first and with its thread replies, and moves a
  channel's cursor past it only once stored, so a failed sync resumes where it
  stopped. Long syncs no longer fail with "server closed the connection
  unexpectedly". A message whose store fails 3 times (worker crashes included,
  lost database or Redis connections not) is skipped and logged as an ERROR.
  That log line, in the operator logs, is the lasting record of the skip: the
  workspace's sync error shows it only until the workspace's next sync.
- Email ingest needs less memory per message: attachments over
  `MAX_ATTACHMENT_EMBED_SIZE` are stored but not embedded (`SKIPPED`), also
  when reingested; PDF and Word attachments get page images for their first
  `MAX_ATTACHMENT_EMBED_PAGES` pages and searchable text for every page; each
  attachment's page images are released once it is embedded; and reading a
  mail's body no longer decodes its attachments. PDF pages are rendered no
  larger than the 2 MP an embedding uses.
- Word documents are text-searchable page by page, as PDFs already were.
- Clearing or reindexing an item deletes its vectors from the Qdrant collection
  each chunk is stored in, not one named after the item's modality. Observations
  (stored in `semantic` and `temporal`) used to keep their vectors and log a
  "Collection `observation` doesn't exist" error. Vectors already left behind
  are removed by the nightly `clean-all-collections` task.
- Embedding splits an item's chunks across several Voyage requests, within Voyage's 1000-inputs and per-request token caps (120K text, 320K multimodal), so a long PDF, ebook or forum post no longer fails every embed attempt.
- Two attachments of one email that share a filename (after cleaning) are stored as separate files: the repeat gets a content-hash suffix, stable across reingests. Already-ingested emails keep the overwritten file until reingested.
- Reprocessing a meeting (by hand, by the stuck-meeting cleanup, or a redelivered ingest) keeps its action items: pending ones are recreated by the new extraction, and ones someone started, finished or cancelled stay attached instead of being orphaned. A rerun also reattaches action items an earlier rerun orphaned.
- Items whose embedding failed are retried hourly with backoff (1h, 2h, 4h... up to 8 attempts, about five days) instead of only within 48 hours of being added; up to 300 items per run.
- Items that failed because no Voyage key was set don't use up attempts, and are retried within the hour once a key exists, including a key set in the environment rather than on the settings page.
- Blog and forum posts whose downloaded images are missing from disk are embedded from their text instead of failing.
- Slack history sync picks up replies posted later under threads it had already passed: each sync re-lists the channel's history of the last `SLACK_THREAD_LOOKBACK_DAYS` and reads, from the newest stored reply on, every thread whose `latest_reply` isn't stored. Replies whose events were missed (downtime, deploys, failed webhooks) are no longer lost.
- Container logs cover days rather than hours. Per-item and per-request lines no longer log at INFO: the httpx/httpcore line for every HTTP request, Celery's "Task … received" line (the "succeeded" and failure lines still name each task), passing `/health` checks in the api access log, and per-item ingest lines ("Adding Slack/Discord message", "Successfully processed …", "Connecting to Qdrant", "Job N completed", an email's per-chunk listing, per-item reingest/metadata lines). The api and worker containers keep 5 × 50 MB of logs instead of 3 × 10 MB. ERROR and CRITICAL logging is unchanged.
- Meetings record their size, so search's `min_size`/`max_size` filters no longer drop them.
- A synced meeting is linked to its owner's calendar event for the occurrence it recorded (Fireflies `cal_id` matched to the event's calendar id).
- A meeting whose worker died while it was being embedded is rerun once, like one lost mid-extraction, instead of sitting `extracted` with no chunks; a redelivered rerun recreates its pending action items rather than keeping stale ones.
- The stuck-meeting cleanup reruns a meeting under the job that brought it in, so that job completes (or fails when the cleanup gives up) instead of being failed as lost while the meeting finished.
- Email attachments are stored in a directory per message, so a sender's recurring same-named attachment (a monthly `invoice.pdf`) no longer overwrites the previous one.

Operator note: existing attachment rows keep their old paths and still resolve. Where two emails from the same sender and folder shared an attachment name, the older row already points at the newer file; the original bytes are gone from disk. Reingesting the email doesn't fix this, since an existing message is skipped. Deleting the older message and syncing again would. No migration.
- Archiving mail no longer removes it from Memory. Gmail and IMAP sync used to
  delete every stored message that left the synced labels/folders; now a
  message is removed only once it is deleted or sits in Trash or Spam (IMAP:
  Trash or Junk). An IMAP message moved to another folder is tracked there.
- A Gmail account with no folders selected syncs all mail except Spam and
  Trash, not just INBOX, so mail archived before a sync (by hand or by a
  filter or script) is ingested too.
- Large Google Drive folders finish syncing: the folder sync only lists files
  and queues one job per new or changed file, which downloads and stores it, so
  a first sync no longer hits the 1-hour task limit and starts over. A file
  whose job failed is queued again by the next folder sync, and files that
  failed to download before (including during an earlier interrupted sync) are
  picked up.
- Drive files larger than 50MB are skipped instead of being downloaded.

### Required env vars

None.

### Manual steps

None.

### Other changes for operators

- Scheduled jobs slower than hourly run at fixed UTC times instead of on an
  interval that restarted with every deploy, so near-daily deploys no longer
  stop them: S3 file backup 02:00, Discord catch-up 02:30, Qdrant collection
  cleanup 05:50, comic sync 06:15, Beeper reconcile every 6 hours from 00:20,
  orphan verification every 6 hours from 01:40. Their interval settings
  `S3_BACKUP_INTERVAL`, `DISCORD_BACKFILL_INTERVAL`, `CLEAN_COLLECTION_INTERVAL`,
  `COMIC_SYNC_INTERVAL`, `BEEPER_RECONCILE_INTERVAL` and
  `VERIFICATION_SYNC_INTERVAL` are removed: delete them from `.env`; they are
  ignored.
- A dropped task fails its job with "Worker lost on 3 starts; not retrying",
  records a task failure metric and sends the usual task-failure notification.
  A redelivered task reuses its job row instead of adding one per delivery;
  jobs waiting on a Celery retry show as pending, not processing; a manual
  retry of a failed job starts its start count from zero.
- The lost-job sweep also fails dispatched jobs still pending a day later (a
  retry or queued message that never arrived).
- Redis' `visibility_timeout` is now set (6 hours with default settings: twice
  the longest task time limit plus `DISCORD_BACKFILL_JITTER_SECONDS` plus an
  hour), so long-running or long-countdown tasks are no longer redelivered
  while they run or wait. A message whose worker container died outright
  (rather than one child process) waits up to that long to be redelivered.
- An email stops being queued once its process_message jobs have lost their
  worker on 3 starts in total, counting both the new "Worker lost on 3 starts"
  failures and the sweep's "still processing long after the task time limit"
  ones. The bad email on existing instances (all those sweep failures) is
  therefore skipped from the first sync after the update; a manual retry of
  its failed job still runs it.
- Slack history is read in time windows of at most a page, and a channel's
  sync works for about 5 minutes per turn, then queues its continuation, so a
  large backlog takes several turns on the `slack-sync` worker.
- Slack history messages are now stored one at a time on the `slack-sync`
  worker instead of being fanned out as separate tasks, so large backfills and
  rewinds take longer than before.
- New `slack rewind-cursors` command of `tools/run_celery_task.py`: moves the
  cursor of each collected channel back to its newest stored top-level message
  (or to `--since`, when earlier) and resyncs it. Channels mid-sync are listed
  as `busy` and left alone; run it again for them. `--dry-run` only lists what
  it would do.
- New settings `MAX_ATTACHMENT_EMBED_SIZE` (default 20 MiB) and
  `MAX_ATTACHMENT_EMBED_PAGES` (default 50) bound the memory an email
  attachment can take when embedded.
- The ingest-hub container's memory limit rises from 512 MiB to 1 GiB: beat,
  the Discord collector and the Beeper collector together sat at its limit.
- `process_raw_items` runs hourly at :25 instead of daily at 04:00.
- A failed embed logs `Embedding <Type> <id> failed: <ExceptionType>` at WARNING (it used to be ERROR, with no item id). The last error type is stored in `source_item.embed_error`, and the next retry time in `embed_retry_at`: `SELECT type, embed_error, count(*) FROM source_item WHERE embed_status = 'FAILED' GROUP BY 1, 2`.
- New optional setting `SLACK_THREAD_LOOKBACK_DAYS` (default 14; 0 turns it off). Threads started earlier than this are not checked; replies missing there still need `slack rewind-cursors --since`.
- Cost per channel sync: `ceil(messages in the lookback / 100)` extra `conversations.history` calls, plus one `conversations.replies` call per thread with replies that aren't stored. In steady state that's usually zero replies calls. A thread whose latest reply is never stored (a `bot_message`, a reply without an author, or a reply skipped after repeated failures) is read again on every sync until it falls out of the lookback.
- The first sync after deploying reads every thread in the lookback whose latest reply was missed. That is a one-off burst of `conversations.replies` calls; Slack's rate limiting handles it the same way it handles the rest of the sync.
- Docker applies the new `logging` options only to recreated containers: `docker compose up -d` after pulling recreates them. Allow up to 250 MB of disk per api/worker container (5 containers ≈ 1.25 GB worst case).
- The demoted lines are still there at DEBUG: run a worker with `LOGLEVEL=DEBUG` (or `--loglevel=DEBUG` for agents-worker) to get them back while you diagnose something.
- The first sync of a Gmail account with no folders selected backfills its
  archived mail: only messages not stored yet are downloaded, but on a large
  mailbox that can be thousands of messages to fetch and embed (and Voyage
  spend). An interrupted backfill resumes at the next sync. Accounts with folders selected keep
  syncing only those labels.
- Gmail sync now lists the whole mailbox each run (500 ids per call) to tell
  archived mail from deleted mail, and checks the few missing ids in batches
  of 50. The orphan verifier checks Gmail items the same way instead of
  listing the synced labels.
- New optional setting `GOOGLE_DRIVE_MAX_FILE_BYTES` (default 52428800, 50MB):
  Drive files listed as larger are not downloaded. Google Docs, Sheets and
  Slides list no size and are not limited by it.
- Each folder sync now lists the whole folder (metadata only, up to 1000 files
  per Drive call) rather than only files modified since the last sync, and
  compares it with the stored docs. Each queued file shows up as a
  `sync_google_doc` job; a file whose job kills its worker 3 times is not
  queued again until it changes. Folder `last_sync_at` no longer controls
  which files are fetched.
- `sync_google_doc` messages queued by the previous version have a different
  signature and fail after the update; the next folder sync queues those files
  again.

### Migrations

- `20261204_meeting_cal_event_uid`: adds the nullable `meeting.calendar_event_uid`
  column (the calendar event occurrence a meeting recorded). Fast.
- `20261205_meeting_backfill`: fills `size` for stored meetings, links meetings carrying a calendar uid to their owner's matching event, and moves meetings left `extracted` (extracted but never stored) back to `processing` so the hourly `cleanup_stuck_meetings` reruns each once. Fast (meeting table only). The reruns re-extract on the owner's LLM key.
- `20261205_embed_retry` adds `embed_attempts`, `embed_error` and `embed_retry_at` to `source_item`. These are metadata-only column adds, so it's fast even on a large table.

### Post-deploy commands

Delete the second recordings of calls stored before this release, with their
vectors and their pending tasks; tasks someone started or finished are kept,
detached from the meeting. Each call keeps the copy whose tasks someone
worked on, else one whose processing finished, else the one with the longest
transcript. Check the list with `--dry-run` first. Safe to re-run:

```bash
docker compose exec -T worker python - --wait maintenance dedupe-meetings --dry-run < tools/run_celery_task.py
docker compose exec -T worker python - --wait maintenance dedupe-meetings < tools/run_celery_task.py
```

Refetch Slack messages that earlier syncs skipped after a channel's newest
stored message. Look at the dry run first:

```bash
docker compose exec -T worker python - --wait slack rewind-cursors --dry-run < tools/run_celery_task.py
docker compose exec -T worker python - --wait slack rewind-cursors < tools/run_celery_task.py
```

Gaps between stored messages, including thread replies missing under parents
older than a channel's newest stored message, are only refetched by rewinding
further, e.g. `--since 2026-06-01` (dry run first, as above). That rereads
every collected channel's history and threads from that date, which takes a
while under Slack's rate limits.

Reattach the action items earlier meeting reruns left without a meeting. Safe
to re-run; a task whose title was edited since can't be matched and stays
detached:

```bash
docker compose exec -T postgres psql -U kb -d kb -c "
UPDATE task t SET source_item_id = m.id
FROM source_item s, meeting m
WHERE s.id = t.id AND t.source_item_id IS NULL
  AND s.sha256 = digest('meeting:' || m.id || ':' || t.task_title, 'sha256')"
```

## v0.4.8 (2026-11-06)

### Changes

- OAuth client registration accepts the claude.ai, claude.com, ChatGPT and
  Grok connector callbacks by default.
- The Slack app wizard can go back to earlier steps. An app goes live on the
  first event that ingests something; step 6 still shows when that happened.
- Agent runs can be placed on external workers, which claim matching runs with
  `agent_work_claim` and report back with `agent_work_submit`.

### Required env vars

None.

### Manual steps

None.

### Other changes for operators

- Redirect allowlist entries added by hand for the claude.ai, claude.com,
  ChatGPT and Grok connector callbacks can be removed.
- Agent runs can be placed on external workers: an agent's `placement`
  (`server_only`, the default; `runner_only`; `server_first`; `runner_first`),
  `fallback_after` and `requires` decide who runs it. Users or API keys with
  the new `agents:work` scope claim matching runs with `agent_work_claim` and
  finish them with `agent_work_submit`. Grant the scope only to workers you
  trust with other users' run input.
- New optional settings: `AGENT_WORK_LEASE_DEFAULT_SEC`,
  `AGENT_WORK_LEASE_MIN_SEC`, `AGENT_WORK_LEASE_MAX_SEC`,
  `AGENT_WORK_HOLD_MAX_SEC`, `AGENT_WORK_MAX_LAPSES`,
  `AGENT_WORK_MAX_RESULT_BYTES` (see the README).
- The new `sweep_worker_runs` beat task runs on the `agents` queue, so worker
  runs only lapse, fall back and expire while `agents-worker` is running.

### Migrations

- `20261203_agent_run_workers`: adds lease, worker and `placed_at` columns to
  `agent_runs` (backfilling `placed_at` from `created_at`) and two indexes.
  Quick.

### Post-deploy commands

None.

## v0.4.7 (2026-11-06)

### Changes

- Deleting a Discord bot that has collected messages no longer fails with a
  500; its messages are kept with no bot.
- Slack apps whose workspace was disconnected are listed on the Slack tab, to
  reconnect or delete. They were hidden before, while still blocking a new
  app with the same client ID.

### Required env vars

None.

### Manual steps

None.

### Other changes for operators

None.

### Migrations

- `20261202_discord_msg_bot_null`: `discord_message.bot_id` becomes
  `ON DELETE SET NULL`. Re-adding the foreign key validates every Discord
  message row, a quick scan.

### Post-deploy commands

None. Instances updating from before v0.4.5 still run v0.4.5's post-deploy
command.

## v0.4.6 (2026-11-05)

### Changes

- ingest-hub starts again on appliance checkouts that v0.4.5's update left
  crash-looping.
- A transcript account's webhook dialog generates a secret when it opens on
  an account that has none, instead of showing "Not set".

### Required env vars

None.

### Manual steps

None.

### Other changes for operators

- The ingest-hub image makes its supervisor config readable by the
  container's user. `tools/update.sh` writes changed files root-only, so on
  v0.4.5 ingest-hub crash-looped ("could not read config file") and the
  update failed its containers-stay-up check after the stack was already on
  v0.4.5. Updating to v0.4.6 fixes it and clears the failed-update marker.

### Migrations

None.

### Post-deploy commands

None. Instances updating from before v0.4.5 still run v0.4.5's post-deploy
command.

## v0.4.5 (2026-11-05)

### Changes

- Deleting an email account (its mail and attachments), book, Slack channel or
  workspace, forum post or Claude session deletes the source items and chunks
  under it. It used to leave them behind, and search then failed on every
  query that hit one.
- Search skips an item it can't load (logged at WARNING with its id) instead
  of failing.
- Discord gateway bots apply deletes and edits of older messages, and record
  servers they join after connecting. The daily catch-up deletes stored
  messages that are gone from the channel's recent window, and a channel's
  backfill stops once its collection is paused.
- Reactions a gateway bot sees only apply to messages its owner's bots stored,
  as webhook deliveries already did; a reaction on another owner's message is
  dropped.
- A bot leaving a server hands it to another of the same owner's bots still
  in it, else leaves it with no owning bot. The next bot to claim it then
  takes it with the project and sensitivity reset to defaults, so another
  owner never inherits its access settings.
- Each Discord bot gets its own card in Sources, listing only the servers it
  is in; Slack workspaces get the same layout.
- The `discord_list_channels` MCP tool's `collect_messages` is the channel's
  own setting (null when it inherits the server's); the value in effect is in
  the new `effective_collect` field.
- Beeper: memory collects the messages of the networks connected to a Beeper
  account, signed in through a Beeper Server. New MCP tool `beeper_list_chats`.
  Search, `core_list_items` and `core_count_items` take `network` and `chat`
  filters for Beeper messages, and the `channel` and `author` scopes match
  Beeper chats and senders.
- Agent ingest trigger filters take `exclude_channel_ids` (Discord or Slack
  channel ids whose items, threads included, never match). It doesn't count as
  narrowing a filter on its own.
- Memory MCP tools that only read carry `readOnlyHint`, so `agent_catalog`
  reports them as `read_only` and creating a trigger or agent no longer warns
  about them.
- Discord, Slack and Beeper search results are titled by sender and channel
  (`alice in #general`, `[Slack] #general — alice`) in the web UI instead of
  "Untitled".
- Beeper message text that is HTML (Matrix formatted bodies, e.g. bridged
  Slack mentions) is stored as plain text, mentions as `@Name`. Bridged
  "… has joined/left the channel" notices aren't stored, as the Slack
  integration already skips them.

### Required env vars

None.

### Manual steps

None.

### Other changes for operators

- Deleted sources' Qdrant vectors stay until the next scheduled
  `clean_all_collections` run; search drops them meanwhile. The deletion
  trigger runs one DELETE per row at commit, so deleting an account with very
  many items (say 100k emails) makes for a long commit; raise the client's
  statement timeout if one times out.
- Discord bots on a custom API URL no longer request member lists at startup.
  The collector's `POST /refresh_metadata` is now
  `POST /refresh_metadata/{bot_id}` and refreshes only that bot.
- Beeper: users sign a Beeper account in through a Beeper Server at
  `/beeper/*`, and memory collects the messages of the networks connected to
  it in the Beeper app (see the README's Beeper section). Beeper Server is
  closed source with no licence, and unsigned: the opt-in `beeper` compose
  service (`--profile beeper`) builds a pinned release
  (`docker/beeper/Dockerfile`, checked against the update feed's sha512) that
  runs as non-root on a private `beepernet` network shared only with `api`,
  `worker` and `ingest-hub`. Never publish its port: its setup endpoints need
  no auth until sign-in. Its `beeper_data` volume holds the session and
  encryption keys and is not in the S3 backup; back it up as UPDATING.md
  describes. ingest-hub runs a new `beeper-collector` program (a WebSocket per
  verified account); the sync tasks run on the existing `backfill` and
  `discord` queues, and images go to `BEEPER_STORAGE_DIR` (default
  `$FILE_STORAGE_DIR/beeper`, backed up encrypted). New optional settings:
  `BEEPER_SERVER_URLS` (the Server origins users may connect through, e.g.
  `http://beeper:23373`; none by default), `BEEPER_HOMESERVER_ORIGINS`
  (Matrix homeservers that may receive a token to sign it out; default
  `https://matrix.beeper.com`), `BEEPER_SERVER_VERSION` (the pinned release;
  other versions are flagged), `BEEPER_REQUEST_TIMEOUT` (seconds, default
  60), `BEEPER_RECONCILE_INTERVAL` (how often collected chats are re-read,
  seconds, default 6 hours), `BEEPER_SYNC_MAX_MESSAGES_PER_RUN` (default
  2000), `BEEPER_ATTACHMENT_MAX_BYTES` (default 50 MiB) and
  `BEEPER_STORAGE_DIR`. Its MCP tool needs the new `beeper` scope. Qdrant
  collections get a new `beeper_chat_id` payload index, created on startup.
- Discord threads now record their parent channel
  (`discord_channels.parent_id`); threads stored before this release get it
  from the next daily catch-up's thread listing, and until then their messages
  match a trigger even when the parent is excluded.

### Migrations

`20261128_orphaned_source_items`: adds a deferred `AFTER DELETE` trigger to
each source item subclass table (`mail_message`, `book_section`, …) that deletes
the `source_item` row once its subclass row is gone. Fast: it only creates a
function and the triggers.

`20261129_discord_bot_last_sync`: adds the nullable `discord_bots.last_sync_at`
column. Fast.

`20261130_beeper`: creates the `beeper_accounts`, `beeper_networks`,
`beeper_chats`, `beeper_participants`, `beeper_reactions` and `beeper_message`
tables. Fast.

`20261201_discord_channel_parent`: adds the nullable `discord_channels.parent_id`
column. Fast.

### Post-deploy commands

Delete the source items orphaned before the migration, with their chunks and
Qdrant vectors. Safe to re-run:

```bash
docker compose exec -T worker python - --wait maintenance delete-orphaned-source-items < tools/run_celery_task.py
```

## v0.4.4 (2026-11-05)

### Changes

- The web UI is served at the root: `/sources` instead of `/ui/sources`; `/ui`
  links redirect there.
- REST endpoints that shared a path with a UI page moved under `/api`
  (`/api/settings`, `/api/users`, `/api/jobs`, …).
- OAuth sign-in works on an api run on a host whose clock isn't UTC; it used to
  reject every authorization code as expired.
- Discord bots need a token and choose real-time delivery: WebSocket gateway
  (the default) or webhook.
- Discord bots send messages, DMs and reactions through the REST API at their
  API base URL, and a gateway bot with a custom API base URL takes its gateway
  from there too.
- A Discord bot whose login is refused waits for a settings change, or a pause
  and resume, instead of retrying; a crashed one backs off up to an hour.
- The ingest hub re-reads the bot list every minute, so adding, pausing or
  switching a bot no longer needs a restart.
- Signed-in browsers keep refreshing their login across the move to the root.

### Required env vars

None.

### Manual steps

None. A reverse proxy that passes everything to the api, like the Caddyfile
`deploy/aws` writes, needs no change. A proxy that only forwards `/ui` and
named API paths to the api must now forward everything.

### Other changes for operators

- The web UI is served at the root: `https://<domain>/sources` instead of
  `https://<domain>/ui/sources`. `/ui` and everything under it redirect there
  (307, keeping the path and query), so bookmarks, sent password-reset links,
  shared poll links and stored reports that load `/ui/mcp-client.js` keep
  working. The redirect is temporary and will be removed in a later release.
- The REST endpoints that shared a path with a UI page moved under `/api`:
  `/settings`, `/users`, `/usage`, `/jobs`, `/ssh-keys` and `/polls/respond`
  are now `/api/settings`, `/api/users`, … Scripts that call them directly
  need the new paths: meeting ingest clients poll job status at
  `/api/jobs/{id}` or `/api/jobs/external/{external_id}`. `GET /users/{id}` is
  gone.
- The setup token log line now points at `/setup`.
- Every Discord bot needs its token, and chooses its real-time delivery:
  WebSocket gateway (the default) or webhook. Bots that had a public key keep
  webhook delivery; the rest stay on the gateway. Bots registered without a
  token are paused; add the token under **Edit** to resume one.
- Every Discord bot now sends (messages, DMs, reactions) through the REST API
  at its API base URL; the collector's `/send_dm`, `/send_channel` and
  `/add_reaction` endpoints are gone.
- A gateway bot with a custom API base URL takes its gateway from
  `GET <API base URL>/gateway/bot`, so a Discord-compatible server can serve
  the gateway too. Its REST and gateway connections get the same outbound
  address checks as other user-supplied URLs (IP literals included), REST
  redirects are refused, and the gateway host must be on the chat API
  allowlist (`CHAT_API_ALLOWED_ORIGINS`, unless the bot's owner is an admin).
  A bot whose login Discord refuses waits for a settings change, or a pause
  and resume, instead of retrying; one missing privileged intents retries
  hourly; a crashed one backs off from a minute up to an hour.

### Migrations

`20261127_discord_realtime`: adds `discord_bots.realtime` and pauses tokenless
bots. Fast.

### Post-deploy commands

None.

## v0.4.3 (2026-11-05)

### Changes

- Discord bots can receive messages by webhook, now the default for new bots.
  Several users can add the same Discord application, and the same Slack
  client ID.
- Deleted Discord messages are removed.
- Pushed Slack and Discord events only change rows their sender's bots or apps
  own.
- Slack apps and Discord bots can point at Slack- or Discord-compatible servers,
  such as a chat proxy.
- The Discord backfill runs daily and re-reads the last 2 days of every
  collected channel, storing missed messages and edits.
- Reactions on Slack and Discord messages are stored per user and kept in step
  with each add and remove.
- Parallel Postgres `VACUUM` and index builds no longer fail with "could not
  resize shared memory segment".
- Removed: the Claude Code containers runner (`containers_*` tools, `/claude`
  routes, scheduled Claude sessions, environments, configuration snapshots and
  saved GitHub tokens). The Claude Code session archive stays.

### Required env vars

None.

### Manual steps

`docker-compose.claude.yaml` is gone. If `COMPOSE_FILE` in `.env` lists it,
remove it from the list before updating (keep any other overlay, such as
`deploy/aws/compose.proxy.yaml`), or compose fails to find the file. Drop
`-f docker-compose.claude.yaml` if you pass explicit `-f` flags. The
`memory-api-<stack>` Docker network it attached to is no longer used; remove it
with `docker network rm memory-api-prod` once the stack is up.

If you ran Claude Code containers, clean up the host *before* updating. Session
containers hold injected SSH keys and GitHub tokens, and once the migration
drops `claude_environments` nothing knows their environment volumes. The
orchestrator named session containers `claude-u<user>-<source>-<hex>`, their
networks `claude-net-<session>` and environment volumes `claude-env-u<user>-…`.
Use `podman` in place of `docker` if that is what the orchestrator ran on:

```bash
docker ps -a --format '{{.Names}}' | grep -E '^claude-u[0-9]+-' | xargs -r docker rm -f -v
docker network ls --format '{{.Name}}' | grep -E '^claude-net-u[0-9]+-' | xargs -r docker network rm
docker volume ls --format '{{.Name}}' | grep -E '^claude-env-u[0-9]+-' | xargs -r docker volume rm
```

Then stop and remove the orchestrator (the `claude-orchestrator` container;
`docker compose down` in its directory if you installed it with compose) and
delete its socket directory, `sudo rm -rf /var/run/claude-sessions`, and its
logs in `/var/log/claude-sessions`. Keep the orchestrator if something other
than Memory, such as `claude-web`, still uses it; its session containers are
not matched above, because only Memory's session ids start with `u<user>-`.

### Other changes for operators

Discord bots can receive messages by webhook, now the default in **Sources →
Discord**: `POST /discord/events/{bot_id}` takes Discord's Webhook Events
envelope, checks its Ed25519 signature against the bot's public key and queues
the gateway event it carries. Behind a reverse proxy, let `/discord/events/`
through without a login, as `/slack/events/` already is. Gateway bots keep
working; a bot with a public key gets no gateway connection. Several users can
add the same Discord application, and the same Slack client ID, each with their
own row. Deleted Discord messages are now removed. Pushed events only change
rows their sender's bots own: they never rename or claim existing servers or
channels, overwrite stored Discord user names, or edit, delete or react to other
users' bots' messages; a Slack app only takes events for workspaces it was
authorized into, for channels of that workspace, and only changes messages its
owner's apps ingested. A Discord server first seen in a pushed event gives that
bot's owner nothing until a bot with a token (gateway or REST listing) claims it,
which resets its project and sensitivity; servers are listed by that verified
membership. A DM is managed by the owner of the bot that first delivered
it. A webhook bot with a token sends through the REST API.

New setting `CHAT_API_ALLOWED_ORIGINS` (comma-separated https origins, default
empty): the only servers besides slack.com and discord.com that non-admin users
may point a Slack app's authorize, token or API URL, or a Discord bot's API URL,
at. Checked on save and on every use. To use a chat proxy, set it, e.g.
`CHAT_API_ALLOWED_ORIGINS=https://chat-proxy.equistamp.io`.

The Discord backfill runs daily instead of weekly (`DISCORD_BACKFILL_INTERVAL`
now defaults to 86400; an explicit value in `.env` still wins). It reads through
Discord's REST API at each bot's API base URL rather than discord.py, refreshes
each bot's servers and channels, and re-reads the last `DISCORD_CATCH_UP_DAYS`
(new, default 2) of every collected channel, storing missed messages and edits
(not missed deletes or reactions). Slack apps get optional authorize,
token-exchange and Web API URLs for Slack-compatible servers; requests to them,
including the OAuth token exchange, go through the address-checked HTTP client.
Reactions on Slack and Discord messages are stored per user and kept in step
with each add and remove.

Postgres gets a 256 MB `/dev/shm` (Docker's default is 64 MB), so parallel
`VACUUM` and index builds no longer fail with "could not resize shared memory
segment". The update recreates the postgres container to apply it.

The Claude Code containers runner is removed: the `claude_containers` module,
its `/claude` routes, `containers_*` MCP tools and pages, scheduled Claude
sessions, Claude environments and configuration snapshots, saved GitHub tokens
for sessions, the `claude_sessions` scope, default SSH keys ("Use for my
containers") and `public_key` in `meta_get_user`. `GET /github/repos` and
`tools/deploy.sh` are gone too. The Claude Code session archive
(`claude_session_search`, `claude_session_fetch` and the session hook) stays.
SSH keys stay, for notes sync.

`claude_containers` left in `ENABLE_MODULES` or `DISABLE_MODULES` is ignored
with a WARNING at startup rather than stopping it; remove it from `.env`.
These settings no longer do anything and can be removed from `.env`:
`ORCHESTRATOR_SOCKET`, `ORCHESTRATOR_BASE_URL`, `DOCKER_GID`, `MEMORY_STACK`,
`INTERNAL_API_URL`, `HOST_STORAGE_DIR`, `SNAPSHOT_STORAGE_DIR`,
`MAX_CONCURRENT_SESSIONS_PER_USER`, `CLAUDE_MAINTENANCE_INTERVAL_SEC`,
`CLAUDE_SESSION_TIMEOUT_HOURS`, `MAX_TRANSFER_PUSH_BYTES` and
`TRANSFER_TOKEN_TTL_SECONDS`. `TRANSFER_TOKEN_SECRET` stays: it signs ingest
upload URLs and password reset links. The api image no longer has `tmux` or the
Docker CLI, nor `git` and `xz-utils`, which came with the Docker CLI: nothing in
the api runs them (notes sync runs `git` in the workers, whose image keeps it).

### Migrations

`20261124_drop_claude_containers` deletes scheduled Claude sessions (their
executions cascade), drops the `claude_environments`, `claude_config_snapshots`
and `claude_github_tokens` tables and `users.default_ssh_key_id`, strips the
`claude_sessions` scope from users and API keys, and deletes the stored
`module_claude_containers` setting. Each step tolerates the object being
missing. Fast. Its downgrade restores nothing.

`20261125_chat_webhooks` splits Slack credentials' comma-joined scopes into one
entry per scope; makes `slack_apps.client_id` unique per registering user and
adds `authorize_url`, `token_url`, `api_base_url` and `last_event_at`; gives
`discord_bots` a sequence-backed id (existing rows keep theirs, the application
snowflake, which is copied to the new `application_id`), unique per
(application, user), plus `public_key`, `api_base_url` and `last_event_at`; and
adds `discord_channels.bot_id`, filled for DMs from the bot of their first
message; `discord_servers.verified` (true for existing rows) and the
`discord_bot_servers` membership table, filled from existing server owners; and
`slack_message.slack_app_id`, filled with the app of each workspace's
earliest credential. Fast. Its downgrade refuses while bots added under the new
ids or Slack client IDs shared by several users exist.

`20261126_per_user_reactions` rewrites stored reactions as `{emoji: [user ids]}`.
Slack reactions keep their users. Discord reactions were stored as counts without
users, so they are dropped; reactions from now on are recorded per user. It
rewrites every message row that has reactions, which may take a while on a large
archive. Its downgrade restores the list shapes, with counts from the user lists.

### Post-deploy commands

Delete the configuration snapshot tarballs. Safe to re-run:

```bash
docker compose exec -T api python -c "import shutil; from memory.common import settings; p = settings.FILE_STORAGE_DIR / 'snapshots'; p.exists() and shutil.rmtree(p)"
```

If you had set `SNAPSHOT_STORAGE_DIR` to somewhere else, delete that directory
instead.

## v0.4.2 (2026-11-04)

### Changes

- Search runs the query-analysis LLM only for queries that mention a date or
  time (a year, month, weekday, "yesterday", "last 3 weeks", "upcoming"...);
  others are searched as typed, which is faster and ranks better.
- A search returns `limit` distinct items with up to 3 matching chunks each, so
  one long item no longer fills the results on its own; with `useScores` the
  LLM scores each item's best chunk only.
- Titles recalled by query analysis are matched in the database rather than
  among an arbitrary 200 items, and also match reports, tasks, calendar events
  and observations.
- The reranker sees each chunk's item title above its text (chunks rarely
  repeat it), which ranks slightly better.
- Keyword (BM25) search works again.
- Memory's own LLM calls on Anthropic keys cache the system prompt, so
  repeated search and summary calls read it from the cache instead of writing
  a new entry each time.
- Oversized chunk images embed instead of failing and leaving copies behind.
- Fireflies meetings arrive by webhook, with a daily catch-up poll. Adding a
  Fireflies account in the UI generates its webhook secret and shows the setup
  steps.
- A Fireflies resend (`reprocessed_at` on `meeting.transcribed` or
  `meeting.summarized`) with a changed transcript replaces the stored meeting
  and reruns extraction instead of being ignored; its `pending` action items
  are recreated and the rest kept, detached.
- A Fireflies webhook and the poll ingesting the same meeting at once no longer
  fails one of them, and a successful webhook fetch clears the account's
  earlier sync error.
- Emails whose Message-ID is already stored (GitHub re-sends notifications
  under the same Message-ID with a changed body) count as already ingested.
  They no longer fail in `email.process_message` and get queued again on every
  sync.

### Required env vars

None.

### Manual steps

Optional, before deploying: count the chunks longer than 900k characters:
only their first 500k characters (200k for mostly multibyte text, such as
Japanese) become searchable by keyword. Nothing needs doing
about them:

```bash
docker compose exec -T postgres psql -U kb -d kb -c "SELECT count(*) FROM chunk WHERE length(content) > 900000"
```

Fireflies accounts now expect a webhook and poll only once a day. For each
existing Fireflies account, open **Sources → Transcripts → Webhook**, generate a
secret, and add it in Fireflies' Webhooks V2 settings for `meeting.transcribed`
(README, "Fireflies transcripts"). Without it, meetings arrive up to a day
late. A reverse proxy must pass `/transcripts/ingest/` through to the api
unauthenticated.

### Other changes for operators

Keyword (BM25) search works again: `chunk.search_vector` is filled from the
first 500k characters of chunk content (200k when that would pass Postgres's
1MB tsvector limit) by a trigger, and GIN-indexed. Until the
backfill below finishes, keyword search only finds chunks written after the
update.

New chunk images (`files/chunks/`) are saved as WebP, downscaled to at most 2
megapixels (the most voyage-multimodal-3 uses) and 16383 pixels a side. Images
larger than Voyage accepts used to fail to embed and were retried, leaving a
copy behind each time; an email attachment already stored from an earlier
email (a signature logo, say) also left one. A new daily `clean_chunk_images`
task (04:35) deletes chunk images that no chunk references and that are over a
day old. Existing referenced images are left as they are.

`TRANSCRIPTS_SYNC_INTERVAL` is removed (it is ignored if still set): the
Fireflies catch-up poll runs daily at 05:10 UTC and walks the last 14 days with
pagination. The weekly 365-day rescan is gone; **Full rescan** on an account
still runs it on demand.

The hourly `refresh_metric_summaries` task and its `METRICS_SUMMARY_REFRESH_MINUTE`
setting are removed. Nothing read the `metric_summaries` view.

Slack `channel_not_found` and `not_in_channel` responses, for channels the app
has left or can't see, are logged at INFO instead of ERROR.

### Migrations

`20261114_drop_secrets` (since v0.4.0) no longer fails with `index "ix_secrets_name" does not exist` on databases created before the complete-schema baseline. An update that stopped there can simply be re-run.

`20261123_chunk_search_vector` adds the trigger (waiting at most 10s for its
lock on `chunk`) and builds the `chunk_search_vector_idx` GIN index
`CONCURRENTLY`, so it takes no long lock. It is fast while `search_vector` is
empty. Over an already-filled column it took 111s for 250k chunks locally. If
it fails, re-running it drops and rebuilds the index.

### Post-deploy commands

Fill `search_vector` for existing chunks. It commits every 1000 chunks, so it
doesn't lock the table, and skips chunks that already have one, so it is safe to
re-run. It logs progress every 50 batches. Locally it took 564s for 250k chunks
with the index in place, so expect about 16 minutes for 423k:

```bash
docker compose exec -T worker python - --wait --timeout 3600 maintenance backfill-search-vectors < tools/run_celery_task.py
```

Then reclaim the dead row versions the backfill leaves and refresh planner
statistics. `PARALLEL 0` because parallel vacuum workers need more shared
memory than Docker's default 64 MB `/dev/shm`. Safe to re-run:

```bash
docker compose exec -T postgres psql -U kb -d kb -c "VACUUM (ANALYZE, PARALLEL 0) chunk"
```

Optionally, delete orphaned chunk images now rather than at the next 04:35 run.
It only deletes files no chunk references and is safe to re-run. Unlinking
25,000 small files took 3s locally; chris has about 23,000 orphans holding
106 GB:

```bash
docker compose exec -T worker python - --wait maintenance clean-chunk-images < tools/run_celery_task.py
```

## v0.4.1 (2026-10-29)

### Changes

- Email senders and subjects are stored decoded (`Karolina Walęcik <k@far.ai>`,
  not `=?UTF-8?Q?...?=`). Email attachments are no longer tagged with their
  sender, and mail Qdrant payloads no longer copy the sender and recipients
  into `tags`: filter by `sender`/`recipients` instead.
- Search and fetch show a mail's body, not its raw RFC822 source.
- `notes_upsert` refuses filenames that don't end in `.md`, that have a hidden
  (dot-prefixed) segment, or that are absolute paths outside the notes folder;
  it no longer appends `.md` or re-roots absolute paths under the notes
  folder. Vault syncs skip hidden folders such as `.obsidian/`, and
  `notes_note_files` no longer lists them.
- Meeting action items resolve relative due dates against the meeting date
  instead of inventing absolute ones.
- Discord channel tools only reach channels in the servers of the caller's
  bots (admins see all).
- The Telemetry page becomes Usage, with LLM spend charts across memory's own
  calls, agent runs and Claude Code, and an admin-only audit log.
- Spend on memory's own LLM calls (summaries, search steps, meeting
  extraction, session summaries) records the user it was spent for, the model
  and the tokens. Deleting a provider key or a user keeps that spend as
  history; spend on a deleted key no longer counts toward any daily cap.

### Required env vars

None.

### Manual steps

None.

### Other changes for operators

Audit records (MCP mutating calls, settings changes and restarts) moved from
`telemetry_events` to a new admin-only `audit_events` table, readable at
`GET /audit`. They no longer show in `/telemetry/raw`, survive deleting their
user (the actor's email is kept), and OTLP ingest drops events with audit names.

The telemetry read endpoints moved under `/usage`: `/telemetry/raw`,
`/telemetry/metrics`, `/telemetry/sessions` and `/telemetry/api-keys` are now
`/usage/raw`, `/usage/metrics`, `/usage/sessions` and `/usage/api-keys`,
`/sessions/stats/tool-usage` is `/usage/tools`, and `/telemetry/users` is
removed. Scripts calling the old paths must switch. OTLP ingest is
unchanged (`/telemetry/ingest`, `/telemetry/v1/*`), so Claude Code's
`OTEL_EXPORTER_OTLP_ENDPOINT` needs no change. New `GET /usage` returns LLM cost
and tokens over time across memory's own calls, agent runs and Claude Code
telemetry, grouped by user, key, agent, model or kind. Every `/usage/*`
endpoint and `/audit` treat `to` as exclusive.

Purging agent runs past their retention now first adds their cost and tokens to
`llm_spend` (as `kind = 'run'`, bucketed by the hour the run was created), so
usage history outlives `AGENT_RUN_RETENTION_DAYS`. These rows don't count toward
daily caps. Runs purged before this release are not recovered.

A daily maintenance task (`purge_old_usage`, 04:05) deletes `telemetry_events`
and `llm_spend` rows older than `USAGE_RETENTION_DAYS` (default 365) and
`audit_events` rows older than `AUDIT_RETENTION_DAYS` (default 730). Its first
run deletes everything already past those windows; set the variables higher
beforehand to keep more.

### Migrations

`20261119_email_sender_tags` removes sender and raw-header tags from email
attachment rows. One UPDATE over email attachments.

`20261120_audit_events` creates `audit_events` and moves the `mcp.call`,
`settings.*` and `team.external_membership_snapshot` rows out of
`telemetry_events`: one INSERT and one DELETE over `telemetry_events` filtered
by name (indexed). Audit-named rows with an API key, session id or body were
uploaded through OTLP, not written by the server: they are deleted, not moved.
Downgrading moves the audit rows back, minus those whose user has since been
deleted.

`20261121_llm_spend_usage` adds those columns to `llm_spend`. Existing rows get no
user or model and zero tokens. The table holds one row per key, agent and hour,
so the migration is quick.

`20261122_llm_spend_run_kind` adds `llm_spend.kind`, puts it in the bucket index
and indexes `hour`. Quick, for the same reason. Downgrading deletes the folded
run rows.

### Post-deploy commands

Decode the sender and subject of stored mail and drop the sender, recipient and
raw-header tags from existing mail and attachment Qdrant payloads. It visits
every mail message and is safe to re-run:

```bash
docker compose exec -T worker python - maintenance clean-mail-headers < tools/run_celery_task.py
```

Delete notes indexed from hidden vault folders, Memory's `.md` copies of
non-markdown vault files (`*.json.md`, `*.js.md`, `*.css.md`, `.gitignore.md`)
whose original is still in the vault, and notes under `notes/app/memory_files/`
(created from absolute filenames) whose original exists, with their vectors.
Re-rooted notes without an original and notes whose file is gone are listed
under `needs_review` and kept. The deleted copies are also removed from the
vault (and committed if it's a git repo); files in hidden folders stay. Check the counts with `--dry-run` first. Safe to re-run:

```bash
docker compose exec -T worker python - --wait maintenance cleanup-junk-notes --dry-run < tools/run_celery_task.py
docker compose exec -T worker python - --wait maintenance cleanup-junk-notes < tools/run_celery_task.py
```

## v0.4.0 (2026-10-28)

### Changes

- Admins can set another user's timezone from the Edit user dialog in User
  management.
- The Telemetry page's "Token usage by tool" chart reads only
  `tool.token.usage` telemetry, like the rest of the page. It no longer falls
  back to re-parsing every stored session transcript when that window has no
  such events, which made the page slow and showed tool data while every other
  panel was empty.
- Session uploads that resend events the server already has no longer count
  their tool calls again; tool totals recorded before this release may be
  inflated.
- LessWrong comments are ingested and searchable, and the LessWrong sync is
  configured from the Forums tab (admin only) instead of code defaults.
  Comments carry their post's tags plus `comment`, so a tag filter can find
  them.
- Search takes an `exclude_tags` filter (MCP `search` and the search page's
  "Exclude tags" picker) that leaves out items carrying any of those tags,
  e.g. `["comment"]` for LessWrong posts without their comments.
- Polled sources (RSS feeds, email, GitHub, Google Drive, calendars, LessWrong)
  each sync on their own "Sync every" interval, edited on their Sources tab.
- RSS feeds have a "Wait before ingesting" delay (default 0): a new entry is
  fetched once that long has passed since it was published, e.g. for blogs
  whose paywall lifts after a few days. LessWrong has the same setting, seeded
  with the 8 hours it previously waited.
- API keys, login sessions, OAuth tokens and poll edit tokens are stored
  hashed. A key is shown only when it is created, and users list, create and
  revoke their own keys in Settings.
- An API key's scopes are capped by its user's, and work it sets up to run
  later acts with the key's scopes.
- Agent runs expire after their retention unless starred.
- `agent_run` takes the agent to run as a single `agent` argument: a
  definition's id or name, a prebuilt agent's slug (`fact-check`), a system
  agent's slug (`system:summarise`, ...) or an inline spec. Its
  `definition_id` and `spec` arguments are gone; MCP clients passing them must
  switch to `agent`. `agent_upsert` and inline specs accept `provider_key` as a
  key name as well as an id.
- Agents can start, wait for and cancel child runs.
- Scheduled Claude sessions accept the form's SSH key and dev channels choices.
- Removed: the Secrets page and `/secrets` API, with every stored secret.

### Required env vars

None.

### Manual steps

Stored secrets are deleted by this update, and the Secrets page can't show their
values afterwards: before updating, copy any you still need from the Secrets page.

The first nightly purge after the update deletes unstarred agent runs that
finished more than 14 days ago: star the ones you want to keep before updating.

### Other changes for operators

People now record who created them and whether that was ingest or an admin. A
new user is linked to an existing Person with their email only if ingest or an
admin made it; one a non-admin made is passed over, and when several Persons
share an email, ingest prefers the one linked to a user, then one ingest or an
admin made. People that existed before this release count as made by ingest, so
if a non-admin may have planted one with someone's email, check it before
creating that user.

An API key's scopes are now capped by its user's: a key never carries a scope
its user lacks (e.g. `*` minted for a non-admin, or kept after a demotion). A key
without its own scopes now acts exactly as its user's login session does,
including `read` and `write`. Creating a key with scopes its user lacks is
refused. Scheduled Claude sessions, scheduled notifications and agent runs set
up through an API key act with that key's scopes when they fire (a notification
whose key is gone pauses like a Claude session), and ingest triggers only see
items that key may read. Scheduled Claude sessions set up through a key without
`*` whose owner's default SSH key is a shared one now fail every fire: re-save
them with `ssh_key_id` set to a key of the owner's, or with `use_ssh_key` false.
`/auth/me` reports the credential's scopes, and `/setup/status` gives source
counts and MCP clients only to a login session.

`LESSWRONG_SYNC_INTERVAL` is no longer read. The migration copies it into the
new settings row (`0` there leaves the scheduled sync off), so keep it set until
the migration has run, then remove it. The migration reads it from the container
that runs migrations, so check the Forums tab afterwards if you set it only on
the workers. Afterwards change the schedule with
"Sync every" and "Sync on a schedule" on the Forums tab.

Polled sources (RSS feeds, email accounts, GitHub repos, Google Drive folders,
calendar accounts, LessWrong) are now each synced on their own "Sync every"
interval, edited on their Sources tab; beat checks every minute what is due.
These env vars are no longer read: `EMAIL_SYNC_INTERVAL`,
`ARTICLE_FEED_SYNC_INTERVAL`, `GITHUB_SYNC_INTERVAL`,
`GOOGLE_DRIVE_SYNC_INTERVAL`, `CALENDAR_SYNC_INTERVAL`, `LESSWRONG_SYNC_CHECK_INTERVAL`
(`0` there turns the LessWrong scheduled sync off in the migration). The migration raises each
row's interval to the env value (it used to be the floor), and gives email
accounts the `EMAIL_SYNC_INTERVAL` value, so keep them set until the migration
has run in the container that runs migrations, then remove them. Calendar
accounts set to sync every 15 minutes under the default 1-hour
`CALENDAR_SYNC_INTERVAL` become 60; lower them on the Calendar tab if wanted.
Turn a source's scheduled sync off with its pause button (the Forums tab's
"Sync on a schedule" for LessWrong).

The first syncs fetch the comments with karma 5 or more on each post from the
last year that has any, 200 posts per run by default, so expect extra LessWrong
traffic and embedding spend for the first few weeks. Lower "Max posts fetched
per sync" on the Forums tab to spread it out, or turn comments off. Because of
the karma floor, every older post due a recheck has its comments refetched, so a
comment that later reaches 5 gets in; set "Min comment karma" to 0 to store
every comment and refetch only when a post's comment count changes.

The Secrets page, the `/secrets` API and the `secrets` table are removed, and
every stored secret is deleted. Claude sessions take their GitHub tokens from
those saved in the Claude sessions form: users who kept their tokens as secrets
must re-enter them there. `github_token` and `github_token_write` on
`POST /claude/spawn` now take literal tokens only: a secret's name passed there
is injected into the session as the token itself.

Scheduled Claude sessions no longer accept `github_token` or
`github_token_write` in `spawn_config`; creating or updating a schedule with
either is rejected. Scheduled sessions get the owner's saved clone token, and
their saved write token when `use_saved_write_token` is set. The migration
removes both fields from existing schedules and sets `use_saved_write_token`
on those that had a write token, so they keep their write access through the
owner's saved write token. Owners without a saved token have to save one
before their schedules can clone or push.

API keys are stored as SHA-256 hashes. Existing keys keep working, but no key
can be shown again: a key is visible only when it is created, and the key
list shows its type prefix (only for a known key type) and last four
characters. The per-key Show button and
`POST /users/{id}/api-keys/{key_id}/reveal` are removed; lose a key and you
revoke it and create a new one. Users list, create (choosing the key type) and
revoke their own keys in Settings. Bot users no longer get an unused key on the
`users` row; give a bot a key with Generate key in User management.
`tools/add_user.py` now creates a real API key (it printed one that never
authenticated), and refuses an `--api-key` shorter than 32 characters.

Login sessions, OAuth access and refresh tokens and poll edit tokens are stored
as SHA-256 hashes too; existing ones keep working. Rotating an MCP OAuth refresh
token now also ends the access token issued with it, and logging out revokes
the refresh tokens issued alongside the session, as intended: they were never
linked before.

Agent runs now expire: a finished run that isn't starred is deleted, with its
transcript, after its agent's `retention_days`, else `AGENT_RUN_RETENTION_DAYS`
(default 14; at most 3650), and runs started by a starred run are kept with it.
A scheduled agent run whose run was deleted shows "run expired" in its history.
This replaces `AGENT_STEP_RETENTION_DAYS`, which is no longer read
(it deleted only transcripts, after 90 days, and kept run rows forever). The
first nightly purge after the update deletes every unstarred run that finished
more than 14 days ago; star the ones you want to keep before updating.

Agents can start runs: a spec that lists `agent_run` (and `agent_wait`,
`agent_cancel` to follow and stop its children) may submit runs. A child gets at
most its parent's scopes and tools and is capped by what the parent has left of
its budget (half of it, unless the child sets `max_cost_usd`), and runs nest at
most `AGENT_MAX_SPAWN_DEPTH` (default 2) deep. Cancelling a run, or the run
ending, cancels the runs it started that are still going.

### Migrations

`20261112_forum_comments`: adds the `forum_comment` and `forum_sync_configs`
tables (seeded with the previous defaults) and two columns on `forum_post`. Fast.

`20261113_person_creator` adds `creator_id` and `creator_trusted` to `people`;
existing people count as trusted. Fast.

`20261113_sync_schedule`: adds `sync_queued_at` to each polled source table,
`check_interval` to `email_accounts`, `ingest_delay` to `article_feeds` and
`forum_sync_configs`, and the `delayed_feed_items` table; renames
`article_feeds.last_checked_at` to `last_sync_at` and
`calendar_accounts.sync_past_days` to `days_back`. Fast.

`20261114_drop_secrets` drops the `secrets` table and removes the token fields
from scheduled Claude sessions as above. Downgrading recreates an empty table;
neither the secrets nor the schedules' token fields come back.

`20261115_hash_api_keys` replaces `api_keys.key` with `key_hash` and
`key_preview`, and drops `users.api_key` and the `user_has_auth_method` check.
Fast. Downgrading can't restore the keys: it revokes every API key, and each
has to be reissued.

`20261116_session_api_key` adds a nullable, indexed `api_key_id` to `user_sessions`. Fast.

`20261117_hash_session_tokens` replaces `user_sessions.id` (and
`oauth_refresh_tokens.access_token_session_id`) with the hash of the session
token, `oauth_refresh_tokens.token` with `token_hash`, and
`poll_responses.edit_token` with `edit_token_hash`. Fast. Downgrading can't
restore the tokens: everyone is logged out, MCP clients have to authorize again
and poll edit links stop working.

`20261118_run_ledger` adds `agent` and `executor` to `agent_runs`, moves the
prebuilt agent slug out of `trigger_ref`, indexes `parent_run_id` and
`finished_at`, and drops `steps_purged_at`. Fast.

### Post-deploy commands

None.

## v0.3.3 (2026-10-28)

### Changes

- The nightly Qdrant snapshot no longer gets Qdrant OOM-killed once the vectors
  pass about 1.5 GB.

### Required env vars

None.

### Manual steps

None.

### Other changes for operators

Qdrant snapshots now go to the `qdrant_snapshots` volume instead of a tmpfs. The
tmpfs counted against Qdrant's 2G memory limit, so the nightly full snapshot got
Qdrant OOM-killed once the vectors grew past about 1.5 GB.

### Migrations

None.

### Post-deploy commands

None.

## v0.3.2 (2026-10-26)

### Changes

- Admins can set and clear shared LLM default keys on the keys page, one per
  provider. System agents fall back to them when a user has no usable personal
  default; usage shares the key's daily cap, and custom agent runs still
  require personal keys. Existing keys remain personal-only.
- Deleting a user cleans up their source accounts and OAuth refresh tokens
  instead of failing on database ownership constraints.
- The notes sync interval is set under Sources → Notes instead of Server
  settings.
- Removed: sharing a Discord bot with other users. Bots belong to their
  registrant.

### Required env vars

None.

### Manual steps

None.

### Other changes for operators

The notes sync interval is now configured under Sources → Notes instead of Server
settings. Existing values and environment overrides are preserved.

Discord bot user sharing is removed from Sources and the API. Bots belong to
their recorded registrant; additional shared users no longer have bot access.
Discord member identities, messages, and Person attribution are preserved.
The Discord setup CLI uses `set-owner` in place of `authorize-user`.

### Migrations

`20261111_system_default_keys` adds an opt-in shared-default flag and unique
index per provider to LLM keys. Existing keys remain personal-only.

`20261110_drop_discord_bot_users` removes the sharing table. Bots without a
registrant retain the lowest-ID remaining shared user as their owner. Bots with
no registrant or shared user remain unowned. Downgrading restores owner entries
only, not the former sharing grants.

### Post-deploy commands

None.

## v0.3.1 (2026-10-26)

### Changes

- GitHub App repository sync works in the worker image again.

### Required env vars

None.

### Manual steps

None.

### Other changes for operators

- Declare PyJWT with RSA signing support in shared dependencies so GitHub App
  repository sync works in worker images. Worker builds check that RS256 is available.
  Rebuild and recreate the worker image to apply the fix.

### Migrations

None.

### Post-deploy commands

None.

## v0.3.0 (2026-10-26)

### Changes

- Slack workspaces with a verified Events setup ingest from pushed events, with
  a daily history reconciliation instead of polling.
- Search unwraps the server's result list correctly, displaying individual
  matches and accurate counts instead of one empty "Untitled" result. Relevant
  sections display the text returned by the search service.
- Job dispatch, retries, and reingestion commit their pending record and task
  ID before publishing to Celery. Fast workers can now update job status
  reliably, avoiding completed Notes syncs remaining marked as queued.
- Calendar events can be refreshed after ingestion.
- Discord channel lists send the supported server filter, fixing the
  unexpected server_id argument error when expanding a server in Sources.
- Google account setup includes expandable step-by-step instructions for
  project configuration, API enablement, OAuth consent, and downloading the
  credentials JSON.

### Required env vars

None.

### Manual steps

None.

### Other changes for operators

- Verified Slack Events installations use daily history reconciliation by default.
  Change the interval under Sources → Slack → Background sweep frequency.
  Unverified installations retain polling; initial imports and Sync now still run immediately.
- Slack history tasks use a dedicated `slack-sync-worker` (one slot, 1 GiB limit),
  keeping rate-limit waits out of other ingestion. Custom worker deployments must
  consume the new `slack-sync` queue. Already queued tasks stay on their original queue.

### Migrations

- `20261109_slack_reconcile`: adds the per-workspace background sweep interval.

### Post-deploy commands

None.

## v0.2.6 (2026-10-26)

### Changes

- Notes initialization adopts the remote's existing history and default branch
  before committing local notes. Overlapping local files are preserved and
  reported so setup can be retried after reconciliation.
- Notes sync distinguishes unrelated histories, rejected write access, branch
  rules, and concurrent remote changes in its failure messages.
- GitHub account setup shows instructions for the selected authentication
  method, including read permissions and installation repository access.
- Manage repos offers Refresh from GitHub to bypass cached discovery after
  installation permissions or repository access change.

### Required env vars

None.

### Manual steps

Existing vaults with independent local and remote histories still require manual
reconciliation; the update does not rewrite either history.

### Other changes for operators

None.

### Migrations

None.

### Post-deploy commands

None.

## v0.2.5 (2026-10-26)

### Changes

- Google Calendar fetch and queue failures appear on the calendar account
  instead of being reported as successful empty syncs.
- Gmail source settings fetch available labels and allow selecting folders to
  sync. Multiple labels sync their combined messages without duplicates. Empty
  selections retain the existing INBOX default. Gmail listing failures surface
  as sync errors and stop deletion tracking instead of treating failed
  listings as empty.

### Required env vars

None.

### Manual steps

None.

### Other changes for operators

None.

### Migrations

None.

### Post-deploy commands

None.

## v0.2.4 (2026-10-25)

### Changes

- The frontend image builds again: release sources include the Secrets page,
  which v0.2.3 left out.
- EC2 updates no longer fail on a user-owned maintenance lock.

### Required env vars

None.

### Manual steps

None for new AWS instances. If an existing instance cannot update because
`/run/lock/memory-nightly.lock` is user-owned, run
`sudo chown root:root /run/lock/memory-nightly.lock` and `sudo chmod 600 /run/lock/memory-nightly.lock`,
then retry with sudo. Do not delete the lock file.

### Other changes for operators

Release sources include the Secrets page and its tests. Release validation now
builds the frontend from a Git archive, so ignored local files cannot hide
missing source files. This fixes the v0.2.3 frontend image build failure.

AWS host configuration creates the maintenance lock as root and restores it on
every boot. Configuration also repairs existing ownership without replacing the
lock inode. Maintenance scripts reject unprivileged use of the default system
lock before creating it, with an instruction to use sudo.

### Migrations

None.

### Post-deploy commands

None. AWS bootstrap and nightly updates apply the host configuration automatically.

## v0.2.3 (2026-10-25)

### Changes

- Ingestion overview has a dedicated page at `/ui/ingestion`, linked from the
  sidebar and dashboard.
- SSH keys has a dedicated page under System operations (`/ui/ssh-keys`), to
  import, generate, replace and delete personal and instance keys. Existing
  Sources links redirect to it.
- Notes SSH sync uses those keys, and can probe an SSH server and show its
  host-key fingerprints for explicit confirmation.
- The dashboard shows access-scoped ingested item totals, indexing status,
  additions in the last 24 hours, and counts by content type.
- Secrets has its own page at `/ui/secrets`; old Sources links redirect there.
- Search filters load correctly when there are no searchable tags.
- GitHub App authentication encodes the JWT issuer as a string, fixing
  “Issuer (iss) must be a string” errors when managing or syncing
  repositories.
- Qdrant no longer stops accepting connections during heavy ingestion.
- Security: credential management needs an interactive login, API keys are
  checked against their own scopes, IMAP verifies TLS, ingestion downloads
  refuse non-public addresses, and snapshot uploads are size-capped.

### Required env vars

None.

### Manual steps

For Notes SSH sync using an operator-mounted key, import that key in System operations → SSH keys, then select it in Sources → Notes and configure trusted host keys. Notes SSH sync now requires a database key.

### Other changes for operators

- Qdrant's open-file limit is raised to 65536. Under Docker's default of 1024,
  ingestion exhausted it and qdrant stopped accepting connections. Takes effect when
  `docker compose up -d` recreates the qdrant container.
- Notes can probe an SSH server and show its host-key fingerprints for explicit
  confirmation. Manual host-key entry is under Advanced. The API image includes
  `openssh-client`; rebuild it to enable probing. Probing does not authenticate or
  save trust until confirmed.
- System operations → SSH keys manages encrypted personal and instance SSH identities:
  import a private key (including passphrase-protected files), generate, replace,
  delete, or choose a container default. Notes selects an accessible personal or instance key and stores
  remote-bound host trust separately. Claude sessions and MCP public-key discovery
  use the shared table. Private keys are write-only. Notes no longer falls back to operator-mounted keys. Requires the existing `SECRETS_ENCRYPTION_KEY`.
- A `SERVER_URL` change no longer needs an image rebuild: the web UI uses
  same-origin URLs instead of a baked-in host.
- The server's own `SERVER_URL` origin is now always allowed as an OAuth redirect
  target, so `OAUTH_REDIRECT_URI_ALLOWLIST` is only needed for other origins.
- REST account/key recovery, secret management/export, and source credential
  management now require an interactive login session. API keys use their own
  scopes for access checks; general content/job reads require `read` and
  mutations require `write`. Ownerless jobs are visible only to admins.
- IMAP verifies TLS certificates and hostnames. Accounts with `use_ssl=false`
  must support STARTTLS; plaintext login is no longer supported. For private
  certificate authorities, install the CA in the worker's trust store or set
  Python's `SSL_CERT_FILE` to a PEM trust bundle containing the required roots.
- Ingestion downloads validate the address actually dialed, ignore proxy
  environment variables, and refuse non-public destinations. Servers must honor
  `Accept-Encoding: identity`; individual responses are capped at 100 MiB,
  with smaller existing caller limits still enforced.
- Snapshot uploads reject archives above 100 MiB compressed, 512 MiB expanded,
  or 10,000 members. Archive parsing runs outside the API event loop.

### Migrations

- `20261108_ssh_keys` moves existing users’ encrypted SSH keys into `ssh_keys`
  and selects them as defaults, without decrypting or changing the ciphertext.
  Adds Notes SSH configuration and removes the old user key columns. No content
  tables are rewritten. Downgrading preserves only each user’s default key;
  additional keys and Notes host-trust configuration are lost.

### Post-deploy commands

None.

## v0.2.2 (2026-10-25)

### Changes

- Notes Git credentials are read fresh and bound to the selected repository,
  preventing token leakage while queued remote changes and worker caches
  differ. Restricted API keys no longer inherit their owner's admin access
  when reading the Notes source configuration or note count.
- Admins can change the Notes vault's remote and branch from Sources, with
  explicit confirmation before uploading history to a new destination. Remote
  changes clear the saved token unless a replacement is supplied.
- Creating People with email, Slack, GitHub or Discord identity fields requires
  admin rights, matching the restriction on editing those fields. Ambiguous
  name and email matches no longer select an arbitrary Person.
- Identical agent observations from different users retain separate copies.
  Repeated submissions still reuse the same user's existing observation,
  including observations stored before this fix. Previously discarded
  submissions must be submitted again; their content cannot be recovered from
  the deduplicated row.
- Note submissions that match another user's stored content enforce ownership
  before returning duplicate metadata or recreating a missing vault file.
- A new EC2 instance is claimed in the web UI with the setup token instead of
  creating the first user itself.
- EC2 installs: SSM runs produce output, re-running a failed first install
  picks up fixes, the whole stack starts, and images live on the data volume.
- `tools/install.sh` and `tools/update.sh` pin `COMPOSE_PROJECT_NAME`, so a
  checkout reached by another path keeps its stack.

### Required env vars

None.

### Manual steps

- Re-save any Notes Git token previously stored through the UI. Stored tokens
  now require an explicit repository binding; unbound legacy tokens are not
  sent. Operator-provided `NOTES_GIT_TOKEN` is unchanged.
- `deploy/aws`: the `admin_email` variable is gone. Remove it from your
  tfvars (Terraform warns about an undeclared variable). The apply deletes the `admin_email` and
  `admin_password` SSM parameters and outputs; the existing admin account and
  its password are unaffected.
- EC2: re-apply Terraform so the SSM documents run their scripts through
  `systemd-run`. Run through the SSM agent, the snap AWS CLI's output to a
  file comes out empty, which broke bootstrap re-runs and the repair actions.
  An SSM run that times out now reports TimedOut while the script keeps running
  until it next writes output.
- EC2, an instance whose first install failed: the apply doesn't touch user
  data, so it keeps its old `memory-bootstrap`, which neither of the changes
  below reaches. Follow "Recovering a failed first install on an older
  instance" in deploy/aws/README.md: check out the newest signed release with
  `select_release.sh`, then run
  `sudo systemd-run --wait --pipe --collect /usr/local/sbin/memory-bootstrap`.
- EC2, existing instances: Docker's containerd image store keeps image layers
  on the 30 GB root disk, not the data volume, and the worker image can fill
  it. Follow "Moving images off the root disk" in deploy/aws/README.md:
  `docker compose down` (no `-v`), prune images and build cache, add
  `"features": {"containerd-snapshotter": false}` to `/etc/docker/daemon.json`,
  restart docker, `docker compose up -d`. The stack is down while the images
  rebuild. New instances get this from bootstrap.

### Other changes for operators

- An operator-configured Notes token (`NOTES_GIT_TOKEN`) must be removed
  before changing the vault's remote from Sources.
- EC2: the nightly update prunes build cache older than a week. It leaves
  images alone; `disk-cleanup` prunes those.
- EC2, instances created from this release on: `sudo memory-bootstrap` runs
  itself in a transient systemd unit. Until an install has succeeded, a re-run
  moves to the newest signed release, or keeps the checkout when it is already
  that release, so re-running after a failed first install picks up a fix.
- `tools/install.sh --non-interactive` builds the images before migrating, so a
  re-run on a newer checkout no longer starts the old ones. It now ends with the
  whole stack up; before, a first install (and so the EC2 bootstrap) started
  only the api, leaving the workers and ingest-hub down.
- A new EC2 instance no longer creates the first user. Create it at
  `/ui/setup` with the setup token, read over Session Manager
  ([deploy/aws/README.md](deploy/aws/README.md#first-login)).
- `tools/install.sh` and `tools/update.sh` write `COMPOSE_PROJECT_NAME` to
  `.env` when it is missing, so compose stays on the same project (and its
  volumes) whichever path the checkout is reached by: on EC2, a run from
  `/srv/memory/app` instead of `/opt/memory` built a second, empty stack.
  A fresh install (no `.env` yet) takes the directory name as invoked. On an
  existing install, the name is the single project whose containers run from
  the checkout, and only if it owns a `<project>_db_data` volume or no other
  project does. Otherwise, or when `docker` can't be asked (e.g. podman), the
  scripts stop and ask you to set `COMPOSE_PROJECT_NAME` in `.env` by hand. A
  `-p` in `COMPOSE` still wins over `.env`.

### Migrations

None.

### Post-deploy commands

None.

## v0.2.1 (2026-10-25)

### Changes

- The api image contains `tools/add_user.py`, so `tools/install.sh` and the
  EC2 bootstrap can create the first admin. v0.2.0 failed at that step.

### Required env vars

None.

### Manual steps

None.

### Other changes for operators

None.

### Migrations

None.

### Post-deploy commands

None.

## v0.2.0 (2026-10-25)

### Changes

- Optional modules (Claude containers, multiple users, teams), off by default
  and switched on the Server settings page.
- First-run setup wizard at `/ui/setup`.
- Every LLM call runs a system agent on a user's own Anthropic or OpenAI key;
  the server's LLM keys are gone.
- Agents: definitions, runs, schedules, ingest triggers and external MCP
  servers, run by a new `agents-worker`.
- Notes are a Sources tab, with git sync set up in the UI.
- Transcript accounts accept pushed transcripts, and a push-only `generic`
  provider needs no API key.
- Password reset by emailed link.
- Per-user timezones; cron schedules follow their owner's local time.
- Search returns each item's summary; HyDE is off by default.
- Synced items are readable by the owner of their account, and meetings by
  their transcript account's project.
- Signed release tags, repair scripts in `tools/ops/`, key rotation and an EC2
  bootstrap.
- Removed: the Check queue, one-time API keys, the GitHub MCP server, the
  Discord management tools, and team sync with Discord roles and GitHub teams.

### Required env vars

- **Modules: Claude containers, multiple users and teams are now off by
  default. Read this before updating.** Three optional modules are turned on
  on the Server settings page, or by the operator with `ENABLE_MODULES` and
  `DISABLE_MODULES` (comma-separated names, in `.env`; a change needs a
  restart), which lock the modules they name on the page:

  | Module | Turns on |
  |---|---|
  | `claude_containers` | Claude Code containers: the `/claude` routes, their MCP tools, the `claude_session` scheduled-task type, the `claude_sessions` scope, the Claude sessions and configurations pages. Needs the compose overlay below |
  | `multi_user` | More than one human user. Off, there is one human, `tools/add_user.py` creates them with `*`, and `POST /users` refuses a second human (bot users and API keys are unaffected). The api logs a warning at startup if the only human lacks `*` |
  | `teams` | Teams, their members, and projects: the `teams` and `projects` MCP servers, the four `teams`/`projects` scopes, and the team and project pickers |

  To keep what you had, add to `.env` (drop the names you don't use, and the
  `COMPOSE_FILE` line if you don't run Claude containers):

  ```
  ENABLE_MODULES=claude_containers,multi_user,teams
  COMPOSE_FILE=docker-compose.yaml:docker-compose.claude.yaml
  ```

  If `.env` already has a `COMPOSE_FILE` line (`tools/install.sh
  --non-interactive` writes
  `COMPOSE_FILE=docker-compose.yaml:deploy/aws/compose.proxy.yaml`), append
  `:docker-compose.claude.yaml` to it instead of adding a second one, e.g.
  `COMPOSE_FILE=docker-compose.yaml:deploy/aws/compose.proxy.yaml:docker-compose.claude.yaml`.
  Replacing it drops the proxy overlay silently: the api stops trusting the
  proxy's forwarded headers and rate limits key on the proxy's address.
  A `docker-compose.override.yml` is no longer read once `COMPOSE_FILE` is
  set, so list it too if you have one.

  Then `docker compose up -d`. What stops a process from starting:
  - The api refuses to start while `multi_user` is off and several human users
    exist ("N human users exist; set ENABLE_MODULES=multi_user or remove users").
  - A misspelled module name, or one listed in both `ENABLE_MODULES` and
    `DISABLE_MODULES`, stops every process, with the offending names in the message.
  - With `claude_containers` on, the api refuses to start without
    `ORCHESTRATOR_SOCKET`, which the overlay sets.

  **On the settings page.** A module the env doesn't name is a toggle under
  Modules on `/ui/server-settings` (and in the setup wizard), applied when the
  api restarts from the page's banner; the separate `/ui/modules` page and
  `GET /modules` are gone. The Instance settings page is now Server settings,
  at `/ui/server-settings`; the old `/ui/instance-settings` URL doesn't
  redirect. The page refuses `claude_containers` without `ORCHESTRATOR_SOCKET`
  and `multi_user` off while several humans exist. If the
  env changes under a saved value, the api logs `Ignoring the stored value of
  module_<name>` at ERROR, boots with the module the other way and the page
  flags the row, rather than refusing to start. The api records the modules it
  booted with (a `running_modules` row in `instance_settings`), and workers
  follow that record, not the saved values: due `claude_session` tasks wait
  until the api runs the module, and the agents worker mounts or hides a
  module's MCP servers at the start of each agent task. The api now waits for
  a healthy Postgres (`depends_on: condition: service_healthy`).
  The MCP server checklist no longer lists `teams`, `projects` or `containers`:
  they go with their module.

  **Compose overlay.** The Claude container wiring (the orchestrator socket
  mount, `group_add` with `DOCKER_GID`, the external `memory-api-<MEMORY_STACK>`
  network and its alias, and `ORCHESTRATOR_SOCKET`) left `docker-compose.yaml`
  for `docker-compose.claude.yaml`, enabled by the `COMPOSE_FILE` line above.
  Stacks that start compose with explicit `-f` flags add
  `-f docker-compose.claude.yaml` after the base file. Without the overlay, a
  fresh `docker compose up` no longer needs the `memory-api-<stack>` network;
  `tools/install.sh` and `deploy/aws/bootstrap.sh` create it only when
  `COMPOSE_FILE` lists the overlay.

  **What off means.** The routes return 404 and leave OpenAPI, the MCP servers
  are not mounted (their tools leave `tools/list`, agent catalogs and
  in-process agent runs, and an agent definition that allowlists one is
  reported as unavailable), and the scopes leave the pickers and
  `/users/scopes`. Nothing is migrated or deleted: rows, scope grants and
  `project_id`s stay, and turning a module back on restores the surfaces.
  Access control is unchanged by modules, and core features (search, ingest,
  people, notes, reports, journal, tasks, polls, agents, keys, transcript
  search) never depend on them. The enabled set is returned to logged-in users
  with the user payload (`/auth/me`), not in `/health`. Only the module flags
  unmount a server; a server disabled on the settings page is hidden and
  refused live, as before.

  **MCP tool renames.** The `claude` MCP server is split. Transcript search
  (`claude_session_search`, `claude_session_fetch`) stays core. The file
  transfer tools moved to the `claude_containers` module under new names:
  `claude_session_list` is now `containers_list_sessions`,
  `claude_session_list_dir` is `containers_list_dir`, `claude_session_pull_url`
  is `containers_pull_url` and `claude_session_push_url` is `containers_push_url`.
  Update MCP clients, skills and agent allowlists that name the old ones.

  **Leftovers when `claude_containers` is off.** Due `claude_session`
  scheduled tasks are skipped (no execution row, the next run of a cron task
  advances, a one-off task keeps its time and runs once the module is back; one
  log line) and the scheduler MCP refuses to create new ones. Executions already
  pending or running when the module goes off are not cancelled: pending ones
  fail on their next attempt, running ones wait for the stale-execution timeout
  (2 hours). Running containers are not killed, because the orchestrator is
  external. In the agents worker (which has no `ORCHESTRATOR_SOCKET`)
  `containers_list_sessions` and `containers_list_dir` fail as before.

- **Required action if you relied on the old backup defaults.** S3 backups no
  longer default to a bucket: they run only when `S3_BACKUP_BUCKET` is set, and
  `S3_BACKUP_ENABLED` is gone. An instance that had `BACKUP_ENCRYPTION_KEY` set
  and nothing else was backing up to bucket `equistamp-memory-backup` under
  prefix `Daniel`; after this update it stops backing up silently. To keep
  backing up, set `S3_BACKUP_BUCKET` and `S3_BACKUP_PREFIX` in `.env` (the old
  values, if you want your existing backups to stay in the same place) and keep
  `BACKUP_ENCRYPTION_KEY`; setting a bucket without the key makes the
  workers refuse to start. If you had
  `S3_BACKUP_ENABLED=false` to keep backups off, remove it and unset
  `S3_BACKUP_BUCKET`: the workers refuse to start while `S3_BACKUP_ENABLED` is set,
  and a bucket alone turns backups on. If you had a bucket set without
  `BACKUP_ENCRYPTION_KEY` (backups were off), either add the key or unset the
  bucket, or the workers refuse to start. To check: `grep S3_BACKUP .env` should show
  `S3_BACKUP_BUCKET`; while it is unset the `backup-all` task logs
  "S3 backup is disabled". `S3_BACKUP_PREFIX` now defaults to
  `memory` (was `Daniel`) and `tools/backup_databases.sh` and
  `tools/restore_*` also have no default bucket. New installs choose explicitly:
  `tools/install.sh` asks, and `--non-interactive` needs `S3_BACKUP_BUCKET` or
  `S3_BACKUP=off`.

- `SECRETS_ENCRYPTION_KEY` must be set to register external MCP servers (their
  tokens, client secrets and headers are encrypted with it). Registration fails
  with a clear error without it; nothing else changes.
- The api and workers now check settings at startup and refuse to start,
  listing everything that's missing. `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`
  are no longer read.

### Manual steps

- Add your own Anthropic or OpenAI key under LLM API keys before or right after
  updating, or ingest stops summarising and search drops HyDE and query
  analysis. Claude sessions of users without a default key stop being
  summarised. System agents run on Claude Haiku with an Anthropic key, else on
  `gpt-6-luna` with an OpenAI key. Summary batching (`SUMMARIZER_USE_BATCH`)
  uses the key's provider: Anthropic's Message Batches API or OpenAI's Batch
  API, both at half price. OpenAI batches upload a JSONL file to the key's
  account, deleted once its results are collected (files of batches never
  collected expire after 30 days). A key whose batch OpenAI refuses for its
  queue limit gets smaller batches until they succeed. OpenAI batches can take
  hours, so each OpenAI key has one batch in flight at a time, of up to 50,000
  requests (`SUMMARIZER_BATCH_MAX_REQUESTS` applies to Anthropic batches), and
  a batch only takes as many requests as the key's daily pool could pay for.
- **Before updating**, drain the summary queue: the migration drops every
  queued summary request, since they were queued on the server's key. Set
  `SUMMARIZER_USE_BATCH=false` on the running version and restart its workers,
  so ingest stops queueing new requests, then let in-flight batches finish
  (`collect_summary_batches` runs every `SUMMARIZER_BATCH_POLL_INTERVAL`). The
  queue is drained once
  `SELECT count(*) FROM summary_request WHERE status != 'failed'` is 0. Restore
  `SUMMARIZER_USE_BATCH` when updating. If you can't wait, list the items that
  will lose their summary with
  `SELECT DISTINCT s.id, s.type FROM summary_request r JOIN source_item s ON s.id = r.source_id WHERE r.status != 'failed'`
  and, once updated, reingest each with
  `python tools/run_celery_task.py maintenance reingest-item --item-id <id> --item-type <type>`.
- **Before updating**, check each transcript account's project and
  sensitivity (Sources → Transcripts). Once updated, newly synced meetings and,
  from the next daily sweep, existing ones take them on: every contributor on that
  project will see every meeting synced from it, and its action items, and a
  `public` account's meetings are visible to every user. Raise the sensitivity
  (`internal` for leads, `confidential` for admins), or move the account to a
  narrower project, wherever that is too wide.
- External MCP servers must be registered again (Agents → MCP servers): the
  migration drops the old `mcp_servers` and `mcp_server_assignments` tables,
  whose tokens were stored in plaintext.
- The OAuth redirect URI for external MCP servers is now
  `{SERVER_URL}/auth/mcp/callback`; `/auth/callback/discord` is gone. Update any
  OAuth app you registered by hand with the old URI.
- **Before updating, check that no user is linked to more than one Person.**
  The `20261106_unique_person_user` migration refuses to run if one is, and
  `tools/update.sh` has stopped the api by then. On the running old version:

  ```
  docker compose exec postgres psql -U kb kb -c "SELECT user_id, array_agg(id) AS person_ids, array_agg(identifier) FROM people WHERE user_id IS NOT NULL GROUP BY user_id HAVING count(*) > 1"
  ```

  For each row, either merge those people with the `people_merge` MCP tool
  (still on the old version), or keep one link and clear the others:

  ```
  docker compose exec postgres psql -U kb kb -c "UPDATE people SET user_id = NULL WHERE id IN (<person ids to unlink>)"
  ```

  If an update already failed on this migration, run the rollback commands
  `tools/update.sh` printed, clear the duplicates as above, then run
  `tools/update.sh` again.
- **Optional, before updating: note Discord accounts linked straight to a
  user.** `20261107_drop_discord_user_link` drops that link; a Discord
  account now reaches a user only through its Person. Non-admin bot owners
  could set the old link on any account their bot had seen, so it is not
  copied over automatically. To list the accounts that would lose their only
  link, on the running old version:

  ```
  docker compose exec postgres psql -U kb kb -c "SELECT d.id, d.username, d.system_user_id, p.identifier AS users_person FROM discord_users d LEFT JOIN people p ON p.user_id = d.system_user_id WHERE d.system_user_id IS NOT NULL AND d.person_id IS NULL"
  ```

  After updating, an admin can link the ones you trust to the user's Person
  with `people_upsert`'s `contact_info.discord`.

### Other changes for operators

- Notes are a Sources tab. Admins set up the vault's git sync there (remote,
  commit author, and an access token for an https remote) and can sync it now;
  the `notes setup-git-notes` command of `tools/run_celery_task.py` is gone. A
  vault that is already a repository keeps its history and remote. New optional
  settings: `NOTES_GIT_TOKEN` (or `NOTES_GIT_TOKEN_FILE`) locks the token, and
  `NOTES_SYNC_INTERVAL` now locks the interval, which is otherwise set on the
  Server settings page and applies without a restart. It must be 60 to 86400
  seconds, or the api and workers refuse to start. Beat's `sync-notes-changes`
  entry is replaced by a minutely `sync-notes-when-due` check. `SSH_PUBLIC_KEY_FILE`
  (default `/run/secrets/ssh_public_key`) is the key the tab shows for an ssh
  remote.
- First-run setup wizard. While no human account exists, the web UI sends
  visitors to `/ui/setup`, where the first account is created as an admin.
  Creating it needs a setup token that the api writes to its log at startup
  (`docker compose logs api | grep "setup token"`); a new token is made on each
  restart until the account exists. The wizard then walks the new admin through
  LLM keys, the Voyage key, modules, a first source, connecting an AI client,
  and their timezone. It runs
  once: an instance that already has an account never shows it.
  `POST /setup/account` returns 404 once any human account exists.
- `tools/install.sh` no longer asks about modules or writes `ENABLE_MODULES`:
  modules are switched in the setup wizard and on the Server settings page. It
  asks only whether to wire up Claude Code containers (`CLAUDE_CONTAINERS=on`
  with `--non-interactive`; default off): then it detects `DOCKER_GID`, appends
  `docker-compose.claude.yaml` to `COMPOSE_FILE` and creates the
  `memory-api-<stack>` network, and the admin turns the module on in the UI. A
  re-run keeps an existing `ENABLE_MODULES` line and an already wired overlay.
- `tools/install.sh --non-interactive` no longer requires `ADMIN_EMAIL` and
  `ADMIN_PASSWORD` (set both or neither). Without them it creates no user and
  prints the setup token for `/ui/setup`. It now runs `docker compose run --rm
  migrate` before starting the api, so creating the admin no longer meets an
  empty schema on a first install. The interactive installer's next steps say
  the same.

- One-time API keys are removed: the `meta_create_one_time_key` MCP tool, the
  `one_time` key type, the `ONE_TIME_KEY_RATE_LIMIT` and `ONE_TIME_KEY_TTL_SECONDS`
  settings (delete them from `.env`) and the `cleanup-used-one-time-keys` beat
  task. Existing one-time keys are deleted by the migration and stop working.
  Anything that minted them, such as a script calling `/claude/spawn`,
  needs a normal API key instead.
- Each user has a timezone (default `UTC`), set in User settings or through
  `PATCH /users/{id}`. Cron schedules, both scheduled tasks and agent schedules,
  run on their owner's local time, following DST; changing a timezone moves
  the user's pending cron runs.
- `VOYAGE_API_KEY` is no longer required. Without it the api and workers start,
  search is keyword-only (no reranking or HyDE), and new items are stored with
  `embed_status = FAILED` until an admin sets the key; saving it queues
  `reingest-all-empty-source-items` to embed them. `secrets/voyage_key.txt` must
  still exist (compose needs it), and may be empty: `tools/install.sh` now always
  writes it, makes the Voyage prompt skippable (also with `--non-interactive`),
  and no longer copies the key into `.env`. EC2: `voyage_api_key` is optional in
  the Terraform; leave it out to enter the key in the web UI.
- New admin settings API (`GET`/`PATCH /settings`, `POST /settings/restart`) for
  the Voyage key, the disabled MCP servers and the search `ENABLE_*` switches. A
  value set in the env still wins and is read-only there; to hand a setting to
  the UI, remove it from `.env` (or empty its secret file) and restart. Secrets
  set there are encrypted with `SECRETS_ENCRYPTION_KEY`, and `rotate-keys.sh`
  re-encrypts them. A server disabled on the page hides its tools at once, in
  the api and in agent runs.
  The compose api service now sets `API_SELF_RESTART=1`, which lets an admin
  restart it from the page for restart-bound settings (it exits, and
  `restart: unless-stopped` brings it back). The compose
  file no longer sets `ENABLE_BM25_SEARCH` (it was the default), so it can be
  changed in the UI. `/health` reports a `boot_id`.
- `DISABLED_MCP_SERVERS` is removed and ignored. Servers it listed are mounted
  again; switch them off on the Server settings page (this hides their tools and
  refuses calls, but does not unmount them). To unmount a server's code, turn
  off its module.
- The Check queue is removed: the `check_*` MCP tools, the `/check` HTTP
  endpoints, the `check` scope and the Check page. Fact-checking runs on the
  prebuilt fact-check agent instead: `agent_run(input=..., agent="fact-check")`
  then `agent_wait`, paid with the caller's own provider key (it needs the
  `agents` scope). The external `checker` Firefox extension and laptop worker
  stop working. Tokens and users still carrying `check` keep working and the
  value is ignored; asking for it in a new grant or key is rejected as an
  invalid scope. The `CHECK_*` variables can be dropped from `.env`. Leftover
  `check:*` Redis keys expire on their own within 14 days. No migration.
- The Discord collector no longer serves its channel, category and permission
  endpoints (the collector API keeps only health, DMs, channel messages,
  reactions and metadata refresh). The `discord-admin` and
  `discord-admin:write` scopes are removed; tokens and users still carrying
  them keep working, the values are ignored and no longer shown in the users API.
- Teams no longer link to Discord roles or GitHub teams. `teams_upsert` drops
  `guild`, `discord_role`, `auto_sync_discord`, `github_org`,
  `github_team_slug`, `auto_sync_github` and `force_empty`; adding or removing a
  team member, and creating or updating a GitHub-backed project, no longer
  touches Discord roles, GitHub team membership or GitHub repo access. Existing
  Discord roles and GitHub teams are left as they are. MCP clients that read
  them lose the response keys `discord_sync` and `github_sync` (`teams_upsert`),
  `sync` (`teams_team_add_member`/`teams_team_remove_member`), and
  `github_team_sync` and `teams_from_github` (`projects_upsert`). The Discord
  collector's role endpoints (`/guilds/{id}/roles`, `/roles/add_member`,
  `/roles/remove_member`) are gone.
- **Narrower visibility for new repo projects.** Creating a project for a
  GitHub repo no longer adds the local teams whose GitHub team had access to
  that repo. The project gets only the teams passed in, so it can be visible
  to fewer people than before; assign teams explicitly.
- `people_upsert`: `replace_aliases` is gone. A list of aliases is always
  added, and an alias is removed by mapping it to null
  (`aliases={"@old": null}`). In `contact_info`, a null removes a key, lists
  gain entries instead of being replaced (remove one entry with
  `{"discord": {"old_handle": null}}`), and a shape change that would drop data
  (e.g. a string replacing a dict) is refused. A new scalar still replaces the
  old one: `{"email": "new@x"}` overwrites the old email.
- `people_merge` keeps both people's contact info: values that differ become
  a list (primary's first) instead of the primary's value winning. Email,
  GitHub and Slack lookups match any value in such a list; notifications reach
  every merged Slack/GitHub account, and single-value uses (email target,
  Discord suggestion) take the primary's. The people edit form only sends
  contact fields that changed.
- `tools/ops/rotate-keys.sh --yes [--only=NAME,...]` rotates the keys Memory
  generates: `SECRETS_ENCRYPTION_KEY` (re-encrypting the stored secrets),
  `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `BACKUP_ENCRYPTION_KEY` and an
  explicitly set `TRANSFER_TOKEN_SECRET`. It keeps a fresh dump, stops the app,
  writes the new values (and on EC2 the SSM parameters), recreates the
  containers and puts the old values back if anything fails. It prints names and
  counts only. The backup and secrets keys backups need stay in
  `secrets/previous/` (and SSM `<prefix>/previous/`) indefinitely; UPDATING.md
  has the command to delete the ones older than your oldest backup. On Docker
  installs, copy `secrets/` and `.env` off the machine after rotating.
  `restore-latest-backup.sh` and `rollback.sh` use them: a backup from before a
  rotation restores, and its stored secrets move to the current key. On EC2 it
  is the operator document `rotate-keys` in the `recover` role. See
  [UPDATING.md](UPDATING.md#rotating-keys).
- `JWT_SECRET` is gone: nothing read it. `install.sh` no longer writes
  `secrets/jwt_secret.txt`, which you may delete, and on EC2 the next apply
  deletes the `jwt_secret` parameter.
- **EC2:** the re-apply adds the `rotate-keys` document, lets the instance write
  its generated secrets and create `previous/*` (`ssm:PutParameter` there,
  `kms:Encrypt`). Terraform no longer resets those SSM values
  on apply (`ignore_changes`); `voyage_api_key` moves to its own resource,
  without a replacement. If SSM's `backup_encryption_key` differs from the one
  the box backs up with, the box keeps its own, and the nightly update stops
  after rendering the rest of the config.
- The Claude "New session" form saves GitHub tokens on the server (encrypted,
  per user, only the last 4 characters are ever shown) instead of in the
  browser's localStorage, and clears the old localStorage copy on load. Users
  re-enter their tokens once. Every session a user spawns gets their saved clone
  token unless the spawn request names its own. The saved write token goes only to
  sessions spawned from the form, or whose request or schedule sets
  `use_saved_write_token`. Any API key with the `claude_sessions` scope can
  therefore obtain the saved tokens by spawning a session and reading its env.
- Users can reset a forgotten password by emailed link ("Forgot password?" on the
  login page). It is off until you set `PASSWORD_RESET_FROM_EMAIL` to the address
  of an existing send-enabled email account (Sources → Email) owned by an admin,
  which sends the links. Whoever owns that account can read every reset link. It
  also needs `SECRETS_ENCRYPTION_KEY` (or `TRANSFER_TOKEN_SECRET`). A reset ends
  all the user's sessions; API keys are kept. Email sync never ingests reset mail.
- `tools/install.sh --non-interactive` installs from environment variables
  without prompting and creates the first user (see the README). Both modes now
  also generate `secrets/secrets_encryption_key.txt` when absent (compose mounts
  it), and tighten `.env` and `secrets/` to owner-only permissions. `tools/add_user.py`
  reads the password from `ADD_USER_PASSWORD` and accepts `--if-missing`.
- `tools/update.sh` accepts `--require-signed <allowed_signers>` and
  `--min-age-hours N` to install only signed, sufficiently old release tags
  (age counts from when the host first saw the tag; state in `$STATE_DIR`).
  Without them it behaves as before. New `tools/release.sh` cuts signed tags.
- New repair scripts in `tools/ops/` (status, logs, restart, update-to, rollback,
  restore-latest-backup, disk-cleanup, reboot); see UPDATING.md. `tools/update.sh`
  now sources `tools/ops/lib.sh`, writes `$STATE_DIR/update-failed` when an update
  fails (default `STATE_DIR=/var/lib/memory`), and `last-good` records the
  previous version. `rollback.sh` adds the tag it leaves to `$STATE_DIR/skip-tags`,
  which the automatic release selection passes over.
  Automatic selection never returns a tag at or behind the checkout (exit 3,
  "up to date"), and `update.sh` does nothing when the target is already checked out.
- API log records now carry a level (`[time: LEVEL/logger] message`, as in the
  workers), and app warnings and errors in the API go through it instead of
  printing bare.
- `tools/restore_databases.sh` is removed; use `tools/ops/restore-latest-backup.sh`
  (set `BACKUP_SOURCE` to a local directory laid out like the bucket). Its Qdrant
  restore never worked: Qdrant has no API to restore full snapshots. The new one
  tars the Qdrant volume to `backups/pre-restore-qdrant-*.tar.gz`, then starts
  Qdrant on the snapshot (`--storage-snapshot`), and puts the tarball back if that fails.
- `tools/backup_databases.sh` with `MAX_BACKUPS=0` keeps every backup, for a
  bucket whose lifecycle rule expires them.
- Automatic release selection counts an installed, signed tag as up to date
  whatever its age, so the night after an install that skipped the delay
  doesn't fail.
- `tools/ops/restore-latest-backup.sh` hands `BACKUP_ENCRYPTION_KEY` to the
  worker container that decrypts file backups, so the key no longer has to be
  in `.env`.
- `tools/install.sh` creates the external `memory-api-<MEMORY_STACK>` Docker
  network if it is missing; before, `docker compose up` failed on a new host
  until it was created by hand. The first user it creates now has full access.
  `tools/add_user.py --admin` grants the `*` scope; without it users get `read`.
- `RATE_LIMIT_TRUSTED_PROXIES` accepts CIDRs, like `FORWARDED_ALLOW_IPS`
  already did (the rate limiter matched exact IPs only).
- `tools/install.sh --non-interactive` now adds `deploy/aws/compose.proxy.yaml`
  through `COMPOSE_FILE` in `.env`. It pins the `kbnet` subnet and gateway
  (`KBNET_SUBNET`, `KBNET_GATEWAY`; default `10.213.47.0/24`, `.1`) and makes
  kbnet the api's gateway network, and both trust settings become loopback
  plus that gateway. Docker hands a host proxy's loopback connections to the
  api from that gateway, so before this the api saw every client as the
  gateway: one rate-limit bucket for everyone, and `X-Forwarded-Proto`
  ignored. The override needs Docker Engine 28 or later, and each stack on a
  host its own `KBNET_SUBNET`. The base compose file and other installs are
  unchanged.
- New EC2 first-boot script `deploy/aws/bootstrap.sh` and nightly host jobs in
  `deploy/aws/host/` (update, backup, reboot window); see deploy/aws/README.md.
- Transcript accounts accept pushed transcripts at
  `POST /transcripts/ingest/{account_id}`, authenticated by a per-account
  secret generated in the UI (Sources → Transcripts → Push). A new `generic`
  provider is push-only and needs no API key; Fireflies accounts can point
  Fireflies webhooks at the same URL. The path bypasses the auth middleware,
  so a reverse proxy that allow-lists public paths must allow
  `/transcripts/ingest/`. The old free-text webhook secret field is gone and
  the migration clears any secret stored through it, so owners must generate
  a new one in the UI before pushing. Polling is unchanged.
- MCP tools gated on "has any items" (organizer task and calendar tools,
  deadlines, books, observations) are still hidden from tool listings while
  their table is empty, but calling one no longer fails with "Access denied";
  it returns empty results. The Calendar and Tasks pages work again on an
  instance with no events or tasks yet.
- Slack polling no longer waits for a Slack app's push-event setup. Any
  workspace with collection on and an unexpired OAuth token on an active app is
  polled, so workspaces whose app setup was never finished (stuck in `draft` or
  `signing_verified`) start polling again after this deploys, and the first
  sync backfills everything since they stopped. A collecting workspace that
  still can't be polled logs a warning naming it and the reason, and "Sync Now"
  returns that reason instead of silently doing nothing. The Sources page shows
  "Polling only" for such apps, with a button that resumes the setup wizard.
- Two new nightly maintenance tasks prune tables that previously grew without
  bound: `celery_taskmeta` (Celery results older than
  `TASK_RESULT_RETENTION_DAYS`, default 7) and `pending_jobs` (complete or
  failed jobs older than `PENDING_JOB_RETENTION_DAYS`, default 30; pending and
  processing jobs are never deleted). Before pruning, it marks jobs still
  `processing` more than twice the Celery task time limit (2h by
  default) after they started as `failed` ("Worker lost"): their worker
  was killed (time limit, OOM, deploy) before it could record the outcome.
  Celery's own `celery.backend_cleanup` was scheduled onto a queue no worker
  consumes, so it never ran; it is now disabled (`result_expires=None`). The
  first run on a large table deletes a lot, in committed batches of 5000 rows.
  Afterwards, run `VACUUM (ANALYZE) celery_taskmeta, pending_jobs;` once to
  make the space reusable. It does not shrink the files; `VACUUM FULL` does,
  but takes an exclusive lock on each table while it runs, so only use it in a
  quiet window.
- Ingestion commits each new item (as `RAW`) before calling the embedding API,
  so a slow Voyage call no longer outlasts Postgres' 60s idle-in-transaction
  timeout, which killed the connection and rolled the item back. An item whose
  embedding fails is now kept as `FAILED` rather than re-fetched by the next
  sync. The daily `process_raw_items` retries `FAILED` items inserted in the
  last 48 hours (so about twice each), and skips `RAW` items updated in the
  last hour, which may still be embedding. Items that failed before this
  release, or re-embeds of items older than 48 hours, are not retried
  automatically; re-embed those with the `reingest_all_empty_source_items`
  maintenance task.
- Ingest summaries and tags run only on a provider key: a source's explicit key
  (Sources → each source → LLM key), else the default key of whoever brought the
  item in. Items without one are embedded without a summary. Spend counts
  against the key's daily cap. Anyone who can see a source may switch it to one
  of their own keys (they pay from then on), to default, or off.
- Meeting extraction (summary, notes, action items) runs on the meeting's key
  like other ingest LLM work; meetings without one are stored with extraction
  `skipped`. Its prompt, system prompt and model can no longer be overridden
  per call.
- Batch summaries are sent as one batch per paying key, at half the key's
  synchronous price against its daily cap. A key that runs out of daily cap
  or that its provider rejects keeps its requests queued until it can pay; a
  batch the provider refuses (a 4xx other than auth or rate limit) uses up one of
  the requests' `SUMMARIZER_BATCH_MAX_ATTEMPTS`, and the reason is logged.
- Ingestion summaries now go through the Anthropic Message Batches API
  (`SUMMARIZER_USE_BATCH`, default `true`). Items are searchable at once, and
  their summary chunk arrives when the batch ends, usually within an hour.
  Set `SUMMARIZER_USE_BATCH=false` to summarize inline as before. The
  `SUMMARIZER_BATCH_*` settings tune batch size, retries and the
  submit/poll intervals.
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `SUMMARIZER_MODEL`, `RANKER_MODEL`,
  `SESSION_SUMMARIZER_MODEL` and `MEETING_MODEL` are removed: every LLM call
  runs a system agent (Agents → System agents) on a user's own key. Users need
  a default Anthropic or OpenAI key (LLM API keys, `/ui/keys`) for summaries,
  search's LLM steps, meeting notes and session summaries (see Manual steps);
  Claude sessions of users without one stop being summarised. Each provider's
  keys also pay for agents on its models (OpenAI: `openai/gpt-6-astra`,
  `openai/gpt-6.1-sol`, `openai/gpt-6-luna`). The `openai_key` /
  `anthropic_key` docker secrets can be deleted.
- `DISCORD_PROCESS_MESSAGES`, `DISCORD_MODEL` and `DISCORD_MAX_TOOL_CALLS` are
  removed; nothing read them. Delete them from `.env` if set.
- `DEFAULT_LLM_RATE_LIMIT_WINDOW_MINUTES`, `DEFAULT_LLM_RATE_LIMIT_MAX_INPUT_TOKENS`,
  `DEFAULT_LLM_RATE_LIMIT_MAX_OUTPUT_TOKENS` and `LLM_USAGE_REDIS_PREFIX` are
  removed; nothing enforced them. Each key's daily cap limits spend instead.
  The `llm_usage*` Redis keys can be deleted.
- New optional agent-run settings: `AGENT_MAX_TIMEOUT_SEC`
  (1800), `AGENT_MAX_WAIT_SEC` (300), `AGENT_LLM_TIMEOUT_SEC` (600, per model
  request), `AGENT_TOOL_RESULT_MAX_CHARS` (50000), `AGENT_CONTEXT_TRIM_AT_TOKENS`
  (100000), `AGENT_CONTEXT_TRIM_TO_TOKENS` (60000) and `AGENT_STEP_RETENTION_DAYS`
  (90).
- New `agents-worker` service (its own image, `docker/agents/Dockerfile`, with
  the api and worker dependencies) consumes the `<prefix>-agents` queue;
  `AGENT_WORKER_CONCURRENCY` (2) sets its concurrency; each run process takes
  about 270MB, so raise the service's memory limit with it.
  Without it, agent runs stay pending and are failed by the sweep after
  `AGENT_MAX_TIMEOUT_SEC` plus 10 minutes.
- New `agents` user scope gates the `agent_*` MCP tools and the Agents dashboard.
  Only `*` admins have it by default; grant it to anyone else who should use agents.
- Users can add their own Anthropic/OpenAI API keys for agent runs (LLM API
  keys). Runs are capped per run by the agent's `max_cost_usd` or the new
  `AGENT_DEFAULT_USER_KEY_RUN_COST_USD` (1.00), and per key per day by the key's
  or the agent's daily cap, or else the new `AGENT_DEFAULT_USER_KEY_DAILY_COST_USD`
  (20.00).
  Keys are encrypted with `SECRETS_ENCRYPTION_KEY`.
- Agent runs no longer fall back to the server's `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`:
  each run pays with its owner's key. The `llm_server_key` scope and the
  `AGENT_MAX_RUN_COST_USD` / `AGENT_MAX_DAILY_COST_USD` settings are removed (they only
  bounded server-key spend). Let the `agents` queue drain before upgrading: runs
  queued on the server key fail after the migration.
- Workers now refuse to start when the prefix they listen on (`QUEUE_PREFIX`,
  default `memory`) differs from `CELERY_QUEUE_PREFIX` (default `APP_NAME`,
  itself default `memory`), which tasks are routed with: such a worker would
  never receive any. Before updating, run
  `grep -E '^(APP_NAME|QUEUE_PREFIX|CELERY_QUEUE_PREFIX)=' .env`. If it prints
  nothing, or `APP_NAME=memory` alone, there is nothing to do. Otherwise
  `QUEUE_PREFIX` (or `memory` if unset) must equal `CELERY_QUEUE_PREFIX` (or
  `APP_NAME` if unset); if they differ, set both to the same value in `.env`.
  `tools/update.sh` runs this check on the new image before stopping anything,
  so a mismatch fails the update while the old stack keeps running.
- The `anthropic` SDK is upgraded to 2.1.0, so the images must be rebuilt.
  `tools/update.sh` does this.
- The GitHub MCP server and the Discord management MCP tools are removed. The
  Discord server keeps `send_message`, `channel_history` and `list_channels`.
- Qdrant: a new `task` collection, and a `source_id` payload index on every
  collection. Both are created automatically at startup.
- `/health` now reports `version`, `db_revision` and `expected_revision`.
  Optionally, set `LATEST_RELEASE_URL` to get `update_available`.
- HyDE query expansion is now off by default (`ENABLE_HYDE_EXPANSION=false`):
  it added about 0.5 s per search without improving results. Set it to `true`
  to keep it.
- Search has new timeouts: `RERANK_TIMEOUT` (default 4 s per 100 candidates;
  a slower rerank falls back to fused order), `QUERY_EMBEDDING_TIMEOUT`
  (default 5 s) and `QDRANT_SEARCH_TIMEOUT` (default 5 s; a collection that
  misses it is left out of that search). Ingestion keeps `QDRANT_TIMEOUT`.
- Dependency security update: Python packages (pillow, cryptography,
  python-multipart, mcp/fastmcp, awscli, markdownify and others) and the
  frontend lockfile are bumped, and the Qdrant image moves to `v1.14.1`.
  The api image's frontend build stage moves from Node 18 to Node 22.
  The images must be rebuilt. `tools/update.sh` does this.
- Agents can now run unattended: on a schedule (`agent_schedule`, stored as a
  scheduled task of type `agent_run`, counting toward `MAX_SCHEDULED_TASKS_PER_USER`)
  and on ingest triggers (`agent_trigger_upsert`). Both start runs on the
  `agents-worker`, so it must be running. New optional settings:
  `AGENT_MAX_RUNS_PER_HOUR` (60, per definition; a definition's
  `max_runs_per_hour` can only lower it) and `AGENT_MAX_TRIGGERS_PER_USER`
  (20 enabled triggers). A trigger that hits a rate or cost cap disables itself
  and notifies its owner. New beat task `sweep-trigger-buffers` (every minute,
  `maintenance` queue) fires batched triggers. Once any trigger exists, every
  stored item queues a `MATCH_INGEST_TRIGGERS` task on `maintenance`. The daily
  `purge-agent-run-steps` task now also deletes trigger match claims older than
  30 days.
- `core_list_items` and `core_count_items` take `since`, `until` (when the
  content happened), `channel`, `author` and `repo`.
- Agents can call tools on external MCP servers that each user registers and
  authorizes (OAuth with dynamic or manual client registration, static headers,
  or none; streamable HTTP or SSE). New `agent_server_*` MCP tools and dashboard
  pages under `/ui/agents/servers`. stdio servers aren't supported.
- External MCP servers may be on private and LAN addresses (RFC 1918, ULA,
  CGNAT): **every user of the instance can reach your LAN through them**. Always
  blocked: loopback, link-local and cloud metadata, the default docker/podman
  address pools (172.16.0.0/12, 10.88.0.0/16, 10.89.0.0/16), this instance's own
  host on every port, the compose services, and, in the api and workers images
  (`MCP_BLOCK_CONTAINER_NETWORKS=1`), every network the container is attached
  to. Block more with `MCP_BLOCKED_NETWORKS` (comma-separated CIDRs). If the host
  publishes ports on 0.0.0.0, add its own LAN address, e.g.
  `MCP_BLOCKED_NETWORKS=192.168.1.10/32`; on a host attached to several networks,
  add each one it shouldn't reach.
- New optional settings: `MCP_MAX_SERVERS_PER_USER` (20), `MCP_MAX_TOOLS_PER_SERVER`
  (200), `MCP_MAX_TOOL_DESCRIPTION_CHARS` (4000), `MCP_MAX_TOOL_SCHEMA_BYTES` (16384),
  `MCP_MAX_RESPONSE_BYTES` (10 MiB), `MCP_CALL_TIMEOUT_SEC` (120),
  `MCP_CONNECT_TIMEOUT_SEC` (30), `MCP_TOKEN_REFRESH_SKEW_SEC` (300),
  `MCP_OAUTH_STATE_TTL_SEC` (600), `MCP_SERVER_CHANGES_PER_HOUR` (30),
  `MCP_BLOCKED_NETWORKS` (empty) and `MCP_BLOCK_CONTAINER_NETWORKS` (set to 1 by
  the images; if the route table can't be read, external servers can't be dialed).
- Agent tool names are now limited to 64 characters (the strictest provider
  limit), so an external tool whose `mcp_<server id>_<name>` is longer is listed
  as unavailable.
- The unused MCP connector plumbing in the LLM providers (`mcp_servers=`) is removed.
- **Access widens**: synced items now record the owner of the account they came
  from as their creator, and a creator can read their items whatever their
  project and sensitivity, in SQL, BM25 and vector search alike. Owners are the
  email, GitHub, Google Drive, calendar and transcript account's user, and the
  article feed's user. Slack and Discord messages get none. Email attachments
  and meeting action items take their parent's creator, so a transcript
  account's owner can also update their meetings' action-item tasks. Uploads
  through `/books/upload`, `/photos/upload` and `POST /meetings` record the
  uploader. Updating a report keeps its first creator. Moving an account to a
  project its owner can't access no longer hides its content from them; remove
  the account instead.
- Feed blog posts now also inherit their article feed's project and
  sensitivity, not just its owner.
- **Meetings become visible to their project**: meetings now inherit project
  and sensitivity from their transcript account (default `basic`), and their
  action-item tasks and email attachments from their parent. Synced meetings
  used to be visible only to superadmins and their attendees; now every
  contributor on the account's project sees them. See Manual steps.
- Items now resolve their inherited project, sensitivity and creator when they
  are stored, so they are searchable by their project and fire their owner's
  ingest triggers at once rather than after the next access-control sweep.
- Qdrant: chunk payloads gain `creator_id`. Existing points get it from the
  post-deploy command below; until then owners find their items outside their
  projects through BM25 only.
- Qdrant: `project_id`, `sensitivity` and `creator_id` payload indexes on every
  collection, since every non-admin vector search filters on them. Workers
  create them at startup; the first start after the update waits while they
  are built, which is slower on a large instance.
- **Security**: `/files/` and `/reports/` render only passive types inline (PDF,
  plain text, raster images, audio, video); HTML, SVG, XML, JS and unknown
  types download instead. Stored HTML (e.g. an email attachment) used to render
  on memory's origin with the viewer's session. HTML reports opened through
  `/reports/` are unchanged.
- New `claude_sessions` user scope gates every Claude container route (`/claude/*`:
  spawn, list, kill, attach, logs, stats, differ, terminal, environments,
  snapshots, including `tools/claude_snapshot.py` uploads) and the
  `claude_session_list`/`_list_dir`/`_pull_url`/`_push_url` MCP tools. The
  migration grants it to users who own a Claude config snapshot or environment
  (nobody else could spawn); `*` admins pass without it. Grant it to anyone else
  who should use Claude sessions. An API key with its own scope list now needs
  `claude_sessions` in that list, and REST routes gated on a scope now check an
  API key's own scopes rather than its owner's (inline admin checks still see
  the owner's `*`, a known gap).
- Scheduling a `claude_session` task needs `claude_sessions`. Each run re-checks
  that the user, and the API key that set it up, still hold it; if not, the run
  fails, the schedule is disabled and its owner is notified.
- A scheduled `claude_session` execution now stays `running` until its
  container exits (`completed`; `failed` if the container is `dead`, or was
  removed before its exit was seen), and the next run of that schedule waits
  for it. The api checks this every `CLAUDE_MAINTENANCE_INTERVAL_SEC` (300),
  then removes exited and dead containers through the orchestrator's
  `/cleanup`, so files can no longer be pulled from an exited container after
  that. Running containers are never touched. An execution still running after
  `CLAUDE_SESSION_TIMEOUT_HOURS` (24) is marked `failed`, by the api or the
  scheduler worker; its container keeps running. With
  `CLAUDE_MAINTENANCE_INTERVAL_SEC=0`, or an api without the orchestrator
  socket (logged at startup), nothing settles executions early, so each run of
  a repeating schedule waits up to that timeout.
- The LLM API keys page moved to `/ui/keys` and is open to every user; key names are
  now unique per user (tools accept a key's name or id).
- HyDE, query analysis and result scoring run on the searcher's default key for
  their provider and are skipped for users without one; search still works.
  `RANKER_MODEL` is removed; delete it from `.env` if set.
- Search results and `core_fetch` return each item's LLM summary as `summary`.
  A long item's search `content` is its summary instead of its first
  characters, and a match on the summary ranks slightly above an equal match on
  the text. Only summaries made after this update are marked as summaries
  (`chunk.is_summary`): older items show and rank as before until they are
  reingested.
- `DISCORD_COLLECTOR_ENABLED`, `DISCORD_COLLECT_DMS`, `DISCORD_COLLECT_BOTS`, `DISCORD_CONTEXT_WINDOW`,
  `DISCORD_ACTIVITY_CHANNEL`, `DISCORD_DISCOVERY_CHANNEL`, `DISCORD_CHAT_CHANNEL`,
  `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET` and `SLACK_REDIRECT_URI` are
  removed. Nothing read them; delete them from `.env` if set. Each Slack app
  keeps its own credentials, entered in the Slack app wizard.
- `PROFILES_FOLDER` is removed, and notes under `profiles/` are now indexed and
  searchable like any other note (git sync already carried them). Notes vaults
  from before Feb 2026 may hold a stale `profiles/` folder of People exports;
  delete it if you don't want it indexed.
- "Regenerate API key" now atomically creates a new key and revokes the user's
  other API keys, as its confirmation always said. Concurrent rotations are
  serialized per user; only the latest key remains valid. Revoke a single key with
  `DELETE /users/{id}/api-keys/{key_id}`.
- Slack apps are owner-only: the list of extra authorized users per app is gone
  (see `20261107_drop_slack_app_users`). Only an app's creator can see it, run
  its wizard or connect workspaces through it. Workspaces other users already
  connected keep syncing, but those users can't reconnect or see the app.
- These REST endpoints are removed; nothing in the app called them:
  `GET /api/metrics/raw`, `GET /source-items/{id}`, `POST /source-items/{id}/reingest`,
  `POST /sessions/ingest` (the hook uses `/sessions/ingest/batch`),
  `GET /sessions/`, `GET /sessions/projects`, `GET /sessions/{id}`,
  `GET /claude/{session_id}/stats`, `GET /secrets/by-name/{name}`,
  `DELETE /users/{id}/api-keys/{key_id}/permanent`, `/discord/users`
  (`GET` list, `GET`/`PATCH` one), `POST`/`DELETE /slack/apps/{id}/authorized-users`,
  and the single-item `GET`s of `/email-accounts/{id}`, `/article-feeds/{id}`,
  `/github/projects/{id}` and `/github/accounts/{id}/repos`.

### Migrations

- `20261023_summary_requests`: creates the empty `summary_request` table. Fast.
- `20261024_agent_runs`: creates the empty `agent_definitions`, `agent_runs` and
  `agent_run_steps` tables, and adds two nullable columns to `api_keys`. Fast.
- `20261025_agent_triggers`: creates the empty `agent_triggers` and
  `agent_trigger_matches` tables. Fast.
- `20261026_external_mcp_servers`: drops `mcp_server_assignments` and
  `mcp_servers` (their rows are lost; see Manual steps) and creates the empty
  `external_mcp_servers` table. Fast.
- `20261027_llm_provider_keys`: creates the empty `llm_provider_keys` table, adds
  `key_source` (default `server`) and `provider_key_id` to `agent_runs`, and grants
  `llm_server_key` to users and API keys holding `agents`. Fast.
- `20261028_source_item_creators`: **slow on large instances**. Sets
  `source_item.creator_id` from the source owners above on every item that has
  none, in batches of 50,000 ids, committing after each batch. It can run while
  the app is up, and re-running it after a failure continues where it stopped.
  This is the access widening described above. Downgrading leaves the creators.
- `20261028_claude_sessions_scope`: grants `claude_sessions` to users who own a
  Claude config snapshot or environment. Fast.
- `20261029_telemetry_api_key`: adds a nullable `api_key_id` to
  `telemetry_events` with a foreign key and a partial index. The index is built
  concurrently, so telemetry writes keep flowing, but the build reads the whole
  table and can take a while on large instances. Events stored before it have
  no key.
- `20261030_sync_failing_since`: adds nullable `sync_failing_since` to every
  synced source table, and `sync_error` to `google_folders`, `github_repos` and
  `article_feeds`. Fast. Errors already stored show without a start time until
  the next sync.
- `20261031_transcript_push_only`: makes `transcript_accounts.api_key_encrypted`
  nullable and clears every stored `webhook_secret_encrypted`. Fast.
  Downgrading deletes push-only (`generic`) accounts.
- `20261101_claude_github_tokens`: creates the empty `claude_github_tokens`
  table. Fast.
- `20261102_drop_team_sync`: drops the Discord role and GitHub team columns
  from `teams` (`discord_role_id`, `discord_guild_id`, `auto_sync_discord`,
  `github_team_id`, `github_team_slug`, `github_org`, `auto_sync_github`). Fast.
  The links are lost; downgrading restores the columns empty.
- `20261029_user_keys_only`: pins agents set to the server's key to their owner's default
  key where one exists (the others lose the pin and are refused until their owner adds a
  key), removes the `llm_server_key` scope, drops `agent_runs.key_source`. Fast.
- `20261029_key_names_per_user`: renames same-named keys of one user (`name (provider)`)
  and makes names unique per user. Fast.
- `20261029_llm_spend`: new table for per-key LLM spend outside agent runs. Fast.
- `20261029_source_llm_keys`: adds `llm_key_id`/`llm_disabled` to every source table and
  `discord_bots.created_by_user_id` (backfilled with each bot's lowest-id authorized user).
  Fast.
- `20261029_summary_request_keys`: deletes all `summary_request` rows (see Manual steps)
  and adds `provider_key_id`. Fast.
- `20261103_drop_one_time_keys`: deletes the `api_keys` rows of type `one_time`. Fast.
  Downgrading does not restore them.
- `20261103_user_timezone`: adds `users.timezone` (IANA name, default `UTC`). Fast.
- `20261103_instance_settings`: creates the empty `instance_settings` table. Fast.
- `20261103_summary_batch_pacing`: adds the nullable
  `llm_provider_keys.summary_batch_limit` and `summary_request.submitted_at`. Fast.
- `20261104_chunk_is_summary`: adds `chunk.is_summary` (default false). Fast: a
  constant default doesn't rewrite the table.
- `20261105_unique_note_filename`: one note per filename. Where several notes
  share a filename, all but the newest lose their filename and get a new content
  hash. Their content stays in the database and stays searchable, and the new
  hash stops them from swallowing a later save of the same text. Downgrading
  can't restore the cleared filenames. Then builds a partial unique index on
  `source_item.filename` for notes, concurrently, so writes keep flowing, but the
  build reads the whole `source_item` table and can take a while on large
  instances. If the index build fails, re-run the migration.
- `20261106_unique_person_user`: makes `people.user_id` unique (a user is linked
  to at most one Person). Fast. **It refuses to run if a user is already linked
  to several people** and lists them; see Manual steps for the check to run
  before updating and how to recover.
- `20261107_drop_discord_user_link`: drops `discord_users.system_user_id`.
  Fast. See Manual steps for the accounts it unlinks.
- `20261107_drop_obs_tables`: drops the `reaction_pattern`, `observation_pattern`,
  `belief_cluster` and `conversation_metrics` tables, which nothing wrote to. Fast.
  Any rows in them are lost; downgrading restores the tables empty.
- `20261107_drop_slack_app_users`: drops `slack_app_users`, the extra users
  authorized per Slack app. Fast. The grants are lost; downgrading restores the
  table empty.

### Post-deploy commands

Embed items that never got vectors (tasks never had a collection before). Each
command picks only items with no chunks, or FAILED ones, and reingesting an item
replaces its chunks, so re-running is safe:

```bash
for t in task calendar_event github_item slack_message person_tidbit; do
  docker compose exec -T worker python - maintenance reingest-empty-source-items --item-type "$t" < tools/run_celery_task.py
done
```

Give existing meetings,
their action items and email attachments their inherited project and
sensitivity, and write `creator_id` into every Qdrant payload. The daily sweep
at 05:30 resolves the SQL rows on its own, but only this command writes
`creator_id` for items whose rows didn't change. It can take hours on a large
instance and is safe to re-run:

```bash
docker compose exec -T worker python - maintenance reconcile-access-control --rewrite-payloads < tools/run_celery_task.py
```

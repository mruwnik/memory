"""The run input an ingest trigger renders from its template and matched items.

Placeholders: {items} (every item, default rendering), {items_json} (a JSON list
of each item's short fields) and, for per-item delivery, {item.<field>}. Fields
come from an allow-list per item type. Free text people wrote is wrapped in
<untrusted_item> delimiters it can't close, so an agent can tell data from orders.
"""

import json
import re
from datetime import datetime
from typing import Any, Callable, Collection, Mapping

from memory.agents.triggers import item_body, item_title
from memory.common.db.models import (
    DiscordMessage,
    ForumPost,
    GithubItem,
    GithubPR,
    MailMessage,
    Meeting,
    SlackMessage,
    SourceItem,
)

ITEMS = "items"
ITEMS_JSON = "items_json"
ITEM_PREFIX = "item."
# Long text is clipped to this in the default rendering; a template naming the
# field gets all of it.
PREVIEW_CHARS = 1000
BARE_CHARS = 200
CLIPPED = "\n[input truncated]"
PLACEHOLDER = re.compile(r"\{([A-Za-z_][\w.]*)\}")
DELIMITER = re.compile(r"<(/?)(untrusted_item)", re.IGNORECASE)

Getter = Callable[[Any], Any]


def github_url(item: GithubItem) -> str | None:
    number = item.number if item.number is not None else item.parent_number
    if number is None:
        return f"https://github.com/{item.repo_path}"
    path = "pull" if item.kind == "pr" else "issues"
    return f"https://github.com/{item.repo_path}/{path}/{number}"


def slack_url(item: SlackMessage) -> str | None:
    domain = item.workspace.domain if item.workspace else None
    if not domain or not item.message_ts:
        return None
    url = f"https://{domain}.slack.com/archives/{item.channel_id}/p{item.message_ts.replace('.', '')}"
    if item.thread_ts and item.thread_ts != item.message_ts:
        url += f"?thread_ts={item.thread_ts}&cid={item.channel_id}"
    return url


def discord_url(item: DiscordMessage) -> str:
    guild = item.server_id if item.server_id is not None else "@me"
    return f"https://discord.com/channels/{guild}/{item.channel_id}/{item.message_id}"


def item_url(item: SourceItem) -> str | None:
    if isinstance(item, GithubItem):
        return github_url(item)
    if isinstance(item, SlackMessage):
        return slack_url(item)
    if isinstance(item, DiscordMessage):
        return discord_url(item)
    url = getattr(item, "url", None)
    return url if isinstance(url, str) else None


def item_author(item: SourceItem) -> str | None:
    if isinstance(item, SlackMessage):
        return item.author_name or item.author_id
    if isinstance(item, DiscordMessage):
        return item.author.username if item.author else str(item.author_id)
    if isinstance(item, MailMessage):
        return item.sender
    if isinstance(item, ForumPost):
        return ", ".join(item.authors or []) or None
    author = getattr(item, "author", None)
    return author if isinstance(author, str) else None


def pr_files(item: GithubItem) -> list[str]:
    files = (item.pr_data.files if item.pr_data else None) or []
    return [
        f"{f.get('filename')} (+{f.get('additions', 0)} -{f.get('deletions', 0)})"
        for f in files
    ]


def action_items(meeting: Meeting) -> list[dict[str, Any]]:
    return [
        {
            "title": t.task_title,
            "assignee": ", ".join(p.display_name for p in t.people) or None,
            "due": t.due_date,
            "status": t.status,
        }
        for t in getattr(meeting, "spawned_tasks", [])
    ]


def discord_parent_id(item: DiscordMessage) -> int | None:
    return item.channel.parent_id if item.channel else None


COMMON: dict[str, Getter] = {
    "id": lambda i: i.id,
    "type": lambda i: i.type,
    "url": item_url,
    "title": item_title,
    "author": item_author,
    "inserted_at": lambda i: i.inserted_at,
    "content": item_body,
}

BY_TYPE: dict[type[SourceItem], dict[str, Getter]] = {
    GithubItem: {
        "repo": lambda i: i.repo_path,
        "number": lambda i: i.number if i.number is not None else i.parent_number,
        "kind": lambda i: i.kind,
        "state": lambda i: i.state,
        "labels": lambda i: i.labels,
    },
    GithubPR: {
        "head_sha": lambda i: i.head_sha,
        "checks": lambda i: i.checks,
        "diff_summary": lambda i: i.diff_summary,
        "files": pr_files,
    },
    SlackMessage: {
        "workspace_id": lambda i: i.workspace_id,
        "channel_id": lambda i: i.channel_id,
        "channel_name": lambda i: i.channel.name if i.channel else None,
        "ts": lambda i: i.message_ts,
        "thread_ts": lambda i: i.thread_ts,
        "user": lambda i: i.author_id,
    },
    DiscordMessage: {
        "guild_id": lambda i: i.server_id,
        "channel_id": lambda i: i.channel_id,
        "message_id": lambda i: i.message_id,
        "thread_id": lambda i: i.thread_id,
        "parent_id": discord_parent_id,
        "author_id": lambda i: i.author_id,
    },
    Meeting: {
        "date": lambda i: i.meeting_date,
        "attendees": lambda i: [p.display_name for p in i.people],
        "summary": lambda i: i.summary,
        "notes": lambda i: i.notes,
        "action_items": action_items,
    },
    MailMessage: {
        "from": lambda i: i.sender,
        "to": lambda i: i.recipients,
        "subject": lambda i: i.subject,
        "message_id": lambda i: i.message_id,
    },
}

# Common fields these types have no value for.
ABSENT: dict[type[SourceItem], frozenset[str]] = {
    SlackMessage: frozenset({"title"}),
    DiscordMessage: frozenset({"title"}),
    Meeting: frozenset({"url", "author"}),
    MailMessage: frozenset({"url", "title", "author"}),
}

# Left out of {items_json} and clipped in the default rendering.
LONG = frozenset({"content", "summary", "notes", "diff_summary", "files"})
UNTRUSTED = LONG | {"title", "subject", "labels", "action_items"}
# Shown in the default rendering's header line instead.
HEADER = frozenset({"id", "type", "changes"})


def type_fields(cls: type[SourceItem]) -> dict[str, Getter]:
    absent = frozenset().union(*(a for c, a in ABSENT.items() if issubclass(cls, c)))
    found = {k: v for k, v in COMMON.items() if k not in absent}
    for c, fields in BY_TYPE.items():
        if issubclass(cls, c):
            found |= fields
    return found


def field_names(source_types: Collection[str] = ()) -> list[str]:
    """Fields a template can name for items of these types (all types when empty)."""
    polymorphic = SourceItem.__mapper__.polymorphic_map
    classes = [polymorphic[t].class_ for t in source_types if t in polymorphic]
    return sorted({"changes"}.union(*map(type_fields, classes or [SourceItem, *BY_TYPE])))


def check_template(template: str, source_types: Collection[str], per_item: bool) -> None:
    """Raise ValueError naming any placeholder the renderer wouldn't fill."""
    valid = field_names(source_types)
    for name in PLACEHOLDER.findall(template):
        if name in (ITEMS, ITEMS_JSON):
            continue
        if not known(name):
            raise ValueError(
                f"unknown placeholder {{{name}}}; use {{items}}, {{items_json}} or {{item.<field>}}"
            )
        if not per_item:
            raise ValueError(
                f"{{{name}}} needs per_item delivery; batches use {{items}} or {{items_json}}"
            )
        field = name.removeprefix(ITEM_PREFIX)
        if field not in valid:
            raise ValueError(f"unknown field {{{name}}}; valid fields: {', '.join(valid)}")


def item_fields(item: SourceItem, changes: list[str]) -> dict[str, Any]:
    return {name: get(item) for name, get in type_fields(type(item)).items()} | {"changes": changes}


def plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    return value


def as_text(value: Any) -> str:
    value = plain(value)
    if value is None:
        return ""
    if isinstance(value, list) and all(isinstance(v, dict) for v in value) and value:
        return "\n".join(
            "- " + "; ".join(f"{k}: {v}" for k, v in d.items() if v is not None)
            for d in value
        )
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def untrusted(item_id: Any, field: str, text: str) -> str:
    """Escapes the delimiter inside the text so it can't end the block early."""
    safe = DELIMITER.sub(lambda m: f"&lt;{m.group(1)}{m.group(2)}", text)
    return f"<untrusted_item id={item_id} field={field}>{safe}</untrusted_item>"


def bare(text: str) -> str:
    # Single line and short, so a name can't pass for the input's structure.
    return " ".join(text.split())[:BARE_CHARS]


def field_text(item_id: int, field: str, value: Any) -> str:
    text = as_text(value)
    if not text:
        return ""
    return untrusted(item_id, field, text) if field in UNTRUSTED else bare(text)


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def default_item(fields: dict[str, Any]) -> str:
    item_id = fields["id"]
    label = f"{fields['type']} #{item_id}"
    lines = [f"- [{label}] {', '.join(fields['changes'])}".rstrip()]
    # Ids and links first, then text, the item's own content last.
    for name, value in sorted(fields.items(), key=lambda f: (f[0] in UNTRUSTED, f[0] == "content")):
        if name in HEADER:
            continue
        text = as_text(value).strip()
        if not text:
            continue
        if name in LONG:
            text = clip(text, PREVIEW_CHARS)
        if name in UNTRUSTED:
            lines.append("  " + untrusted(item_id, name, text).replace("\n", "\n  "))
        else:
            lines.append(f"  {name}: {bare(text)}")
    return "\n".join(lines)


def items_json(all_fields: list[dict[str, Any]]) -> str:
    short = [
        {k: plain(v) for k, v in fields.items() if k not in LONG and v not in (None, [], "")}
        for fields in all_fields
    ]
    return untrusted("all", ITEMS_JSON, json.dumps(short, separators=(",", ":"), default=str))


def known(name: str) -> bool:
    return name in (ITEMS, ITEMS_JSON) or name.startswith(ITEM_PREFIX)


def changes_for(item_id: int, changes: Mapping[Any, list[str]]) -> list[str]:
    # Run refs round-trip through JSON, which turns the ids into strings.
    return list(changes.get(item_id) or changes.get(str(item_id)) or [])


def render_input(
    template: str,
    items: list[SourceItem],
    max_chars: int,
    changes: Mapping[Any, list[str]] = {},
) -> str:
    """Fill the template in one pass, so item text can't add placeholders of its own.

    A template without placeholders gets the default rendering appended. Clipped
    rather than refused: an oversized input would trip the trigger.
    """
    all_fields = [item_fields(i, changes_for(i.id, changes)) for i in items]
    first = all_fields[0] if all_fields else {}

    def fill(match: re.Match) -> str:
        name = match.group(1)
        if name == ITEMS:
            return "\n".join(default_item(f) for f in all_fields)
        if name == ITEMS_JSON:
            return items_json(all_fields)
        field = name.removeprefix(ITEM_PREFIX)
        if name.startswith(ITEM_PREFIX) and len(all_fields) == 1:
            return field_text(first["id"], field, first.get(field))
        return match.group(0)

    if any(known(n) for n in PLACEHOLDER.findall(template)):
        text = PLACEHOLDER.sub(fill, template)
    else:
        text = f"{template}\n\n" + "\n".join(default_item(f) for f in all_fields)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - len(CLIPPED)] + CLIPPED

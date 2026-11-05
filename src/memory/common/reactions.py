"""Chat message reactions, stored per user as ``{emoji: [user ids]}``.

Add/remove events apply as set operations, so a retried event changes
nothing. Order still matters for one user's add and remove of one emoji:
applied the other way round they leave the opposite state.
"""

from typing import Literal

Reactions = dict[str, list[str]]
ReactionAction = Literal["add", "remove", "clear_emoji", "clear"]


def apply_reaction(
    reactions: Reactions | None,
    action: ReactionAction,
    emoji: str | None = None,
    user_id: str | None = None,
) -> Reactions:
    """A new map with one reaction event applied; emojis left with no users go."""
    current = {e: list(users) for e, users in (reactions or {}).items()}
    if action == "clear":
        return {}
    if emoji is None:
        return current
    if action == "clear_emoji":
        current.pop(emoji, None)
        return current
    users = current.get(emoji, [])
    if action == "add" and user_id is not None and user_id not in users:
        current[emoji] = [*users, user_id]
    if action == "remove":
        remaining = [u for u in users if u != user_id]
        if remaining:
            current[emoji] = remaining
        else:
            current.pop(emoji, None)
    return current


def reaction_counts(reactions: Reactions | None) -> dict[str, int]:
    return {emoji: len(users) for emoji, users in (reactions or {}).items()}


def slack_reactions(api_reactions: list[dict] | None) -> Reactions | None:
    """Slack's ``[{"name", "users", "count"}]`` as a per-user map."""
    if api_reactions is None:
        return None
    return {r["name"]: list(r.get("users") or []) for r in api_reactions if r.get("name")}

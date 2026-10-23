"""Runner-side context trimming, identical for every provider.

Old tool results are replaced by a stub in the in-memory request view; the
messages themselves are copied, never mutated, so the stored transcript is
untouched. Any trim edits earlier turns, which invalidates replayed reasoning
on providers that bind it to the exact history, so a trim also drops all
replay data.
"""

from dataclasses import dataclass, field

from memory.common.llms.neutral import NeutralMessage, Replay, ToolResult
from memory.common.tokens import CHARS_PER_TOKEN

STUB_PREFIX = "[cleared: "


@dataclass
class View:
    messages: list[NeutralMessage] = field(default_factory=list)
    replay: list[Replay | None] = field(default_factory=list)

    def append(self, message: NeutralMessage, replay: Replay | None = None) -> None:
        self.messages.append(message)
        self.replay.append(replay)


def stub(result: ToolResult) -> ToolResult:
    text = f"{STUB_PREFIX}{result.name} result, {len(result.text)} chars — call the tool again if needed]"
    return result.model_copy(update={"text": text})


def trim(
    view: View, *, estimated_tokens: int, trim_at: int, trim_to: int, keep_recent: int
) -> int:
    """Stub old tool results until the estimate is under `trim_to`; returns how many."""
    if estimated_tokens <= trim_at:
        return 0
    last_assistant = max(
        (i for i, m in enumerate(view.messages) if m.role == "assistant"), default=-1
    )
    positions = [
        (i, j)
        for i, m in enumerate(view.messages)
        for j, b in enumerate(m.content)
        if isinstance(b, ToolResult) and not b.text.startswith(STUB_PREFIX)
    ]
    keep = set(positions[len(positions) - keep_recent :]) if keep_recent else set()
    # The latest turn and the results answering it are what the model acts on next.
    protected = {last_assistant, last_assistant + 1}
    candidates = [
        (i, j) for i, j in positions if (i, j) not in keep and i not in protected
    ]

    stubbed, estimate = 0, estimated_tokens
    for i, j in candidates:
        if estimate <= trim_to:
            break
        message = view.messages[i]
        original = message.content[j]
        assert isinstance(original, ToolResult)
        replacement = stub(original)
        content = message.content[:j] + [replacement] + message.content[j + 1 :]
        view.messages[i] = message.model_copy(update={"content": content})
        estimate -= (len(original.text) - len(replacement.text)) // CHARS_PER_TOKEN
        stubbed += 1
    if stubbed:
        view.replay = [None] * len(view.replay)
    return stubbed

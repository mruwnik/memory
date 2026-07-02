import json

import pytest

from memory.parsers.claude_sessions import (
    MAX_MESSAGE_CHARS,
    build_segments,
    extract_text_blocks,
    iter_transcript_messages,
    parse_event,
)


def make_event(
    event_type: str = "user",
    content: str | list | None = "hello there, this is a message",
    timestamp: str = "2026-07-01T12:00:00Z",
    is_meta: bool = False,
    model: str | None = None,
) -> dict:
    message: dict = {"role": event_type, "content": content}
    if model:
        message["model"] = model
    return {
        "uuid": "some-uuid",
        "type": event_type,
        "timestamp": timestamp,
        "message": message,
        "is_meta": is_meta,
    }


@pytest.mark.parametrize(
    "content, expected",
    [
        ("plain string", "plain string"),
        ("  padded  ", "padded"),
        ([{"type": "text", "text": "block text"}], "block text"),
        (
            [
                {"type": "text", "text": "first"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                {"type": "text", "text": "second"},
            ],
            "first\nsecond",
        ),
        ([{"type": "tool_result", "content": "output"}], ""),
        ([{"type": "thinking", "thinking": "hmm"}], ""),
        ({"weird": "shape"}, ""),
        (None, ""),
        ([], ""),
    ],
)
def test_extract_text_blocks(content, expected):
    assert extract_text_blocks(content) == expected


def test_parse_event_user_message():
    message = parse_event(3, make_event("user", "what does this code do?"))
    assert message is not None
    assert message.index == 3
    assert message.role == "user"
    assert message.text == "what does this code do?"
    assert message.timestamp is not None
    assert message.timestamp.isoformat() == "2026-07-01T12:00:00+00:00"
    assert message.model is None


def test_parse_event_assistant_records_model():
    message = parse_event(
        0, make_event("assistant", "the code does X", model="claude-fable-5")
    )
    assert message is not None
    assert message.model == "claude-fable-5"


@pytest.mark.parametrize(
    "event",
    [
        make_event("user", "hi", is_meta=True),
        make_event("system", "system prompt"),
        make_event("user", [{"type": "tool_result", "content": "stdout"}]),
        make_event("user", ""),
        {"type": "user"},  # no message at all
    ],
)
def test_parse_event_skips_non_conversation(event):
    assert parse_event(0, event) is None


def test_parse_event_truncates_long_messages():
    message = parse_event(0, make_event("user", "x" * (MAX_MESSAGE_CHARS + 100)))
    assert message is not None
    assert len(message.text) < MAX_MESSAGE_CHARS + 50
    assert message.text.endswith("[... truncated]")


def write_transcript(path, events):
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_iter_transcript_messages_indices_and_filtering(tmp_path):
    file = tmp_path / "transcript.jsonl"
    events = [
        make_event("user", "first question"),  # 0
        make_event("assistant", "tool call incoming"),  # 1
        make_event("user", [{"type": "tool_result", "content": "out"}]),  # 2
        make_event("assistant", "the answer"),  # 3
    ]
    write_transcript(file, events)

    messages = list(iter_transcript_messages(file))
    assert [(m.index, m.role) for m in messages] == [
        (0, "user"),
        (1, "assistant"),
        (3, "assistant"),
    ]


def test_iter_transcript_messages_start_index(tmp_path):
    file = tmp_path / "transcript.jsonl"
    write_transcript(
        file, [make_event("user", f"message number {i}") for i in range(5)]
    )

    messages = list(iter_transcript_messages(file, start_index=3))
    assert [m.index for m in messages] == [3, 4]


def test_iter_transcript_messages_malformed_lines_keep_indices(tmp_path):
    file = tmp_path / "transcript.jsonl"
    lines = [
        json.dumps(make_event("user", "first")),
        "{not json",
        "",
        json.dumps(make_event("user", "second")),
    ]
    file.write_text("\n".join(lines) + "\n")

    messages = list(iter_transcript_messages(file))
    assert [m.index for m in messages] == [0, 3]


def test_build_segments_respects_token_budget():
    events = [make_event("user", f"message {i}: " + "word " * 40) for i in range(6)]
    messages = [m for i, e in enumerate(events) if (m := parse_event(i, e))]

    segments = build_segments(messages, max_tokens=100)

    assert len(segments) > 1
    # Every message lands in exactly one segment, in order
    all_indices = [m.index for s in segments for m in s.messages]
    assert all_indices == list(range(6))
    # Segments carry inclusive line ranges
    assert segments[0].start_index == 0
    assert segments[-1].end_index == 5


def test_build_segments_metadata():
    messages = [
        parse_event(0, make_event("user", "question about the parser")),
        parse_event(
            2, make_event("assistant", "answer about the parser", model="claude-fable-5")
        ),
    ]
    segments = build_segments([m for m in messages if m], max_tokens=1000)

    assert len(segments) == 1
    segment = segments[0]
    assert segment.start_index == 0
    assert segment.end_index == 2
    assert segment.roles == ["assistant", "user"]
    assert segment.models == ["claude-fable-5"]
    assert "User: question about the parser" in segment.text
    assert "Assistant: answer about the parser" in segment.text
    assert segment.start_time is not None
    assert segment.end_time is not None


def test_build_segments_deterministic():
    events = [make_event("user", f"message {i}: " + "word " * 30) for i in range(8)]

    def run():
        messages = [m for i, e in enumerate(events) if (m := parse_event(i, e))]
        return [(s.start_index, s.end_index, s.text) for s in build_segments(messages, max_tokens=120)]

    assert run() == run()


def test_build_segments_oversized_message_gets_own_segment():
    messages = [
        parse_event(0, make_event("user", "short lead-in")),
        parse_event(1, make_event("assistant", "word " * 500)),
        parse_event(2, make_event("user", "short follow-up")),
    ]
    segments = build_segments([m for m in messages if m], max_tokens=100)

    assert [len(s.messages) for s in segments] == [1, 1, 1]

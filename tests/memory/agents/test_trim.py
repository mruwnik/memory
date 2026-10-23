from memory.agents.trim import View, trim
from memory.common.llms.neutral import (
    Block,
    NeutralMessage,
    Text,
    ToolCall,
    ToolResult,
)


def conversation(n_results: int, size: int = 4000) -> View:
    view = View()
    view.append(NeutralMessage(role="user", content=[Text(text="go")]))
    for i in range(n_results):
        view.append(
            NeutralMessage(
                role="assistant",
                content=[ToolCall(id=f"t{i}", name="core_search", input={})],
            ),
            {"provider": "fake", "blocks": [i]},
        )
        view.append(
            NeutralMessage(
                role="user",
                content=[
                    ToolResult(
                        tool_call_id=f"t{i}", name="core_search", text="x" * size
                    )
                ],
            )
        )
    return view


def result_texts(view: View) -> list[str]:
    return [
        b.text for m in view.messages for b in m.content if isinstance(b, ToolResult)
    ]


def test_below_threshold_is_a_no_op():
    view = conversation(5)

    assert (
        trim(view, estimated_tokens=1000, trim_at=2000, trim_to=1000, keep_recent=3)
        == 0
    )
    assert all(r is None or r["blocks"] for r in view.replay)


def test_stubs_oldest_first_until_under_target_and_keeps_recent():
    view = conversation(6)  # each result ~1000 tokens

    stubbed = trim(
        view, estimated_tokens=6500, trim_at=5000, trim_to=4000, keep_recent=3
    )

    texts = result_texts(view)
    assert stubbed == 3
    assert [
        t.startswith("[cleared: core_search result, 4000 chars") for t in texts
    ] == [
        True,
        True,
        True,
        False,
        False,
        False,
    ]


def test_never_stubs_the_most_recent_results_even_if_still_over():
    view = conversation(3)

    assert (
        trim(view, estimated_tokens=99_999, trim_at=10, trim_to=5, keep_recent=3) == 0
    )


def test_a_trim_clears_all_replay():
    view = conversation(5)

    trim(view, estimated_tokens=99_999, trim_at=10, trim_to=5, keep_recent=3)

    assert view.replay == [None] * len(view.messages)


def test_stubs_are_sticky_and_not_counted_twice():
    view = conversation(5)
    trim(view, estimated_tokens=99_999, trim_at=10, trim_to=5, keep_recent=3)

    assert (
        trim(view, estimated_tokens=99_999, trim_at=10, trim_to=5, keep_recent=3) == 0
    )


def test_latest_assistant_turn_is_never_stubbed():
    view = conversation(4)
    view.append(
        NeutralMessage(
            role="assistant",
            content=[
                ToolCall(id="s", name="web_search", input={}, executed_by="provider"),
                ToolResult(tool_call_id="s", name="web_search", text="y" * 8000),
            ],
        )
    )

    trim(view, estimated_tokens=99_999, trim_at=10, trim_to=5, keep_recent=0)

    assert view.messages[-1].content[1] == ToolResult(
        tool_call_id="s", name="web_search", text="y" * 8000
    )


def test_trimming_leaves_the_original_messages_untouched():
    view = conversation(5)
    originals = list(view.messages)
    before = [m.model_copy(deep=True) for m in originals]

    trim(view, estimated_tokens=99_999, trim_at=10, trim_to=5, keep_recent=0)

    assert originals == before


def test_results_answering_the_latest_turn_are_never_stubbed():
    view = conversation(2)
    calls: list[Block] = [
        ToolCall(id=f"c{i}", name="core_search", input={}) for i in range(5)
    ]
    view.append(NeutralMessage(role="assistant", content=calls))
    latest: list[Block] = [
        ToolResult(tool_call_id=f"c{i}", name="core_search", text="z" * 4000)
        for i in range(5)
    ]
    view.append(NeutralMessage(role="user", content=latest))

    trim(view, estimated_tokens=99_999, trim_at=10, trim_to=5, keep_recent=3)

    assert view.messages[-1].content == latest

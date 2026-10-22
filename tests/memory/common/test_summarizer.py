from unittest.mock import patch

import pytest

from memory.common import settings, summarizer


def test_parse_summary():
    assert summarizer.parse_summary('{"summary": "s", "tags": ["a"]}') == ("s", ["a"])


@pytest.mark.parametrize(
    "response",
    [
        "",
        # A refusal, or a result requested before the switch to JSON output
        "<summary>s</summary>",
        # Output that hit max_tokens
        '{"summary": "s", "tags": ["a',
    ],
)
def test_parse_summary_rejects_unparseable_response(response):
    with pytest.raises(ValueError):
        summarizer.parse_summary(response)


@pytest.mark.parametrize("response", ["", '{"summary": "s", "tags": ["a'])
def test_summarize_falls_back_to_content_on_unparseable_response(response):
    with patch.object(summarizer.llms, "summarize", return_value=response):
        assert summarizer.summarize("short text") == ("short text", [])


def test_summarize_requests_summary_schema():
    response = '{"summary": "short", "tags": ["t"]}'
    with patch.object(summarizer.llms, "summarize", return_value=response) as llm:
        assert summarizer.summarize(LONG_TEXT) == ("short", ["t"])
    assert llm.call_args.kwargs["json_schema"] == summarizer.SUMMARY_SCHEMA


LONG_TEXT = "word " * 2000


def test_summarize_or_defer_outside_deferral_summarizes_inline():
    with patch.object(summarizer, "summarize", return_value=("s", ["t"])) as summarize:
        assert summarizer.summarize_or_defer(LONG_TEXT, {"type": "x"}) == ("s", ["t"])
    summarize.assert_called_once_with(LONG_TEXT)


@pytest.mark.parametrize("chunk_metadata", [{"type": "summary"}, None])
@pytest.mark.parametrize("modality", ["book", None])
def test_summarize_or_defer_records_request_without_calling_llm(chunk_metadata, modality):
    with (
        patch.object(summarizer.llms, "summarize") as llm,
        summarizer.deferring(True) as deferred,
    ):
        assert summarizer.summarize_or_defer(LONG_TEXT, chunk_metadata, modality) is None

    llm.assert_not_called()
    assert deferred == [
        summarizer.DeferredSummary(
            prompt=summarizer.build_prompt(LONG_TEXT, settings.DEFAULT_CHUNK_TOKENS),
            chunk_metadata=chunk_metadata,
            modality=modality,
        )
    ]
    assert "Text to summarize" in deferred[0].prompt


def test_deferring_disabled_summarizes_inline():
    with (
        patch.object(summarizer, "summarize", return_value=("s", [])),
        summarizer.deferring(False) as deferred,
    ):
        assert summarizer.summarize_or_defer(LONG_TEXT, {}) == ("s", [])
    assert deferred == []


def test_deferring_resets_after_block():
    with summarizer.deferring(True):
        pass
    with patch.object(summarizer, "summarize", return_value=("s", [])):
        assert summarizer.summarize_or_defer(LONG_TEXT, {}) == ("s", [])


@pytest.mark.parametrize(
    "use_batch, model, expected",
    [
        (True, "anthropic/claude-haiku-4-5", True),
        (False, "anthropic/claude-haiku-4-5", False),
        (True, "openai/gpt-4o-mini", False),
    ],
)
def test_batch_enabled(use_batch, model, expected):
    with (
        patch.object(settings, "SUMMARIZER_USE_BATCH", use_batch),
        patch.object(settings, "SUMMARIZER_MODEL", model),
    ):
        assert summarizer.batch_enabled() is expected

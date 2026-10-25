import pytest

from memory.common import settings, summarizer, tokens

LONG = "word " * 5000
SHORT = "a short note about gardening"


@pytest.mark.parametrize("summary,expected", [("s", "s"), (None, "")])
def test_parse_summary_returns_summary_and_tags(summary, expected):
    assert summarizer.parse_summary({"summary": summary, "tags": ["a"]}) == (expected, ["a"])


def test_parse_summary_caps_overlong_summaries():
    summary, _ = summarizer.parse_summary({"summary": "x " * 20000, "tags": []})
    assert len(summary) <= settings.DEFAULT_CHUNK_TOKENS * tokens.CHARS_PER_TOKEN + 3


def test_without_an_llm_there_is_no_summary():
    assert summarizer.summarize(LONG) is None


def test_summaries_come_from_the_summariser_in_scope():
    prompts = []

    def fn(prompt):
        prompts.append(prompt)
        return {"summary": "s", "tags": ["t"]}

    with summarizer.summarising(fn):
        assert summarizer.summarize(LONG) == ("s", ["t"])
    assert summarizer.summarize(LONG) is None
    assert "Text to summarize" in prompts[0]


@pytest.mark.parametrize("content", [SHORT, "word " * 600], ids=["short", "within-two-chunks"])
def test_content_not_needing_a_summary_is_only_tagged(content):
    prompts = []

    def fn(prompt):
        prompts.append(prompt)
        return {"summary": None, "tags": ["t"]}

    with summarizer.summarising(fn):
        assert summarizer.summarize(content) == ("", ["t"])
    assert prompts[0].count(content) == 1
    assert "Text to summarize" not in prompts[0]


@pytest.mark.parametrize(
    "chunks, expected",
    [(0, False), (1, False), (2, False), (2.01, True), (10, True)],
)
def test_needs_summary_beyond_two_chunks(chunks, expected):
    length = int(chunks * settings.DEFAULT_CHUNK_TOKENS * tokens.CHARS_PER_TOKEN)
    assert summarizer.needs_summary("a" * length) is expected


def test_a_failed_summariser_leaves_no_summary():
    with summarizer.summarising(lambda prompt: None):
        assert summarizer.summarize(LONG) is None


@pytest.mark.parametrize("chunk_metadata,modality", [({"type": "summary"}, "book"), (None, None)])
def test_summarize_or_defer_records_request_without_calling_llm(chunk_metadata, modality):
    with summarizer.summarising(lambda p: pytest.fail("called")), summarizer.deferring(True) as deferred:
        assert summarizer.summarize_or_defer(LONG, chunk_metadata, modality) is None
    assert [(d.chunk_metadata, d.modality) for d in deferred] == [(chunk_metadata, modality)]


def test_deferring_resets_after_block():
    with summarizer.deferring(True):
        pass
    with summarizer.summarising(lambda prompt: {"summary": "s", "tags": []}):
        assert summarizer.summarize_or_defer(LONG, {}) == ("s", [])


@pytest.mark.parametrize("use_batch,expected", [(True, True), (False, False)])
def test_batch_enabled_follows_the_setting(monkeypatch, use_batch, expected):
    monkeypatch.setattr(settings, "SUMMARIZER_USE_BATCH", use_batch)
    assert summarizer.batch_enabled() is expected


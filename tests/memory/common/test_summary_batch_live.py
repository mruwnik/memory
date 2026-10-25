"""A live summary batch on OpenAI. It costs well under a cent: run it by hand.

OPENAI_API_KEY must be set, plus --run-slow --run-live. OpenAI may take up to
24h to run a batch; the test gives up (and cancels it) after POLL_FOR.
"""

import hashlib
import os
import time
from contextlib import suppress
from datetime import timedelta
from decimal import Decimal

import openai
import pytest

from memory.common import summary_batch
from memory.common.db.models import LLMProviderKey, LLMSpend, SourceItem, SummaryRequest

LIVE_KEY = os.getenv("OPENAI_API_KEY", "")
POLL_FOR = timedelta(hours=3)
POLL_EVERY = timedelta(seconds=30)

pytestmark = [
    pytest.mark.slow,
    pytest.mark.live,
    pytest.mark.skipif(not LIVE_KEY, reason="needs OPENAI_API_KEY"),
]


def poll(db_session) -> dict[str, int]:
    deadline = time.monotonic() + POLL_FOR.total_seconds()
    stats: dict[str, int] = {}
    while not stats and time.monotonic() < deadline:
        time.sleep(POLL_EVERY.total_seconds())
        stats = summary_batch.collect_finished(db_session)
    return stats


def test_an_openai_summary_batch_is_applied_charged_and_cleaned_up(
    db_session, regular_user
):
    key = LLMProviderKey(user_id=regular_user.id, provider="openai", label="live")
    key.key = LIVE_KEY
    content = "Notes from the gardening club: we planted tomatoes and basil."
    item = SourceItem(
        content=content,
        modality="text",
        sha256=hashlib.sha256(content.encode()).digest(),
        mime_type="text/plain",
    )
    db_session.add_all([key, item])
    db_session.flush()
    # No chunk metadata: only tags come back, so nothing is embedded
    db_session.add(
        SummaryRequest(
            source_id=item.id,
            prompt=f"Summarise in one sentence and give two tags:\n\n{content}",
            provider_key_id=key.id,
        )
    )
    db_session.commit()

    (batch_id,) = summary_batch.submit_pending(db_session)
    client = openai.OpenAI(api_key=LIVE_KEY)
    try:
        stats = poll(db_session)
    finally:
        # Stops an unfinished batch; an ended one refuses the cancel
        with suppress(openai.APIError):
            client.batches.cancel(batch_id)

    assert stats == {"applied": 1}
    assert db_session.query(SummaryRequest).count() == 0
    (spend,) = db_session.query(LLMSpend).all()
    assert Decimal(0) < spend.cost_usd < Decimal("0.01")
    batch = client.batches.retrieve(batch_id)
    assert batch.output_file_id is not None
    for file_id in (batch.input_file_id, batch.output_file_id):
        with pytest.raises(openai.NotFoundError):
            client.files.retrieve(file_id)

"""Live check of the settings page's Voyage key check. A one-word embed costs well under $0.0001.

VOYAGE_API_KEY (or VOYAGE_API_KEY_FILE) must be set, plus --run-slow --run-live.
"""

import os
from pathlib import Path

import pytest
import voyageai

from memory.common import embedding

KEY_FILE = os.getenv("VOYAGE_API_KEY_FILE")
LIVE_KEY = os.getenv("VOYAGE_API_KEY") or (Path(KEY_FILE).read_text().strip() if KEY_FILE else "")

pytestmark = [
    pytest.mark.slow,
    pytest.mark.live,
    pytest.mark.skipif(not LIVE_KEY, reason="needs VOYAGE_API_KEY"),
]


@pytest.fixture(autouse=True)
def real_voyage(monkeypatch, mock_voyage_client):
    monkeypatch.setattr(voyageai, "Client", mock_voyage_client.real_client)


# ids, so the key never appears in the test name.
@pytest.mark.parametrize(
    "key, expected", [(LIVE_KEY, None), ("pa-not-a-real-key", 401)], ids=["valid", "invalid"]
)
def test_check_key_reports_voyages_answer(key, expected):
    status = embedding.check_key(key)
    assert status == expected

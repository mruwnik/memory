from types import SimpleNamespace
from unittest.mock import patch

import pytest

from memory.common import chat_urls

ADMIN = SimpleNamespace(scopes=["*"])
MEMBER = SimpleNamespace(scopes=["read", "write"])


@pytest.mark.parametrize(
    "url, owner, allowed_origins, allowed",
    [
        ("https://slack.com/api", MEMBER, [], True),
        ("https://slack.com/oauth/v2/authorize", None, [], True),
        ("https://discord.com/api/v10", MEMBER, [], True),
        ("https://chat.example.com/api", MEMBER, [], False),
        ("https://chat.example.com/api", None, [], False),
        ("https://chat.example.com/api", MEMBER, ["https://chat.example.com"], True),
        ("https://CHAT.example.com/discord/api/v10", MEMBER, ["https://chat.example.com/"], True),
        ("https://chat.example.com:8443/api", MEMBER, ["https://chat.example.com"], False),
        ("https://evil.example.com/api", MEMBER, ["https://chat.example.com"], False),
        ("https://slack.com.evil.example.com/api", MEMBER, [], False),
        ("https://chat.example.com/api", ADMIN, [], True),
    ],
)
def test_chat_url_allowed(url, owner, allowed_origins, allowed):
    with patch.object(chat_urls.settings, "CHAT_API_ALLOWED_ORIGINS", allowed_origins):
        assert chat_urls.chat_url_allowed(url, owner) is allowed

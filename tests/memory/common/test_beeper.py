import httpx
import pytest

from memory.common import beeper
from tests.memory.fake_beeper import URL, FakeBeeperServer


@pytest.mark.parametrize(
    "url, allowed",
    [
        (URL, True),
        (URL + "/v1/accounts", True),
        ("HTTP://Beeper:23373", True),
        ("http://beeper:23374", False),
        ("https://beeper:23373", False),
        ("http://postgres:5432", False),
    ],
)
def test_only_allowlisted_server_origins_are_allowed(fake_beeper, url, allowed):
    assert beeper.server_url_allowed(url) is allowed


@pytest.mark.asyncio
async def test_requests_off_the_allowlist_never_leave(fake_beeper):
    async with beeper.BeeperClient("http://postgres:5432", "token") as client:
        with pytest.raises(beeper.BeeperError) as error:
            await client.accounts()

    assert (error.value.status, error.value.code) == (0, "BeeperURLRefused")
    assert fake_beeper.paths == []


@pytest.mark.asyncio
async def test_redirects_are_not_followed(fake_beeper, monkeypatch):
    def redirect(request: httpx.Request) -> httpx.Response:
        fake_beeper.paths.append(str(request.url))
        return httpx.Response(302, headers={"Location": URL + "/v1/accounts"})

    monkeypatch.setattr(beeper, "inner_transport", lambda: httpx.MockTransport(redirect))
    async with beeper.BeeperClient(URL, "token") as client:
        with pytest.raises(beeper.BeeperError):
            await client.accounts()

    assert fake_beeper.paths == [URL + "/v1/accounts"]


@pytest.mark.asyncio
async def test_matrix_logout_only_goes_to_allowlisted_homeservers(fake_beeper):
    fake_beeper.signed_in = True

    with pytest.raises(beeper.BeeperError):
        await beeper.matrix_logout("https://evil.example.com/", fake_beeper.token)
    assert fake_beeper.paths == []

    await beeper.matrix_logout("https://matrix.beeper.com/", fake_beeper.token)
    assert not fake_beeper.signed_in


@pytest.mark.asyncio
async def test_errors_carry_beepers_code_and_the_version_is_recorded(fake_beeper: FakeBeeperServer):
    fake_beeper.signed_in = True
    fake_beeper.version = "4.4.0"

    async with beeper.BeeperClient(URL) as client:
        with pytest.raises(beeper.BeeperError) as error:
            await client.setup_start()

    assert (error.value.status, error.value.code) == (409, "already_logged_in")
    assert (client.server_version, client.version_matches) == ("4.4.0", False)


def test_chat_ids_are_escaped_in_paths():
    assert beeper.quote_id("!abc/def:beeper.local") == "%21abc%2Fdef%3Abeeper.local"

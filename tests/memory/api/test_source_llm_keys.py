import pytest

from memory.api.llm_keys import llm_key_view
from memory.common.db.models import (
    ArticleFeed,
    CalendarAccount,
    DiscordBot,
    DiscordServer,
    EmailAccount,
    GithubAccount,
    GoogleAccount,
    GoogleFolder,
    LLMProviderKey,
    SlackApp,
    SlackUserCredentials,
    SlackWorkspace,
    TranscriptAccount,
)


def key(db, owner_id, label, provider="anthropic", is_default=False):
    row = LLMProviderKey(user_id=owner_id, provider=provider, label=label, is_default=is_default)
    row.key = f"sk-{label}"
    db.add(row)
    db.commit()
    return row


def email(db, user):
    row = EmailAccount(
        user_id=user.id,
        name="e",
        email_address="e@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        username="u",
        password="p",
    )
    db.add(row)
    db.commit()
    return row, f"/email-accounts/{row.id}"


def feed(db, user):
    row = ArticleFeed(url="https://example.com/feed", user_id=user.id)
    db.add(row)
    db.commit()
    return row, f"/article-feeds/{row.id}"


def github(db, user):
    row = GithubAccount(user_id=user.id, name="g", auth_type="pat")
    db.add(row)
    db.commit()
    return row, f"/github/accounts/{row.id}"


def drive_folder(db, user):
    account = GoogleAccount(user_id=user.id, name="g", email="g@example.com")
    db.add(account)
    db.flush()
    row = GoogleFolder(account_id=account.id, folder_id="f", folder_name="F")
    db.add(row)
    db.commit()
    return row, f"/google-drive/accounts/{account.id}/folders/{row.id}"


def calendar(db, user):
    row = CalendarAccount(user_id=user.id, name="c", calendar_type="caldav")
    db.add(row)
    db.commit()
    return row, f"/calendar-accounts/{row.id}"


def transcripts(db, user):
    row = TranscriptAccount(user_id=user.id, name="t", provider="fireflies")
    row.api_key = "ff-key"
    db.add(row)
    db.commit()
    return row, f"/transcript-accounts/{row.id}"


def slack(db, user):
    row = SlackWorkspace(id="T1", name="w")
    app = SlackApp(client_id="c", name="a", setup_state="live", created_by_user_id=user.id)
    db.add_all([row, app])
    db.flush()
    cred = SlackUserCredentials(
        slack_app_id=app.id, workspace_id="T1", user_id=user.id, scopes=[], slack_user_id="U1"
    )
    cred.access_token = "xoxp"
    db.add(cred)
    db.commit()
    return row, "/slack/workspaces/T1"


def discord(db, user):
    bot = DiscordBot(id=7, name="b", created_by_user_id=user.id)
    bot.authorized_users.append(user)
    db.add(bot)
    db.flush()
    row = DiscordServer(id=5, name="s", bot_id=bot.id)
    db.add(row)
    db.commit()
    return row, "/discord/servers/5"


SOURCES = [email, feed, github, drive_folder, calendar, transcripts, slack, discord]


@pytest.mark.parametrize("make", SOURCES)
@pytest.mark.parametrize("disabled,choice", [(False, "none"), (True, "default")])
def test_every_source_takes_and_shows_its_llm_choice(
    client, db_session, user, make, disabled, choice
):
    row, url = make(db_session, user)
    row.llm_disabled = disabled
    db_session.commit()

    response = client.patch(url, json={"llm_key": choice})

    assert response.status_code == 200, response.text
    assert response.json()["llm_key"]["choice"] == choice


@pytest.mark.parametrize("make", SOURCES)
def test_an_explicit_key_is_kept_with_its_label(client, db_session, user, make):
    mine = key(db_session, user.id, "work")
    _, url = make(db_session, user)

    response = client.patch(url, json={"llm_key": mine.id})

    assert response.status_code == 200, response.text
    assert response.json()["llm_key"] == {
        "choice": "key",
        "key_id": mine.id,
        "label": "work",
        "provider": "anthropic",
        "paid_by_you": True,
        "runs": True,
    }


def own_email(db, caller, other):
    return email(db, caller)


def admin_on_email(db, caller, other):
    return email(db, other)


def slack_co_user(db, caller, other):
    row, url = slack(db, other)
    cred = SlackUserCredentials(
        slack_app_id=db.query(SlackApp).one().id,
        workspace_id=row.id,
        user_id=caller.id,
        scopes=[],
        slack_user_id="U2",
    )
    cred.access_token = "xoxp-2"
    db.add(cred)
    db.commit()
    return row, url


def discord_co_user(db, caller, other):
    row, url = discord(db, other)
    bot = db.get_one(DiscordBot, row.bot_id)
    bot.authorized_users.append(caller)
    db.commit()
    return row, url


def slack_admin(db, caller, other):
    return slack(db, other)


# The caller is always `user`; admin_user owns or registered each source and pays for
# its default.
PAYERS = [(own_email, "regular_client")]
ADMINS = [(admin_on_email, "client"), (slack_admin, "client")]
CO_USERS = [(slack_co_user, "regular_client"), (discord_co_user, "regular_client")]
VIEWERS = PAYERS + ADMINS + CO_USERS


def on_their_key(db, user, admin_user, make):
    theirs = key(db, admin_user.id, "theirs")
    row, url = make(db, user, admin_user)
    row.llm_key_id = theirs.id
    db.commit()
    return row, url, theirs


@pytest.mark.parametrize("make,client_fixture", VIEWERS)
@pytest.mark.parametrize("value,expected", [("mine", (False, "mine")), ("none", (True, None))])
def test_anyone_who_sees_a_source_may_switch_it_to_their_own_key_or_off(
    request, db_session, user, admin_user, make, client_fixture, value, expected
):
    caller = request.getfixturevalue(client_fixture)
    mine = key(db_session, user.id, "mine")
    row, url, _ = on_their_key(db_session, user, admin_user, make)
    values = {"mine": mine.id, "none": "none"}

    response = caller.patch(url, json={"llm_key": values[value]})

    assert response.status_code == 200, response.text
    db_session.refresh(row)
    key_ids = {"mine": mine.id, None: None}
    assert (row.llm_disabled, row.llm_key_id) == (expected[0], key_ids[expected[1]])


@pytest.mark.parametrize(
    "make,client_fixture,status,stored",
    [(*viewer, 200, "default") for viewer in PAYERS + ADMINS]
    + [(*viewer, 403, "theirs") for viewer in CO_USERS],
)
def test_only_the_default_payer_or_an_admin_may_choose_default(
    request, db_session, user, admin_user, make, client_fixture, status, stored
):
    """Default spends the owner's or registrant's money."""
    caller = request.getfixturevalue(client_fixture)
    row, url, theirs = on_their_key(db_session, user, admin_user, make)

    response = caller.patch(url, json={"llm_key": "default"})

    assert response.status_code == status, response.text
    assert "theirs" not in response.text
    db_session.refresh(row)
    key_ids = {"default": None, "theirs": theirs.id}
    assert (row.llm_disabled, row.llm_key_id) == (False, key_ids[stored])


@pytest.mark.parametrize("make,client_fixture", CO_USERS)
def test_anyone_may_echo_an_unchanged_default(
    request, db_session, user, admin_user, make, client_fixture
):
    caller = request.getfixturevalue(client_fixture)
    _, url = make(db_session, user, admin_user)

    response = caller.patch(url, json={"llm_key": "default"})

    assert response.status_code == 200, response.text


@pytest.mark.parametrize("make,client_fixture", VIEWERS)
@pytest.mark.parametrize("start", ["theirs", "default"])
@pytest.mark.parametrize(
    "setup,detail",
    [("openai", "is an openai key"), ("theirs", "unknown API key"), ("missing", "unknown API key")],
)
def test_only_the_callers_own_source_provider_keys_may_be_chosen(
    request, db_session, user, admin_user, make, client_fixture, start, setup, detail
):
    """Resending the hidden key a source is already on fails like an unknown id, so key
    ids can't be enumerated."""
    caller = request.getfixturevalue(client_fixture)
    gpt = key(db_session, user.id, "gpt", provider="openai")
    row, url, theirs = on_their_key(db_session, user, admin_user, make)
    starts = {"theirs": theirs.id, "default": None}
    row.llm_key_id = starts[start]
    db_session.commit()
    values = {"openai": gpt.id, "theirs": theirs.id, "missing": theirs.id + 1000}

    response = caller.patch(url, json={"llm_key": values[setup]})

    assert response.status_code == 400
    assert detail in response.json()["detail"]
    assert "sk-" not in response.text
    assert "theirs" not in response.text
    db_session.refresh(row)
    assert (row.llm_disabled, row.llm_key_id) == (False, starts[start])


@pytest.mark.parametrize("value", ["mine", "default", "none"])
def test_a_user_who_cannot_see_a_source_cannot_change_it(
    regular_client, db_session, user, admin_user, value
):
    mine = key(db_session, user.id, "mine")
    row, url, theirs = on_their_key(db_session, user, admin_user, admin_on_email)
    values = {"mine": mine.id, "default": "default", "none": "none"}

    response = regular_client.patch(url, json={"llm_key": values[value]})

    assert response.status_code == 404
    db_session.refresh(row)
    assert (row.llm_disabled, row.llm_key_id) == (False, theirs.id)


def test_default_shows_the_key_that_would_pay(db_session, user):
    key(db_session, user.id, "personal", is_default=True)
    row, _ = feed(db_session, user)

    view = llm_key_view(db_session, user, row)

    assert (view.choice, view.label, view.paid_by_you, view.runs) == (
        "default",
        "personal",
        True,
        True,
    )


def test_someone_elses_default_is_not_named(db_session, user, admin_user):
    key(db_session, admin_user.id, "theirs", is_default=True)
    row, _ = feed(db_session, admin_user)

    view = llm_key_view(db_session, user, row)

    assert (view.label, view.paid_by_you, view.runs) == (None, False, True)


def test_a_payer_without_a_default_key_runs_nothing(db_session, user):
    row, _ = feed(db_session, user)

    view = llm_key_view(db_session, user, row)

    assert (view.paid_by_you, view.runs) == (False, False)


@pytest.mark.parametrize(
    "viewer,expected",
    [("user", (False, None, False)), ("admin_user", (True, "theirs", True))],
)
def test_a_chosen_key_is_named_only_to_its_owner(
    request, db_session, user, admin_user, viewer, expected
):
    row, _, theirs = on_their_key(db_session, user, admin_user, own_email)

    view = llm_key_view(db_session, request.getfixturevalue(viewer), row)

    assert (view.choice, view.runs) == ("key", True)
    assert (view.key_id == theirs.id, view.label, view.paid_by_you) == expected


def test_a_new_folder_takes_its_owners_key(client, db_session, user):
    mine = key(db_session, user.id, "work")
    account = GoogleAccount(user_id=user.id, name="g", email="g@example.com")
    db_session.add(account)
    db_session.commit()

    response = client.post(
        f"/google-drive/accounts/{account.id}/folders",
        json={"folder_id": "f", "folder_name": "F", "llm_key": mine.id},
    )

    assert response.status_code == 200, response.text
    assert response.json()["llm_key"]["key_id"] == mine.id

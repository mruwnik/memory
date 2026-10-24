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


@pytest.mark.parametrize(
    "setup,detail",
    [("openai", "is an openai key"), ("foreign", "unknown API key")],
)
def test_unusable_explicit_keys_are_refused_on_save(
    client, db_session, user, admin_user, setup, detail
):
    keys = {
        "openai": key(db_session, user.id, "gpt", provider="openai"),
        "foreign": key(db_session, admin_user.id, "theirs"),
    }
    _, url = email(db_session, user)

    response = client.patch(url, json={"llm_key": keys[setup].id})

    assert response.status_code == 400
    assert detail in response.json()["detail"]
    assert "sk-" not in response.text


def slack_co_user(db, payer, caller):
    row, url = slack(db, payer)
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


def discord_co_user(db, payer, caller):
    row, url = discord(db, payer)
    bot = db.get_one(DiscordBot, row.bot_id)
    bot.authorized_users.append(caller)
    db.commit()
    return row, url


def admin_on_email(db, payer, caller):
    return email(db, payer)


NON_PAYERS = [
    (slack_co_user, "regular_client"),
    (discord_co_user, "regular_client"),
    (admin_on_email, "client"),
]


@pytest.mark.parametrize("make,client_fixture", NON_PAYERS)
@pytest.mark.parametrize("value", ["default", "own key"])
def test_only_the_payer_may_change_the_choice(
    request, db_session, user, admin_user, make, client_fixture, value
):
    caller = request.getfixturevalue(client_fixture)
    values = {"default": "default", "own key": key(db_session, user.id, "mine").id}
    row, url = make(db_session, admin_user, user)
    row.llm_disabled = True
    db_session.commit()

    response = caller.patch(url, json={"llm_key": values[value]})

    assert response.status_code == 403, response.text
    db_session.refresh(row)
    assert (row.llm_disabled, row.llm_key_id) == (True, None)


@pytest.mark.parametrize("make,client_fixture", NON_PAYERS)
def test_a_non_payer_may_echo_the_unchanged_choice(
    request, db_session, user, admin_user, make, client_fixture
):
    caller = request.getfixturevalue(client_fixture)
    row, url = make(db_session, admin_user, user)
    row.llm_disabled = True
    db_session.commit()

    response = caller.patch(url, json={"llm_key": "none"})

    assert response.status_code == 200, response.text
    assert response.json()["llm_key"]["choice"] == "none"


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


def test_a_key_the_payer_no_longer_holds_falls_back_to_their_default(
    db_session, user, admin_user
):
    """Ingest skips a chosen key that isn't the payer's, so the view must too."""
    key(db_session, user.id, "personal", is_default=True)
    row, _ = feed(db_session, user)
    row.llm_key_id = key(db_session, admin_user.id, "theirs").id
    db_session.commit()

    view = llm_key_view(db_session, user, row)

    assert (view.choice, view.key_id, view.label) == ("default", None, "personal")


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

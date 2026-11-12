import pytest

from memory.common.people import link_person_to_user
from memory.common.db.models import (
    DiscordBot,
    DiscordUser,
    EmailAccount,
    HumanUser,
    GithubAccount,
    GoogleAccount,
    Person,
    PersonTidbit,
    SlackApp,
    SlackUserCredentials,
    SlackWorkspace,
    Team,
    team_members,
)
from memory.common.db.models.source_item import source_item_people
from memory.common.db.models.sources import GithubUser
from memory.common.identity import (
    claim_person,
    link_discord_user,
    link_proven_person,
    suggestions,
)
from memory.common.notify import channel_problems, user_info_for
from memory.workers.tasks.github import _resolve_author_person
from memory.workers.tasks.maintenance import link_user_people
from tests.conftest import make_source_item

DISCORD_ID = 123456789012345678
GITHUB_ID = 583231


def slack_credential(db, user, workspace_owner_scopes=("*",)) -> None:
    owner = HumanUser(email="slack-owner@example.com", name="o", password_hash="x", scopes=list(workspace_owner_scopes))
    db.add(owner)
    db.flush()
    db.add(SlackWorkspace(id="T1", name="w", owner_id=owner.id))
    app = SlackApp(client_id="c", name="app", created_by_user_id=user.id)
    db.add(app)
    db.flush()
    db.add(SlackUserCredentials(slack_app_id=app.id, workspace_id="T1", user_id=user.id, slack_user_id="U1"))


def prove(db, user, kind: str) -> None:
    """Give ``user`` the credential that proves the ``kind`` account the ingest Person carries."""
    if kind == "slack":
        slack_credential(db, user)
    elif kind == "github":
        db.add(GithubAccount(user_id=user.id, name="whatever", auth_type="pat", verified_login="octo", verified_github_id=GITHUB_ID))
    elif kind == "email":
        db.add(GoogleAccount(user_id=user.id, name="g", email="me@corp.com", email_verified=True))
    db.flush()


def ingest_person(db, identifier: str = "daniel", creator_id: int | None = None) -> Person:
    """A Person ingest made, carrying every account prove() can prove."""
    person = Person(
        identifier=identifier,
        display_name=identifier,
        creator_id=creator_id,
        contact_info={"slack": {"T1": {"user_id": "U1"}}, "github": "octo", "email": "me@corp.com"},
    )
    db.add(person)
    db.flush()
    db.add(DiscordUser(id=DISCORD_ID, username="dan#1", person_id=person.id))
    db.add(GithubUser(id=GITHUB_ID, username="octo", person_id=person.id, linked_by_id=True))
    db.flush()
    return person


def own_person(db, user, **kwargs) -> Person:
    person = Person(identifier=f"own_{user.id}", display_name="own", user_id=user.id, creator_id=user.id, **kwargs)
    db.add(person)
    db.flush()
    return person


PROOFS = ["slack", "github", "email"]


@pytest.mark.parametrize("kind", PROOFS)
def test_a_user_without_a_person_is_linked_to_the_one_their_account_proves(db_session, regular_user, kind):
    person = ingest_person(db_session)
    prove(db_session, regular_user, kind)

    assert link_proven_person(db_session, regular_user) == []
    assert person.user_id == regular_user.id


@pytest.mark.parametrize(
    "unproven",
    [
        # A GitHub account's name is self-attested; only the verified id proves anything.
        lambda db, u: db.add(GithubAccount(user_id=u.id, name="octo", auth_type="pat")),
        # A login can be renamed and reused by someone else.
        lambda db, u: db.add(GithubAccount(user_id=u.id, name="g", auth_type="pat", verified_login="octo")),
        # An App installation belongs to an org or account, not to the user.
        lambda db, u: db.add(
            GithubAccount(user_id=u.id, name="g", auth_type="app", app_id=1, installation_id=2, verified_login="octo", verified_github_id=GITHUB_ID)
        ),
        # Google didn't verify the address.
        lambda db, u: db.add(GoogleAccount(user_id=u.id, name="g", email="me@corp.com")),
        # Anyone can make a Slack workspace with any user in it; only one an admin connected counts.
        lambda db, u: slack_credential(db, u, workspace_owner_scopes=("teams",)),
        # An IMAP account's address is typed in by the user.
        lambda db, u: db.add(
            EmailAccount(user_id=u.id, name="e", email_address="me@corp.com", account_type="imap", imap_server="x", imap_port=993, username="me", use_ssl=True)
        ),
        # A Discord link code can be phished, so it never makes a Person a candidate.
        lambda db, u: setattr(db.get(DiscordUser, DISCORD_ID), "verified_user_id", u.id),
    ],
)
def test_self_attested_accounts_link_nothing(db_session, regular_user, unproven):
    person = ingest_person(db_session)
    unproven(db_session, regular_user)
    db_session.flush()

    assert link_proven_person(db_session, regular_user) == []
    assert person.user_id is None


@pytest.mark.parametrize("kind", PROOFS)
def test_a_person_linked_to_another_user_is_never_a_candidate(db_session, regular_user, admin_user, kind):
    person = ingest_person(db_session)
    person.user_id = admin_user.id
    own = own_person(db_session, regular_user)
    prove(db_session, regular_user, kind)

    assert link_proven_person(db_session, regular_user) == []
    assert person.user_id == admin_user.id
    assert own.user_id == regular_user.id
    with pytest.raises(PermissionError):
        claim_person(db_session, regular_user, person.id)


@pytest.mark.parametrize("kind", PROOFS)
def test_a_person_a_non_admin_planted_is_never_a_candidate(db_session, regular_user, test_user, kind):
    planted = ingest_person(db_session, creator_id=test_user.id)
    prove(db_session, regular_user, kind)

    assert link_proven_person(db_session, regular_user) == []
    assert planted.user_id is None
    with pytest.raises(PermissionError):
        claim_person(db_session, regular_user, planted.id)


def victim_person(db, email="victim@corp.com") -> Person:
    """Someone else's Person: in a team, holding their Discord account."""
    person = Person(identifier="victim", display_name="Victim", contact_info={"email": email})
    db.add(person)
    db.flush()
    team = Team(name="secret-team", slug="secret-team")
    db.add(team)
    db.flush()
    db.execute(team_members.insert().values(team_id=team.id, person_id=person.id, role="member"))
    db.add(DiscordUser(id=DISCORD_ID, username="victim#1", person_id=person.id))
    db.flush()
    return person


def add_tidbit(db, person, user):
    db.add(PersonTidbit(person_id=person.id, content="x", tidbit_type="note", creator_id=user.id, modality="text", sha256=b"t" * 32))


def add_item(db, person, user):
    item = make_source_item(db, 1)
    db.execute(source_item_people.insert().values(source_item_id=item.id, person_id=person.id))


def add_team(db, person, user):
    team = Team(slug="t", name="t")
    db.add(team)
    db.flush()
    db.execute(team_members.insert().values(team_id=team.id, person_id=person.id, role="member"))


def made_by_someone_else(db, person, user):
    person.creator_id = None


def second_candidate(db, person, user):
    two = Person(identifier="dan_two", display_name="dan two")
    db.add(two)
    db.flush()
    db.add(GithubUser(id=GITHUB_ID + 1, username="octo-two", person_id=two.id, linked_by_id=True))
    db.add(GithubAccount(user_id=user.id, name="two", auth_type="pat", verified_login="octo-two", verified_github_id=GITHUB_ID + 1))


@pytest.mark.parametrize(
    "spoil", [add_tidbit, add_item, add_team, made_by_someone_else, second_candidate]
)
def test_only_a_bare_setup_person_is_merged_away_otherwise_suggest(db_session, regular_user, spoil):
    candidate = ingest_person(db_session)
    own = own_person(db_session, regular_user, contact_info={"email": regular_user.email})
    prove(db_session, regular_user, "github")
    spoil(db_session, own, regular_user)
    db_session.flush()

    suggestions = link_proven_person(db_session, regular_user)

    assert candidate.id in [s.person.id for s in suggestions]
    assert own.user_id == regular_user.id
    assert candidate.user_id is None


def test_a_bare_setup_person_merges_into_the_proven_one(db_session, regular_user):
    candidate = ingest_person(db_session)
    own = own_person(db_session, regular_user, contact_info={"email": regular_user.email})
    prove(db_session, regular_user, "slack")

    assert link_proven_person(db_session, regular_user) == []

    db_session.expire_all()
    assert db_session.get(Person, own.id) is None
    survivor = db_session.get(Person, candidate.id)
    assert survivor.user_id == regular_user.id
    assert regular_user.email in survivor.contact_info["email"]


def test_maintenance_links_a_setup_person_to_the_ingest_person_sharing_its_slack_id(db_session, regular_user):
    """The setup Person ("dan") gives way to the one ingest made ("daniel"), and its Discord account notifies."""
    setup_person = link_person_to_user(db_session, regular_user)
    assert setup_person is not None
    setup_person_id = setup_person.id
    daniel = ingest_person(db_session)
    db_session.add(DiscordBot(application_id=1, name="bot", created_by_user_id=regular_user.id))
    prove(db_session, regular_user, "slack")
    db_session.commit()

    assert link_user_people() == {"users": 1, "suggestions": 0}

    db_session.expire_all()
    assert db_session.get(Person, setup_person_id) is None
    assert regular_user.person.id == daniel.id
    info = user_info_for(db_session, regular_user)
    assert list(info["discord_accounts"]) == [str(DISCORD_ID)]
    assert "discord" not in channel_problems({"user": info})


def test_link_discord_user_attaches_an_unlinked_account_to_the_users_person(db_session, regular_user):
    own = own_person(db_session, regular_user)
    add_tidbit(db_session, own, regular_user)
    db_session.add(DiscordUser(id=DISCORD_ID, username="dan#1"))
    db_session.flush()

    assert link_discord_user(db_session, regular_user, DISCORD_ID) == own

    discord_user = db_session.get(DiscordUser, DISCORD_ID)
    assert (discord_user.person_id, discord_user.verified_user_id) == (own.id, regular_user.id)


@pytest.mark.parametrize("has_person", [True, False])
def test_link_discord_user_leaves_an_account_on_another_person_where_it_is(db_session, regular_user, has_person):
    """A phished code: the account's owner DMed someone else's code. Their Person
    must not become the code user's, merged, linked or suggested."""
    victim = victim_person(db_session)
    own = link_person_to_user(db_session, regular_user) if has_person else None

    assert link_discord_user(db_session, regular_user, DISCORD_ID) is None

    db_session.expire_all()
    assert db_session.get(DiscordUser, DISCORD_ID).person_id == victim.id
    assert db_session.get(Person, victim.id).user_id is None
    assert (regular_user.person and regular_user.person.id) == (own and own.id)
    assert suggestions(db_session, regular_user) == []
    with pytest.raises(PermissionError):
        claim_person(db_session, regular_user, victim.id)


def test_link_discord_user_gives_a_user_without_a_person_a_new_one_not_their_emails(db_session, regular_user):
    """A user's email is self-service, so it can't pick which Person a Discord link joins."""
    victim = victim_person(db_session, email="victim@corp.com")
    regular_user.email = "victim@corp.com"
    db_session.add(DiscordUser(id=111, username="attacker#1"))
    db_session.flush()

    person = link_discord_user(db_session, regular_user, 111)

    assert person is not None and person.id != victim.id
    assert (person.user_id, regular_user.person.id) == (regular_user.id, person.id)
    assert db_session.get(Person, victim.id).user_id is None
    assert db_session.get(DiscordUser, 111).person_id == person.id


def test_link_discord_user_refuses_an_account_on_another_users_person(db_session, regular_user, admin_user):
    candidate = ingest_person(db_session)
    candidate.user_id = admin_user.id
    own_person(db_session, regular_user)

    assert link_discord_user(db_session, regular_user, DISCORD_ID) is None

    discord_user = db_session.get(DiscordUser, DISCORD_ID)
    assert (discord_user.person_id, discord_user.verified_user_id) == (candidate.id, None)


def test_claim_merges_the_proven_person_into_the_users_own(db_session, regular_user):
    candidate = ingest_person(db_session)
    own = own_person(db_session, regular_user)
    add_tidbit(db_session, own, regular_user)
    prove(db_session, regular_user, "github")

    claim_person(db_session, regular_user, candidate.id)

    db_session.expire_all()
    assert db_session.get(Person, candidate.id) is None
    assert db_session.get(DiscordUser, DISCORD_ID).person_id == own.id
    assert db_session.get(Person, own.id).contact_info["github"] == "octo"


@pytest.mark.parametrize("give_person", [lambda db, u: None, own_person])
def test_claim_without_proof_is_refused(db_session, regular_user, give_person):
    candidate = ingest_person(db_session)
    give_person(db_session, regular_user)

    with pytest.raises(PermissionError):
        claim_person(db_session, regular_user, candidate.id)
    assert candidate.user_id is None


def test_a_github_handle_in_contact_info_proves_nothing(db_session, regular_user):
    person = Person(identifier="gh", display_name="gh", contact_info={"github": "octo"})
    db_session.add(person)
    db_session.flush()
    prove(db_session, regular_user, "github")

    assert link_proven_person(db_session, regular_user) == []
    assert person.user_id is None


def test_ingest_matching_a_typed_github_handle_makes_no_proof(db_session, regular_user):
    """An unverified handle on someone's Person; whoever holds that login on
    GitHub authors a synced item, and then proves the account with their PAT."""
    victim = victim_person(db_session)
    victim.contact_info = {"github": "victim-handle"}
    db_session.flush()
    own = link_person_to_user(db_session, regular_user)
    assert _resolve_author_person(db_session, "victim-handle", 777) == victim.id
    db_session.add(
        GithubAccount(user_id=regular_user.id, name="x", auth_type="pat", verified_login="victim-handle", verified_github_id=777)
    )
    db_session.flush()

    assert link_proven_person(db_session, regular_user) == []
    db_session.refresh(regular_user)
    assert (regular_user.person and regular_user.person.id) == (own and own.id)
    assert db_session.get(Person, victim.id).user_id is None

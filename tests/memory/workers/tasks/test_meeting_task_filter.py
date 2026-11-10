"""Which action items a transcript account turns into Tasks."""

import pytest

from memory.common.db.models import Person, Task, TranscriptAccount
from memory.common.db.models.source_items import Meeting
from memory.workers.tasks import meetings
from tests.conftest import unique_sha256
from tests.memory.agents.fakes import submitted


def make_person(db, display_name, aliases=(), email=None):
    person = Person(
        identifier=unique_sha256(display_name).hex()[:16],
        display_name=display_name,
        aliases=list(aliases),
        contact_info={"email": email} if email else {},
    )
    db.add(person)
    db.flush()
    return person


@pytest.fixture
def me(db_session):
    return make_person(db_session, "Daniel O'Connell", aliases=["Danny"], email="dan@example.com")


@pytest.fixture
def alice(db_session):
    return make_person(db_session, "Alice Smith")


def make_meeting(db, creator_id, account=None, attendees=(), content="Alice: hi"):
    meeting = Meeting(
        modality="meeting", sha256=unique_sha256("m"), content=content, creator_id=creator_id
    )
    meeting.transcript_account = account
    meeting.people = list(attendees)
    db.add(meeting)
    db.flush()
    return meeting


def make_account(db, user_id, create_tasks=True, task_people=()):
    account = TranscriptAccount(
        user_id=user_id,
        name="FF",
        provider="fireflies",
        create_tasks=create_tasks,
        task_people=list(task_people),
    )
    db.add(account)
    db.flush()
    return account


def item(description, assignee):
    return {"description": description, "assignee": assignee, "due_date": None, "priority": None}


def assigned(db, assignee, chosen, attendees=(), content="Alice: hi"):
    meeting = make_meeting(db, None, attendees=attendees, content=content)
    others = meetings.other_attendee_names(meeting, chosen)
    return meetings.assigned_to(assignee, chosen, others)


@pytest.mark.parametrize(
    "assignee, expected",
    [
        ("Daniel O'Connell", True),
        ("  daniel   O'CONNELL ", True),
        ("Danny", True),
        ("DAN@example.com", True),
        ("Daniel", False),
        ("Alice Smith", False),
        ("Alice", False),
        ("Bob Nobody", False),
        ("Bob", False),
        ("", False),
        (None, False),
    ],
)
def test_assigned_to_matches_the_chosen_people(db_session, me, alice, assignee, expected):
    assert assigned(db_session, assignee, [me], [me, alice]) is expected


def test_assigned_to_matches_any_chosen_person(db_session, me):
    other_me = make_person(db_session, "Dee", aliases=["The Boss"])

    assert assigned(db_session, "the boss", [me, other_me])


@pytest.mark.parametrize(
    "assignee, content",
    [
        ("Danny", "Danny Brown: I'll do it"),
        ("Danny", "Attendees: Daniel O'Connell, Danny Brown\n\nDaniel O'Connell: hi"),
        ("Danny", "Attendees: Danny\n\nDanny Brown: hi"),
    ],
)
def test_a_name_an_unlinked_attendee_shares_is_ambiguous(db_session, me, assignee, content):
    assert not assigned(db_session, assignee, [me], [me], content)


@pytest.mark.parametrize(
    "assignee, namesake",
    [
        ("Danny", ("Danny Brown", ())),
        ("Danny", ("Bob Brown", ("Danny",))),
        ("Daniel O'Connell", ("Daniel O'Connell", ())),
    ],
)
def test_a_name_a_linked_attendee_shares_is_ambiguous(db_session, me, assignee, namesake):
    other = make_person(db_session, namesake[0], aliases=namesake[1])

    assert not assigned(db_session, assignee, [me], [me, other])


@pytest.mark.parametrize(
    "attendees, content",
    [
        ([], ""),
        (["Dbrown"], "Attendees: dan@example.com, dbrown@x.com\n\nDbrown: hi"),
        (["Daniel Brown"], "Daniel Brown: hi"),
    ],
)
def test_a_bare_first_name_never_matches(db_session, me, attendees, content):
    people = [make_person(db_session, name) for name in attendees]

    assert not assigned(db_session, "Daniel", [me], people, content)


@pytest.mark.parametrize(
    "assignee, content",
    [
        ("Danny", "Attendees: Daniel O'Connell, Alice Smith\n\nDanny: hi\nAlice Smith: hello"),
        ("Daniel O'Connell", "Daniel: hi\nDaniel Brown: hello"),
        ("Daniel O'Connell", ""),
        ("dan@example.com", "Attendees: dan@example.com\n\ndan@example.com: hi"),
    ],
)
def test_labels_naming_the_chosen_person_or_someone_else_dont_block_them(
    db_session, me, assignee, content
):
    assert assigned(db_session, assignee, [me], [], content)


def test_a_name_two_chosen_people_share_matches(db_session, me):
    namesake = make_person(db_session, "Danny Jones")

    assert assigned(db_session, "Danny", [me, namesake], [me, namesake])


@pytest.mark.parametrize(
    "content, expected",
    [
        ("", set()),
        ("Alice: hi\nBob Brown: hello: there", {"alice", "bob brown"}),
        ("Attendees: Ann, b@x.com\n\nCarl: hi", {"ann", "b@x.com", "carl"}),
        ("See https://x.com/a\nno label here", set()),
    ],
)
def test_transcript_names_reads_the_attendee_list_and_speaker_labels(content, expected):
    assert meetings.transcript_names(content) == expected


ITEMS = [item("Mine", "Daniel O'Connell"), item("Hers", "Alice Smith"), item("Nobody's", None)]


@pytest.mark.parametrize(
    "create_tasks, chosen, expected",
    [
        (True, [], ["Mine", "Hers", "Nobody's"]),
        (True, ["me"], ["Mine"]),
        (True, ["me", "alice"], ["Mine", "Hers"]),
        (False, [], []),
        (False, ["me"], []),
    ],
)
def test_task_action_items_follow_the_account(
    db_session, regular_user, me, alice, create_tasks, chosen, expected
):
    people = {"me": me, "alice": alice}
    account = make_account(db_session, regular_user.id, create_tasks, [people[c] for c in chosen])
    meeting = make_meeting(db_session, regular_user.id, account, [me, alice])

    kept = meetings.task_action_items(meeting, ITEMS)

    assert [i["description"] for i in kept] == expected


def test_meetings_without_an_account_make_tasks_for_every_item(db_session, regular_user):
    meeting = make_meeting(db_session, regular_user.id)

    assert meetings.task_action_items(meeting, ITEMS) == ITEMS


EXTRACTED = {"summary": "We met.", "notes": "- a", "action_items": ITEMS}


def meeting_tasks(db, meeting):
    return sorted(t.task_title for t in db.query(Task).filter(Task.source_item_id == meeting.id))


def test_extraction_makes_tasks_only_for_the_chosen_people(
    db_session, regular_user, llm_key, scripted_llm, me, alice
):
    scripted_llm.script = [submitted(EXTRACTED)]
    account = make_account(db_session, regular_user.id, task_people=[me])
    meeting = make_meeting(db_session, regular_user.id, account, [me, alice])

    result = meetings.extract_and_update_meeting(db_session, meeting)

    assert (result["tasks_created"], meeting_tasks(db_session, meeting)) == (1, ["Mine"])


def test_extraction_with_tasks_off_keeps_the_notes(db_session, regular_user, llm_key, scripted_llm):
    scripted_llm.script = [submitted(EXTRACTED)]
    account = make_account(db_session, regular_user.id, create_tasks=False)
    meeting = make_meeting(db_session, regular_user.id, account)

    result = meetings.extract_and_update_meeting(db_session, meeting)

    assert (result["status"], meeting.summary, meeting.notes) == ("success", "We met.", "- a")
    assert meeting_tasks(db_session, meeting) == []


def test_reextracting_after_turning_tasks_off_drops_untouched_tasks(
    db_session, regular_user, llm_key, scripted_llm
):
    scripted_llm.script = [submitted(EXTRACTED), submitted(EXTRACTED)]
    account = make_account(db_session, regular_user.id)
    meeting = make_meeting(db_session, regular_user.id, account)
    meetings.extract_and_update_meeting(db_session, meeting)
    started = db_session.query(Task).filter(Task.task_title == "Hers").one()
    started.status = "in_progress"

    account.create_tasks = False
    meetings.extract_and_update_meeting(db_session, meeting)

    assert meeting_tasks(db_session, meeting) == ["Hers"]

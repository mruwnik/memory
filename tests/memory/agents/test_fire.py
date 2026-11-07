from datetime import timedelta

import pytest

from memory.agents import triggers
from memory.agents.fire import fire_trigger, trip
from memory.agents.triggers import content_version
from memory.common import settings
from memory.common.celery_app import FIRE_TRIGGER, RUN_AGENT, SEND_NOTIFICATION
from memory.common.db.models import (
    AgentDefinition,
    AgentRun,
    AgentTrigger,
    AgentTriggerMatch,
    APIKey,
    BlogPost,
)
from memory.common.db.models.sources import (
    Person,
    Project,
    Team,
    project_teams,
    team_members,
)
from tests.conftest import route_item, unique_sha256
from tests.memory.agents.test_triggers import posted_run, slack_message

SPEC = {"system_prompt": "x", "model": "anthropic/m", "reasoning": "high"}
CONFIG = {
    "filter": {"tags_any": ["ai"]},
    "delivery": {"mode": "per_item"},
    "template": "New:\n{items}",
}


@pytest.fixture(autouse=True)
def ceilings(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_MAX_RUNS_PER_HOUR", 60)


def setup(db, user, spec=SPEC, enabled=True, config=CONFIG):
    d = AgentDefinition(user_id=user.id, name="watcher", spec=spec)
    db.add(d)
    db.flush()
    t = AgentTrigger(
        user_id=user.id,
        name="t",
        definition_id=d.id,
        handler={"agent": d.id},
        kind="ingest",
        config=config,
        enabled=enabled,
    )
    db.add(t)
    db.commit()
    return t


def post(db, title="Post", project_id=None, creator_id=None):
    p = BlogPost(
        sha256=unique_sha256("p"),
        modality="blog",
        title=title,
        content="body",
        tags=["ai"],
        embed_status="STORED",
        project_id=project_id,
        creator_id=creator_id,
    )
    db.add(p)
    db.commit()
    return p


def fire_dispatched(dispatch):
    """Run the FIRE_TRIGGER tasks dispatched since the last call."""
    fires = [c.kwargs["args"] for c in dispatch.call_args_list if c.args[0] == FIRE_TRIGGER]
    for args in fires:
        fire_trigger(*args)
    dispatch.reset_mock()


def rendered(p, change="new", body="body"):
    return (
        f"- [blog_post #{p.id}] {change}\n"
        f"  inserted_at: {p.inserted_at.isoformat()}\n"
        f"  <untrusted_item id={p.id} field=title>{p.title}</untrusted_item>\n"
        f"  <untrusted_item id={p.id} field=content>{body}</untrusted_item>"
    )


def claimed(db, trigger, *items):
    """Pending deliveries of `items` to the trigger, as the router writes them."""
    db.add_all(
        AgentTriggerMatch(
            trigger_id=trigger.id, item_id=i.id, content_hash=content_version(i), changes=["new"]
        )
        for i in items
    )
    db.commit()
    return [f"{i.id}:{content_version(i).hex()}" for i in items]


def test_a_fire_starts_one_run_with_the_rendered_input(
    db_session, admin_user, fake_providers, admin_keys, no_celery_dispatch
):
    trigger = setup(db_session, admin_user)
    a, b = post(db_session, "A"), post(db_session, "B")
    deliveries = claimed(db_session, trigger, b, a)

    run_id = fire_trigger(trigger.id, deliveries)
    a.content, a.sha256 = "edited after the claim", unique_sha256("edited")
    db_session.commit()
    redelivered = fire_trigger(trigger.id, deliveries[::-1])

    run = db_session.get(AgentRun, run_id)
    assert run is not None
    assert redelivered is None
    assert (run.trigger_kind, run.trigger_ref, run.definition_id, run.trigger_id) == (
        "ingest",
        {
            "trigger_id": trigger.id,
            "item_ids": [a.id, b.id],
            "changes": {str(a.id): ["new"], str(b.id): ["new"]},
        },
        trigger.definition_id,
        trigger.id,
    )
    assert run.input == "New:\n" + "\n".join(rendered(p) for p in (a, b))
    no_celery_dispatch.assert_called_once_with(RUN_AGENT, args=[run.id], task_id=run.id)


def test_a_code_slug_trigger_runs_that_agent(db_session, admin_user, code_agents, admin_keys):
    trigger = AgentTrigger(
        user_id=admin_user.id, name="t", handler={"agent": "echo"}, kind="ingest", config=CONFIG
    )
    db_session.add(trigger)
    db_session.commit()
    item = post(db_session)

    run = db_session.get_one(AgentRun, fire_trigger(trigger.id, claimed(db_session, trigger, item)))

    assert (run.agent, run.definition_id, run.input) == ("echo", None, "New:\n" + rendered(item))


@pytest.mark.parametrize(
    "delivery,inputs",
    [
        (
            {"mode": "per_item"},
            [("new", "body"), ("edited", "body, edited")],
        ),
        # Edited before the batch went out, so the run is the agent's first sight of it.
        (
            {"mode": "batch", "window_sec": 60, "max_items": 10},
            [("new", "body, edited")],
        ),
    ],
)
def test_an_edit_starts_a_run_labelled_by_what_the_agent_saw_before(
    db_session, admin_user, fake_providers, admin_keys, no_celery_dispatch, delivery, inputs
):
    trigger = setup(db_session, admin_user, config=CONFIG | {"delivery": delivery, "on_edit": True})
    item = post(db_session, "A")

    route_item(db_session, item.id)
    fire_dispatched(no_celery_dispatch)
    item.content = "body, edited"
    db_session.commit()
    route_item(db_session, item.id)
    for m in db_session.query(AgentTriggerMatch).filter_by(trigger_id=trigger.id):
        m.created_at -= timedelta(minutes=2)
    db_session.commit()
    triggers.sweep_deliveries()
    fire_dispatched(no_celery_dispatch)

    # Every run in a test shares one transaction, so created_at can't order them.
    assert sorted(r.input for r in db_session.query(AgentRun)) == sorted(
        "New:\n" + rendered(item, change, body) for change, body in inputs
    )


def test_the_changes_that_matched_reach_the_runs_trigger_ref(
    db_session, admin_user, fake_providers, admin_keys, no_celery_dispatch
):
    setup(db_session, admin_user, config=CONFIG | {"on_edit": True})
    item = post(db_session, "A")

    route_item(db_session, item.id)
    item.content = "body, pushed"
    db_session.commit()
    route_item(db_session, item.id, changes=["commits"])
    fire_dispatched(no_celery_dispatch)

    refs = sorted(run.trigger_ref["changes"][str(item.id)] for run in db_session.query(AgentRun))
    assert refs == [["commits"], ["new"]]


def test_disabled_triggers_and_unreadable_items_start_nothing(
    db_session, admin_user, regular_user, fake_providers
):
    regular_user.scopes = ["agents", "read"]
    off = setup(db_session, admin_user, enabled=False)
    theirs = setup(db_session, regular_user)
    hidden = post(db_session, project_id=None)

    fired = (
        fire_trigger(off.id, claimed(db_session, off, hidden)),
        fire_trigger(theirs.id, claimed(db_session, theirs, hidden)),
    )

    db_session.expire_all()
    assert fired == (None, None)
    assert db_session.query(AgentRun).count() == 0
    # A disabled trigger's deliveries wait for it to be enabled again.
    assert [
        db_session.get(AgentTriggerMatch, (t.id, hidden.id, content_version(hidden))).status
        for t in (off, theirs)
    ] == ["pending", "skipped:inaccessible"]


@pytest.mark.parametrize(
    "key_scopes,revoked,reason",
    [(["agents"], True, "no longer valid"), (["read"], False, "'agents' scope")],
)
def test_lost_credentials_trip_the_breaker(
    db_session, admin_user, fake_providers, admin_keys, key_scopes, revoked, reason
):
    key = APIKey.create(user_id=admin_user.id, name="k", scopes=key_scopes)
    key.revoked = revoked
    db_session.add(key)
    db_session.commit()
    trigger = setup(db_session, admin_user)
    trigger.api_key_id, trigger.via_api_key = key.id, True
    db_session.commit()

    assert fire_trigger(trigger.id, claimed(db_session, trigger, post(db_session))) is None

    db_session.refresh(trigger)
    assert trigger.enabled is False
    assert reason in trigger.state["tripped"]["reason"]


def test_a_valid_key_bounds_the_triggered_run(db_session, admin_user, fake_providers, admin_keys):
    scopes = ["agents", "read"]
    key = APIKey.create(user_id=admin_user.id, name="k", scopes=scopes)
    db_session.add(key)
    db_session.commit()
    trigger = setup(db_session, admin_user)
    trigger.api_key_id, trigger.via_api_key = key.id, True
    db_session.commit()

    item = post(db_session, creator_id=admin_user.id)

    run = db_session.get(AgentRun, fire_trigger(trigger.id, claimed(db_session, trigger, item)))

    assert run is not None
    assert (run.api_key_id, sorted(run.scopes)) == (key.id, scopes)


@pytest.mark.parametrize(
    "key_scopes,reads",
    [
        (["*"], [True, True]),
        (["agents", "read"], [False, True]),
        (["agents"], [False, False]),
    ],
)
def test_a_trigger_reads_items_with_its_keys_scopes(
    db_session, admin_user, fake_providers, admin_keys, no_celery_dispatch, key_scopes, reads
):
    key = APIKey.create(user_id=admin_user.id, name="k", scopes=key_scopes)
    db_session.add(key)
    db_session.commit()
    trigger = setup(db_session, admin_user)
    trigger.api_key_id, trigger.via_api_key = key.id, True
    db_session.commit()
    items = [post(db_session), post(db_session, creator_id=admin_user.id)]

    fired = [fire_trigger(trigger.id, claimed(db_session, trigger, i)) is not None for i in items]
    db_session.query(AgentTriggerMatch).delete()
    db_session.commit()
    matched = [route_item(db_session, i.id) == [trigger.id] for i in items]

    assert fired == matched == reads


def test_a_deleted_pinned_provider_key_trips_the_breaker(
    db_session, admin_user, fake_providers, admin_keys
):
    trigger = setup(db_session, admin_user, spec=SPEC | {"provider_key": 99999})

    assert fire_trigger(trigger.id, claimed(db_session, trigger, post(db_session))) is None

    db_session.refresh(trigger)
    assert trigger.enabled is False
    assert "is not yours or was deleted" in trigger.state["tripped"]["reason"]


def test_an_invalid_spec_trips_the_breaker_and_tells_the_owner(
    db_session, admin_user, fake_providers, admin_keys, no_celery_dispatch
):
    trigger = setup(db_session, admin_user, SPEC | {"model": "openai/m", "reasoning": "medium"})

    assert fire_trigger(trigger.id, claimed(db_session, trigger, post(db_session))) is None

    db_session.refresh(trigger)
    assert trigger.enabled is False
    assert "reasoning" in trigger.state["tripped"]["reason"]
    [notification] = no_celery_dispatch.call_args_list
    assert notification.args[0] == SEND_NOTIFICATION
    assert "Agent trigger paused: watcher" in notification.kwargs["args"][2]


def test_a_deleted_key_trips_rather_than_widening(db_session, admin_user, fake_providers, admin_keys):
    trigger = setup(db_session, admin_user)
    trigger.via_api_key = True
    db_session.commit()

    assert fire_trigger(trigger.id, claimed(db_session, trigger, post(db_session))) is None

    db_session.refresh(trigger)
    assert "was deleted" in trigger.state["tripped"]["reason"]


def test_only_the_first_trip_disables_and_notifies(
    db_session, admin_user, no_celery_dispatch
):
    trigger = setup(db_session, admin_user)

    tripped = [trip(db_session, trigger.id, admin_user, "rate") for _ in range(2)]

    assert tripped == [True, False]
    assert [c.args[0] for c in no_celery_dispatch.call_args_list] == [SEND_NOTIFICATION]


def test_a_batch_runs_over_only_the_items_still_readable_when_it_fires(
    db_session, regular_user, fake_providers
):
    regular_user.scopes = ["agents"]
    project = Project(title="P", state="open")
    team = Team(name="T", slug="t-fire")
    person = Person(identifier="p-fire", display_name="P", user_id=regular_user.id)
    db_session.add_all([project, team, person])
    db_session.flush()
    db_session.execute(project_teams.insert().values(project_id=project.id, team_id=team.id))
    db_session.execute(
        team_members.insert().values(team_id=team.id, person_id=person.id, role="member")
    )
    trigger = setup(db_session, regular_user)
    readable = post(db_session, "kept", project_id=project.id)
    moved = post(db_session, "moved", project_id=project.id)
    moved.project_id = None
    db_session.commit()

    run = db_session.get(
        AgentRun, fire_trigger(trigger.id, claimed(db_session, trigger, readable, moved))
    )

    db_session.expire_all()
    assert run is not None
    assert run.trigger_ref["item_ids"] == [readable.id]
    assert "moved" not in run.input
    assert [
        (m.item_id, m.status, m.run_id)
        for m in db_session.query(AgentTriggerMatch).order_by(AgentTriggerMatch.item_id)
    ] == [(readable.id, "done", run.id), (moved.id, "skipped:inaccessible", None)]


def test_a_fire_skips_what_the_trigger_reported_posting_after_it_was_routed(
    db_session, admin_user, fake_providers, admin_keys
):
    """A worker's reply can be stored and routed before its report says it posted it."""
    trigger = setup(db_session, admin_user)
    reply = slack_message(db_session, admin_user, "U_OTHER")
    other = post(db_session)
    deliveries = claimed(db_session, trigger, reply, other)
    posted_run(db_session, trigger, [{"source": "slack", "external_id": "C1:1.0"}])

    run = db_session.get(AgentRun, fire_trigger(trigger.id, deliveries))

    db_session.expire_all()
    assert run is not None
    assert run.trigger_ref["item_ids"] == [other.id]
    assert {m.item_id: m.status for m in db_session.query(AgentTriggerMatch)} == {
        reply.id: "skipped:posted",
        other.id: "done",
    }

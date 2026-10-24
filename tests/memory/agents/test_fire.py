import time
from decimal import Decimal

import fakeredis
import pytest

from memory.agents import triggers
from memory.agents.fire import fire_trigger, trip
from memory.agents.triggers import Delivery, content_version
from memory.common import settings
from memory.common.celery_app import RUN_AGENT, SEND_NOTIFICATION
from memory.common.db.models import (
    AgentDefinition,
    AgentRun,
    AgentTrigger,
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
from tests.conftest import unique_sha256

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
    t = AgentTrigger(definition_id=d.id, kind="ingest", config=config, enabled=enabled)
    db.add(t)
    db.commit()
    return t


def post(db, title="Post", project_id=None):
    p = BlogPost(
        sha256=unique_sha256("p"),
        modality="blog",
        title=title,
        content="body",
        tags=["ai"],
        embed_status="STORED",
        project_id=project_id,
    )
    db.add(p)
    db.commit()
    return p


def fire_dispatched(dispatch):
    """Run the FIRE_TRIGGER tasks matching dispatched since the last call."""
    fires = [c.kwargs["args"] for c in dispatch.call_args_list]
    for args in fires:
        fire_trigger(*args)
    dispatch.reset_mock()


def claimed(*items):
    return [Delivery(i.id, content_version(i).hex(), False).encode() for i in items]


def test_a_fire_starts_one_run_with_the_rendered_input(
    db_session, admin_user, fake_providers, admin_keys, no_celery_dispatch
):
    trigger = setup(db_session, admin_user)
    a, b = post(db_session, "A"), post(db_session, "B")
    deliveries = claimed(b, a)

    run_id = fire_trigger(trigger.id, deliveries)
    a.content, a.sha256 = "edited after the claim", unique_sha256("edited")
    db_session.commit()
    redelivered = fire_trigger(trigger.id, deliveries[::-1])

    run = db_session.get(AgentRun, run_id)
    assert run is not None
    assert redelivered == run_id
    assert (run.trigger_kind, run.trigger_ref, run.definition_id) == (
        "ingest",
        {"trigger_id": trigger.id, "item_ids": [a.id, b.id]},
        trigger.definition_id,
    )
    assert run.input == (
        f"New:\n- [blog_post #{a.id}] A: body\n- [blog_post #{b.id}] B: body"
    )
    no_celery_dispatch.assert_called_once_with(RUN_AGENT, args=[run.id], task_id=run.id)


@pytest.mark.parametrize(
    "delivery,inputs",
    [
        (
            {"mode": "per_item"},
            ["New:\n- [blog_post #{id}] A: body", "New:\n- [blog_post #{id}, edited] A: body, edited"],
        ),
        # Edited before the batch went out, so the run is the agent's first sight of it.
        (
            {"mode": "batch", "window_sec": 60, "max_items": 10},
            ["New:\n- [blog_post #{id}] A: body, edited"],
        ),
    ],
)
def test_an_edit_starts_a_run_labelled_by_what_the_agent_saw_before(
    db_session, admin_user, fake_providers, admin_keys, no_celery_dispatch, monkeypatch, delivery, inputs
):
    client = fakeredis.FakeRedis()
    setup(db_session, admin_user, config=CONFIG | {"delivery": delivery, "on_edit": True})
    item = post(db_session, "A")

    triggers.match_ingest_triggers(item.id, client)
    fire_dispatched(no_celery_dispatch)
    item.content = "body, edited"
    db_session.commit()
    triggers.match_ingest_triggers(item.id, client)
    window_closed = time.time() + 120
    with monkeypatch.context() as m:
        m.setattr(time, "time", lambda: window_closed)
        triggers.sweep_trigger_buffers(client)
    fire_dispatched(no_celery_dispatch)

    # Every run in a test shares one transaction, so created_at can't order them.
    assert sorted(r.input for r in db_session.query(AgentRun)) == sorted(
        i.format(id=item.id) for i in inputs
    )


def test_disabled_triggers_and_unreadable_items_start_nothing(
    db_session, admin_user, regular_user, fake_providers
):
    off = setup(db_session, admin_user, enabled=False)
    theirs = setup(db_session, regular_user)
    hidden = post(db_session, project_id=None)

    assert (fire_trigger(off.id, claimed(hidden)), fire_trigger(theirs.id, claimed(hidden))) == (
        None,
        None,
    )
    assert db_session.query(AgentRun).count() == 0


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

    assert fire_trigger(trigger.id, claimed(post(db_session))) is None

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

    run = db_session.get(AgentRun, fire_trigger(trigger.id, claimed(post(db_session))))

    assert run is not None
    assert (run.api_key_id, sorted(run.scopes)) == (key.id, scopes)


def test_a_deleted_pinned_provider_key_trips_the_breaker(
    db_session, admin_user, fake_providers, admin_keys
):
    trigger = setup(db_session, admin_user, spec=SPEC | {"provider_key": 99999})

    assert fire_trigger(trigger.id, claimed(post(db_session))) is None

    db_session.refresh(trigger)
    assert trigger.enabled is False
    assert "is not yours or was deleted" in trigger.state["tripped"]["reason"]


@pytest.mark.parametrize(
    "spec,earlier_cost,reason",
    [
        (SPEC | {"max_runs_per_hour": 1}, "0", "max_runs_per_hour"),
        (SPEC | {"max_cost_usd_per_day": "0.50"}, "0.50", "definition pool"),
        (SPEC | {"model": "openai/m", "reasoning": "medium"}, "0", "reasoning"),
    ],
)
def test_hitting_a_cap_trips_the_breaker_and_tells_the_owner(
    db_session, admin_user, fake_providers, admin_keys, no_celery_dispatch, spec, earlier_cost, reason
):
    trigger = setup(db_session, admin_user, spec)
    db_session.add(
        AgentRun(
            user_id=admin_user.id,
            definition_id=trigger.definition_id,
            spec=SPEC,
            input="earlier",
            max_cost_usd=Decimal("1"),
            cost_usd=Decimal(earlier_cost),
            trigger_kind="ingest",
            status="done",
        )
    )
    db_session.commit()
    item = post(db_session)

    assert fire_trigger(trigger.id, claimed(item)) is None

    db_session.refresh(trigger)
    assert trigger.enabled is False
    assert reason in trigger.state["tripped"]["reason"]
    [notification] = no_celery_dispatch.call_args_list
    assert notification.args[0] == SEND_NOTIFICATION
    assert "Agent trigger paused: watcher" in notification.kwargs["args"][2]


def test_a_deleted_key_trips_rather_than_widening(db_session, admin_user, fake_providers, admin_keys):
    trigger = setup(db_session, admin_user)
    trigger.via_api_key = True
    db_session.commit()

    assert fire_trigger(trigger.id, claimed(post(db_session))) is None

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

    run = db_session.get(AgentRun, fire_trigger(trigger.id, claimed(readable, moved)))

    assert run is not None
    assert run.trigger_ref["item_ids"] == [readable.id]
    assert "moved" not in run.input

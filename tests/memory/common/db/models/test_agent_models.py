from decimal import Decimal

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from memory.common.db.models import (
    AgentDefinition,
    AgentRun,
    AgentRunStep,
    APIKey,
    RunStatus,
)


def make_run(db, user, **overrides) -> AgentRun:
    run = AgentRun(
        user_id=user.id,
        spec={"system_prompt": "x"},
        input="q",
        max_cost_usd=Decimal("1"),
        trigger_kind="manual",
        **overrides,
    )
    db.add(run)
    db.commit()
    return run


def test_run_defaults(db_session, regular_user):
    run = make_run(db_session, regular_user)

    assert run.status == RunStatus.PENDING
    assert run.cost_usd == Decimal("0")
    assert run.starred is False
    assert len(run.id) == 36


def test_definition_names_are_unique_per_user(db_session, regular_user, admin_user):
    db_session.add_all(
        [
            AgentDefinition(user_id=regular_user.id, name="digest", spec={}),
            AgentDefinition(user_id=admin_user.id, name="digest", spec={}),
        ]
    )
    db_session.commit()

    db_session.add(AgentDefinition(user_id=regular_user.id, name="digest", spec={}))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_deleting_a_definition_keeps_its_runs(db_session, regular_user):
    definition = AgentDefinition(user_id=regular_user.id, name="d", spec={})
    db_session.add(definition)
    db_session.commit()
    run = make_run(db_session, regular_user, definition_id=definition.id)

    db_session.delete(definition)
    db_session.commit()
    db_session.refresh(run)

    assert run.definition_id is None


def test_steps_cascade_and_never_serialize_replay(db_session, regular_user):
    run = make_run(db_session, regular_user)
    step = AgentRunStep(
        run_id=run.id,
        seq=0,
        kind="assistant",
        content=[{"type": "text", "text": "hi"}],
        replay={"provider": "x", "blocks": []},
    )
    db_session.add(step)
    db_session.commit()

    assert "replay" not in step.serialize()
    assert step.serialize()["content"] == [{"type": "text", "text": "hi"}]

    db_session.delete(run)
    db_session.commit()
    assert db_session.query(AgentRunStep).count() == 0


def test_dedupe_key_is_unique_per_definition(db_session, regular_user):
    definition = AgentDefinition(user_id=regular_user.id, name="d", spec={})
    db_session.add(definition)
    db_session.commit()
    make_run(db_session, regular_user, definition_id=definition.id, dedupe_key="k")

    with pytest.raises(IntegrityError):
        make_run(db_session, regular_user, definition_id=definition.id, dedupe_key="k")


def test_api_key_caps_serialize(db_session, regular_user):
    key = APIKey.create(user_id=regular_user.id, name="k")
    key.agent_max_run_cost_usd = Decimal("0.25")
    db_session.add(key)
    db_session.commit()

    assert key.serialize()["agent_max_run_cost_usd"] == "0.250000"
    assert key.serialize()["agent_max_daily_cost_usd"] is None


@pytest.mark.parametrize("model", [AgentDefinition, AgentRun, AgentRunStep])
def test_migration_matches_model_nullability(db_session, model):
    columns = inspect(db_session.connection()).get_columns(model.__tablename__)

    assert {c["name"]: c["nullable"] for c in columns} == {
        c.name: c.nullable for c in model.__table__.columns
    }

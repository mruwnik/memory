import asyncio
import logging
from decimal import Decimal

import pytest

from memory.agents import call, caps, provider_keys
from memory.agents.call import LLMError, Record, run_agent, run_agent_sync
from memory.agents.provider_keys import Credential, credential
from memory.agents.system import SUMMARISE
from memory.common.db.models import AgentRun, AgentRunStep, LLMProviderKey, LLMSpend
from tests.memory.agents.fakes import DEFAULT_USAGE, PRICING, key_rejected, model_unavailable, reply, submitted

SUMMARY = {"summary": "short", "tags": ["a"]}


@pytest.mark.asyncio
async def test_a_schema_agent_answers_through_submit_result(db_session, regular_user, llm_key, scripted_llm):
    scripted_llm.script = [submitted(SUMMARY)]

    result = await run_agent(SUMMARISE.slug, "text", user=regular_user.id, key=credential(llm_key), record=Record.COST)

    request = scripted_llm.requests[0]
    assert (result.data, result.run_id) == (SUMMARY, None)
    assert (request["api_key"], request["system"], request["tool_choice"]) == ("sk-test-default", SUMMARISE.system_prompt, "required")
    assert [t.name for t in request["tools"]] == ["submit_result"]


@pytest.mark.asyncio
async def test_cost_calls_charge_the_key_counter_without_a_run(db_session, regular_user, llm_key, scripted_llm):
    scripted_llm.script = [submitted(SUMMARY)]

    await run_agent(SUMMARISE.slug, "text", user=regular_user.id, key=credential(llm_key), record=Record.COST)

    (bucket,) = db_session.query(LLMSpend).all()
    assert (bucket.provider_key_id, bucket.agent, bucket.cost_usd, bucket.calls) == (
        llm_key.id, SUMMARISE.slug, PRICING.cost(DEFAULT_USAGE), 1,
    )
    assert db_session.query(AgentRun).count() == 0


@pytest.mark.asyncio
async def test_full_calls_keep_the_turn_as_a_run(db_session, regular_user, llm_key, scripted_llm):
    scripted_llm.script = [submitted(SUMMARY)]

    result = await run_agent(SUMMARISE.slug, "text", user=regular_user.id, key=credential(llm_key), record=Record.FULL)

    run = db_session.get(AgentRun, result.run_id)
    steps = db_session.query(AgentRunStep).filter(AgentRunStep.run_id == run.id).order_by(AgentRunStep.seq).all()
    assert (run.status, run.result, run.trigger_kind, run.trigger_ref, run.provider_key_id) == (
        "done", {"data": SUMMARY}, "system", {"agent": SUMMARISE.slug}, llm_key.id,
    )
    assert [s.kind for s in steps] == ["user", "assistant"]
    assert db_session.query(LLMSpend).count() == 0


@pytest.mark.asyncio
async def test_a_schema_agent_that_does_not_submit_fails_but_is_still_charged(db_session, regular_user, llm_key, scripted_llm):
    scripted_llm.script = [reply("no tool call")]

    with pytest.raises(LLMError, match="answered without submitting a result"):
        await run_agent(SUMMARISE.slug, "text", user=regular_user.id, key=credential(llm_key), record=Record.COST)

    assert db_session.query(LLMSpend).one().calls == 1


@pytest.mark.asyncio
async def test_a_call_its_caller_gave_up_on_is_still_charged(db_session, regular_user, llm_key, scripted_llm):
    async def slow():
        await asyncio.sleep(0.1)
        return submitted(SUMMARY)

    scripted_llm.script = [slow]

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            run_agent(SUMMARISE.slug, "text", user=regular_user.id, key=credential(llm_key), record=Record.COST),
            timeout=0.01,
        )
    await asyncio.sleep(0.3)

    db_session.expire_all()
    assert db_session.query(LLMSpend).one().calls == 1


@pytest.mark.asyncio
async def test_a_detached_call_that_fails_unexpectedly_is_logged(
    db_session, regular_user, llm_key, scripted_llm, monkeypatch, caplog
):
    async def slow():
        await asyncio.sleep(0.1)
        return submitted(SUMMARY)

    def broken_charge(*args):
        raise RuntimeError("db down")

    scripted_llm.script = [slow]
    monkeypatch.setattr(call, "charge", broken_charge)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            run_agent(SUMMARISE.slug, "text", user=regular_user.id, key=credential(llm_key), record=Record.COST),
            timeout=0.01,
        )
    await asyncio.sleep(0.3)

    records = [r for r in caplog.records if r.name == call.__name__]
    assert [(r.levelno, r.getMessage()) for r in records] == [
        (logging.ERROR, f"{SUMMARISE.slug} call on key {llm_key.id} failed after it was sent")
    ]
    assert records[0].exc_info is not None


@pytest.mark.asyncio
async def test_a_rejected_key_is_marked_invalid_and_its_owner_told_once(db_session, regular_user, llm_key, scripted_llm, monkeypatch):
    sent = []
    monkeypatch.setattr(provider_keys, "notify_user_now", lambda db, user, *args, **kw: sent.append(user.id))
    scripted_llm.script = [key_rejected, key_rejected]

    for _ in range(2):
        with pytest.raises(LLMError, match="API key 'default' was rejected by anthropic"):
            await run_agent(SUMMARISE.slug, "text", user=regular_user.id, key=credential(llm_key), record=Record.COST)

    db_session.refresh(llm_key)
    assert (llm_key.status, sent) == ("invalid", [regular_user.id])


@pytest.mark.asyncio
async def test_a_spent_key_is_refused_before_calling(db_session, regular_user, llm_key, scripted_llm):
    llm_key.max_cost_usd_per_day = Decimal("1")
    caps.record_spend(db_session, key_id=llm_key.id, agent="system:hyde", cost=Decimal("1"))
    db_session.commit()

    with pytest.raises(LLMError, match="provider key pool: spent"):
        await run_agent(SUMMARISE.slug, "text", user=regular_user.id, key=credential(llm_key), record=Record.COST)
    assert scripted_llm.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,model",
    [("anthropic", "anthropic/claude-haiku-5-5"), ("openai", "openai/gpt-6-luna")],
)
async def test_the_keys_provider_picks_the_agents_model(db_session, regular_user, scripted_llm, provider, model):
    key = LLMProviderKey(user_id=regular_user.id, provider=provider, label="k")
    key.key = "sk-k"
    db_session.add(key)
    db_session.commit()
    scripted_llm.script = [submitted(SUMMARY)]

    result = await run_agent(SUMMARISE.slug, "text", user=regular_user.id, key=credential(key), record=Record.FULL)

    assert scripted_llm.requests[0]["model"] == model
    assert db_session.get(AgentRun, result.run_id).spec["model"] == model


@pytest.mark.asyncio
async def test_a_key_of_a_provider_the_agent_does_not_run_on_is_refused(regular_user, scripted_llm):
    other = Credential(1, regular_user.id, "other", "k", "sk-k")

    with pytest.raises(LLMError, match="needs an anthropic or openai key"):
        await run_agent(SUMMARISE.slug, "text", user=regular_user.id, key=other, record=Record.COST)
    assert scripted_llm.requests == []


@pytest.mark.asyncio
async def test_an_invalid_key_is_refused_before_calling(db_session, regular_user, llm_key, scripted_llm):
    llm_key.status = "invalid"
    db_session.commit()

    with pytest.raises(LLMError, match="was rejected by anthropic"):
        await run_agent(SUMMARISE.slug, "text", user=regular_user.id, key=credential(llm_key), record=Record.COST)
    assert scripted_llm.requests == []


def test_sync_callers_get_the_same_call(db_session, regular_user, llm_key, scripted_llm):
    scripted_llm.script = [submitted(SUMMARY)]

    result = run_agent_sync(SUMMARISE.slug, "text", user=regular_user.id, key=credential(llm_key), record=Record.COST)

    assert result.data == SUMMARY


@pytest.mark.asyncio
async def test_an_unavailable_model_is_reported_once_per_key_at_error_without_the_prompt(
    db_session, regular_user, llm_key, scripted_llm, caplog, monkeypatch
):
    monkeypatch.setattr(call, "reported_unavailable", {})
    scripted_llm.script = [model_unavailable, model_unavailable]

    for _ in range(2):
        with pytest.raises(LLMError, match="unavailable to this"):
            await run_agent(SUMMARISE.slug, "secret item text", user=regular_user.id, key=credential(llm_key), record=Record.COST)

    (record,) = [r for r in caplog.records if r.levelname == "ERROR"]
    assert f"key {llm_key.id}" in record.getMessage()
    assert "claude-haiku-5-5" in record.getMessage()
    assert "anthropic/" not in record.getMessage()
    assert "secret item text" not in caplog.text
    assert db_session.query(LLMSpend).count() == 0


def test_the_two_call_sites_share_one_report_per_key(db_session, llm_key, caplog, monkeypatch):
    monkeypatch.setattr(call, "reported_unavailable", {})
    key = credential(llm_key)

    call.report_model_unavailable(key, "claude-haiku-5-5", SUMMARISE.slug)
    call.report_model_unavailable(key, "claude-haiku-5-5")

    assert len([r for r in caplog.records if r.levelname == "ERROR"]) == 1

# store functions intentionally return Optional (e.g. get_job/claim_next ->
# None when absent); these tests subscript or attribute-access results they have
# just created or claimed, so the None case is covered by dedicated tests, not
# here.
# pyright: reportOptionalSubscript=false, reportOptionalMemberAccess=false
import json

import pytest

from memory.common.check import store
from memory.common.check.redis_client import (
    job_key,
    jobs_index_key,
    lease_key,
    open_key,
    wake_key,
)
from memory.common.check.schemas import (
    SubmitRequest,
    QueueFull,
    JobGone,
    JobAlreadyComplete,
    PayloadTooLarge,
)

pytestmark = pytest.mark.asyncio


async def test_submit_rejects_oversized_text(r):
    big = "a" * (64 * 1024 + 1)
    with pytest.raises(PayloadTooLarge):
        await store.submit_job(r, user_id=5, req=SubmitRequest(text=big))


async def test_submit_creates_job_and_indexes(r):
    job_id = await store.submit_job(
        r, user_id=5, req=SubmitRequest(text="check this", mode="verify",
                                        context={"url": "u"}, callback_token="t")
    )
    assert job_id.startswith("chk_")

    job = await r.hgetall(job_key(job_id))
    assert job["status"] == "queued"
    assert job["mode"] == "verify"
    assert job["text"] == "check this"
    assert json.loads(job["context"]) == {"url": "u"}
    assert job["user_id"] == "5"
    assert job["callback_token"] == "t"
    assert job["submitted_at"]
    assert job["attempts"] == "0"

    assert await r.zrange(open_key(5), 0, -1) == [job_id]
    assert await r.zscore(jobs_index_key(5), job_id) is not None
    # doorbell: a wake token was pushed for the claimer
    assert await r.lrange(wake_key(5), 0, -1)


async def test_submit_enforces_queue_depth(r, monkeypatch):
    monkeypatch.setattr("memory.common.settings.CHECK_QUEUE_MAX_DEPTH", 2)
    for _ in range(2):
        await store.submit_job(r, user_id=5, req=SubmitRequest(text="x"))
    with pytest.raises(QueueFull):
        await store.submit_job(r, user_id=5, req=SubmitRequest(text="x"))


async def test_submit_bounds_wake_list(r):
    for _ in range(20):
        await store.submit_job(r, user_id=5, req=SubmitRequest(text="x"))
    # wake list is capped at 16 entries
    assert await r.llen(wake_key(5)) == 16


async def test_get_job_missing_returns_none(r):
    assert await store.get_job(r, "chk_nope") is None


async def test_get_job_returns_record(r):
    job_id = await store.submit_job(r, user_id=5, req=SubmitRequest(text="hi"))
    job = await store.get_job(r, job_id)
    assert job["job_id"] == job_id
    assert job["status"] == "queued"


async def test_claim_leases_via_lease_key(r):
    job_id = await store.submit_job(r, user_id=5, req=SubmitRequest(text="hi"))
    claimed = await store.claim_next(r, user_id=5, wait=0)
    assert claimed.job_id == job_id
    assert claimed.lease_id
    # the lease key holds our fencing token
    assert await r.get(lease_key(job_id)) == claimed.lease_id
    job = await store.get_job(r, job_id)
    assert job["status"] == "in_flight"
    assert job["lease_id"] == claimed.lease_id
    assert job["attempts"] == "1"
    # id remains in the open zset (NOT removed on claim)
    assert await r.zrange(open_key(5), 0, -1) == [job_id]


async def test_claim_empty_returns_none(r):
    assert await store.claim_next(r, user_id=5, wait=0) is None


async def test_claim_only_own_user(r):
    await store.submit_job(r, user_id=5, req=SubmitRequest(text="mine"))
    assert await store.claim_next(r, user_id=6, wait=0) is None


async def test_two_claims_never_collide(r):
    id1 = await store.submit_job(r, user_id=5, req=SubmitRequest(text="a"))
    id2 = await store.submit_job(r, user_id=5, req=SubmitRequest(text="b"))
    c1 = await store.claim_next(r, user_id=5, wait=0)
    c2 = await store.claim_next(r, user_id=5, wait=0)
    assert {c1.job_id, c2.job_id} == {id1, id2}
    # both leased -> third claim finds nothing free
    assert await store.claim_next(r, user_id=5, wait=0) is None


async def test_lease_expiry_makes_claimable_again(r):
    job_id = await store.submit_job(r, user_id=5, req=SubmitRequest(text="hi"))
    c1 = await store.claim_next(r, user_id=5, wait=0)
    # simulate the lease TTL elapsing
    await r.delete(lease_key(job_id))
    c2 = await store.claim_next(r, user_id=5, wait=0)
    assert c2.job_id == job_id
    assert c2.lease_id != c1.lease_id
    assert (await store.get_job(r, job_id))["attempts"] == "2"


async def test_poison_job_marked_expired(r, monkeypatch):
    monkeypatch.setattr("memory.common.settings.CHECK_MAX_REQUEUE_ATTEMPTS", 1)
    job_id = await store.submit_job(r, user_id=5, req=SubmitRequest(text="hi"))
    await store.claim_next(r, user_id=5, wait=0)  # attempts -> 1
    await r.delete(lease_key(job_id))             # lease "expires"
    # attempts would become 2 > 1 -> poison
    assert await store.claim_next(r, user_id=5, wait=0) is None
    job = await store.get_job(r, job_id)
    assert job["status"] == "expired"
    assert job["completed_at"]
    assert await r.zrange(open_key(5), 0, -1) == []
    assert await r.get(lease_key(job_id)) is None


async def test_claim_cleans_tombstone(r):
    job_id = await store.submit_job(r, user_id=5, req=SubmitRequest(text="hi"))
    await r.delete(job_key(job_id))  # hash TTL-expired but still in open zset
    assert await store.claim_next(r, user_id=5, wait=0) is None
    assert await r.zrange(open_key(5), 0, -1) == []
    assert await r.get(lease_key(job_id)) is None


async def _claim(r, user_id=5, text="hi"):
    job_id = await store.submit_job(r, user_id=user_id, req=SubmitRequest(text=text))
    claimed = await store.claim_next(r, user_id=user_id, wait=0)
    return job_id, claimed.lease_id


async def test_complete_ok_stores_result(r):
    job_id, lease = await _claim(r)
    await store.complete_job(r, job_id, lease, status="ok",
                             result={"summary": "done"}, error=None)
    job = await store.get_job(r, job_id)
    assert job["status"] == "ok"
    assert json.loads(job["result"]) == {"summary": "done"}
    assert job["completed_at"]
    assert await r.zrange(open_key(5), 0, -1) == []
    assert await r.get(lease_key(job_id)) is None


async def test_complete_error_stores_error(r):
    job_id, lease = await _claim(r)
    await store.complete_job(r, job_id, lease, status="error",
                             result=None, error="boom")
    job = await store.get_job(r, job_id)
    assert job["status"] == "error"
    assert job["error"] == "boom"


async def test_complete_stale_lease_raises_gone(r):
    job_id, lease = await _claim(r)
    with pytest.raises(JobGone):
        await store.complete_job(r, job_id, "wrong-lease", status="ok",
                                 result={}, error=None)


async def test_complete_unknown_job_raises_gone(r):
    with pytest.raises(JobGone):
        await store.complete_job(r, "chk_missing", "x", status="ok",
                                 result={}, error=None)


async def test_complete_twice_raises_already_complete(r):
    job_id, lease = await _claim(r)
    await store.complete_job(r, job_id, lease, status="ok", result={}, error=None)
    with pytest.raises(JobAlreadyComplete):
        await store.complete_job(r, job_id, lease, status="ok", result={}, error=None)


async def test_fencing_rejects_old_worker(r):
    """Core fencing scenario: slow worker A's lease expires, worker B re-claims;
    A's late result must be rejected (410), B's accepted."""
    job_id, lease_a = await _claim(r)
    await r.delete(lease_key(job_id))             # A's lease "expires"
    claimed_b = await store.claim_next(r, user_id=5, wait=0)
    lease_b = claimed_b.lease_id
    assert lease_b != lease_a
    with pytest.raises(JobGone):
        await store.complete_job(r, job_id, lease_a, status="ok", result={}, error=None)
    await store.complete_job(r, job_id, lease_b, status="ok",
                             result={"v": 1}, error=None)
    assert (await store.get_job(r, job_id))["status"] == "ok"


async def test_complete_after_reclaim_rejects_first_worker(r):
    """Worker B reclaims the lease (new value) before A reports; A's completion
    is fenced out (410), B's is accepted."""
    job_id, lease_a = await _claim(r)
    await r.set(lease_key(job_id), "LEASE_B")  # simulate B reclaimed
    with pytest.raises(JobGone):
        await store.complete_job(r, job_id, lease_a, status="ok", result={}, error=None)
    await store.complete_job(r, job_id, "LEASE_B", status="ok",
                             result={"v": 1}, error=None)
    assert (await store.get_job(r, job_id))["status"] == "ok"


async def test_complete_on_expired_status_rejected(r):
    """A job whose status was marked expired (poison) rejects a late completion
    even if the caller still holds the original lease value."""
    job_id, lease = await _claim(r)
    await r.hset(job_key(job_id), mapping={"status": "expired"})
    with pytest.raises(JobGone):
        await store.complete_job(r, job_id, lease, status="ok", result={}, error=None)


async def test_complete_rejects_invalid_status(r):
    job_id, lease = await _claim(r)
    with pytest.raises(ValueError):
        await store.complete_job(r, job_id, lease, status="weird",
                                 result={}, error=None)


async def test_list_jobs_newest_first_and_prunes(r):
    j1 = await store.submit_job(r, user_id=5, req=SubmitRequest(text="a"))
    j2 = await store.submit_job(r, user_id=5, req=SubmitRequest(text="b"))
    await r.delete(job_key(j1))  # simulate hash TTL expiry
    jobs = await store.list_jobs(r, user_id=5, status=None, limit=50, offset=0)
    ids = [j["job_id"] for j in jobs]
    assert ids == [j2]
    assert await r.zscore(jobs_index_key(5), j1) is None  # pruned


async def test_list_jobs_status_filter(r):
    j1, lease = await _claim(r, text="a")
    j2 = await store.submit_job(r, user_id=5, req=SubmitRequest(text="b"))
    await store.complete_job(r, j1, lease, status="ok", result={}, error=None)
    done = await store.list_jobs(r, user_id=5, status="ok", limit=50, offset=0)
    assert [j["job_id"] for j in done] == [j1]
    queued = await store.list_jobs(r, user_id=5, status="queued", limit=50, offset=0)
    assert [j["job_id"] for j in queued] == [j2]


async def test_list_jobs_pagination_status_none(r):
    ids = [await store.submit_job(r, user_id=5, req=SubmitRequest(text=str(i)))
           for i in range(5)]
    page = await store.list_jobs(r, user_id=5, status=None, limit=2, offset=1)
    # newest-first => ids[4],ids[3],ids[2],ids[1],ids[0]; offset 1 limit 2 => ids[3],ids[2]
    assert [j["job_id"] for j in page] == [ids[3], ids[2]]


async def test_list_jobs_tombstone_in_window_does_not_shorten_page(r):
    """A tombstone (hash TTL-expired) inside the requested window must not
    shrink the page: live jobs beyond it shift into view."""
    ids = [await store.submit_job(r, user_id=5, req=SubmitRequest(text=str(i)))
           for i in range(5)]
    # newest-first order is ids[4],ids[3],ids[2],ids[1],ids[0]; tombstone ids[3]
    await r.delete(job_key(ids[3]))
    page = await store.list_jobs(r, user_id=5, status=None, limit=2, offset=0)
    # must still return 2 LIVE jobs: ids[4] then ids[2] (ids[3] skipped+pruned)
    assert [j["job_id"] for j in page] == [ids[4], ids[2]]
    assert await r.zscore(jobs_index_key(5), ids[3]) is None  # pruned


async def test_wait_for_answer_returns_terminal(r):
    job_id, lease = await _claim(r)
    await store.complete_job(r, job_id, lease, status="ok",
                             result={"v": 1}, error=None)
    rec = await store.wait_for_answer(r, job_id, timeout=0)
    assert rec is not None
    assert rec["status"] == "ok"
    assert json.loads(rec["result"]) == {"v": 1}


async def test_wait_for_answer_unknown_returns_none(r):
    assert await store.wait_for_answer(r, "chk_missing", timeout=0) is None


async def test_wait_for_answer_times_out_pending(r, monkeypatch):
    # never let a real sleep run; just exercise the poll-until-deadline path
    async def no_sleep(*_a, **_k):
        return None
    monkeypatch.setattr("memory.common.check.store.asyncio.sleep", no_sleep)
    job_id = await store.submit_job(r, user_id=5, req=SubmitRequest(text="x"))
    rec = await store.wait_for_answer(r, job_id, timeout=0.05, interval=0.01)
    assert rec is not None
    assert rec["status"] == "queued"  # still pending, returned not-None


async def test_wait_for_answer_clamped_to_max(r, monkeypatch):
    monkeypatch.setattr("memory.common.settings.CHECK_MAX_WAIT_SEC", 0)
    job_id = await store.submit_job(r, user_id=5, req=SubmitRequest(text="x"))
    # timeout requested huge but clamped to 0 -> single read, returns pending
    rec = await store.wait_for_answer(r, job_id, timeout=9999)
    assert rec is not None and rec["status"] == "queued"


async def test_delete_job_removes_all_structures(r):
    job_id = await store.submit_job(r, user_id=5, req=SubmitRequest(text="x"))
    await store.claim_next(r, user_id=5, wait=0)  # leased + in open + index
    job = await store.get_job(r, job_id)
    assert job is not None
    await store.delete_job(r, job)
    assert await store.get_job(r, job_id) is None
    assert await r.zscore(open_key(5), job_id) is None
    assert await r.zscore(jobs_index_key(5), job_id) is None
    assert await r.get(lease_key(job_id)) is None

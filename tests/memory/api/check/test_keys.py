from memory.api.check import redis_client as rc


def test_key_helpers():
    assert rc.job_key("chk_x") == "check:job:chk_x"
    assert rc.open_key(7) == "check:open:7"
    assert rc.lease_key("chk_x") == "check:lease:chk_x"
    assert rc.wake_key(7) == "check:wake:7"
    assert rc.jobs_index_key(7) == "check:jobs:7"

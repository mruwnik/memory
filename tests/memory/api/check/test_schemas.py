# Several tests construct models with deliberately missing/invalid fields (e.g.
# an out-of-Literal mode) to assert they raise ValidationError; the call-issue
# and argument-type rules firing on those are expected.
# pyright: reportCallIssue=false, reportArgumentType=false
import pytest
from pydantic import ValidationError

from memory.common.check.schemas import SubmitRequest, ResultRequest


def test_mode_defaults_to_research():
    req = SubmitRequest(text="hello")
    assert req.mode == "research"
    assert req.context == {}


@pytest.mark.parametrize(
    "mode", ["verify", "research", "link", "deep-dive", "investigation-team"]
)
def test_valid_modes(mode):
    assert SubmitRequest(text="x", mode=mode).mode == mode


def test_unknown_mode_rejected():
    with pytest.raises(ValidationError):
        SubmitRequest(text="x", mode="bogus")


def test_missing_text_rejected():
    with pytest.raises(ValidationError):
        SubmitRequest(mode="verify")


def test_result_requires_status():
    with pytest.raises(ValidationError):
        ResultRequest(result={"summary": "x"})


@pytest.mark.parametrize("status", ["ok", "error"])
def test_result_status_valid(status):
    assert ResultRequest(status=status, lease_id="L").status == status

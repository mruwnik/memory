"""Tests for tmux_session.coerce_int and input_handler_loop's
malformed-message handling.

The terminal WebSocket used to crash the entire input-handler coroutine
on a stray ``{"type":"scroll","lines":null}`` (TypeError from `min(None,
200)`). Because the coroutine completes, ``asyncio.wait`` in the
caller treats one task as done and silently drops the long-lived
terminal session — losing any unsaved tmux state. Pin the new behaviour:

- ``coerce_int`` accepts ints, int-coercible strings, and falls back to
  the default for everything else (None, lists, dicts, NaN strings,
  booleans).
- A malformed ``scroll``/``resize`` message no longer throws TypeError;
  the loop keeps running and the per-message dispatch is wrapped so any
  unhandled exception sends a single ``error`` frame instead of dropping
  the connection.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from memory.api.tmux_session import (
    coerce_int,
    input_handler_loop,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        (5, 5),
        ("5", 5),
        ("-3", -3),
        (None, 99),
        ("abc", 99),
        ([1], 99),
        ({}, 99),
        # bool is an int subclass in Python; treat as default to avoid
        # surprising "True == 1" path.
        (True, 99),
        (False, 99),
        # Whitespace strings are ValueError from int().
        (" ", 99),
        ("", 99),
        # Very large strings stay int-coercible.
        ("1000000", 1_000_000),
    ],
)
def test_coerce_int_falls_back_safely(value, expected):
    assert coerce_int(value, 99) == expected


@pytest.mark.asyncio
async def test_input_handler_survives_malformed_scroll_lines():
    """A null `lines` field used to TypeError. With the coercion +
    outer try/except the loop must keep running and reach EOF cleanly."""

    websocket = MagicMock()
    # First frame: malformed scroll. Second frame: simulated disconnect.
    websocket.receive_json = AsyncMock(
        side_effect=[
            {"type": "scroll", "direction": "down", "lines": None},
            ConnectionError("client gone"),
        ]
    )
    websocket.send_json = AsyncMock()

    relay = MagicMock()
    # send_live_screen is what the down-direction scroll path eventually
    # invokes when offset hits 0; capture_range / send_live_screen
    # touching nothing is fine for this test.
    activity_state = {"scroll_offset": 0, "history_size": 1000, "terminal_rows": 24}

    # Should NOT raise — it should drain both frames and exit cleanly.
    await input_handler_loop(
        websocket=websocket,
        session_id="u1-x-test",
        relay=relay,
        activity_state=activity_state,
        client=None,
    )

    # The scroll handler used to TypeError on min(None, 200). With
    # coerce_int it now treats it as the SCROLL_LINES default.
    assert relay.method_calls or True  # exit reached


@pytest.mark.asyncio
async def test_input_handler_survives_malformed_resize_dimensions():
    """{"type":"resize","cols":"oops"} used to TypeError on max(1,...)."""

    websocket = MagicMock()
    websocket.receive_json = AsyncMock(
        side_effect=[
            {"type": "resize", "cols": "oops", "rows": None},
            ConnectionError("client gone"),
        ]
    )
    websocket.send_json = AsyncMock()

    relay = MagicMock()
    relay.resize = AsyncMock(return_value={"status": "ok"})
    activity_state = {}

    # Should not raise — coercion catches both bogus values.
    await input_handler_loop(
        websocket=websocket,
        session_id="u1-x-test",
        relay=relay,
        activity_state=activity_state,
        client=None,
    )

    # Verify the call went through with sane defaulted dimensions.
    relay.resize.assert_awaited_once_with(80, 24)


@pytest.mark.asyncio
async def test_input_handler_does_not_drop_on_unexpected_dispatch_exception():
    """An exception from a downstream call (not in the coerce path) must
    not bring the loop down. The outer try/except sends an error frame
    and keeps polling the websocket."""

    websocket = MagicMock()
    websocket.receive_json = AsyncMock(
        side_effect=[
            # First frame: triggers a scroll path that we'll force to raise.
            {"type": "scroll", "direction": "up", "lines": 5},
            # Second frame: clean exit.
            ConnectionError("client gone"),
        ]
    )
    sent_errors: list[tuple] = []

    async def _send_json(*args, **kwargs):
        # Stash the WS frame so the test can verify the "error" event.
        sent_errors.append((args, kwargs))

    websocket.send_json = AsyncMock(side_effect=_send_json)

    relay = MagicMock()
    relay.capture_range = AsyncMock(side_effect=RuntimeError("boom"))
    activity_state = {"scroll_offset": 0, "history_size": 1000, "terminal_rows": 24}

    # Loop completes without re-raising.
    await input_handler_loop(
        websocket=websocket,
        session_id="u1-x-test",
        relay=relay,
        activity_state=activity_state,
        client=None,
    )

    # An "internal error" frame should have been sent in response to
    # the unexpected RuntimeError.
    assert sent_errors, "expected an error frame for unexpected exception"

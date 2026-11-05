import pytest

from memory.common.reactions import apply_reaction, reaction_counts, slack_reactions


@pytest.mark.parametrize(
    "before, action, emoji, user, after",
    [
        (None, "add", "fire", "U1", {"fire": ["U1"]}),
        ({"fire": ["U1"]}, "add", "fire", "U1", {"fire": ["U1"]}),
        ({"fire": ["U1"]}, "add", "fire", "U2", {"fire": ["U1", "U2"]}),
        ({"fire": ["U1"]}, "add", "ok", "U1", {"fire": ["U1"], "ok": ["U1"]}),
        ({"fire": ["U1", "U2"]}, "remove", "fire", "U1", {"fire": ["U2"]}),
        ({"fire": ["U1"]}, "remove", "fire", "U1", {}),
        ({"fire": ["U1"]}, "remove", "fire", "U9", {"fire": ["U1"]}),
        (None, "remove", "fire", "U1", {}),
        ({"fire": ["U1"], "ok": ["U2"]}, "clear_emoji", "fire", None, {"ok": ["U2"]}),
        ({"fire": ["U1"], "ok": ["U2"]}, "clear", None, None, {}),
    ],
)
def test_apply_reaction(before, action, emoji, user, after):
    assert apply_reaction(before, action, emoji, user) == after


def test_apply_reaction_leaves_its_input_alone():
    before = {"fire": ["U1"]}

    apply_reaction(before, "add", "fire", "U2")

    assert before == {"fire": ["U1"]}


@pytest.mark.parametrize(
    "reactions, counts",
    [
        (None, {}),
        ({}, {}),
        ({"fire": ["U1", "U2"], "ok": ["U3"]}, {"fire": 2, "ok": 1}),
    ],
)
def test_reaction_counts(reactions, counts):
    assert reaction_counts(reactions) == counts


@pytest.mark.parametrize(
    "api, stored",
    [
        (None, None),
        ([], {}),
        (
            [{"name": "fire", "count": 2, "users": ["U1", "U2"]}, {"name": "ok", "count": 1}],
            {"fire": ["U1", "U2"], "ok": []},
        ),
    ],
)
def test_slack_reactions(api, stored):
    assert slack_reactions(api) == stored

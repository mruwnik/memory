"""Beeper message text is stored as plain text, without channel membership notices."""

import time

import pytest

from memory.beeper import ingest
from memory.common import beeper
from memory.common.db.models import BeeperChat, BeeperNetwork, BeeperParticipant
from memory.common.db.models.source_items import BeeperMessage

ALICE = "@slackgo_t1-u1:beeper.local"


def pill(user_id: str, text: str = "") -> str:
    return f'<a href="https://matrix.to/#/{user_id}" rel="noopener noreferrer" target="_blank">{text}</a>'


@pytest.mark.parametrize(
    "text, expected",
    [
        ("plain text", "plain text"),
        ("a < b and c > d", "a < b and c > d"),
        ("x <3 y", "x <3 y"),
        ("use <foo> here", "use <foo> here"),
        ("snake_case and *stars*", "snake_case and *stars*"),
        (f"hi {pill('@happy-carrot-8708:beeper.com', 'daniel-claude')}", "hi @daniel-claude"),
        (f"hi {pill('@x:beeper.com', '@Bob')}", "hi @Bob"),
        (f"hi {pill(ALICE)}", "hi @Alice Smith"),
        (f"hi {pill('@stranger_1:beeper.local')}", "hi @stranger_1"),
        ('hi <a href="https://matrix.to/#/%40stranger:beeper.local"></a>', "hi @stranger"),
        ('see <a href="https://example.com/x">the docs</a>', "see [the docs](https://example.com/x)"),
        ("<p>Big drop:</p>\n<ul>\n<li>LINE</li>\n<li>Polls</li>\n</ul>", "Big drop:\n\n* LINE\n* Polls"),
        ("<p>one<br>two  </p>", "one\ntwo"),
        ("<b>bold</b> &amp; more", "**bold** & more"),
        ("<em>x</em> and <code>y</code>", "*x* and `y`"),
        ("<blockquote>quoted</blockquote>", "> quoted"),
        ("1 &lt; 2<br>3", "1 < 2\n3"),
        ("if a<b and c>d then", "if a<b and c>d then"),
        ("use the <div> tag", "use the <div> tag"),
        ("a <span> element", "a <span> element"),
        ("put a <br> there", "put a <br> there"),
        ("<b> is bold, <i> italic", "<b> is bold, <i> italic"),
        ("fish &amp; chips", "fish &amp; chips"),
        ("compare a<b and <i>x</i> \n\nnext", "compare a<b and <i>x</i> \n\nnext"),
        ("see https://matrix.to/#/@x:b\n  - item &amp; more", "see https://matrix.to/#/@x:b\n  - item &amp; more"),
        (
            '<mx-reply><blockquote><a href="https://matrix.to/#/!room:b/$ev">In reply to</a> '
            f'{pill("@a:b", "@a:b")}<br>parent</blockquote></mx-reply>child',
            "child",
        ),
        ('<p>look <img src="mxc://beeper.local/abc" alt="x.png"></p>', "look"),
    ],
)
def test_html_text_becomes_plain_text(text, expected):
    assert ingest.plain_text(text, {ALICE: "Alice Smith"}.get) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("<b>x</b>", True),
        ("<B class='y'>x</b >", True),
        ("<b>x</i>", False),
        ("</b><b>", False),
        ("<b><i>x</i>", True),
        ("<h2>t</h2>", True),
        ("<h2>t</h3>", False),
        ("<abbr>t</abbr>", False),
    ],
)
def test_has_paired_tag(text, expected):
    assert ingest.has_paired_tag(text) is expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("<mx-reply>q</mx-reply>a", "a"),
        ("a<MX-REPLY>q</mx-reply>b<mx-reply>r</mx-reply>c", "abc"),
        ("a<mx-reply>q</mx-reply>b<mx-reply>open", "ab<mx-reply>open"),
        ("<mx-reply><mx-reply>q</mx-reply>a", "a"),
    ],
)
def test_drop_replies(text, expected):
    assert ingest.drop_replies(text) == expected


def test_deeply_nested_html_text_becomes_its_text():
    assert ingest.plain_text("<div>" * 3000 + "<b>deep</b>" + "</div>" * 3000, {}.get) == "deep"


@pytest.mark.parametrize(
    "text",
    [
        "<b>" * 30_000,
        "<b>x" * 25_000 + "</i>",
        "<mx-reply>" * 10_000,
    ],
    ids=["unclosed", "mismatched-closer", "unclosed-replies"],
)
def test_html_detection_is_linear_in_the_text(text):
    start = time.perf_counter()
    ingest.plain_text(text, {}.get)
    # Quadratic matching took seconds on these; linear takes milliseconds.
    assert time.perf_counter() - start < 0.5


@pytest.fixture
def chat(db_session, beeper_account, qdrant) -> BeeperChat:
    network = BeeperNetwork(
        account=beeper_account, network_account_id="slackgo.1", network="Slack", collect_messages=True,
    )
    chat = BeeperChat(network=network, chat_id="!social:beeper.local", title="#social", chat_type="group")
    db_session.add_all([
        network, chat, BeeperParticipant(network=network, participant_id=ALICE, full_name="Alice Smith"),
    ])
    db_session.commit()
    return chat


def message(chat: BeeperChat, message_id: str, text: str, kind: str = "TEXT") -> beeper.Message:
    return beeper.Message.model_validate({
        "id": message_id, "chatID": chat.chat_id, "accountID": chat.network.network_account_id,
        "senderID": "@bob:beeper.local", "senderName": "Bob", "type": kind,
        "timestamp": "2026-11-05T10:00:00.000Z", "sortKey": message_id, "text": text,
    })


def stored(db_session, chat: BeeperChat) -> dict[str, str]:
    db_session.expire_all()
    return {m.message_id: m.content for m in db_session.query(BeeperMessage).filter_by(chat_id=chat.id)}


def test_a_mention_is_stored_with_the_participants_name(db_session, chat):
    ingest.store_message(db_session, chat, message(chat, "m1", f"ping {pill(ALICE)} about lunch"), [])

    assert stored(db_session, chat) == {"m1": "ping @Alice Smith about lunch"}


NOTICES = [
    f"{pill(ALICE, 'daniel-claude')} has joined the channel",
    f"{pill(ALICE)} has joined the channel",
    f"{pill(ALICE)} has left the channel",
    f"{pill(ALICE)} was added to the channel by {pill('@bob:beeper.local', 'Bob')}",
    f"{pill(ALICE)} was removed from the channel",
]


@pytest.mark.parametrize("text", NOTICES)
@pytest.mark.parametrize("kind", ["TEXT", "NOTICE"])
def test_channel_membership_notices_are_not_stored(db_session, chat, text, kind):
    assert ingest.store_message(db_session, chat, message(chat, "m1", text, kind), []) == "ignored"

    assert stored(db_session, chat) == {}


@pytest.mark.parametrize(
    "text",
    [
        "Bob has joined the channel",
        f"I think {pill(ALICE)} has joined the channel",
        f"{pill(ALICE)} has joined the channel, say hi",
        f"{pill(ALICE)} has joined the team",
    ],
)
def test_messages_that_only_mention_joining_are_stored(db_session, chat, text):
    ingest.store_message(db_session, chat, message(chat, "m1", text), [])

    assert list(stored(db_session, chat)) == ["m1"]

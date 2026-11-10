import pytest

from memory.common.discord_attachments import message_id_of


@pytest.mark.parametrize(
    "name, message_id",
    [
        ("discord/801/4ec59bc8.png", 801),
        ("/elsewhere/discord/801/4ec59bc8.png", 801),
        ("discord/general/4ec59bc8.png", None),
        ("discord/²/4ec59bc8.png", None),
        ("discord/٣/4ec59bc8.png", None),
        (f"discord/{2**63 - 1}/4ec59bc8.png", 2**63 - 1),
        (f"discord/{2**63}/4ec59bc8.png", None),
        ("4ec59bc8.png", None),
        ("", None),
    ],
)
def test_message_id_of_reads_the_images_directory(name, message_id):
    assert message_id_of(name) == message_id

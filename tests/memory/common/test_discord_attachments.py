import pytest

from memory.common.discord_attachments import message_id_of


@pytest.mark.parametrize(
    "name, message_id",
    [
        ("discord/801/4ec59bc8.png", 801),
        ("/elsewhere/discord/801/4ec59bc8.png", 801),
        ("discord/general/4ec59bc8.png", None),
        ("4ec59bc8.png", None),
        ("", None),
    ],
)
def test_message_id_of_reads_the_images_directory(name, message_id):
    assert message_id_of(name) == message_id

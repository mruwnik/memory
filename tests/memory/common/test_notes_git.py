import os
import subprocess

import pytest

from memory.common.notes_git import TOKEN_ENV, check_remote_url, credential_args

TOKEN = "ghp_se\\ncret1234"


def fill(tmp_path, asked: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *credential_args("git.example.com"), "credential", "fill"],
        input=asked + "\n",
        env={**os.environ, TOKEN_ENV: TOKEN, "GIT_TERMINAL_PROMPT": "0", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "asked, username",
    [("", "x-access-token"), ("username=me\n", "me")],
)
def test_the_credential_helper_answers_the_remotes_host_with_the_token_verbatim(tmp_path, asked, username):
    res = fill(tmp_path, f"protocol=https\nhost=git.example.com\n{asked}")

    assert f"username={username}\npassword={TOKEN}\n" in res.stdout


def test_the_credential_helper_never_answers_another_host(tmp_path):
    res = fill(tmp_path, "protocol=https\nhost=evil.example.com\n")

    assert res.returncode != 0
    assert "ghp_" not in res.stdout


@pytest.mark.parametrize(
    "url",
    ["https://h.example.com/a\nb", "https://h.example.com/a\tb", "git@h.example.com:a\x00b", "ssh://-u@h.example.com/notes.git"],
)
def test_remote_urls_with_control_characters_or_an_option_like_user_are_rejected(url):
    with pytest.raises(ValueError):
        check_remote_url(url)

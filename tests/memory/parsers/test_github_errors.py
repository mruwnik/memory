"""Tests for memory.common.github.errors and the structured-error behaviour of
the GitHub team-management client (invite/membership/422 handling)."""

import pytest
import requests
from unittest.mock import Mock, patch

from memory.common.github import GithubClient, GithubCredentials
from memory.common.github.errors import (
    describe_github_error,
    expand_scopes,
    is_already_member_error,
    missing_scope_message,
    parse_json_body,
    split_oauth_scopes,
)


def make_error_response(status_code, headers=None, json_body=None):
    """Build a mock response with arbitrary status, headers and JSON body."""
    response = Mock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.headers = headers or {}
    response.json.return_value = json_body or {}
    response.text = ""
    return response


# =============================================================================
# errors module: scope parsing / missing-scope detection
# =============================================================================


@pytest.mark.parametrize(
    "header,expected",
    [
        (None, set()),
        ("", set()),
        ("repo, write:org", {"repo", "write:org"}),
        (" admin:org ,read:user ", {"admin:org", "read:user"}),
    ],
)
def test_split_oauth_scopes(header, expected):
    assert split_oauth_scopes(header) == expected


def test_expand_scopes_includes_implied_children():
    assert expand_scopes({"admin:org"}) >= {"admin:org", "write:org", "read:org"}


def test_missing_scope_message_flags_missing_admin_org():
    resp = make_error_response(
        403,
        {"X-Accepted-OAuth-Scopes": "admin:org", "X-OAuth-Scopes": "repo, write:org"},
    )
    msg = missing_scope_message(resp)
    assert msg is not None
    assert "admin:org" in msg


def test_missing_scope_message_none_when_held_via_parent():
    resp = make_error_response(
        403, {"X-Accepted-OAuth-Scopes": "read:org", "X-OAuth-Scopes": "admin:org"}
    )
    assert missing_scope_message(resp) is None


def test_missing_scope_message_none_for_non_403():
    resp = make_error_response(404, {"X-Accepted-OAuth-Scopes": "admin:org"})
    assert missing_scope_message(resp) is None


def test_parse_json_body_returns_empty_on_non_json():
    resp = Mock()
    resp.json.side_effect = ValueError("no json")
    assert parse_json_body(resp) == {}


def test_parse_json_body_returns_empty_on_non_object():
    resp = Mock()
    resp.json.return_value = ["not", "a", "dict"]
    assert parse_json_body(resp) == {}


def test_invite_to_org_success_tolerates_non_json_body():
    credentials = GithubCredentials(auth_type="pat", access_token="token")
    user_resp = make_error_response(200, json_body={"id": 1})
    ok_resp = make_error_response(201)
    ok_resp.json.side_effect = ValueError("no json")
    with patch.object(requests.Session, "get", return_value=user_resp), patch.object(
        requests.Session, "post", return_value=ok_resp
    ):
        client = GithubClient(credentials)
        result = client.invite_to_org("org", "user")

    assert result == {}  # structured (non-raising), no "error" key -> success


def test_describe_github_error_uses_body_message_when_not_scope():
    resp = make_error_response(404, {}, {"message": "Not Found"})
    msg = describe_github_error(resp, "Doing thing")
    assert "HTTP 404" in msg
    assert "Not Found" in msg


@pytest.mark.parametrize(
    "error_data,expected",
    [
        ({"message": "Invitee is already a part of this org."}, True),
        ({"errors": [{"message": "already invited"}]}, True),
        ({"message": "Over invitation rate limit"}, False),
        ({"message": "Validation Failed", "errors": [{"message": "bad data"}]}, False),
        ({}, False),
    ],
)
def test_is_already_member_error(error_data, expected):
    assert is_already_member_error(error_data) is expected


# =============================================================================
# client: invite_to_org / check_org_membership / add_team_member error surfacing
# =============================================================================


def test_invite_to_org_reports_missing_scope():
    credentials = GithubCredentials(auth_type="pat", access_token="token")
    user_resp = make_error_response(200, json_body={"id": 123})
    invite_resp = make_error_response(
        403,
        {"X-Accepted-OAuth-Scopes": "admin:org", "X-OAuth-Scopes": "repo, write:org"},
        {"message": "You must be an admin to create an invitation."},
    )
    with patch.object(requests.Session, "get", return_value=user_resp), patch.object(
        requests.Session, "post", return_value=invite_resp
    ):
        client = GithubClient(credentials)
        result = client.invite_to_org("EquiStamp", "rpast", team_ids=[1])

    assert result["status_code"] == 403
    assert "admin:org" in result["error"]


def test_invite_to_org_reports_user_not_found():
    credentials = GithubCredentials(auth_type="pat", access_token="token")
    with patch.object(requests.Session, "get", return_value=make_error_response(404)):
        client = GithubClient(credentials)
        result = client.invite_to_org("EquiStamp", "ghost")

    assert "not found" in result["error"]


def test_invite_to_org_user_lookup_without_id_is_error():
    credentials = GithubCredentials(auth_type="pat", access_token="token")
    user_resp = make_error_response(200, json_body={})  # 2xx but no "id"
    with patch.object(requests.Session, "get", return_value=user_resp), patch.object(
        requests.Session, "post"
    ) as mock_post:
        client = GithubClient(credentials)
        result = client.invite_to_org("org", "user")

    assert "no id" in result["error"]
    mock_post.assert_not_called()


def test_invite_to_org_422_already_member_is_success():
    credentials = GithubCredentials(auth_type="pat", access_token="token")
    user_resp = make_error_response(200, json_body={"id": 1})
    invite_resp = make_error_response(
        422,
        json_body={
            "message": "Validation Failed",
            "errors": [{"message": "Invitee is already a part of this org."}],
        },
    )
    with patch.object(requests.Session, "get", return_value=user_resp), patch.object(
        requests.Session, "post", return_value=invite_resp
    ):
        client = GithubClient(credentials)
        result = client.invite_to_org("org", "user")

    assert result.get("status") == "already_invited_or_member"
    assert "error" not in result


def test_invite_to_org_422_rate_limit_is_error():
    credentials = GithubCredentials(auth_type="pat", access_token="token")
    user_resp = make_error_response(200, json_body={"id": 1})
    invite_resp = make_error_response(
        422, json_body={"message": "Over invitation rate limit"}
    )
    with patch.object(requests.Session, "get", return_value=user_resp), patch.object(
        requests.Session, "post", return_value=invite_resp
    ):
        client = GithubClient(credentials)
        result = client.invite_to_org("org", "user")

    assert result["status_code"] == 422
    assert "rate limit" in result["error"].lower()


def test_check_org_membership_returns_none_on_404():
    credentials = GithubCredentials(auth_type="pat", access_token="token")
    with patch.object(requests.Session, "get", return_value=make_error_response(404)):
        client = GithubClient(credentials)
        assert client.check_org_membership("org", "user") is None


def test_check_org_membership_raises_on_non_404_error():
    credentials = GithubCredentials(auth_type="pat", access_token="token")
    resp = make_error_response(403)
    resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    with patch.object(requests.Session, "get", return_value=resp):
        client = GithubClient(credentials)
        with pytest.raises(requests.HTTPError):
            client.check_org_membership("org", "user")


def test_add_team_member_reports_membership_check_failure():
    credentials = GithubCredentials(auth_type="pat", access_token="token")
    client = GithubClient(credentials)
    bad_resp = make_error_response(
        403,
        {"X-Accepted-OAuth-Scopes": "read:org", "X-OAuth-Scopes": "repo"},
    )
    with patch.object(
        client,
        "check_org_membership",
        side_effect=requests.HTTPError(response=bad_resp),
    ):
        result = client.add_team_member("org", "team", "user")

    assert result["success"] is False
    assert result["org_membership"] is None
    assert "read:org" in result["error"]


def test_add_team_member_surfaces_invite_error():
    credentials = GithubCredentials(auth_type="pat", access_token="token")
    client = GithubClient(credentials)
    with patch.object(client, "check_org_membership", return_value=None), patch.object(
        client, "fetch_team", return_value={"github_id": 1, "slug": "t"}
    ), patch.object(
        client,
        "invite_to_org",
        return_value={"error": "needs 'admin:org'", "status_code": 403},
    ):
        result = client.add_team_member("org", "t", "user")

    assert result["success"] is False
    assert "admin:org" in result["error"]


def test_add_team_member_invited_success():
    credentials = GithubCredentials(auth_type="pat", access_token="token")
    client = GithubClient(credentials)
    with patch.object(client, "check_org_membership", return_value=None), patch.object(
        client, "fetch_team", return_value={"github_id": 1, "slug": "t"}
    ), patch.object(client, "invite_to_org", return_value={"id": 99, "login": "user"}):
        result = client.add_team_member("org", "t", "user")

    assert result["success"] is True
    assert result["action"] == "invited"

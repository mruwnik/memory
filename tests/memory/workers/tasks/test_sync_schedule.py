from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from memory.common.celery_app import (
    SYNC_ACCOUNT,
    SYNC_ARTICLE_FEED,
    SYNC_CALENDAR_ACCOUNT,
    SYNC_GITHUB_REPO,
    SYNC_GOOGLE_FOLDER,
    SYNC_LESSWRONG,
)
from memory.common.db.models import (
    ArticleFeed,
    CalendarAccount,
    EmailAccount,
    ForumSyncConfig,
    GithubAccount,
    GithubRepo,
    GoogleAccount,
    GoogleFolder,
)
from memory.workers.tasks import sync_schedule


def minutes_ago(minutes: float | None) -> datetime | None:
    return None if minutes is None else datetime.now(timezone.utc) - timedelta(minutes=minutes)


@pytest.fixture
def send_task():
    with patch.object(sync_schedule.app, "send_task") as send:
        yield send


def queued(send_task, task) -> list:
    """The args of each queued ``task``."""
    return [c.kwargs["args"] for c in send_task.call_args_list if c.args == (task,)]


@pytest.mark.parametrize(
    "active, queued_minutes_ago, expected",
    [
        (True, None, True),
        (True, 61, True),
        (True, 59, False),
        (False, None, False),
    ],
)
def test_a_source_is_queued_once_its_interval_has_passed(
    db_session, send_task, active, queued_minutes_ago, expected
):
    feed = ArticleFeed(
        url="https://example.com/feed.xml",
        active=active,
        check_interval=60,
        sync_queued_at=minutes_ago(queued_minutes_ago),
    )
    db_session.add(feed)
    db_session.commit()

    sync_schedule.sync_due_sources()

    assert (queued(send_task, SYNC_ARTICLE_FEED) == [[feed.id]]) is expected


def test_a_queued_source_is_not_queued_again_before_its_interval(db_session, send_task):
    """A slow or failing sync must not be queued on every tick."""
    db_session.add(ArticleFeed(url="https://example.com/feed.xml", check_interval=60))
    db_session.commit()

    sync_schedule.sync_due_sources()
    sync_schedule.sync_due_sources()

    assert len(queued(send_task, SYNC_ARTICLE_FEED)) == 1


def feed(user):
    return ArticleFeed(url="https://example.com/feed.xml"), SYNC_ARTICLE_FEED


def email(user):
    account = EmailAccount(user_id=user.id, name="Mail", email_address="me@example.com")
    return account, SYNC_ACCOUNT


def repo(user):
    account = GithubAccount(user_id=user.id, name="GH", auth_type="pat", access_token="t")
    return GithubRepo(account=account, owner="org", name="repo"), SYNC_GITHUB_REPO


def folder(user):
    account = GoogleAccount(user_id=user.id, name="Drive", email="drive@example.com")
    return GoogleFolder(account=account, folder_id="f1", folder_name="F"), SYNC_GOOGLE_FOLDER


def calendar(user):
    return CalendarAccount(name="Cal", calendar_type="caldav"), SYNC_CALENDAR_ACCOUNT


@pytest.mark.parametrize("make_source", [feed, email, repo, folder, calendar])
def test_each_polled_source_is_queued_with_its_own_task(db_session, test_user, send_task, make_source):
    source, task = make_source(test_user)
    db_session.add(source)
    db_session.commit()

    sync_schedule.sync_due_sources()

    assert queued(send_task, task) == [[source.id]]


@pytest.mark.parametrize("queued_minutes_ago, expected", [(None, [[]]), (60, [])])
def test_the_forum_sync_is_queued_when_due(db_session, send_task, queued_minutes_ago, expected):
    db_session.merge(
        ForumSyncConfig(forum="lesswrong", check_interval=24 * 60, sync_queued_at=minutes_ago(queued_minutes_ago))
    )
    db_session.commit()

    sync_schedule.sync_due_sources()

    assert queued(send_task, SYNC_LESSWRONG) == expected

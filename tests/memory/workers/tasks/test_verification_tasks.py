"""
Tests for verification tasks.
"""

from unittest.mock import MagicMock, patch

import pytest

from memory.workers.tasks import verification


# Test verify_orphans


@patch("memory.workers.tasks.verification.verify_source_batch")
@patch("memory.workers.tasks.verification.group_items_by_batch_key")
@patch("memory.workers.tasks.verification.select_items_for_verification")
@patch("memory.workers.tasks.verification.make_session")
def test_verify_orphans_no_items(
    mock_make_session, mock_select, mock_group, mock_verify_batch
):
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_select.return_value = []

    result = verification.verify_orphans()

    assert result == {"status": "no_items", "checked": 0}
    mock_group.assert_not_called()
    mock_verify_batch.delay.assert_not_called()


@patch("memory.workers.tasks.verification.verify_source_batch")
@patch("memory.workers.tasks.verification.group_items_by_batch_key")
@patch("memory.workers.tasks.verification.select_items_for_verification")
@patch("memory.workers.tasks.verification.make_session")
def test_verify_orphans_dispatches_tasks(
    mock_make_session, mock_select, mock_group, mock_verify_batch
):
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    # Create mock items
    item1 = MagicMock()
    item1.id = 1
    item2 = MagicMock()
    item2.id = 2
    item3 = MagicMock()
    item3.id = 3

    mock_select.return_value = [item1, item2, item3]

    # Mock grouping
    mock_group.return_value = {
        ("mail_message", 100): [item1, item2],
        ("github_item", 200): [item3],
    }

    # Mock task creation
    mock_task1 = MagicMock()
    mock_task1.id = "task-uuid-1"
    mock_task2 = MagicMock()
    mock_task2.id = "task-uuid-2"
    mock_verify_batch.delay.side_effect = [mock_task1, mock_task2]

    result = verification.verify_orphans()

    assert result["status"] == "dispatched"
    assert result["total_items"] == 3
    assert result["groups"] == 2
    assert len(result["tasks"]) == 2

    # Verify first task
    assert result["tasks"][0]["source_type"] == "mail_message"
    assert result["tasks"][0]["key"] == "100"
    assert result["tasks"][0]["items"] == 2
    assert result["tasks"][0]["task_id"] == "task-uuid-1"

    # Verify second task
    assert result["tasks"][1]["source_type"] == "github_item"
    assert result["tasks"][1]["key"] == "200"
    assert result["tasks"][1]["items"] == 1
    assert result["tasks"][1]["task_id"] == "task-uuid-2"

    # Verify delay was called correctly
    assert mock_verify_batch.delay.call_count == 2
    mock_verify_batch.delay.assert_any_call("mail_message", 100, [1, 2])
    mock_verify_batch.delay.assert_any_call("github_item", 200, [3])


@patch("memory.workers.tasks.verification.select_items_for_verification")
@patch("memory.workers.tasks.verification.make_session")
@patch("memory.workers.tasks.verification.settings")
def test_verify_orphans_uses_default_batch_size(
    mock_settings, mock_make_session, mock_select
):
    mock_settings.VERIFICATION_BATCH_SIZE = 500
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session
    mock_select.return_value = []

    verification.verify_orphans()

    mock_select.assert_called_once_with(
        mock_session, batch_size=500, source_types=None
    )


@patch("memory.workers.tasks.verification.select_items_for_verification")
@patch("memory.workers.tasks.verification.make_session")
def test_verify_orphans_uses_custom_batch_size(mock_make_session, mock_select):
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session
    mock_select.return_value = []

    verification.verify_orphans(batch_size=100)

    mock_select.assert_called_once_with(
        mock_session, batch_size=100, source_types=None
    )


@patch("memory.workers.tasks.verification.select_items_for_verification")
@patch("memory.workers.tasks.verification.make_session")
def test_verify_orphans_filters_by_source_types(mock_make_session, mock_select):
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session
    mock_select.return_value = []

    verification.verify_orphans(source_types=["mail_message", "github_item"])

    call_args = mock_select.call_args[1]
    assert call_args["source_types"] == ["mail_message", "github_item"]


@patch("memory.workers.tasks.verification.verify_source_batch")
@patch("memory.workers.tasks.verification.group_items_by_batch_key")
@patch("memory.workers.tasks.verification.select_items_for_verification")
@patch("memory.workers.tasks.verification.make_session")
def test_verify_orphans_handles_multiple_groups(
    mock_make_session, mock_select, mock_group, mock_verify_batch
):
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    items = [MagicMock(id=i) for i in range(10)]
    mock_select.return_value = items

    # Create 5 different groups
    mock_group.return_value = {
        ("mail_message", 1): items[:2],
        ("mail_message", 2): items[2:4],
        ("github_item", 3): items[4:7],
        ("github_item", 4): items[7:9],
        ("blog_post", 5): items[9:],
    }

    mock_task = MagicMock()
    mock_task.id = "task-id"
    mock_verify_batch.delay.return_value = mock_task

    result = verification.verify_orphans()

    assert result["status"] == "dispatched"
    assert result["total_items"] == 10
    assert result["groups"] == 5
    assert len(result["tasks"]) == 5
    assert mock_verify_batch.delay.call_count == 5


@patch("memory.workers.tasks.verification.verify_source_batch")
@patch("memory.workers.tasks.verification.group_items_by_batch_key")
@patch("memory.workers.tasks.verification.select_items_for_verification")
@patch("memory.workers.tasks.verification.make_session")
def test_verify_orphans_single_item_group(
    mock_make_session, mock_select, mock_group, mock_verify_batch
):
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    item = MagicMock()
    item.id = 123
    mock_select.return_value = [item]

    mock_group.return_value = {("mail_message", 1): [item]}

    mock_task = MagicMock()
    mock_task.id = "task-uuid"
    mock_verify_batch.delay.return_value = mock_task

    result = verification.verify_orphans()

    assert result["status"] == "dispatched"
    assert result["total_items"] == 1
    assert result["groups"] == 1
    assert result["tasks"][0]["items"] == 1


# Test verify_source_batch


@patch("memory.workers.tasks.verification.VERIFIERS", {"mail_message": MagicMock()})
@patch("memory.workers.tasks.verification.verify_items")
@patch("memory.workers.tasks.verification.make_session")
def test_verify_source_batch_success(mock_make_session, mock_verify_items):
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    # Mock verification result
    mock_result = MagicMock()
    mock_result.verified = 5
    mock_result.orphaned = 2
    mock_result.errors = 0
    mock_result.deleted = 2
    mock_verify_items.return_value = mock_result

    result = verification.verify_source_batch(
        "mail_message", 100, [1, 2, 3, 4, 5, 6, 7]
    )

    assert result["status"] == "completed"
    assert result["source_type"] == "mail_message"
    assert result["batch_key"] == "100"
    assert result["verified"] == 5
    assert result["orphaned"] == 2
    assert result["errors"] == 0
    assert result["deleted"] == 2

    mock_verify_items.assert_called_once_with(
        mock_session, "mail_message", 100, [1, 2, 3, 4, 5, 6, 7]
    )
    mock_session.commit.assert_called_once()


@patch("memory.workers.tasks.verification.VERIFIERS", {})
def test_verify_source_batch_unknown_source_type():
    result = verification.verify_source_batch("unknown_type", 100, [1, 2, 3])

    assert result["status"] == "error"
    assert "No verifier for unknown_type" in result["error"]


@patch("memory.workers.tasks.verification.VERIFIERS", {"github_item": MagicMock()})
@patch("memory.workers.tasks.verification.verify_items")
@patch("memory.workers.tasks.verification.make_session")
def test_verify_source_batch_with_errors(mock_make_session, mock_verify_items):
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_result = MagicMock()
    mock_result.verified = 8
    mock_result.orphaned = 1
    mock_result.errors = 1
    mock_result.deleted = 1
    mock_verify_items.return_value = mock_result

    result = verification.verify_source_batch("github_item", 200, [10, 20, 30])

    assert result["status"] == "completed"
    assert result["errors"] == 1


@patch("memory.workers.tasks.verification.VERIFIERS", {"mail_message": MagicMock()})
@patch("memory.workers.tasks.verification.verify_items")
@patch("memory.workers.tasks.verification.make_session")
def test_verify_source_batch_all_orphaned(mock_make_session, mock_verify_items):
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_result = MagicMock()
    mock_result.verified = 0
    mock_result.orphaned = 5
    mock_result.errors = 0
    mock_result.deleted = 5
    mock_verify_items.return_value = mock_result

    result = verification.verify_source_batch("mail_message", 100, [1, 2, 3, 4, 5])

    assert result["orphaned"] == 5
    assert result["deleted"] == 5
    assert result["verified"] == 0


@patch("memory.workers.tasks.verification.VERIFIERS", {"mail_message": MagicMock()})
@patch("memory.workers.tasks.verification.verify_items")
@patch("memory.workers.tasks.verification.make_session")
def test_verify_source_batch_all_verified(mock_make_session, mock_verify_items):
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_result = MagicMock()
    mock_result.verified = 10
    mock_result.orphaned = 0
    mock_result.errors = 0
    mock_result.deleted = 0
    mock_verify_items.return_value = mock_result

    result = verification.verify_source_batch("mail_message", 100, list(range(10)))

    assert result["verified"] == 10
    assert result["orphaned"] == 0
    assert result["deleted"] == 0


@patch("memory.workers.tasks.verification.VERIFIERS", {"blog_post": MagicMock()})
@patch("memory.workers.tasks.verification.verify_items")
@patch("memory.workers.tasks.verification.make_session")
def test_verify_source_batch_batch_key_conversion(
    mock_make_session, mock_verify_items
):
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_result = MagicMock()
    mock_result.verified = 1
    mock_result.orphaned = 0
    mock_result.errors = 0
    mock_result.deleted = 0
    mock_verify_items.return_value = mock_result

    # Test with None batch key
    result = verification.verify_source_batch("blog_post", None, [1])
    assert result["batch_key"] == "None"

    # Test with string batch key
    result = verification.verify_source_batch("blog_post", "key-123", [1])
    assert result["batch_key"] == "key-123"

    # Test with int batch key
    result = verification.verify_source_batch("blog_post", 456, [1])
    assert result["batch_key"] == "456"


@patch("memory.workers.tasks.verification.VERIFIERS", {"mail_message": MagicMock()})
@patch("memory.workers.tasks.verification.verify_items")
@patch("memory.workers.tasks.verification.make_session")
def test_verify_source_batch_empty_item_list(mock_make_session, mock_verify_items):
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_result = MagicMock()
    mock_result.verified = 0
    mock_result.orphaned = 0
    mock_result.errors = 0
    mock_result.deleted = 0
    mock_verify_items.return_value = mock_result

    result = verification.verify_source_batch("mail_message", 100, [])

    assert result["verified"] == 0
    mock_verify_items.assert_called_once_with(mock_session, "mail_message", 100, [])


@pytest.mark.parametrize(
    "source_type",
    ["mail_message", "github_item", "blog_post", "calendar_event"],
)
@patch("memory.workers.tasks.verification.verify_items")
@patch("memory.workers.tasks.verification.make_session")
def test_verify_source_batch_different_source_types(
    mock_make_session, mock_verify_items, source_type
):
    # Add the source type to VERIFIERS
    with patch.dict(
        "memory.workers.tasks.verification.VERIFIERS", {source_type: MagicMock()}
    ):
        mock_session = MagicMock()
        mock_make_session.return_value.__enter__.return_value = mock_session

        mock_result = MagicMock()
        mock_result.verified = 1
        mock_result.orphaned = 0
        mock_result.errors = 0
        mock_result.deleted = 0
        mock_verify_items.return_value = mock_result

        result = verification.verify_source_batch(source_type, 1, [1])

        assert result["status"] == "completed"
        assert result["source_type"] == source_type


@patch("memory.workers.tasks.verification.VERIFIERS", {"mail_message": MagicMock()})
@patch("memory.workers.tasks.verification.verify_items")
@patch("memory.workers.tasks.verification.make_session")
def test_verify_source_batch_commits_session(mock_make_session, mock_verify_items):
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_result = MagicMock()
    mock_result.verified = 1
    mock_result.orphaned = 0
    mock_result.errors = 0
    mock_result.deleted = 0
    mock_verify_items.return_value = mock_result

    verification.verify_source_batch("mail_message", 100, [1])

    # Verify commit was called
    mock_session.commit.assert_called_once()


@patch("memory.workers.tasks.verification.VERIFIERS", {"mail_message": MagicMock()})
@patch("memory.workers.tasks.verification.verify_items")
@patch("memory.workers.tasks.verification.make_session")
def test_verify_source_batch_large_batch(mock_make_session, mock_verify_items):
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_result = MagicMock()
    mock_result.verified = 1000
    mock_result.orphaned = 50
    mock_result.errors = 5
    mock_result.deleted = 50
    mock_verify_items.return_value = mock_result

    # Large batch of 1000 items
    item_ids = list(range(1, 1001))
    result = verification.verify_source_batch("mail_message", 100, item_ids)

    assert result["verified"] == 1000
    assert result["orphaned"] == 50
    mock_verify_items.assert_called_once_with(
        mock_session, "mail_message", 100, item_ids
    )

"""
Tests for observation syncing tasks.
"""

from unittest.mock import MagicMock, patch


from memory.workers.tasks import observations


class TestSyncObservation:
    """Tests for sync_observation task."""

    @patch("memory.workers.tasks.observations.process_content_item")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_creates_new_observation(
        self, mock_hash, mock_session, mock_check, mock_process
    ):
        # Setup mocks
        mock_hash.return_value = "test_hash_123"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_check.return_value = None  # No existing observation
        mock_process.return_value = {"status": "created"}

        observations.sync_observation(
            subject="User preferences",
            content="User prefers dark mode",
            observation_type="preference",
        )

        # Verify hash was created from content, subject, and type
        mock_hash.assert_called_once_with(
            "User prefers dark modeUser preferencespreference"
        )

        # Verify check for existing
        mock_check.assert_called_once_with(mock_db, observations.AgentObservation, sha256="test_hash_123")

        # Verify processing was called
        mock_process.assert_called_once()
        created_observation = mock_process.call_args[0][0]
        assert created_observation.content == "User prefers dark mode"
        assert created_observation.subject == "User preferences"
        assert created_observation.observation_type == "preference"
        assert created_observation.sha256 == "test_hash_123"

    @patch("memory.workers.tasks.observations.create_task_result")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_returns_existing_observation(
        self, mock_hash, mock_session, mock_check, mock_result
    ):
        # Setup mocks
        mock_hash.return_value = "duplicate_hash"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        existing_obs = MagicMock()
        existing_obs.subject = "Existing subject"
        mock_check.return_value = existing_obs

        mock_result.return_value = {"status": "already_exists", "id": 123}

        result = observations.sync_observation(
            subject="Duplicate",
            content="Same content",
            observation_type="fact",
        )

        # Should return existing result
        assert result == {"status": "already_exists", "id": 123}
        mock_result.assert_called_once_with(existing_obs, "already_exists")

    @patch("memory.workers.tasks.observations.process_content_item")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_sets_evidence_field(
        self, mock_hash, mock_session, mock_check, mock_process
    ):
        mock_hash.return_value = "hash"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_check.return_value = None
        mock_process.return_value = {"status": "created"}

        evidence = {"quote": "test quote", "context": "test context"}

        observations.sync_observation(
            subject="Test",
            content="Content",
            observation_type="fact",
            evidence=evidence,
        )

        created_observation = mock_process.call_args[0][0]
        assert created_observation.evidence == evidence

    @patch("memory.workers.tasks.observations.process_content_item")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_sets_tags_field(
        self, mock_hash, mock_session, mock_check, mock_process
    ):
        mock_hash.return_value = "hash"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_check.return_value = None
        mock_process.return_value = {"status": "created"}

        tags = ["preference", "ui", "theme"]

        observations.sync_observation(
            subject="Test",
            content="Content",
            observation_type="preference",
            tags=tags,
        )

        created_observation = mock_process.call_args[0][0]
        assert created_observation.tags == tags

    @patch("memory.workers.tasks.observations.process_content_item")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_empty_tags_defaults_to_empty_list(
        self, mock_hash, mock_session, mock_check, mock_process
    ):
        mock_hash.return_value = "hash"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_check.return_value = None
        mock_process.return_value = {"status": "created"}

        observations.sync_observation(
            subject="Test",
            content="Content",
            observation_type="fact",
            tags=[],
        )

        created_observation = mock_process.call_args[0][0]
        assert created_observation.tags == []

    @patch("memory.workers.tasks.observations.process_content_item")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_sets_session_id(
        self, mock_hash, mock_session, mock_check, mock_process
    ):
        mock_hash.return_value = "hash"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_check.return_value = None
        mock_process.return_value = {"status": "created"}

        observations.sync_observation(
            subject="Test",
            content="Content",
            observation_type="fact",
            session_id="session-uuid-123",
        )

        created_observation = mock_process.call_args[0][0]
        assert created_observation.session_id == "session-uuid-123"

    @patch("memory.workers.tasks.observations.process_content_item")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_sets_agent_model(
        self, mock_hash, mock_session, mock_check, mock_process
    ):
        mock_hash.return_value = "hash"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_check.return_value = None
        mock_process.return_value = {"status": "created"}

        observations.sync_observation(
            subject="Test",
            content="Content",
            observation_type="fact",
            agent_model="claude-3-opus-20240229",
        )

        created_observation = mock_process.call_args[0][0]
        assert created_observation.agent_model == "claude-3-opus-20240229"

    @patch("memory.workers.tasks.observations.process_content_item")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_defaults_agent_model_to_unknown(
        self, mock_hash, mock_session, mock_check, mock_process
    ):
        mock_hash.return_value = "hash"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_check.return_value = None
        mock_process.return_value = {"status": "created"}

        observations.sync_observation(
            subject="Test",
            content="Content",
            observation_type="fact",
        )

        created_observation = mock_process.call_args[0][0]
        assert created_observation.agent_model == "unknown"

    @patch("memory.workers.tasks.observations.process_content_item")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_calculates_size_from_content(
        self, mock_hash, mock_session, mock_check, mock_process
    ):
        mock_hash.return_value = "hash"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_check.return_value = None
        mock_process.return_value = {"status": "created"}

        content = "This is test content"
        observations.sync_observation(
            subject="Test",
            content=content,
            observation_type="fact",
        )

        created_observation = mock_process.call_args[0][0]
        assert created_observation.size == len(content)

    @patch("memory.workers.tasks.observations.process_content_item")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_sets_mime_type_to_text_plain(
        self, mock_hash, mock_session, mock_check, mock_process
    ):
        mock_hash.return_value = "hash"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_check.return_value = None
        mock_process.return_value = {"status": "created"}

        observations.sync_observation(
            subject="Test",
            content="Content",
            observation_type="fact",
        )

        created_observation = mock_process.call_args[0][0]
        assert created_observation.mime_type == "text/plain"

    @patch("memory.workers.tasks.observations.process_content_item")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_sets_modality_to_observation(
        self, mock_hash, mock_session, mock_check, mock_process
    ):
        mock_hash.return_value = "hash"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_check.return_value = None
        mock_process.return_value = {"status": "created"}

        observations.sync_observation(
            subject="Test",
            content="Content",
            observation_type="fact",
        )

        created_observation = mock_process.call_args[0][0]
        assert created_observation.modality == "observation"

    @patch("memory.workers.tasks.observations.AgentObservation")
    @patch("memory.workers.tasks.observations.process_content_item")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_updates_confidences(
        self, mock_hash, mock_session, mock_check, mock_process, mock_obs_class
    ):
        mock_hash.return_value = "hash"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_check.return_value = None
        mock_process.return_value = {"status": "created"}

        # Create mock observation instance
        mock_observation = MagicMock()
        mock_obs_class.return_value = mock_observation

        confidences = {"temporal": 0.9, "semantic": 0.85}

        observations.sync_observation(
            subject="Test",
            content="Content",
            observation_type="fact",
            confidences=confidences,
        )

        # Verify update_confidences was called on the observation instance
        mock_observation.update_confidences.assert_called_once_with(confidences)

    @patch("memory.workers.tasks.observations.AgentObservation")
    @patch("memory.workers.tasks.observations.process_content_item")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_empty_confidences(
        self, mock_hash, mock_session, mock_check, mock_process, mock_obs_class
    ):
        mock_hash.return_value = "hash"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_check.return_value = None
        mock_process.return_value = {"status": "created"}

        # Create mock observation instance
        mock_observation = MagicMock()
        mock_obs_class.return_value = mock_observation

        observations.sync_observation(
            subject="Test",
            content="Content",
            observation_type="fact",
            confidences={},
        )

        # Verify update_confidences was called with empty dict
        mock_observation.update_confidences.assert_called_once_with({})

    @patch("memory.workers.tasks.observations.process_content_item")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_none_evidence_field(
        self, mock_hash, mock_session, mock_check, mock_process
    ):
        mock_hash.return_value = "hash"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_check.return_value = None
        mock_process.return_value = {"status": "created"}

        observations.sync_observation(
            subject="Test",
            content="Content",
            observation_type="fact",
            evidence=None,
        )

        created_observation = mock_process.call_args[0][0]
        assert created_observation.evidence is None

    @patch("memory.workers.tasks.observations.process_content_item")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_none_session_id(
        self, mock_hash, mock_session, mock_check, mock_process
    ):
        mock_hash.return_value = "hash"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_check.return_value = None
        mock_process.return_value = {"status": "created"}

        observations.sync_observation(
            subject="Test",
            content="Content",
            observation_type="fact",
            session_id=None,
        )

        created_observation = mock_process.call_args[0][0]
        assert created_observation.session_id is None

    @patch("memory.workers.tasks.observations.process_content_item")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_hash_includes_content_subject_and_type(
        self, mock_hash, mock_session, mock_check, mock_process
    ):
        mock_hash.return_value = "combined_hash"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_check.return_value = None
        mock_process.return_value = {"status": "created"}

        observations.sync_observation(
            subject="Subject A",
            content="Content B",
            observation_type="Type C",
        )

        # Hash should be created from concatenation
        mock_hash.assert_called_once_with("Content BSubject AType C")

    @patch("memory.workers.tasks.observations.process_content_item")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_different_types_same_content_different_hash(
        self, mock_hash, mock_session, mock_check, mock_process
    ):
        mock_hash.return_value = "hash1"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_check.return_value = None
        mock_process.return_value = {"status": "created"}

        # First call with type "preference"
        observations.sync_observation(
            subject="Test",
            content="Same content",
            observation_type="preference",
        )

        # Hash should include "preference"
        assert "preference" in mock_hash.call_args[0][0]

        # Reset mocks
        mock_hash.reset_mock()
        mock_hash.return_value = "hash2"

        # Second call with type "fact"
        observations.sync_observation(
            subject="Test",
            content="Same content",
            observation_type="fact",
        )

        # Hash should include "fact"
        assert "fact" in mock_hash.call_args[0][0]

    @patch("memory.workers.tasks.observations.process_content_item")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_observation_types(
        self, mock_hash, mock_session, mock_check, mock_process
    ):
        """Test various observation type values."""
        mock_hash.return_value = "hash"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_check.return_value = None
        mock_process.return_value = {"status": "created"}

        for obs_type in ["preference", "fact", "behavior", "goal", "pattern"]:
            observations.sync_observation(
                subject="Test",
                content="Content",
                observation_type=obs_type,
            )

            created_observation = mock_process.call_args[0][0]
            assert created_observation.observation_type == obs_type

            # Reset for next iteration
            mock_process.reset_mock()

    @patch("memory.workers.tasks.observations.process_content_item")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_unicode_content(
        self, mock_hash, mock_session, mock_check, mock_process
    ):
        mock_hash.return_value = "hash"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_check.return_value = None
        mock_process.return_value = {"status": "created"}

        unicode_content = "User prefers café ☕ and naïve résumé 中文"

        observations.sync_observation(
            subject="Unicode test",
            content=unicode_content,
            observation_type="preference",
        )

        created_observation = mock_process.call_args[0][0]
        assert created_observation.content == unicode_content
        assert created_observation.size == len(unicode_content)

    @patch("memory.workers.tasks.observations.process_content_item")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_empty_content(
        self, mock_hash, mock_session, mock_check, mock_process
    ):
        mock_hash.return_value = "hash"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_check.return_value = None
        mock_process.return_value = {"status": "created"}

        observations.sync_observation(
            subject="Test",
            content="",
            observation_type="fact",
        )

        created_observation = mock_process.call_args[0][0]
        assert created_observation.content == ""
        assert created_observation.size == 0

    @patch("memory.workers.tasks.observations.process_content_item")
    @patch("memory.workers.tasks.observations.check_content_exists")
    @patch("memory.workers.tasks.observations.make_session")
    @patch("memory.workers.tasks.observations.create_content_hash")
    def test_long_content(
        self, mock_hash, mock_session, mock_check, mock_process
    ):
        mock_hash.return_value = "hash"
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_check.return_value = None
        mock_process.return_value = {"status": "created"}

        long_content = "A" * 10000

        observations.sync_observation(
            subject="Long content test",
            content=long_content,
            observation_type="fact",
        )

        created_observation = mock_process.call_args[0][0]
        assert created_observation.content == long_content
        assert created_observation.size == 10000

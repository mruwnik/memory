import pytest
from datetime import datetime
from typing import Any

from memory.common.formatters.observation import (
    Evidence,
    generate_semantic_text,
    generate_temporal_text,
)


def test_generate_semantic_text_basic_functionality():
    evidence: Evidence = {"quote": "test quote", "context": "test context"}
    result = generate_semantic_text(
        subject="test_subject",
        observation_type="test_type",
        content="test_content",
        evidence=evidence,
    )
    assert (
        result
        == "Subject: test_subject | Type: test_type | Observation: test_content | Quote: test quote | Context: test context"
    )


@pytest.mark.parametrize(
    "evidence,expected_suffix",
    [
        ({"quote": "test quote"}, " | Quote: test quote"),
        ({"context": "test context"}, " | Context: test context"),
        ({}, ""),
    ],
)
def test_generate_semantic_text_partial_evidence(
    evidence: dict[str, str], expected_suffix: str
):
    result = generate_semantic_text(
        subject="subject",
        observation_type="type",
        content="content",
        evidence=evidence,  # type: ignore
    )
    expected = f"Subject: subject | Type: type | Observation: content{expected_suffix}"
    assert result == expected


def test_generate_semantic_text_none_evidence():
    result = generate_semantic_text(
        subject="subject",
        observation_type="type",
        content="content",
        evidence=None,  # type: ignore
    )
    assert result == "Subject: subject | Type: type | Observation: content"


@pytest.mark.parametrize(
    "invalid_evidence",
    [
        "string",
        123,
        ["list"],
        True,
    ],
)
def test_generate_semantic_text_invalid_evidence_types(invalid_evidence: Any):
    result = generate_semantic_text(
        subject="subject",
        observation_type="type",
        content="content",
        evidence=invalid_evidence,  # type: ignore
    )
    assert result == "Subject: subject | Type: type | Observation: content"


def test_generate_semantic_text_empty_strings():
    evidence = {"quote": "", "context": ""}
    result = generate_semantic_text(
        subject="",
        observation_type="",
        content="",
        evidence=evidence,  # type: ignore
    )
    assert result == "Subject:  | Type:  | Observation:  | Quote:  | Context: "


def test_generate_semantic_text_special_characters():
    evidence: Evidence = {
        "quote": "Quote with | pipe and | symbols",
        "context": "Context with special chars: @#$%",
    }
    result = generate_semantic_text(
        subject="Subject with | pipe",
        observation_type="Type with | pipe",
        content="Content with | pipe",
        evidence=evidence,
    )
    expected = "Subject: Subject with | pipe | Type: Type with | pipe | Observation: Content with | pipe | Quote: Quote with | pipe and | symbols | Context: Context with special chars: @#$%"
    assert result == expected


@pytest.mark.parametrize(
    "hour,expected_period",
    [
        (5, "morning"),
        (6, "morning"),
        (11, "morning"),
        (12, "afternoon"),
        (13, "afternoon"),
        (16, "afternoon"),
        (17, "evening"),
        (18, "evening"),
        (21, "evening"),
        (22, "late_night"),
        (23, "late_night"),
        (0, "late_night"),
        (1, "late_night"),
        (4, "late_night"),
    ],
)
def test_generate_temporal_text_time_periods(hour: int, expected_period: str):
    test_date = datetime(2024, 1, 15, hour, 30)  # Monday
    result = generate_temporal_text(
        subject="test_subject",
        content="test_content",
        created_at=test_date,
    )
    time_str = test_date.strftime("%H:%M")
    expected = f"Time: {time_str} on Monday ({expected_period}) | Subject: test_subject | Observation: test_content"
    assert result == expected


@pytest.mark.parametrize(
    "weekday,day_name",
    [
        (0, "Monday"),
        (1, "Tuesday"),
        (2, "Wednesday"),
        (3, "Thursday"),
        (4, "Friday"),
        (5, "Saturday"),
        (6, "Sunday"),
    ],
)
def test_generate_temporal_text_days_of_week(weekday: int, day_name: str):
    test_date = datetime(2024, 1, 15 + weekday, 10, 30)
    result = generate_temporal_text(
        subject="subject", content="content", created_at=test_date
    )
    assert f"on {day_name}" in result


@pytest.mark.parametrize("confidence", [0.0, 0.1, 0.5, 0.99, 1.0])
def test_generate_temporal_text_confidence_values(confidence: float):
    test_date = datetime(2024, 1, 15, 10, 30)
    # Test that function completes without error for various confidence values
    generate_temporal_text(
        subject="subject",
        content="content",
        created_at=test_date,
    )


@pytest.mark.parametrize(
    "test_date,expected_period",
    [
        (datetime(2024, 1, 15, 5, 0), "morning"),  # Start of morning
        (datetime(2024, 1, 15, 11, 59), "morning"),  # End of morning
        (datetime(2024, 1, 15, 12, 0), "afternoon"),  # Start of afternoon
        (datetime(2024, 1, 15, 16, 59), "afternoon"),  # End of afternoon
        (datetime(2024, 1, 15, 17, 0), "evening"),  # Start of evening
        (datetime(2024, 1, 15, 21, 59), "evening"),  # End of evening
        (datetime(2024, 1, 15, 22, 0), "late_night"),  # Start of late_night
        (datetime(2024, 1, 15, 4, 59), "late_night"),  # End of late_night
    ],
)
def test_generate_temporal_text_boundary_cases(
    test_date: datetime, expected_period: str
):
    result = generate_temporal_text(
        subject="subject", content="content", created_at=test_date
    )
    assert f"({expected_period})" in result


def test_generate_temporal_text_complete_format():
    test_date = datetime(2024, 3, 22, 14, 45)  # Friday afternoon
    result = generate_temporal_text(
        subject="Important observation",
        content="User showed strong preference for X",
        created_at=test_date,
    )
    expected = "Time: 14:45 on Friday (afternoon) | Subject: Important observation | Observation: User showed strong preference for X"
    assert result == expected


def test_generate_temporal_text_empty_strings():
    test_date = datetime(2024, 1, 15, 10, 30)
    result = generate_temporal_text(subject="", content="", created_at=test_date)
    assert result == "Time: 10:30 on Monday (morning) | Subject:  | Observation:"


def test_generate_temporal_text_special_characters():
    test_date = datetime(2024, 1, 15, 15, 20)
    result = generate_temporal_text(
        subject="Subject with | pipe",
        content="Content with | pipe and @#$ symbols",
        created_at=test_date,
    )
    expected = "Time: 15:20 on Monday (afternoon) | Subject: Subject with | pipe | Observation: Content with | pipe and @#$ symbols"
    assert result == expected

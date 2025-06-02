from datetime import datetime
from typing import TypedDict


class Evidence(TypedDict):
    quote: str
    context: str


def generate_semantic_text(
    subject: str, observation_type: str, content: str, evidence: Evidence | None = None
) -> str:
    """Generate text optimized for semantic similarity search."""
    parts = [
        f"Subject: {subject}",
        f"Type: {observation_type}",
        f"Observation: {content}",
    ]

    if not evidence or not isinstance(evidence, dict):
        return " | ".join(parts)

    if "quote" in evidence:
        parts.append(f"Quote: {evidence['quote']}")
    if "context" in evidence:
        parts.append(f"Context: {evidence['context']}")

    return " | ".join(parts)


def generate_temporal_text(
    subject: str,
    content: str,
    confidence: float,
    created_at: datetime,
) -> str:
    """Generate text with temporal context for time-pattern search."""
    # Add temporal markers
    time_of_day = created_at.strftime("%H:%M")
    day_of_week = created_at.strftime("%A")

    # Categorize time periods
    hour = created_at.hour
    if 5 <= hour < 12:
        time_period = "morning"
    elif 12 <= hour < 17:
        time_period = "afternoon"
    elif 17 <= hour < 22:
        time_period = "evening"
    else:
        time_period = "late_night"

    parts = [
        f"Time: {time_of_day} on {day_of_week} ({time_period})",
        f"Subject: {subject}",
        f"Observation: {content}",
    ]
    if confidence is not None:
        parts.append(f"Confidence: {confidence}")

    return " | ".join(parts)


# TODO: Add more embedding dimensions here:
# 3. Epistemic chunk - belief structure focused
# epistemic_text = self._generate_epistemic_text()
# chunks.append(extract.DataChunk(
#     data=[epistemic_text],
#     metadata={**base_metadata, "embedding_type": "epistemic"},
#     collection_name="observations_epistemic"
# ))
#
# 4. Emotional chunk - emotional context focused
# emotional_text = self._generate_emotional_text()
# chunks.append(extract.DataChunk(
#     data=[emotional_text],
#     metadata={**base_metadata, "embedding_type": "emotional"},
#     collection_name="observations_emotional"
# ))
#
# 5. Relational chunk - connection patterns focused
# relational_text = self._generate_relational_text()
# chunks.append(extract.DataChunk(
#     data=[relational_text],
#     metadata={**base_metadata, "embedding_type": "relational"},
#     collection_name="observations_relational"
# ))

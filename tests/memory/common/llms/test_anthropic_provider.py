import pytest
from anthropic import transform_schema

from memory.agents.system import (
    EXTRACTION_SCHEMA,
    QUERY_ANALYSIS_SCHEMA,
    SCORE_SCHEMA,
    SUMMARY_SCHEMA,
)


@pytest.mark.parametrize(
    "schema",
    [
        SUMMARY_SCHEMA,
        SCORE_SCHEMA,
        EXTRACTION_SCHEMA,
        QUERY_ANALYSIS_SCHEMA,
    ],
)
def test_json_schemas_are_already_strict(schema):
    # The SDK's strict-mode normaliser leaves a schema the API accepts untouched
    assert transform_schema(schema) == schema

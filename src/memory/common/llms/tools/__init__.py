from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolDefinition:
    """Definition of a tool that can be called by the LLM."""

    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema for the tool's parameters
    strict: bool = False


def is_strict_schema(schema: dict[str, Any]) -> bool:
    """Whether strict tool use (Anthropic and OpenAI alike) accepts this schema."""
    if {"oneOf", "allOf", "$ref"} & schema.keys():
        return False
    if "anyOf" in schema:
        return all(is_strict_schema(b) for b in schema["anyOf"])
    if schema.get("type") == "object":
        props = schema.get("properties", {})
        return (
            schema.get("additionalProperties") is False
            and set(schema.get("required", [])) == set(props)
            and all(is_strict_schema(p) for p in props.values())
        )
    if schema.get("type") == "array":
        return is_strict_schema(schema.get("items", {}))
    return True

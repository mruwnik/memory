"""Ping tool for testing LLM tool integration."""

from memory.common.llms.tools import ToolDefinition, ToolInput


def handle_ping_call(message: ToolInput = None) -> str:
    """
    Handle a ping tool call.

    Args:
        message: Optional message to include in response

    Returns:
        Response string
    """
    if message:
        return f"pong: {message}"
    return "pong"


def get_ping_tool() -> ToolDefinition:
    """
    Get a ping tool definition for testing tool calls.

    Returns a simple tool that takes no required parameters and can be used
    to verify that tool calling is working correctly.
    """
    return ToolDefinition(
        name="ping",
        description="A simple test tool that returns 'pong'. Use this to verify tool calling is working.",
        input_schema={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Optional message to echo back",
                }
            },
            "required": [],
        },
        function=handle_ping_call,
    )

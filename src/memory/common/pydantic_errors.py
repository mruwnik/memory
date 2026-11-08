from typing import Any

from pydantic import ValidationError


def plain(e: Exception) -> str:
    """An error as people read it: a ValidationError's problems as one clause each,
    without pydantic's model names and links (our validators' own sentences, else the
    field's key path and what it should be); any other error as it is."""
    if not isinstance(e, ValidationError):
        return str(e)

    def one(error: Any) -> str:
        if error["type"] == "value_error" or not error["loc"]:
            return error["msg"].removeprefix("Value error, ")
        field = ".".join(str(part) for part in error["loc"])
        return f"{field}: {error['msg']}"

    return "; ".join(one(error) for error in e.errors(include_url=False))

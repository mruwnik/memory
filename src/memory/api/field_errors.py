from fastapi import HTTPException


def field_error(loc: tuple[str, ...], msg: str, status_code: int = 400) -> HTTPException:
    """An error about one request field, in the shape of FastAPI's own 422s, so a form can show it on that field."""
    return HTTPException(
        status_code=status_code,
        detail=[{"loc": list(loc), "msg": msg, "type": "value_error"}],
    )

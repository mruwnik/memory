"""Which optional modules this instance runs."""

from fastapi import APIRouter

from memory.api.auth import require_scope
from memory.common import settings
from memory.common.db.models.users import User
from memory.common.modules import MODULE_DESCRIPTIONS, MODULES
from memory.common.scopes import SCOPE_ADMIN

router = APIRouter(prefix="/modules", tags=["modules"])


@router.get("")
def list_modules(_user: User = require_scope(SCOPE_ADMIN)) -> list[dict]:
    return [
        {
            "name": name,
            "description": MODULE_DESCRIPTIONS[name],
            "enabled": name in settings.ENABLED_MODULES,
        }
        for name in MODULES
    ]

"""Provider key names are unique per user, not per provider.

Keys sharing a name with an older key of the same user get the provider
appended ("personal (openai)") so tools can refer to keys by name.

Revision ID: 20261029_key_names_per_user
Revises: 20261029_user_keys_only
Create Date: 2026-10-29
"""

from typing import Sequence, Union

from alembic import op

revision: str = "20261029_key_names_per_user"
down_revision: Union[str, None] = "20261029_user_keys_only"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE llm_provider_keys k SET label = k.label || ' (' || k.provider || ')' "
        "WHERE EXISTS (SELECT 1 FROM llm_provider_keys o "
        "WHERE o.user_id = k.user_id AND o.label = k.label AND o.id < k.id)"
    )
    op.drop_constraint(
        "llm_provider_keys_user_provider_label", "llm_provider_keys", type_="unique"
    )
    op.create_unique_constraint(
        "llm_provider_keys_user_label", "llm_provider_keys", ["user_id", "label"]
    )


def downgrade() -> None:
    op.drop_constraint("llm_provider_keys_user_label", "llm_provider_keys", type_="unique")
    op.create_unique_constraint(
        "llm_provider_keys_user_provider_label",
        "llm_provider_keys",
        ["user_id", "provider", "label"],
    )

"""GitHub PRs and issues get their own source types (github_pr, github_issue).

Triggers whose filter named github_item also get the two new types, so they keep
matching what they matched before.

Revision ID: 20261207_github_pr_issue_types
Revises: 20261207_github_webhook_status
Create Date: 2026-11-07
"""

from alembic import op

revision = "20261207_github_pr_issue_types"
down_revision = "20261207_github_webhook_status"
branch_labels = None
depends_on = None

SOURCE_TYPES = "config->'filter'->'source_types'"


def set_source_types(names: str, where: str) -> None:
    op.execute(
        f"""
        UPDATE agent_triggers SET config = jsonb_set(
            config,
            '{{filter,source_types}}',
            (SELECT jsonb_agg(DISTINCT t) FROM jsonb_array_elements_text({names}) AS t)
        )
        WHERE jsonb_typeof({SOURCE_TYPES}) = 'array' AND ({where})
        """
    )


def upgrade() -> None:
    op.execute(
        """
        UPDATE source_item s
        SET type = CASE g.kind WHEN 'pr' THEN 'github_pr' ELSE 'github_issue' END
        FROM github_item g
        WHERE g.id = s.id AND s.type = 'github_item' AND g.kind IN ('pr', 'issue')
        """
    )
    set_source_types(
        f"""{SOURCE_TYPES} || '["github_pr", "github_issue"]'::jsonb""",
        f"""{SOURCE_TYPES} @> '["github_item"]'::jsonb""",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE source_item SET type = 'github_item' "
        "WHERE type IN ('github_pr', 'github_issue')"
    )
    set_source_types(
        f"""(SELECT jsonb_agg(CASE WHEN t IN ('github_pr', 'github_issue') THEN 'github_item' ELSE t END)
             FROM jsonb_array_elements_text({SOURCE_TYPES}) AS t)""",
        f"""{SOURCE_TYPES} ?| array['github_pr', 'github_issue']""",
    )

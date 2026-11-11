"""Unbind GitHub projects their repo's sync may not file into.

A repo's sync files issues and PRs only into projects whose teams the
tracking account's owner belongs to (or projects with no teams). Bindings made
before that rule, or by admins' repos that skipped it, could route a tracker's
items into a project of teams the tracker never joined. For every repo or
milestone project with teams, none of which the tracker's owner is in, this:

- clears ``project_id`` on that repo's items filed into the project (they stay
  readable by the tracker and admins) and bumps their ``updated_at`` so the
  access-control sweep rewrites their search payloads;
- clears ``milestone_id`` on items linked to it;
- detaches the project from the repo (``repo_id``, ``github_id``, ``number``),
  leaving it a standalone project with its teams.

Revision ID: 20261227_unbind_github_projects
Revises: 20261226_slack_workspace_owner
Create Date: 2026-11-11
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20261227_unbind_github_projects"
down_revision: Union[str, None] = "20261226_slack_workspace_owner"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UNFILEABLE = """
    SELECT p.id, p.repo_id FROM projects p
    WHERE p.repo_id IS NOT NULL
      AND EXISTS (SELECT 1 FROM project_teams pt WHERE pt.project_id = p.id)
      AND NOT EXISTS (
          SELECT 1
          FROM github_repos gr
          JOIN github_accounts ga ON ga.id = gr.account_id
          JOIN people pe ON pe.user_id = ga.user_id
          JOIN team_members tm ON tm.person_id = pe.id
          JOIN project_teams pt ON pt.team_id = tm.team_id
          WHERE gr.id = p.repo_id AND pt.project_id = p.id
      )
"""


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE source_item s SET project_id = NULL, updated_at = now()
        FROM github_item gi, ({UNFILEABLE}) u
        WHERE s.id = gi.id AND gi.repo_id = u.repo_id AND s.project_id = u.id
        """
    )
    op.execute(
        f"""
        UPDATE github_item gi SET milestone_id = NULL
        FROM ({UNFILEABLE}) u
        WHERE gi.milestone_id = u.id
        """
    )
    op.execute(
        f"""
        UPDATE projects p SET repo_id = NULL, github_id = NULL, number = NULL
        FROM ({UNFILEABLE}) u
        WHERE p.id = u.id
        """
    )


def downgrade() -> None:
    # Which repo each project was bound to is not kept; re-bind by hand if needed.
    pass

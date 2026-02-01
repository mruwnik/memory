"""
MCP server with composed subservers.

Subservers are mounted with prefixes. Tool names after mounting:
- books: books_list_books, books_read_book
- core: core_search, core_observe, core_search_observations, core_create_note, core_note_files, core_fetch_file, core_get_item, core_list_items, core_count_items
- discord: discord_send_message, discord_channel_history, discord_list_channels, discord_list_roles, discord_list_role_members, discord_list_categories, discord_add_user_to_role, discord_role_remove, discord_create, discord_perms, discord_set_perms, discord_del_perms, discord_create_channel, discord_create_category, discord_delete_channel, discord_delete_category, discord_edit_channel
- email: email_send
- forecast: forecast_get_forecasts, forecast_clear_cache, forecast_history, forecast_get_market_depth, forecast_compare_forecasts, forecast_resolved, forecast_watch_market, forecast_get_watchlist, forecast_unwatch_market
- github: github_list_entities, github_fetch, github_upsert_issue, github_add_team_member, github_remove_team_member, github_comment_on_issue
- meta: meta_get_metadata_schemas, meta_get_current_time, meta_get_user, meta_notify_user
- organizer: organizer_upcoming, organizer_list_tasks, organizer_get_task, organizer_create_task, organizer_update_task
- people: people_add, people_update, people_get_person, people_list_people, people_delete, people_add_tidbit, people_update_tidbit, people_delete_tidbit, people_list_tidbits
- polling: polling_upsert_poll, polling_list_polls, polling_delete_poll, polling_get_poll
- projects: projects_list_all, projects_fetch, projects_upsert, projects_delete
- slack: slack_send, slack_add_reaction, slack_list_channels, slack_get_channel_history
- teams: teams_upsert, teams_team_get, teams_team_list, teams_team_update, teams_team_add_member, teams_team_remove_member, teams_team_list_members, teams_teams_by_tag, teams_person_teams, teams_project_assign_team, teams_project_unassign_team, teams_project_list_teams, teams_project_list_access, teams_projects_for_person, teams_check_project_access
"""

# Import base to trigger subserver mounting
from memory.api.MCP.base import mcp, get_current_user

__all__ = ["mcp", "get_current_user"]

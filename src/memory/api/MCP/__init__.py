"""
MCP server with composed subservers.

Subservers are mounted with prefixes. Tool names after mounting:
- core: core_search_knowledge_base, core_observe, core_search_observations, core_create_note, core_note_files, core_fetch_file, core_get_source_item, core_list_items, core_count_items
- github: github_list, github_fetch, github_upsert_issue, github_add_team_member, github_remove_team_member
- people: people_add, people_update, people_get, people_list_people, people_delete
- organizer: organizer_get_upcoming_events, organizer_list_tasks, organizer_create_task, organizer_update_task, organizer_complete_task_by_id
- schedule: schedule_schedule_message, schedule_list_scheduled_llm_calls, schedule_cancel_scheduled_llm_call
- books: books_list_books, books_read_book
- meta: meta_get_metadata_schemas, meta_get_all_tags, meta_get_all_subjects, meta_get_all_observation_types, meta_get_current_time, meta_get_authenticated_user, meta_get_forecasts
"""

# Import base to trigger subserver mounting
from memory.api.MCP.base import mcp, get_current_user

__all__ = ["mcp", "get_current_user"]

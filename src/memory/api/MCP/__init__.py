"""
MCP server with composed subservers.

Subservers are mounted with prefixes:
- core: search_knowledge_base, observe, search_observations, create_note, note_files, fetch_file
- github: list_github_issues, search_github_issues, github_issue_details, github_work_summary, github_repo_overview
- people: add_person, update_person_info, get_person, list_people, delete_person
- schedule: schedule_message, list_scheduled_llm_calls, cancel_scheduled_llm_call
- books: all_books, read_book
- meta: get_metadata_schemas, get_all_tags, get_all_subjects, get_all_observation_types, get_current_time, get_authenticated_user, get_forecasts
"""

# Import base to trigger subserver mounting
from memory.api.MCP.base import mcp, get_current_user

__all__ = ["mcp", "get_current_user"]

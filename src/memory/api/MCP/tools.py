"""
MCP tools for the epistemic sparring partner system.
"""

import logging
import pathlib
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP
from sqlalchemy import Text, func
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import ARRAY

from memory.api.search.search import SearchFilters, search
from memory.common import extract, settings
from memory.common.collections import ALL_COLLECTIONS, OBSERVATION_COLLECTIONS
from memory.common.db.connection import make_session
from memory.common.db.models import AgentObservation, SourceItem
from memory.common.formatters import observation
from memory.common.celery_app import app as celery_app, SYNC_OBSERVATION, SYNC_NOTE

logger = logging.getLogger(__name__)

# Create MCP server instance
mcp = FastMCP("memory", stateless_http=True)


def filter_observation_source_ids(
    tags: list[str] | None = None, observation_types: list[str] | None = None
):
    if not tags and not observation_types:
        return None

    with make_session() as session:
        items_query = session.query(AgentObservation.id)

        if tags:
            # Use PostgreSQL array overlap operator with proper array casting
            items_query = items_query.filter(
                AgentObservation.tags.op("&&")(sql_cast(tags, ARRAY(Text))),
            )
        if observation_types:
            items_query = items_query.filter(
                AgentObservation.observation_type.in_(observation_types)
            )
        source_ids = [item.id for item in items_query.all()]

    return source_ids


def filter_source_ids(
    modalities: set[str],
    tags: list[str] | None = None,
):
    if not tags:
        return None

    with make_session() as session:
        items_query = session.query(SourceItem.id)

        if tags:
            # Use PostgreSQL array overlap operator with proper array casting
            items_query = items_query.filter(
                SourceItem.tags.op("&&")(sql_cast(tags, ARRAY(Text))),
            )
        if modalities:
            items_query = items_query.filter(SourceItem.modality.in_(modalities))
        source_ids = [item.id for item in items_query.all()]

    return source_ids


@mcp.tool()
async def get_all_tags() -> list[str]:
    """
    Get all unique tags used across the entire knowledge base.

    Purpose:
        This tool retrieves all tags that have been used in the system, both from
        AI observations (created with 'observe') and other content. Use it to
        understand the tag taxonomy, ensure consistency, or discover related topics.

    Returns:
        Sorted list of all unique tags in the system. Tags follow patterns like:
        - Topics: "machine-learning", "functional-programming"
        - Projects: "project:website-redesign"
        - Contexts: "context:work", "context:late-night"
        - Domains: "domain:finance"
    """
    with make_session() as session:
        tags_query = session.query(func.unnest(SourceItem.tags)).distinct()
        return sorted({row[0] for row in tags_query if row[0] is not None})


@mcp.tool()
async def get_all_subjects() -> list[str]:
    """
    Get all unique subjects from observations about the user.

    Purpose:
        This tool retrieves all subject identifiers that have been used in
        observations (created with 'observe'). Subjects are the consistent
        identifiers for what observations are about. Use this to understand
        what aspects of the user have been tracked and ensure consistency.

    Returns:
        Sorted list of all unique subjects. Common patterns include:
        - "programming_style", "programming_philosophy"
        - "work_habits", "work_schedule"
        - "ai_beliefs", "ai_safety_beliefs"
        - "learning_preferences"
        - "communication_style"
    """
    with make_session() as session:
        return sorted(
            r.subject for r in session.query(AgentObservation.subject).distinct()
        )


@mcp.tool()
async def get_all_observation_types() -> list[str]:
    """
    Get all unique observation types that have been used.

    Purpose:
        This tool retrieves the distinct observation types that have been recorded
        in the system. While the standard types are predefined (belief, preference,
        behavior, contradiction, general), this shows what's actually been used.
        Helpful for understanding the distribution of observation types.

    Standard types:
        - "belief": Opinions or beliefs the user holds
        - "preference": Things they prefer or favor
        - "behavior": Patterns in how they act or work
        - "contradiction": Noted inconsistencies
        - "general": Observations that don't fit other categories

    Returns:
        List of observation types that have actually been used in the system.
    """
    with make_session() as session:
        return sorted(
            {
                r.observation_type
                for r in session.query(AgentObservation.observation_type).distinct()
                if r.observation_type is not None
            }
        )


@mcp.tool()
async def search_knowledge_base(
    query: str,
    previews: bool = False,
    modalities: set[str] = set(),
    tags: list[str] = [],
    limit: int = 10,
) -> list[dict]:
    """
    Search through the user's stored knowledge and content.

    Purpose:
        This tool searches the user's personal knowledge base - a collection of
        their saved content including emails, documents, blog posts, books, and
        more. Use this alongside 'search_observations' to build a complete picture:
        - search_knowledge_base: Finds user's actual content and information
        - search_observations: Finds AI-generated insights about the user
        Together they enable deeply personalized, context-aware assistance.

    When to use:
        - User asks about something they've read/written/received
        - You need to find specific content the user has saved
        - User references a document, email, or article
        - To provide quotes or information from user's sources
        - To understand context from user's past communications
        - When user says "that article about..." or similar references

    How it works:
        Uses hybrid search combining semantic understanding with keyword matching.
        This means it finds content based on meaning AND specific terms, giving
        you the best of both approaches. Results are ranked by relevance.

    Args:
        query: Natural language search query. Be descriptive about what you're
            looking for. The search understands meaning but also values exact terms.
            Examples:
            - "email about project deadline from last week"
            - "functional programming articles comparing Haskell and Scala"
            - "that blog post about AI safety and alignment"
            - "recipe for chocolate cake Sarah sent me"
            Pro tip: Include both concepts and specific keywords for best results.

        previews: Whether to include content snippets in results.
            - True: Returns preview text and image previews (useful for quick scanning)
            - False: Returns just metadata (faster, less data)
            Default is False.

        modalities: Types of content to search. Leave empty to search all.
            Available types:
            - 'email': Email messages
            - 'blog': Blog posts and articles
            - 'book': Book sections and ebooks
            - 'forum': Forum posts (e.g., LessWrong, Reddit)
            - 'observation': AI observations (use search_observations instead)
            - 'photo': Images with extracted text
            - 'comic': Comics and graphic content
            - 'webpage': General web pages
            Examples:
            - ["email"] - only emails
            - ["blog", "forum"] - articles and forum posts
            - [] - search everything

        limit: Maximum results to return (1-100). Default 10.
            Increase for comprehensive searches, decrease for quick lookups.

    Returns:
        List of search results ranked by relevance, each containing:
        - id: Unique identifier for the source item
        - score: Relevance score (0-1, higher is better)
        - chunks: Matching content segments with metadata
        - content: Full details including:
            - For emails: sender, recipient, subject, date
            - For blogs: author, title, url, publish date
            - For books: title, author, chapter info
            - Type-specific fields for each modality
        - filename: Path to file if content is stored on disk

    Examples:
        # Find specific email
        results = await search_knowledge_base(
            query="Sarah deadline project proposal next Friday",
            modalities=["email"],
            previews=True,
            limit=5
        )

        # Search for technical articles
        results = await search_knowledge_base(
            query="functional programming monads category theory",
            modalities=["blog", "book"],
            limit=20
        )

        # Find everything about a topic
        results = await search_knowledge_base(
            query="machine learning deployment kubernetes docker",
            previews=True
        )

        # Quick lookup of a remembered document
        results = await search_knowledge_base(
            query="tax forms 2023 accountant recommendations",
            modalities=["email"],
            limit=3
        )

    Best practices:
        - Include context in queries ("email from Sarah" vs just "Sarah")
        - Use modalities to filter when you know the content type
        - Enable previews when you need to verify content before using
        - Combine with search_observations for complete context
        - Higher scores (>0.7) indicate strong matches
        - If no results, try broader queries or different phrasing
    """
    logger.info(f"MCP search for: {query}")

    if not modalities:
        modalities = set(ALL_COLLECTIONS.keys())
    modalities = set(modalities) & ALL_COLLECTIONS.keys() - OBSERVATION_COLLECTIONS

    upload_data = extract.extract_text(query)
    results = await search(
        upload_data,
        previews=previews,
        modalities=modalities,
        limit=limit,
        min_text_score=0.4,
        min_multimodal_score=0.25,
        filters=SearchFilters(
            tags=tags,
            source_ids=filter_source_ids(tags=tags, modalities=modalities),
        ),
    )

    # Convert SearchResult objects to dictionaries for MCP
    return [result.model_dump() for result in results]


@mcp.tool()
async def observe(
    content: str,
    subject: str,
    observation_type: str = "general",
    confidence: float = 0.8,
    evidence: dict | None = None,
    tags: list[str] | None = None,
    session_id: str | None = None,
    agent_model: str = "unknown",
) -> dict:
    """
    Record an observation about the user to build long-term understanding.

    Purpose:
        This tool is part of a memory system designed to help AI agents build a
        deep, persistent understanding of users over time. Use it to record any
        notable information about the user's preferences, beliefs, behaviors, or
        characteristics. These observations accumulate to create a comprehensive
        model of the user that improves future interactions.

    Quick Reference:
        # Most common patterns:
        observe(content="User prefers X over Y because...", subject="preferences", observation_type="preference")
        observe(content="User always/often does X when Y", subject="work_habits", observation_type="behavior")
        observe(content="User believes/thinks X about Y", subject="beliefs_on_topic", observation_type="belief")
        observe(content="User said X but previously said Y", subject="topic", observation_type="contradiction")

    When to use:
        - User expresses a preference or opinion
        - You notice a behavioral pattern
        - User reveals information about their work/life/interests
        - You spot a contradiction with previous statements
        - Any insight that would help understand the user better in future

    Important: Be an active observer. Don't wait to be asked - proactively record
    observations throughout conversations to build understanding.

    Args:
        content: The observation itself. Be specific and detailed. Write complete
            thoughts that will make sense when read months later without context.
            Bad: "Likes FP"
            Good: "User strongly prefers functional programming paradigms, especially
                   pure functions and immutability, considering them more maintainable"

        subject: A consistent identifier for what this observation is about. Use
            snake_case and be consistent across observations to enable tracking.
            Examples:
            - "programming_style" (not "coding" or "development")
            - "work_habits" (not "productivity" or "work_patterns")
            - "ai_safety_beliefs" (not "AI" or "artificial_intelligence")

        observation_type: Categorize the observation:
            - "belief": An opinion or belief the user holds
            - "preference": Something they prefer or favor
            - "behavior": A pattern in how they act or work
            - "contradiction": An inconsistency with previous observations
            - "general": Doesn't fit other categories

        confidence: How certain you are (0.0-1.0):
            - 1.0: User explicitly stated this
            - 0.9: Strongly implied or demonstrated repeatedly
            - 0.8: Inferred with high confidence (default)
            - 0.7: Probable but with some uncertainty
            - 0.6 or below: Speculative, use sparingly

        evidence: Supporting context as a dict. Include relevant details:
            - "quote": Exact words from the user
            - "context": What prompted this observation
            - "timestamp": When this was observed
            - "related_to": Connection to other topics
            Example: {
                "quote": "I always refactor to pure functions",
                "context": "Discussing code review practices"
            }

        tags: Categorization labels. Use lowercase with hyphens. Common patterns:
            - Topics: "machine-learning", "web-development", "philosophy"
            - Projects: "project:website-redesign", "project:thesis"
            - Contexts: "context:work", "context:personal", "context:late-night"
            - Domains: "domain:finance", "domain:healthcare"

        session_id: UUID string to group observations from the same conversation.
            Generate one UUID per conversation and reuse it for all observations
            in that conversation. Format: "550e8400-e29b-41d4-a716-446655440000"

        agent_model: Which AI model made this observation (e.g., "claude-3-opus",
            "gpt-4", "claude-3.5-sonnet"). Helps track observation quality.

    Returns:
        Dict with created observation details:
        - id: Unique identifier for reference
        - created_at: Timestamp of creation
        - subject: The subject as stored
        - observation_type: The type as stored
        - confidence: The confidence score
        - tags: List of applied tags

    Examples:
        # After user mentions their coding philosophy
        await observe(
            content="User believes strongly in functional programming principles, "
                    "particularly avoiding mutable state which they call 'the root "
                    "of all evil'. They prioritize code purity over performance.",
            subject="programming_philosophy",
            observation_type="belief",
            confidence=0.95,
            evidence={
                "quote": "State is the root of all evil in programming",
                "context": "Discussing why they chose Haskell for their project"
            },
            tags=["programming", "functional-programming", "philosophy"],
            session_id="550e8400-e29b-41d4-a716-446655440000",
            agent_model="claude-3-opus"
        )

        # Noticing a work pattern
        await observe(
            content="User frequently works on complex problems late at night, "
                    "typically between 11pm and 3am, claiming better focus",
            subject="work_schedule",
            observation_type="behavior",
            confidence=0.85,
            evidence={
                "context": "Mentioned across multiple conversations over 2 weeks"
            },
            tags=["behavior", "work-habits", "productivity", "context:late-night"],
            agent_model="claude-3-opus"
        )

        # Recording a contradiction
        await observe(
            content="User now advocates for microservices architecture, but "
                    "previously argued strongly for monoliths in similar contexts",
            subject="architecture_preferences",
            observation_type="contradiction",
            confidence=0.9,
            evidence={
                "quote": "Microservices are definitely the way to go",
                "context": "Designing a new system similar to one from 3 months ago"
            },
            tags=["architecture", "contradiction", "software-design"],
            agent_model="gpt-4"
        )
    """
    task = celery_app.send_task(
        SYNC_OBSERVATION,
        queue="notes",
        kwargs={
            "subject": subject,
            "content": content,
            "observation_type": observation_type,
            "confidence": confidence,
            "evidence": evidence,
            "tags": tags,
            "session_id": session_id,
            "agent_model": agent_model,
        },
    )
    return {
        "task_id": task.id,
        "status": "queued",
    }


@mcp.tool()
async def search_observations(
    query: str,
    subject: str = "",
    tags: list[str] | None = None,
    observation_types: list[str] | None = None,
    min_confidence: float = 0.5,
    limit: int = 10,
) -> list[dict]:
    """
    Search through observations to understand the user better.

    Purpose:
        This tool searches through all observations recorded about the user using
        the 'observe' tool. Use it to recall past insights, check for patterns,
        find contradictions, or understand the user's preferences before responding.
        The more you use this tool, the more personalized and insightful your
        responses can be.

    When to use:
        - Before answering questions where user preferences might matter
        - When the user references something from the past
        - To check if current behavior aligns with past patterns
        - To find related observations on a topic
        - To build context about the user's expertise or interests
        - Whenever personalization would improve your response

    How it works:
        Uses hybrid search combining semantic similarity with keyword matching.
        Searches across multiple embedding spaces (semantic meaning and temporal
        context) to find relevant observations from different angles. This approach
        ensures you find both conceptually related and specifically mentioned items.

    Args:
        query: Natural language description of what you're looking for. The search
            matches both meaning and specific terms in observation content.
            Examples:
            - "programming preferences and coding style"
            - "opinions about artificial intelligence and AI safety"
            - "work habits productivity patterns when does user work best"
            - "previous projects the user has worked on"
            Pro tip: Use natural language but include key terms you expect to find.

        subject: Filter by exact subject identifier. Must match subjects used when
            creating observations (e.g., "programming_style", "work_habits").
            Leave empty to search all subjects. Use this when you know the exact
            subject category you want.

        tags: Filter results to only observations with these tags. Observations must
            have at least one matching tag. Use the same format as when creating:
            - ["programming", "functional-programming"]
            - ["context:work", "project:thesis"]
            - ["domain:finance", "machine-learning"]

        observation_types: Filter by type of observation:
            - "belief": Opinions or beliefs the user holds
            - "preference": Things they prefer or favor
            - "behavior": Patterns in how they act or work
            - "contradiction": Noted inconsistencies
            - "general": Other observations
            Leave as None to search all types.

        min_confidence: Only return observations with confidence >= this value.
            - Use 0.8+ for high-confidence facts
            - Use 0.5-0.7 to include inferred observations
            - Default 0.5 includes most observations
            Range: 0.0 to 1.0

        limit: Maximum results to return (1-100). Default 10. Increase when you
            need comprehensive understanding of a topic.

    Returns:
        List of observations sorted by relevance, each containing:
        - subject: What the observation is about
        - content: The full observation text
        - observation_type: Type of observation
        - evidence: Supporting context/quotes if provided
        - confidence: How certain the observation is (0-1)
        - agent_model: Which AI model made the observation
        - tags: All tags on this observation
        - created_at: When it was observed (if available)

    Examples:
        # Before discussing code architecture
        results = await search_observations(
            query="software architecture preferences microservices monoliths",
            tags=["architecture"],
            min_confidence=0.7
        )

        # Understanding work style for scheduling
        results = await search_observations(
            query="when does user work best productivity schedule",
            observation_types=["behavior", "preference"],
            subject="work_schedule"
        )

        # Check for AI safety views before discussing AI
        results = await search_observations(
            query="artificial intelligence safety alignment concerns",
            observation_types=["belief"],
            min_confidence=0.8,
            limit=20
        )

        # Find contradictions on a topic
        results = await search_observations(
            query="testing methodology unit tests integration",
            observation_types=["contradiction"],
            tags=["testing", "software-development"]
        )

    Best practices:
        - Search before making assumptions about user preferences
        - Use broad queries first, then filter with tags/types if too many results
        - Check for contradictions when user says something unexpected
        - Higher confidence observations are more reliable
        - Recent observations may override older ones on same topic
    """
    semantic_text = observation.generate_semantic_text(
        subject=subject or "",
        observation_type="".join(observation_types or []),
        content=query,
        evidence=None,
    )
    temporal = observation.generate_temporal_text(
        subject=subject or "",
        content=query,
        confidence=0,
        created_at=datetime.now(timezone.utc),
    )
    results = await search(
        [
            extract.DataChunk(data=[query]),
            extract.DataChunk(data=[semantic_text]),
            extract.DataChunk(data=[temporal]),
        ],
        previews=True,
        modalities={"semantic", "temporal"},
        limit=limit,
        filters=SearchFilters(
            subject=subject,
            confidence=min_confidence,
            tags=tags,
            observation_types=observation_types,
            source_ids=filter_observation_source_ids(tags=tags),
        ),
        timeout=2,
    )

    return [
        {
            "content": r.content,
            "tags": r.tags,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "metadata": r.metadata,
        }
        for r in results
    ]


@mcp.tool()
async def create_note(
    subject: str,
    content: str,
    filename: str | None = None,
    note_type: str | None = None,
    confidence: float = 0.5,
    tags: list[str] = [],
) -> dict:
    """
    Create a note when the user asks for something to be noted down or when you think
    something is important to note down.

    Purpose:
        Use this tool when the user explicitly asks to note, save, or record
        something for later reference. Notes don't have to be really short - long
        markdown docs are fine, as long as that was what was asked for.
        You can also use this tool to note down things that are important to you.

    When to use:
        - User says "note down that..." or "please save this"
        - User asks to record information for future reference
        - User wants to remember something specific

    Args:
        subject: What the note is about (e.g., "meeting_notes", "idea")
        content: The actual content to note down, as markdown
        filename: Optional path relative to notes folder (e.g., "project/ideas.md")
        note_type: Optional categorization of the note
        confidence: How confident you are in the note accuracy (0.0-1.0)
        tags: Optional tags for organization

    Example:
        # User: "Please note down that we decided to use React for the frontend"
        await create_note(
            subject="project_decisions",
            content="Decided to use React for the frontend",
            tags=["project", "frontend"]
        )
    """
    if filename:
        path = pathlib.Path(filename)
        if path.is_absolute():
            path = path.relative_to(settings.NOTES_STORAGE_DIR)
        else:
            path = pathlib.Path(settings.NOTES_STORAGE_DIR) / path
        filename = path.as_posix()

    try:
        task = celery_app.send_task(
            SYNC_NOTE,
            queue="notes",
            kwargs={
                "subject": subject,
                "content": content,
                "filename": filename,
                "note_type": note_type,
                "confidence": confidence,
                "tags": tags,
            },
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        logger.error(f"Error creating note: {e}")
        raise

    return {
        "task_id": task.id,
        "status": "queued",
    }


@mcp.tool()
async def note_files(path: str = "/"):
    """
    List all available note files in the user's note storage system.

    Purpose:
        This tool provides a way to discover and browse the user's organized note
        collection. Notes are stored as Markdown files and can be created either
        through the 'create_note' tool or by the user directly. Use this tool to
        understand what notes exist before reading or referencing them, or to help
        the user navigate their note collection.

    Args:
        path: Directory path to search within the notes collection. Use "/" for the
            root notes directory, or specify subdirectories like "/projects" or
            "/meetings". The path should start with "/" and use forward slashes.
            Examples:
            - "/" - List all notes in the entire collection
            - "/projects" - Only notes in the projects folder
            - "/meetings/2024" - Notes in a specific year's meetings folder

    Examples:
        # List all notes
        all_notes = await note_files("/")
        # Returns: ["/notes/project_ideas.md", "/notes/meetings/daily_standup.md", ...]

        # List notes in a specific folder
        project_notes = await note_files("/projects")
        # Returns: ["/notes/projects/website_redesign.md", "/notes/projects/mobile_app.md"]

        # Check for meeting notes
        meeting_notes = await note_files("/meetings")
        # Returns: ["/notes/meetings/2024-01-15.md", "/notes/meetings/weekly_review.md"]
    """
    root = settings.NOTES_STORAGE_DIR / path.lstrip("/")
    return [
        f"/notes/{f.relative_to(settings.NOTES_STORAGE_DIR)}"
        for f in root.rglob("*.md")
        if f.is_file()
    ]


@mcp.tool()
def fetch_file(filename: str):
    """
    Retrieve the raw content of a file from the user's storage system.

    Purpose:
        This tool allows you to read the actual content of files stored in the
        user's file system, including notes, documents, images, and other files.
        Use this when you need to access the specific content of a file that has
        been referenced or when the user asks you to read/examine a particular file.

    Args:
        filename: Path to the file to fetch, relative to the file storage directory.
            Should start with "/" and use forward slashes. The path structure depends
            on how files are organized in the storage system.
            Examples:
            - "/notes/project_ideas.md" - A note file
            - "/documents/report.pdf" - A PDF document
            - "/images/diagram.png" - An image file
            - "/emails/important_thread.txt" - Saved email content

    Returns:
        Raw bytes content of the file. For text files (like Markdown notes), you'll
        typically want to decode this as UTF-8 to get readable text:
        ```python
        content_bytes = await fetch_file("/notes/my_note.md")
        content_text = content_bytes.decode('utf-8')
        ```

    Raises:
        FileNotFoundError: If the specified file doesn't exist at the given path.

    Security note:
        This tool only accesses files within the configured storage directory,
        ensuring it cannot read arbitrary system files.
    """
    path = settings.FILE_STORAGE_DIR / filename.lstrip("/")
    if not path.exists():
        raise FileNotFoundError(f"File not found: {filename}")

    return path.read_bytes()

from memory.common.db.models.base import Base
from memory.common.db.models.source_item import (
    Chunk,
    SourceItem,
    ConfidenceScore,
    SummaryRequest,
    clean_filename,
    SourceItemPayload,
)
from memory.common.db.models.source_items import (
    MailMessage,
    EmailAttachment,
    AgentObservation,
    ChatMessage,
    DiscordMessage,
    SlackMessage,
    BlogPost,
    Comic,
    BookSection,
    ForumPost,
    GithubItem,
    GithubPRData,
    GitCommit,
    Photo,
    MiscDoc,
    Note,
    GoogleDoc,
    Task,
    CalendarEvent,
    Report,
    MailMessagePayload,
    EmailAttachmentPayload,
    AgentObservationPayload,
    BlogPostPayload,
    ComicPayload,
    BookSectionPayload,
    NotePayload,
    ForumPostPayload,
    GoogleDocPayload,
    TaskPayload,
    CalendarEventPayload,
    MeetingPayload,
    Meeting,
    SessionSegment,
    SessionSegmentPayload,
    ReportPayload,
)
from memory.common.db.models.discord import (
    DiscordBot,
    DiscordServer,
    DiscordChannel,
    DiscordUser,
    discord_bot_users,
)
from memory.common.db.models.slack import (
    SlackApp,
    SlackWorkspace,
    SlackChannel,
    SlackUserCredentials,
    slack_app_users,
)
from memory.common.db.models.mcp import ExternalMCPServer, ServerStatus
from memory.common.db.models.instance_settings import InstanceSetting
from memory.common.db.models.provider_keys import (
    KeyStatus,
    LLMKeyChoice,
    LLMProviderKey,
    LLMSpend,
)
from memory.common.db.models.observations import (
    ObservationContradiction,
    ReactionPattern,
    ObservationPattern,
    BeliefCluster,
    ConversationMetrics,
)
from memory.common.db.models.people import (
    PersonPayload,
    PersonTidbit,
    PersonTidbitPayload,
)
from memory.common.db.models.sources import (
    Book,
    ArticleFeed,
    EmailAccount,
    GithubAccount,
    GithubRepo,
    Project,
    GithubProject,
    GoogleOAuthConfig,
    GoogleAccount,
    GoogleFolder,
    CalendarAccount,
    TranscriptAccount,
    Person,
    Team,
    team_members,
    project_teams,
)
from memory.common.db.models.agents import (
    AgentDefinition,
    AgentRun,
    AgentRunStep,
    AgentTrigger,
    AgentTriggerMatch,
    RunStatus,
    TERMINAL_STATUSES,
)
from memory.common.db.models.users import (
    User,
    HumanUser,
    BotUser,
    UserSession,
    OAuthClientInformation,
    OAuthState,
    OAuthClientState,
    OAuthRefreshToken,
    APIKey,
    APIKeyType,
)
from memory.common.db.models.secrets import (
    Secret,
    extract,
    find_secret,
    create_secret,
    rekey_all_secrets,
    encrypt_value,
    decrypt_value,
)
from memory.common.db.models.scheduled_tasks import (
    ScheduledTask,
    TaskExecution,
    compute_next_cron,
)
from memory.common.db.models.metrics import (
    MetricEvent,
)
from memory.common.db.models.telemetry import (
    TelemetryEvent,
)
from memory.common.db.models.sessions import (
    CodingProject,
    CodingProjectPayload,
    Session,
    SessionPayload,
)
from memory.common.db.models.jobs import (
    PendingJob,
    PendingJobPayload,
    JobStatus,
    JobType,
)
from memory.common.db.models.polls import (
    AvailabilityPoll,
    AvailabilityPollPayload,
    PollResponse,
    PollResponsePayload,
    PollAvailability,
    PollAvailabilityPayload,
    PollStatus,
    AvailabilityLevel,
    SlotAggregation,
)
from memory.common.db.models.claude_config import (
    ClaudeConfigSnapshot,
    ClaudeConfigSnapshotPayload,
    ClaudeEnvironment,
    ClaudeEnvironmentPayload,
    ClaudeGitHubToken,
)
from memory.common.db.models.watchlist import (
    WatchedMarket,
)
from memory.common.db.models.journal import (
    JournalEntry,
    build_journal_access_filter,
)
from memory.common.db.models.deadlines import (
    Deadline,
    DeadlinePayload,
    deadline_attachments,
)
# Importing this registers the before_flush/after_commit listeners that
# dispatch access-control reconciliation when a data source's config changes.
from memory.common.db.models.access_control_events import (
    ACCESS_CONTROLLED_SOURCE_MODELS,
)

Payload = (
    SourceItemPayload
    | AgentObservationPayload
    | NotePayload
    | BlogPostPayload
    | ComicPayload
    | BookSectionPayload
    | ForumPostPayload
    | EmailAttachmentPayload
    | MailMessagePayload
    | PersonPayload
    | PersonTidbitPayload
    | GoogleDocPayload
    | TaskPayload
    | CalendarEventPayload
    | ReportPayload
    | DeadlinePayload
)

__all__ = [
    "ACCESS_CONTROLLED_SOURCE_MODELS",
    "AgentDefinition",
    "AgentRun",
    "AgentRunStep",
    "AgentTrigger",
    "AgentTriggerMatch",
    "RunStatus",
    "TERMINAL_STATUSES",
    "Base",
    "Chunk",
    "SummaryRequest",
    "clean_filename",
    "SourceItem",
    "ConfidenceScore",
    "MailMessage",
    "EmailAttachment",
    "AgentObservation",
    "ChatMessage",
    "DiscordMessage",
    "BlogPost",
    "Comic",
    "BookSection",
    "ForumPost",
    "GithubItem",
    "GithubPRData",
    "GitCommit",
    "Photo",
    "MiscDoc",
    "Note",
    "GoogleDoc",
    "GoogleDocPayload",
    "Task",
    "TaskPayload",
    "CalendarEvent",
    "CalendarEventPayload",
    "Meeting",
    "MeetingPayload",
    "SessionSegment",
    "SessionSegmentPayload",
    "Report",
    "ReportPayload",
    # Observations
    "ObservationContradiction",
    "ReactionPattern",
    "ObservationPattern",
    "BeliefCluster",
    "ConversationMetrics",
    # People
    "Person",
    "PersonPayload",
    "PersonTidbit",
    "PersonTidbitPayload",
    # Teams
    "Team",
    "team_members",
    "project_teams",
    # Calendar
    "CalendarAccount",
    "CalendarEvent",
    "CalendarEventPayload",
    # Sources
    "Book",
    "ArticleFeed",
    "EmailAccount",
    "GithubAccount",
    "GithubRepo",
    "Project",
    "GithubProject",
    "GoogleOAuthConfig",
    "GoogleAccount",
    "GoogleFolder",
    "CalendarAccount",
    "CalendarEvent",
    "CalendarEventPayload",
    "TranscriptAccount",
    "DiscordBot",
    "DiscordServer",
    "DiscordChannel",
    "DiscordUser",
    "discord_bot_users",
    "SlackApp",
    "SlackWorkspace",
    "SlackChannel",
    "SlackUserCredentials",
    "SlackMessage",
    "slack_app_users",
    "ExternalMCPServer",
    "KeyStatus",
    "LLMKeyChoice",
    "InstanceSetting",
    "LLMProviderKey",
    "LLMSpend",
    "ServerStatus",
    # Users
    "User",
    "HumanUser",
    "BotUser",
    "UserSession",
    "OAuthClientInformation",
    "OAuthState",
    "OAuthClientState",
    "OAuthRefreshToken",
    "APIKey",
    "APIKeyType",
    # Scheduled Tasks
    "ScheduledTask",
    "TaskExecution",
    "compute_next_cron",
    # Metrics
    "MetricEvent",
    # Telemetry
    "TelemetryEvent",
    # Sessions (coding projects)
    "CodingProject",
    "CodingProjectPayload",
    "Session",
    "SessionPayload",
    # Jobs
    "PendingJob",
    "PendingJobPayload",
    "JobStatus",
    "JobType",
    # Polls
    "AvailabilityPoll",
    "AvailabilityPollPayload",
    "PollResponse",
    "PollResponsePayload",
    "PollAvailability",
    "PollAvailabilityPayload",
    "PollStatus",
    "AvailabilityLevel",
    "SlotAggregation",
    # Claude Config Snapshots
    "ClaudeConfigSnapshot",
    "ClaudeConfigSnapshotPayload",
    # Claude Environments
    "ClaudeEnvironment",
    "ClaudeEnvironmentPayload",
    "ClaudeGitHubToken",
    # SSH key generation
    # Secrets
    "Secret",
    "extract",
    "find_secret",
    "create_secret",
    "rekey_all_secrets",
    "encrypt_value",
    "decrypt_value",
    # Payloads
    "Payload",
    # Watchlist
    "WatchedMarket",
    # Journal
    "JournalEntry",
    "build_journal_access_filter",
    # Deadlines
    "Deadline",
    "DeadlinePayload",
    "deadline_attachments",
]

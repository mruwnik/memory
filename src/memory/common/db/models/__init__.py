from memory.common.db.models.base import Base
from memory.common.db.models.source_item import (
    Chunk,
    SourceItem,
    ConfidenceScore,
    clean_filename,
    SourceItemPayload,
)
from memory.common.db.models.source_items import (
    MailMessage,
    EmailAttachment,
    AgentObservation,
    ChatMessage,
    DiscordMessage,
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
)
from memory.common.db.models.discord import (
    DiscordServer,
    DiscordChannel,
    DiscordUser,
)
from memory.common.db.models.mcp import (
    MCPServer,
    MCPServerAssignment,
)
from memory.common.db.models.observations import (
    ObservationContradiction,
    ReactionPattern,
    ObservationPattern,
    BeliefCluster,
    ConversationMetrics,
)
from memory.common.db.models.people import (
    Person,
    PersonPayload,
)
from memory.common.db.models.sources import (
    Book,
    ArticleFeed,
    EmailAccount,
    GithubAccount,
    GithubRepo,
    GithubMilestone,
    GithubProject,
    GoogleOAuthConfig,
    GoogleAccount,
    GoogleFolder,
    CalendarAccount,
)
from memory.common.db.models.users import (
    User,
    HumanUser,
    BotUser,
    DiscordBotUser,
    UserSession,
    OAuthClientInformation,
    OAuthState,
    OAuthRefreshToken,
)
from memory.common.db.models.scheduled_calls import (
    ScheduledLLMCall,
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
    | GoogleDocPayload
    | TaskPayload
    | CalendarEventPayload
)

__all__ = [
    "Base",
    "Chunk",
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
    # Observations
    "ObservationContradiction",
    "ReactionPattern",
    "ObservationPattern",
    "BeliefCluster",
    "ConversationMetrics",
    # People
    "Person",
    "PersonPayload",
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
    "GithubMilestone",
    "GithubProject",
    "GoogleOAuthConfig",
    "GoogleAccount",
    "GoogleFolder",
    "CalendarAccount",
    "CalendarEvent",
    "CalendarEventPayload",
    "DiscordServer",
    "DiscordChannel",
    "DiscordUser",
    "MCPServer",
    "MCPServerAssignment",
    # Users
    "User",
    "HumanUser",
    "BotUser",
    "DiscordBotUser",
    "UserSession",
    "OAuthClientInformation",
    "OAuthState",
    "OAuthRefreshToken",
    # Scheduled Calls
    "ScheduledLLMCall",
    # Payloads
    "Payload",
]

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
    GitCommit,
    Photo,
    MiscDoc,
    Note,
    MailMessagePayload,
    EmailAttachmentPayload,
    AgentObservationPayload,
    BlogPostPayload,
    ComicPayload,
    BookSectionPayload,
    NotePayload,
    ForumPostPayload,
)
from memory.common.db.models.discord import (
    DiscordServer,
    DiscordChannel,
    DiscordUser,
)
from memory.common.db.models.observations import (
    ObservationContradiction,
    ReactionPattern,
    ObservationPattern,
    BeliefCluster,
    ConversationMetrics,
)
from memory.common.db.models.sources import (
    Book,
    ArticleFeed,
    EmailAccount,
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
    "GitCommit",
    "Photo",
    "MiscDoc",
    "Note",
    # Observations
    "ObservationContradiction",
    "ReactionPattern",
    "ObservationPattern",
    "BeliefCluster",
    "ConversationMetrics",
    # Sources
    "Book",
    "ArticleFeed",
    "EmailAccount",
    "DiscordServer",
    "DiscordChannel",
    "DiscordUser",
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

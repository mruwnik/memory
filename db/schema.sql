/*========================================================================
  Knowledge-Base schema – first-run script
  ---------------------------------------------------------------
  • PostgreSQL 15+
  • Creates every table, index, trigger and helper in one pass
  • No ALTER statements or later migrations required
  • Enable pgcrypto for UUID helpers (safe to re-run)
  ========================================================================*/

-------------------------------------------------------------------------------
-- 0.  EXTENSIONS
-------------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;      -- gen_random_uuid(), crypt()

-------------------------------------------------------------------------------
-- 1.  CANONICAL ARTEFACT TABLE  (everything points here)
-------------------------------------------------------------------------------
CREATE TABLE source_item (
    id            BIGSERIAL PRIMARY KEY,
    modality      TEXT      NOT NULL,                       -- 'mail'|'chat'|...
    sha256        BYTEA     UNIQUE NOT NULL,                -- 32-byte blob
    inserted_at   TIMESTAMPTZ      DEFAULT NOW(),
    tags          TEXT[]    NOT NULL DEFAULT '{}',          -- flexible labels
    lang          TEXT,                                     -- ISO-639-1 or NULL
    model_hash    TEXT,                                     -- embedding model ver.
    vector_ids    TEXT[]    NOT NULL DEFAULT '{}',          -- 0-N Qdrant IDs
    embed_status  TEXT      NOT NULL DEFAULT 'RAW'
                 CHECK (embed_status IN ('RAW','QUEUED','STORED','FAILED')),
    byte_length   INTEGER,                                  -- original size
    mime_type     TEXT
);

CREATE INDEX source_modality_idx ON source_item (modality);
CREATE INDEX source_status_idx   ON source_item (embed_status);
CREATE INDEX source_tags_idx     ON source_item USING GIN (tags);

-- 1.a  Trigger – vector_ids must be present when status = STORED
CREATE OR REPLACE FUNCTION trg_vector_ids_not_empty()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.embed_status = 'STORED'
     AND (NEW.vector_ids IS NULL OR array_length(NEW.vector_ids,1) = 0) THEN
        RAISE EXCEPTION
          USING MESSAGE = 'vector_ids must not be empty when embed_status = STORED';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER check_vector_ids
BEFORE UPDATE ON source_item
FOR EACH ROW EXECUTE FUNCTION trg_vector_ids_not_empty();

-------------------------------------------------------------------------------
-- 2.  MAIL MESSAGES
-------------------------------------------------------------------------------
CREATE TABLE mail_message (
    id            BIGSERIAL PRIMARY KEY,
    source_id     BIGINT   NOT NULL REFERENCES source_item ON DELETE CASCADE,
    message_id    TEXT     UNIQUE,
    subject       TEXT,
    sender        TEXT,
    recipients    TEXT[],
    sent_at       TIMESTAMPTZ,
    body_raw      TEXT,
    attachments   JSONB
);

CREATE INDEX mail_sent_idx        ON mail_message (sent_at);
CREATE INDEX mail_recipients_idx  ON mail_message USING GIN (recipients);

ALTER TABLE mail_message
  ADD COLUMN tsv tsvector
  GENERATED ALWAYS AS (
        to_tsvector('english',
                    coalesce(subject,'') || ' ' || coalesce(body_raw,'')))
  STORED;
CREATE INDEX mail_tsv_idx ON mail_message USING GIN (tsv);

-------------------------------------------------------------------------------
-- 3.  CHAT  (Slack / Discord)
-------------------------------------------------------------------------------
CREATE TABLE chat_message (
    id            BIGSERIAL PRIMARY KEY,
    source_id     BIGINT NOT NULL REFERENCES source_item ON DELETE CASCADE,
    platform      TEXT CHECK (platform IN ('slack','discord')),
    channel_id    TEXT,
    author        TEXT,
    sent_at       TIMESTAMPTZ,
    body_raw      TEXT
);
CREATE INDEX chat_channel_idx ON chat_message (platform, channel_id);

-------------------------------------------------------------------------------
-- 4.  GIT COMMITS  (local repos)
-------------------------------------------------------------------------------
CREATE TABLE git_commit (
    id             BIGSERIAL PRIMARY KEY,
    source_id      BIGINT NOT NULL REFERENCES source_item ON DELETE CASCADE,
    repo_path      TEXT,
    commit_sha     TEXT UNIQUE,
    author_name    TEXT,
    author_email   TEXT,
    author_date    TIMESTAMPTZ,
    msg_raw        TEXT,
    diff_summary   TEXT,
    files_changed  TEXT[]
);
CREATE INDEX git_files_idx ON git_commit USING GIN (files_changed);
CREATE INDEX git_date_idx  ON git_commit (author_date);

-------------------------------------------------------------------------------
-- 5.  PHOTOS
-------------------------------------------------------------------------------
CREATE TABLE photo (
    id             BIGSERIAL PRIMARY KEY,
    source_id      BIGINT NOT NULL REFERENCES source_item ON DELETE CASCADE,
    file_path      TEXT,
    exif_taken_at  TIMESTAMPTZ,
    exif_lat       NUMERIC(9,6),
    exif_lon       NUMERIC(9,6),
    camera_make    TEXT,
    camera_model   TEXT
);
CREATE INDEX photo_taken_idx ON photo (exif_taken_at);

-------------------------------------------------------------------------------
-- 6.  BOOKS, BLOG POSTS, MISC DOCS
-------------------------------------------------------------------------------
CREATE TABLE book_doc (
    id          BIGSERIAL PRIMARY KEY,
    source_id   BIGINT NOT NULL REFERENCES source_item ON DELETE CASCADE,
    title       TEXT,
    author      TEXT,
    chapter     TEXT,
    published   DATE
);

CREATE TABLE blog_post (
    id          BIGSERIAL PRIMARY KEY,
    source_id   BIGINT NOT NULL REFERENCES source_item ON DELETE CASCADE,
    url         TEXT UNIQUE,
    title       TEXT,
    published   TIMESTAMPTZ
);

CREATE TABLE misc_doc (
    id          BIGSERIAL PRIMARY KEY,
    source_id   BIGINT NOT NULL REFERENCES source_item ON DELETE CASCADE,
    path        TEXT,
    mime_type   TEXT
);

-------------------------------------------------------------------------------
-- 6.5  RSS FEEDS
-------------------------------------------------------------------------------
CREATE TABLE rss_feeds (
    id              BIGSERIAL PRIMARY KEY,
    url             TEXT UNIQUE NOT NULL,
    title           TEXT,
    description     TEXT,
    tags            TEXT[] NOT NULL DEFAULT '{}',
    last_checked_at TIMESTAMPTZ,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX rss_feeds_active_idx ON rss_feeds (active, last_checked_at);
CREATE INDEX rss_feeds_tags_idx ON rss_feeds USING GIN (tags);

-------------------------------------------------------------------------------
-- 7.  GITHUB ITEMS  (issues, PRs, comments, project cards)
-------------------------------------------------------------------------------
CREATE TYPE gh_item_kind AS ENUM ('issue','pr','comment','project_card');

CREATE TABLE github_item (
    id            BIGSERIAL PRIMARY KEY,
    source_id     BIGINT NOT NULL REFERENCES source_item ON DELETE CASCADE,

    kind          gh_item_kind NOT NULL,
    repo_path     TEXT NOT NULL,                  -- "owner/repo"
    number        INTEGER,                        -- issue/PR number (NULL for commit comment)
    parent_number INTEGER,                        -- comment → its issue/PR
    commit_sha    TEXT,                           -- for commit comments
    state         TEXT,                           -- 'open'|'closed'|'merged'
    title         TEXT,
    body_raw      TEXT,
    labels        TEXT[],
    author        TEXT,
    created_at    TIMESTAMPTZ,
    closed_at     TIMESTAMPTZ,
    merged_at     TIMESTAMPTZ,
    diff_summary  TEXT,                           -- PR only

    payload       JSONB                           -- extra GitHub fields
);

CREATE INDEX gh_repo_kind_idx    ON github_item (repo_path, kind);
CREATE INDEX gh_issue_lookup_idx ON github_item (repo_path, kind, number);
CREATE INDEX gh_labels_idx       ON github_item USING GIN (labels);

CREATE INDEX gh_tsv_idx ON github_item
WHERE kind IN ('issue','pr')
USING GIN (to_tsvector('english',
            coalesce(title,'') || ' ' || coalesce(body_raw,'')));

-------------------------------------------------------------------------------
-- 8.  HELPER FUNCTION – add tags
-------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION add_tags(p_source BIGINT, p_tags TEXT[])
RETURNS VOID LANGUAGE SQL AS $$
UPDATE source_item
   SET tags =
       (SELECT ARRAY(SELECT DISTINCT unnest(tags || p_tags)))
 WHERE id = p_source;
$$;

-------------------------------------------------------------------------------
-- 9.  (optional) PARTITION STUBS – create per-year partitions later
-------------------------------------------------------------------------------
/*
-- example:
CREATE TABLE mail_message_2026 PARTITION OF mail_message
  FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
*/

-- =========================================================================
-- Schema creation complete
-- =========================================================================
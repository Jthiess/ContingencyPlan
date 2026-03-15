-- ============================================================
-- Discord Server Backup Database Schema
-- ============================================================

CREATE TABLE guilds (
    id                          BIGINT PRIMARY KEY,
    name                        TEXT NOT NULL,
    description                 TEXT,
    icon_hash                   TEXT,
    banner_hash                 TEXT,
    owner_id                    BIGINT,
    region                      TEXT,
    verification_level          INTEGER,
    explicit_content_filter     INTEGER,
    afk_channel_id              BIGINT,
    afk_timeout                 INTEGER,
    premium_tier                INTEGER,
    premium_subscription_count  INTEGER,
    created_at                  TIMESTAMP WITH TIME ZONE
);

-- ============================================================

CREATE TABLE users (
    id              BIGINT PRIMARY KEY,
    name            TEXT NOT NULL,
    discriminator   TEXT,
    display_name    TEXT,
    bot             BOOLEAN DEFAULT FALSE,
    avatar_hash     TEXT
);

-- ============================================================

CREATE TABLE roles (
    id              BIGINT PRIMARY KEY,
    guild_id        BIGINT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    color           INTEGER DEFAULT 0,
    position        INTEGER NOT NULL,
    hoist           BOOLEAN DEFAULT FALSE,
    mentionable     BOOLEAN DEFAULT FALSE,
    managed         BOOLEAN DEFAULT FALSE,
    permissions     BIGINT DEFAULT 0
);

-- ============================================================

CREATE TABLE members (
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    guild_id        BIGINT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
    nickname        TEXT,
    joined_at       TIMESTAMP WITH TIME ZONE,
    premium_since   TIMESTAMP WITH TIME ZONE,
    pending         BOOLEAN DEFAULT FALSE,
    deaf            BOOLEAN DEFAULT FALSE,
    mute            BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (user_id, guild_id)
);

CREATE TABLE member_roles (
    user_id     BIGINT NOT NULL,
    guild_id    BIGINT NOT NULL,
    role_id     BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, guild_id, role_id),
    FOREIGN KEY (user_id, guild_id) REFERENCES members(user_id, guild_id) ON DELETE CASCADE
);

-- ============================================================

CREATE TABLE channels (
    id                              BIGINT PRIMARY KEY,
    guild_id                        BIGINT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
    type                            TEXT NOT NULL,
    name                            TEXT NOT NULL,
    topic                           TEXT,
    position                        INTEGER,
    category_id                     BIGINT REFERENCES channels(id) ON DELETE SET NULL,
    nsfw                            BOOLEAN DEFAULT FALSE,
    slowmode_delay                  INTEGER DEFAULT 0,
    last_message_id                 BIGINT,
    -- Voice channel fields
    bitrate                         INTEGER,
    user_limit                      INTEGER,
    -- Thread/Forum fields
    default_auto_archive_duration   INTEGER
);

CREATE TABLE permission_overwrites (
    channel_id      BIGINT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    target_id       BIGINT NOT NULL,
    target_type     TEXT NOT NULL CHECK (target_type IN ('role', 'member')),
    allow           BIGINT DEFAULT 0,
    deny            BIGINT DEFAULT 0,
    PRIMARY KEY (channel_id, target_id)
);

-- ============================================================

CREATE TABLE threads (
    id                  BIGINT PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE,
    parent_id           BIGINT REFERENCES channels(id) ON DELETE SET NULL,
    owner_id            BIGINT REFERENCES users(id) ON DELETE SET NULL,
    message_count       INTEGER DEFAULT 0,
    member_count        INTEGER DEFAULT 0,
    archived            BOOLEAN DEFAULT FALSE,
    locked              BOOLEAN DEFAULT FALSE,
    archive_timestamp   TIMESTAMP WITH TIME ZONE
);

-- ============================================================

CREATE TABLE webhooks (
    id          BIGINT PRIMARY KEY,
    guild_id    BIGINT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
    channel_id  BIGINT REFERENCES channels(id) ON DELETE SET NULL,
    name        TEXT,
    avatar_hash TEXT,
    type        INTEGER,
    token       TEXT
);

-- ============================================================

CREATE TABLE messages (
    id              BIGINT PRIMARY KEY,
    channel_id      BIGINT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    guild_id        BIGINT REFERENCES guilds(id) ON DELETE CASCADE,
    author_id       BIGINT REFERENCES users(id) ON DELETE SET NULL,
    content         TEXT,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL,
    edited_at       TIMESTAMP WITH TIME ZONE,
    pinned          BOOLEAN DEFAULT FALSE,
    tts             BOOLEAN DEFAULT FALSE,
    mention_everyone BOOLEAN DEFAULT FALSE,
    type            INTEGER DEFAULT 0,
    reference_id    BIGINT REFERENCES messages(id) ON DELETE SET NULL,
    webhook_id      BIGINT REFERENCES webhooks(id) ON DELETE SET NULL
);

CREATE TABLE attachments (
    id              BIGINT PRIMARY KEY,
    message_id      BIGINT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    filename        TEXT NOT NULL,
    url             TEXT,
    proxy_url       TEXT,
    size            INTEGER,
    width           INTEGER,
    height          INTEGER,
    content_type    TEXT
);

CREATE TABLE embeds (
    id              BIGSERIAL PRIMARY KEY,
    message_id      BIGINT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    title           TEXT,
    description     TEXT,
    url             TEXT,
    color           INTEGER,
    timestamp       TIMESTAMP WITH TIME ZONE,
    footer_text     TEXT,
    footer_icon_url TEXT,
    author_name     TEXT,
    author_url      TEXT,
    image_url       TEXT,
    thumbnail_url   TEXT
);

CREATE TABLE embed_fields (
    id          BIGSERIAL PRIMARY KEY,
    embed_id    BIGINT NOT NULL REFERENCES embeds(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    value       TEXT NOT NULL,
    inline      BOOLEAN DEFAULT FALSE
);

CREATE TABLE reactions (
    message_id  BIGINT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    emoji_id    BIGINT,
    emoji_name  TEXT NOT NULL,
    count       INTEGER DEFAULT 0,
    me          BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (message_id, emoji_name)
);

-- ============================================================

CREATE TABLE emojis (
    id              BIGINT PRIMARY KEY,
    guild_id        BIGINT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    animated        BOOLEAN DEFAULT FALSE,
    managed         BOOLEAN DEFAULT FALSE,
    require_colons  BOOLEAN DEFAULT TRUE,
    available       BOOLEAN DEFAULT TRUE,
    creator_id      BIGINT REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE stickers (
    id          BIGINT PRIMARY KEY,
    guild_id    BIGINT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    format_type INTEGER,
    available   BOOLEAN DEFAULT TRUE
);

CREATE TABLE scheduled_events (
    id          BIGINT PRIMARY KEY,
    guild_id    BIGINT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
    channel_id  BIGINT REFERENCES channels(id) ON DELETE SET NULL,
    name        TEXT NOT NULL,
    description TEXT,
    start_time  TIMESTAMP WITH TIME ZONE NOT NULL,
    end_time    TIMESTAMP WITH TIME ZONE,
    status      INTEGER,
    entity_type INTEGER,
    location    TEXT
);

-- Default channel-level access applied to new users on first login
CREATE TABLE default_channel_permissions (
    channel_id      BIGINT PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE,
    can_access      BOOLEAN DEFAULT TRUE
);

-- ============================================================
-- Indexes for common query patterns
-- ============================================================

CREATE INDEX idx_messages_channel_id     ON messages(channel_id);
CREATE INDEX idx_messages_author_id      ON messages(author_id);
CREATE INDEX idx_messages_created_at     ON messages(created_at);
CREATE INDEX idx_channels_guild_id       ON channels(guild_id);
CREATE INDEX idx_members_guild_id        ON members(guild_id);
CREATE INDEX idx_roles_guild_id          ON roles(guild_id);
CREATE INDEX idx_attachments_message_id  ON attachments(message_id);
CREATE INDEX idx_embeds_message_id       ON embeds(message_id);
CREATE INDEX idx_reactions_message_id    ON reactions(message_id);
CREATE INDEX idx_threads_parent_id       ON threads(parent_id);

-- ============================================================
-- Authentication & Permissions
-- ============================================================

-- App users authenticated via Authentik SSO
CREATE TABLE app_users (
    id              SERIAL PRIMARY KEY,
    authentik_sub   TEXT UNIQUE NOT NULL,
    username        TEXT NOT NULL,
    email           TEXT,
    is_admin        BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_login      TIMESTAMP WITH TIME ZONE
);

-- Link app users to Discord identities in the archive
CREATE TABLE user_discord_links (
    id              SERIAL PRIMARY KEY,
    app_user_id     INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    discord_user_id BIGINT NOT NULL,
    UNIQUE (app_user_id, discord_user_id)
);

-- Guild-level access: which guilds each user can see
-- (no row = no access; row with can_access=true = access granted)
CREATE TABLE user_guild_permissions (
    app_user_id     INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    guild_id        BIGINT NOT NULL,
    can_access      BOOLEAN DEFAULT TRUE,
    PRIMARY KEY (app_user_id, guild_id)
);

-- Channel-level access overrides within an accessible guild
-- (no row = follows guild default; row present = explicit allow/deny)
CREATE TABLE user_channel_permissions (
    app_user_id     INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    channel_id      BIGINT NOT NULL,
    can_access      BOOLEAN DEFAULT TRUE,
    PRIMARY KEY (app_user_id, channel_id)
);
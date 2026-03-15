import logging

import discord

from db import Database

logger = logging.getLogger(__name__)

_TYPE_NAMES = {
    discord.ChannelType.text:           "text",
    discord.ChannelType.voice:          "voice",
    discord.ChannelType.category:       "category",
    discord.ChannelType.news:           "news",
    discord.ChannelType.stage_voice:    "stage",
    discord.ChannelType.forum:          "forum",
    discord.ChannelType.public_thread:  "public_thread",
    discord.ChannelType.private_thread: "private_thread",
    discord.ChannelType.news_thread:    "news_thread",
}


# ── channels + permission overwrites ────────────────────────────────────────

async def clone_channels(db: Database, guild: discord.Guild):
    """Save every channel. Categories are inserted first to satisfy the FK."""
    channels = list(guild.channels)
    logger.info("[channels] Saving %d channels...", len(channels))

    categories = [c for c in channels if isinstance(c, discord.CategoryChannel)]
    others     = [c for c in channels if not isinstance(c, discord.CategoryChannel)]

    for channel in categories + others:
        await _upsert_channel(db, guild.id, channel)
        await _upsert_permission_overwrites(db, channel)

    logger.info("[channels] Done")


# ── threads (active + archived) ─────────────────────────────────────────────

async def clone_threads(db: Database, guild: discord.Guild):
    all_threads: list[discord.Thread] = list(guild.threads)

    # Pull archived threads from text and forum channels
    scannable = list(guild.text_channels) + list(getattr(guild, "forums", []))
    for channel in scannable:
        try:
            async for thread in channel.archived_threads(limit=None):
                if thread not in all_threads:
                    all_threads.append(thread)
        except discord.Forbidden:
            logger.warning("[threads] No permission for archived threads in #%s", channel.name)
        except Exception as e:
            logger.warning("[threads] Error in #%s: %s", channel.name, e)

    logger.info("[threads] Saving %d threads...", len(all_threads))

    for thread in all_threads:
        # Ensure the thread has a channels row
        await _upsert_channel(db, guild.id, thread)

        # owner_id FK — set to NULL if the user left the server
        owner_id = thread.owner_id
        if owner_id:
            user_exists = await db.fetchval("SELECT 1 FROM users WHERE id = $1", owner_id)
            if not user_exists:
                owner_id = None

        await db.execute(
            """
            INSERT INTO threads (
                id, parent_id, owner_id, message_count, member_count,
                archived, locked, archive_timestamp
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (id) DO UPDATE SET
                parent_id         = EXCLUDED.parent_id,
                owner_id          = EXCLUDED.owner_id,
                message_count     = EXCLUDED.message_count,
                member_count      = EXCLUDED.member_count,
                archived          = EXCLUDED.archived,
                locked            = EXCLUDED.locked,
                archive_timestamp = EXCLUDED.archive_timestamp
            """,
            thread.id, thread.parent_id, owner_id,
            thread.message_count, thread.member_count,
            thread.archived, thread.locked, thread.archive_timestamp,
        )

    logger.info("[threads] Done")


# ── internal helpers ─────────────────────────────────────────────────────────

async def _upsert_channel(db: Database, guild_id: int, channel):
    ch_type = _TYPE_NAMES.get(channel.type, str(channel.type))
    await db.execute(
        """
        INSERT INTO channels (
            id, guild_id, type, name, topic, position, category_id,
            nsfw, slowmode_delay, last_message_id,
            bitrate, user_limit, default_auto_archive_duration
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        ON CONFLICT (id) DO UPDATE SET
            type                          = EXCLUDED.type,
            name                          = EXCLUDED.name,
            topic                         = EXCLUDED.topic,
            position                      = EXCLUDED.position,
            category_id                   = EXCLUDED.category_id,
            nsfw                          = EXCLUDED.nsfw,
            slowmode_delay                = EXCLUDED.slowmode_delay,
            last_message_id               = EXCLUDED.last_message_id,
            bitrate                       = EXCLUDED.bitrate,
            user_limit                    = EXCLUDED.user_limit,
            default_auto_archive_duration = EXCLUDED.default_auto_archive_duration
        """,
        channel.id,
        guild_id,
        ch_type,
        channel.name,
        getattr(channel, "topic", None),
        getattr(channel, "position", 0),
        getattr(channel, "category_id", None),
        getattr(channel, "nsfw", False),
        getattr(channel, "slowmode_delay", 0) or 0,
        getattr(channel, "last_message_id", None),
        getattr(channel, "bitrate", None),
        getattr(channel, "user_limit", None),
        getattr(channel, "default_auto_archive_duration", None),
    )


async def _upsert_permission_overwrites(db: Database, channel):
    overwrites = getattr(channel, "overwrites", {})
    for target, overwrite in overwrites.items():
        target_type = "role" if isinstance(target, discord.Role) else "member"
        allow, deny = overwrite.pair()
        await db.execute(
            """
            INSERT INTO permission_overwrites (channel_id, target_id, target_type, allow, deny)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (channel_id, target_id) DO UPDATE SET
                target_type = EXCLUDED.target_type,
                allow       = EXCLUDED.allow,
                deny        = EXCLUDED.deny
            """,
            channel.id, target.id, target_type, allow.value, deny.value,
        )

from __future__ import annotations

import logging

import discord

from db import Database

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from downloader import Downloader

logger = logging.getLogger(__name__)


async def clone_webhooks(
    db: Database, guild: discord.Guild, dl: Downloader | None = None,
):
    """Save every webhook the bot can see (requires Manage Webhooks)."""
    try:
        webhooks = await guild.webhooks()
    except discord.Forbidden:
        logger.warning("[webhooks] No permission (requires Manage Webhooks)")
        return

    logger.info("[webhooks] Saving %d webhooks...", len(webhooks))
    for wh in webhooks:
        await db.execute(
            """
            INSERT INTO webhooks (id, guild_id, channel_id, name, avatar_hash, type, token)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (id) DO UPDATE SET
                channel_id  = EXCLUDED.channel_id,
                name        = EXCLUDED.name,
                avatar_hash = EXCLUDED.avatar_hash,
                type        = EXCLUDED.type,
                token       = EXCLUDED.token
            """,
            wh.id, guild.id, wh.channel_id, wh.name,
            str(wh.avatar) if wh.avatar else None,
            wh.type.value, wh.token,
        )
        if dl:
            await dl.save_asset(wh.avatar, "webhook_avatars", str(wh.id))
    logger.info("[webhooks] Done")

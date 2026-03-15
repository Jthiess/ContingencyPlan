from __future__ import annotations

import logging

import discord

from db import Database
from cloner.helpers import upsert_user

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from downloader import Downloader

logger = logging.getLogger(__name__)


# ── guild metadata ───────────────────────────────────────────────────────────

async def clone_guild_metadata(
    db: Database, guild: discord.Guild, dl: Downloader | None = None,
):
    logger.info("[guild] Saving metadata...")
    await db.execute(
        """
        INSERT INTO guilds (
            id, name, description, icon_hash, banner_hash, owner_id,
            region, verification_level, explicit_content_filter,
            afk_channel_id, afk_timeout, premium_tier,
            premium_subscription_count, created_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
        ON CONFLICT (id) DO UPDATE SET
            name                        = EXCLUDED.name,
            description                 = EXCLUDED.description,
            icon_hash                   = EXCLUDED.icon_hash,
            banner_hash                 = EXCLUDED.banner_hash,
            owner_id                    = EXCLUDED.owner_id,
            region                      = EXCLUDED.region,
            verification_level          = EXCLUDED.verification_level,
            explicit_content_filter     = EXCLUDED.explicit_content_filter,
            afk_channel_id              = EXCLUDED.afk_channel_id,
            afk_timeout                 = EXCLUDED.afk_timeout,
            premium_tier                = EXCLUDED.premium_tier,
            premium_subscription_count  = EXCLUDED.premium_subscription_count
        """,
        guild.id,
        guild.name,
        guild.description,
        str(guild.icon) if guild.icon else None,
        str(guild.banner) if guild.banner else None,
        guild.owner_id,
        str(guild.preferred_locale),
        guild.verification_level.value,
        guild.explicit_content_filter.value,
        guild.afk_channel.id if guild.afk_channel else None,
        guild.afk_timeout,
        guild.premium_tier if isinstance(guild.premium_tier, int) else guild.premium_tier.value,
        guild.premium_subscription_count,
        guild.created_at,
    )

    # Download guild images
    if dl:
        await dl.save_asset(guild.icon, "guild", "icon")
        await dl.save_asset(guild.banner, "guild", "banner")
        await dl.save_asset(
            getattr(guild, "splash", None), "guild", "splash",
        )
        await dl.save_asset(
            getattr(guild, "discovery_splash", None), "guild", "discovery_splash",
        )

    logger.info("[guild] Done")


# ── roles ────────────────────────────────────────────────────────────────────

async def clone_roles(
    db: Database, guild: discord.Guild, dl: Downloader | None = None,
):
    logger.info("[roles] Saving %d roles...", len(guild.roles))
    for role in guild.roles:
        await db.execute(
            """
            INSERT INTO roles (id, guild_id, name, color, position,
                             hoist, mentionable, managed, permissions)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (id) DO UPDATE SET
                name        = EXCLUDED.name,
                color       = EXCLUDED.color,
                position    = EXCLUDED.position,
                hoist       = EXCLUDED.hoist,
                mentionable = EXCLUDED.mentionable,
                managed     = EXCLUDED.managed,
                permissions = EXCLUDED.permissions
            """,
            role.id, guild.id, role.name, role.color.value,
            role.position, role.hoist, role.mentionable,
            role.managed, role.permissions.value,
        )
        if dl:
            await dl.save_asset(
                getattr(role, "icon", None), "role_icons", str(role.id),
            )
    logger.info("[roles] Done")


# ── custom emojis ────────────────────────────────────────────────────────────

async def clone_emojis(
    db: Database, guild: discord.Guild, dl: Downloader | None = None,
):
    emojis = guild.emojis
    logger.info("[emojis] Saving %d emojis...", len(emojis))
    for emoji in emojis:
        if emoji.user:
            await upsert_user(db, emoji.user)
        await db.execute(
            """
            INSERT INTO emojis (id, guild_id, name, animated, managed,
                              require_colons, available, creator_id)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (id) DO UPDATE SET
                name           = EXCLUDED.name,
                animated       = EXCLUDED.animated,
                managed        = EXCLUDED.managed,
                require_colons = EXCLUDED.require_colons,
                available      = EXCLUDED.available,
                creator_id     = EXCLUDED.creator_id
            """,
            emoji.id, guild.id, emoji.name, emoji.animated,
            emoji.managed, emoji.require_colons, emoji.available,
            emoji.user.id if emoji.user else None,
        )
        if dl:
            await dl.save_url(str(emoji.url), "emojis", str(emoji.id))
    logger.info("[emojis] Done")


# ── stickers ─────────────────────────────────────────────────────────────────

async def clone_stickers(
    db: Database, guild: discord.Guild, dl: Downloader | None = None,
):
    stickers = guild.stickers
    logger.info("[stickers] Saving %d stickers...", len(stickers))
    for sticker in stickers:
        await db.execute(
            """
            INSERT INTO stickers (id, guild_id, name, description, format_type, available)
            VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (id) DO UPDATE SET
                name        = EXCLUDED.name,
                description = EXCLUDED.description,
                format_type = EXCLUDED.format_type,
                available   = EXCLUDED.available
            """,
            sticker.id, guild.id, sticker.name,
            sticker.description, sticker.format.value, sticker.available,
        )
        if dl:
            await dl.save_url(str(sticker.url), "stickers", str(sticker.id))
    logger.info("[stickers] Done")


# ── scheduled events ─────────────────────────────────────────────────────────

async def clone_scheduled_events(
    db: Database, guild: discord.Guild, dl: Downloader | None = None,
):
    events = guild.scheduled_events
    logger.info("[events] Saving %d scheduled events...", len(events))
    for event in events:
        channel_id = event.channel.id if event.channel else None
        location = str(event.location) if getattr(event, "location", None) else None
        await db.execute(
            """
            INSERT INTO scheduled_events (
                id, guild_id, channel_id, name, description,
                start_time, end_time, status, entity_type, location
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (id) DO UPDATE SET
                name        = EXCLUDED.name,
                description = EXCLUDED.description,
                channel_id  = EXCLUDED.channel_id,
                start_time  = EXCLUDED.start_time,
                end_time    = EXCLUDED.end_time,
                status      = EXCLUDED.status,
                entity_type = EXCLUDED.entity_type,
                location    = EXCLUDED.location
            """,
            event.id, guild.id, channel_id, event.name,
            event.description, event.start_time, event.end_time,
            event.status.value, event.entity_type.value, location,
        )
        if dl:
            cover = getattr(event, "cover_image", None)
            await dl.save_asset(cover, "event_covers", str(event.id))
    logger.info("[events] Done")

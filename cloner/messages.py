from __future__ import annotations

import asyncio
import logging

import discord

from db import Database
from cloner.helpers import upsert_user

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from downloader import Downloader

logger = logging.getLogger(__name__)


# ── public entry point ───────────────────────────────────────────────────────

async def clone_all_messages(
    db: Database, guild: discord.Guild, dl: Downloader | None = None,
):
    """Clone messages from every readable channel and thread."""
    targets: list = []

    # Text channels (includes announcement/news channels)
    targets.extend(guild.text_channels)

    # Voice channels can hold text messages
    targets.extend(guild.voice_channels)

    # Active threads
    targets.extend(guild.threads)

    # Archived threads from text + forum channels
    scannable = list(guild.text_channels) + list(getattr(guild, "forums", []))
    for ch in scannable:
        try:
            async for t in ch.archived_threads(limit=None):
                if t not in targets:
                    targets.append(t)
        except discord.Forbidden:
            pass
        except Exception as e:
            logger.warning("[messages] Could not list archived threads in #%s: %s", getattr(ch, "name", ch.id), e)

    logger.info("[messages] %d channels / threads to scan", len(targets))
    total = 0
    for target in targets:
        total += await _clone_channel_messages(db, guild.id, target, dl)
    logger.info("[messages] Grand total: %d messages saved", total)


# ── per-channel fetcher ─────────────────────────────────────────────────────

async def _clone_channel_messages(
    db: Database, guild_id: int, channel, dl: Downloader | None,
) -> int:
    name = getattr(channel, "name", str(channel.id))
    count = 0

    while True:
        # Re-read the cursor each iteration so resuming after an interrupt or
        # rate-limit wait always picks up exactly where we left off.
        latest_id = await db.fetchval(
            "SELECT MAX(id) FROM messages WHERE channel_id = $1", channel.id,
        )
        after = discord.Object(id=latest_id) if latest_id else None

        try:
            async for message in channel.history(limit=None, after=after, oldest_first=True):
                await _save_message(db, guild_id, message, dl)
                count += 1
                if count % 1000 == 0:
                    logger.info("[messages]   #%s — %d so far...", name, count)
            break  # channel finished successfully

        except discord.Forbidden:
            logger.warning("[messages] No access to #%s", name)
            break

        except discord.HTTPException as e:
            if e.status == 429:
                wait = float(getattr(e, "retry_after", 5.0))
                logger.warning(
                    "[messages] Rate limited on #%s — waiting %.1fs then resuming",
                    name, wait,
                )
                await asyncio.sleep(wait)
                # loop back — cursor re-read will resume from last saved message

            else:
                logger.error("[messages] HTTP error in #%s (%s): %s", name, e.status, e.text)
                break

        except asyncio.CancelledError:
            raise  # propagate graceful shutdown

        except Exception as e:
            logger.error("[messages] Error in #%s: %s", name, e)
            break

    if count:
        logger.info("[messages] #%s — %d new messages", name, count)
    return count


# ── single-message writer ───────────────────────────────────────────────────

async def _save_message(
    db: Database, guild_id: int, msg: discord.Message, dl: Downloader | None,
):
    """Persist one message together with attachments, embeds, and reactions."""

    # Ensure author is stored as a user
    if msg.author:
        await upsert_user(db, msg.author)

    # Resolve reply reference (NULL if the target hasn't been stored yet)
    reference_id = None
    if msg.reference and msg.reference.message_id:
        exists = await db.fetchval(
            "SELECT 1 FROM messages WHERE id = $1", msg.reference.message_id,
        )
        if exists:
            reference_id = msg.reference.message_id

    # Resolve webhook FK
    webhook_id = None
    if msg.webhook_id:
        exists = await db.fetchval(
            "SELECT 1 FROM webhooks WHERE id = $1", msg.webhook_id,
        )
        if exists:
            webhook_id = msg.webhook_id

    # ── message row ──────────────────────────────────────────────────────
    await db.execute(
        """
        INSERT INTO messages (
            id, channel_id, guild_id, author_id, content,
            created_at, edited_at, pinned, tts,
            mention_everyone, type, reference_id, webhook_id
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        ON CONFLICT (id) DO UPDATE SET
            content      = EXCLUDED.content,
            edited_at    = EXCLUDED.edited_at,
            pinned       = EXCLUDED.pinned,
            type         = EXCLUDED.type,
            reference_id = EXCLUDED.reference_id
        """,
        msg.id, msg.channel.id, guild_id,
        msg.author.id if msg.author else None,
        msg.content, msg.created_at, msg.edited_at,
        msg.pinned, msg.tts, msg.mention_everyone,
        msg.type.value, reference_id, webhook_id,
    )

    # ── attachments ──────────────────────────────────────────────────────
    for att in msg.attachments:
        await db.execute(
            """
            INSERT INTO attachments (
                id, message_id, filename, url, proxy_url,
                size, width, height, content_type
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (id) DO UPDATE SET
                filename = EXCLUDED.filename,
                url      = EXCLUDED.url,
                proxy_url = EXCLUDED.proxy_url,
                size     = EXCLUDED.size
            """,
            att.id, msg.id, att.filename, att.url,
            att.proxy_url, att.size, att.width, att.height, att.content_type,
        )
        if dl:
            await dl.save_attachment(att, msg.channel.id)

    # ── embeds (delete + re-insert so edits are captured) ────────────────
    await db.execute("DELETE FROM embeds WHERE message_id = $1", msg.id)
    for idx, embed in enumerate(msg.embeds):
        embed_id = await db.fetchval(
            """
            INSERT INTO embeds (
                message_id, title, description, url, color,
                timestamp, footer_text, footer_icon_url,
                author_name, author_url, image_url, thumbnail_url
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            RETURNING id
            """,
            msg.id,
            embed.title,
            embed.description,
            embed.url,
            embed.color.value if embed.color else None,
            embed.timestamp,
            embed.footer.text if embed.footer else None,
            embed.footer.icon_url if embed.footer else None,
            embed.author.name if embed.author else None,
            embed.author.url if embed.author else None,
            str(embed.image.url) if embed.image else None,
            str(embed.thumbnail.url) if embed.thumbnail else None,
        )
        for field in embed.fields:
            await db.execute(
                """
                INSERT INTO embed_fields (embed_id, name, value, inline)
                VALUES ($1,$2,$3,$4)
                """,
                embed_id, field.name, field.value, field.inline,
            )

        # Download embed images / GIFs
        if dl:
            ch_id = str(msg.channel.id)
            if embed.type == "gifv":
                # Tenor / Giphy / inline GIFs — prefer the video URL (MP4/GIF),
                # fall back to the image URL if video is absent.
                gif_url = str(embed.video.url) if embed.video else (
                    str(embed.image.url) if embed.image else None
                )
                if gif_url:
                    await dl.save_url(
                        gif_url,
                        f"gifs/{ch_id}",
                        f"{msg.id}_gif{idx}",
                    )
            else:
                if embed.image:
                    await dl.save_url(
                        str(embed.image.url),
                        f"embed_images/{ch_id}",
                        f"{msg.id}_img{idx}",
                    )
                if embed.thumbnail:
                    await dl.save_url(
                        str(embed.thumbnail.url),
                        f"embed_images/{ch_id}",
                        f"{msg.id}_thumb{idx}",
                    )

    # ── reactions ────────────────────────────────────────────────────────
    for reaction in msg.reactions:
        emoji_name = (
            str(reaction.emoji) if isinstance(reaction.emoji, str)
            else reaction.emoji.name
        )
        emoji_id = (
            None if isinstance(reaction.emoji, str)
            else getattr(reaction.emoji, "id", None)
        )
        await db.execute(
            """
            INSERT INTO reactions (message_id, emoji_id, emoji_name, count, me)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (message_id, emoji_name) DO UPDATE SET
                count = EXCLUDED.count,
                me    = EXCLUDED.me
            """,
            msg.id, emoji_id, emoji_name, reaction.count, reaction.me,
        )

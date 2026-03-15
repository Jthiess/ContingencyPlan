from __future__ import annotations

import asyncio
import logging
import time

import discord

from db import Database
from downloader import Downloader
from cloner.guild import (
    clone_guild_metadata,
    clone_roles,
    clone_emojis,
    clone_stickers,
    clone_scheduled_events,
)
from cloner.channels import clone_channels, clone_threads
from cloner.members import clone_members
from cloner.messages import clone_all_messages
from cloner.webhooks import clone_webhooks

logger = logging.getLogger(__name__)


class ServerCloner:
    """Orchestrates a full clone of a Discord guild into PostgreSQL."""

    def __init__(self, db: Database, guild: discord.Guild):
        self.db = db
        self.guild = guild

    async def clone_all(
        self,
        *,
        skip_messages: bool = False,
        skip_downloads: bool = False,
        download_dir: str = "./downloads",
        full_clone: bool = False,
    ):
        g = self.guild
        logger.info("=" * 60)
        logger.info("CLONING SERVER: %s  (ID %s)", g.name, g.id)
        logger.info(
            "  Members: %d | Channels: %d | Roles: %d",
            g.member_count or len(g.members),
            len(g.channels),
            len(g.roles),
        )
        logger.info("=" * 60)

        if full_clone:
            logger.info("[full-clone] Deleting existing data for guild %s...", g.id)
            await self.db.execute("DELETE FROM guilds WHERE id = $1", g.id)
            logger.info("[full-clone] Existing data cleared")

        # Set up the downloader (None when downloads are skipped)
        dl: Downloader | None = None
        if not skip_downloads:
            dl = Downloader(download_dir, g.id)
            await dl.start()
            logger.info("[downloads] Saving files to %s", dl.base)

        t0 = time.monotonic()

        try:
            # Order matters — FKs require parents before children
            await clone_guild_metadata(self.db, g, dl)
            await clone_roles(self.db, g, dl)
            await clone_members(self.db, g, dl)
            await clone_channels(self.db, g)
            await clone_threads(self.db, g)
            await clone_webhooks(self.db, g, dl)
            await clone_emojis(self.db, g, dl)
            await clone_stickers(self.db, g, dl)
            await clone_scheduled_events(self.db, g, dl)

            if skip_messages:
                logger.info("[messages] Skipped (--skip-messages)")
            else:
                await clone_all_messages(self.db, g, dl)

        except asyncio.CancelledError:
            logger.info("Clone interrupted — all data saved so far is safe; re-run to resume")
            raise

        finally:
            if dl:
                dl.log_stats()
                await dl.close()

        elapsed = time.monotonic() - t0
        logger.info("=" * 60)
        logger.info("CLONE COMPLETE — %.1f seconds", elapsed)
        logger.info("=" * 60)

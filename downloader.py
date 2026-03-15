"""Async file downloader for Discord assets, attachments, and URLs."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import aiohttp
import discord

logger = logging.getLogger(__name__)

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe(name: str) -> str:
    """Sanitize a string for use as a filename."""
    return _UNSAFE_CHARS.sub("_", name)[:200]


class Downloader:
    """Rate-limited async downloader that saves Discord media to disk.

    Directory layout under *base_dir*/{guild_id}/:
        guild/              — server icon, banner, splash
        avatars/            — user profile pictures
        role_icons/         — role icons
        emojis/             — custom emoji images
        stickers/           — custom sticker files
        webhook_avatars/    — webhook profile pictures
        event_covers/       — scheduled-event cover images
        attachments/{ch}/   — message attachments, by channel
        embed_images/{ch}/  — embed images / thumbnails, by channel
        gifs/{ch}/          — Tenor / Giphy / gifv embed videos and GIFs, by channel
    """

    def __init__(self, base_dir: str, guild_id: int, max_concurrent: int = 10):
        self.base = Path(base_dir) / str(guild_id)
        self._sem = asyncio.Semaphore(max_concurrent)
        self._session: aiohttp.ClientSession | None = None
        self.downloaded = 0
        self.skipped = 0
        self.failed = 0

    # ── lifecycle ────────────────────────────────────────────────────────

    async def start(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=120),
        )

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None

    # ── public API ───────────────────────────────────────────────────────

    async def save_asset(
        self,
        asset: discord.Asset | None,
        subdir: str,
        filename: str,
    ) -> Path | None:
        """Download a discord.py *Asset* (avatar, icon, emoji, etc.)."""
        if asset is None:
            return None

        ext = "gif" if asset.is_animated() else "png"
        dest = self._dir(subdir) / f"{_safe(filename)}.{ext}"

        if dest.exists():
            self.skipped += 1
            return dest

        async with self._sem:
            try:
                await asset.save(dest)
                self.downloaded += 1
                return dest
            except Exception as e:
                logger.debug("Asset dl failed (%s): %s", filename, e)
                self.failed += 1
                return None

    async def save_url(
        self,
        url: str | None,
        subdir: str,
        filename: str,
        max_retries: int = 3,
    ) -> Path | None:
        """Download a file from a raw URL (emoji / sticker CDN, embed images, audio, video, etc.)."""
        if not url or not self._session:
            logger.error(f"URL missing or session not started: {url}")
            return None

        url_path = url.split("?")[0]
        last_segment = url_path.rsplit("/", 1)[-1]
        ext = last_segment.rsplit(".", 1)[-1] if "." in last_segment else "bin"
        if len(ext) > 10 or "/" in ext:
            ext = "bin"

        dest = self._dir(subdir) / f"{_safe(filename)}.{ext}"

        # Check for incomplete previous download
        if dest.exists() and dest.stat().st_size > 0:
            self.skipped += 1
            return dest
        elif dest.exists():
            logger.warning(f"Previous incomplete file detected, re-downloading: {dest}")

        for attempt in range(1, max_retries + 1):
            async with self._sem:
                try:
                    async with self._session.get(url) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            if not data:
                                raise ValueError("Downloaded file is empty")
                            dest.write_bytes(data)
                            # Check file size after write
                            if dest.stat().st_size == 0:
                                raise ValueError("Written file is empty")
                            self.downloaded += 1
                            return dest
                        else:
                            raise Exception(f"HTTP {resp.status} for {url}")
                except Exception as e:
                    logger.error(f"URL download failed ({url}) [Attempt {attempt}/{max_retries}]: {e}")
                    if attempt == max_retries:
                        self.failed += 1
                        logger.error(f"Failed to download: {url} -> {dest} | Reason: {e}")
                        return None
                    await asyncio.sleep(2 * attempt)

    async def save_attachment(
        self,
        attachment: discord.Attachment,
        channel_id: int,
        max_retries: int = 3,
    ) -> Path | None:
        """Download a message attachment (any media type)."""
        safe_name = f"{attachment.id}_{_safe(attachment.filename)}"
        dest = self._dir("attachments", str(channel_id)) / safe_name

        # Check for incomplete previous download
        if dest.exists() and dest.stat().st_size > 0:
            self.skipped += 1
            return dest
        elif dest.exists():
            logger.warning(f"Previous incomplete file detected, re-downloading: {dest}")

        for attempt in range(1, max_retries + 1):
            async with self._sem:
                try:
                    await attachment.save(dest)
                    # Check file size after write
                    if dest.stat().st_size == 0:
                        raise ValueError("Written file is empty")
                    self.downloaded += 1
                    return dest
                except Exception as e:
                    logger.error(f"Attachment download failed ({attachment.filename}) [Attempt {attempt}/{max_retries}]: {e}")
                    if attempt == max_retries:
                        self.failed += 1
                        logger.error(f"Failed to download: {attachment.filename} -> {dest} | Reason: {e}")
                        return None
                    await asyncio.sleep(2 * attempt)

    def log_stats(self):
        total = self.downloaded + self.skipped + self.failed
        logger.info(
            "[downloads] %d files total — %d downloaded, %d already existed, %d failed",
            total, self.downloaded, self.skipped, self.failed,
        )

    # ── internal ─────────────────────────────────────────────────────────

    def _dir(self, *parts: str) -> Path:
        d = self.base.joinpath(*parts)
        d.mkdir(parents=True, exist_ok=True)
        return d

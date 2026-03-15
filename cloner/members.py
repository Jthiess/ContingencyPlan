from __future__ import annotations

import logging

import discord

from db import Database
from cloner.helpers import upsert_user

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from downloader import Downloader

logger = logging.getLogger(__name__)


async def clone_members(
    db: Database, guild: discord.Guild, dl: Downloader | None = None,
):
    """Save every member: user record + membership row + role assignments."""
    members = guild.members
    logger.info("[members] Saving %d members...", len(members))

    for i, member in enumerate(members, 1):
        # 1 ── user record
        await upsert_user(db, member)

        # 2 ── download avatar
        if dl:
            await dl.save_asset(member.avatar or member.display_avatar, "avatars", str(member.id))

        # 3 ── membership
        await db.execute(
            """
            INSERT INTO members (
                user_id, guild_id, nickname, joined_at,
                premium_since, pending, deaf, mute
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (user_id, guild_id) DO UPDATE SET
                nickname      = EXCLUDED.nickname,
                joined_at     = EXCLUDED.joined_at,
                premium_since = EXCLUDED.premium_since,
                pending       = EXCLUDED.pending,
                deaf          = EXCLUDED.deaf,
                mute          = EXCLUDED.mute
            """,
            member.id, guild.id, member.nick, member.joined_at,
            member.premium_since, member.pending,
            member.voice.deaf if member.voice else False,
            member.voice.mute if member.voice else False,
        )

        # 4 ── role assignments (full replace)
        await db.execute(
            "DELETE FROM member_roles WHERE user_id = $1 AND guild_id = $2",
            member.id, guild.id,
        )
        for role in member.roles:
            if role.id == guild.id:  # skip @everyone
                continue
            await db.execute(
                "INSERT INTO member_roles (user_id, guild_id, role_id) "
                "VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
                member.id, guild.id, role.id,
            )

        if i % 200 == 0:
            logger.info("[members]   %d / %d ...", i, len(members))

    logger.info("[members] Done")

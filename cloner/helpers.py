import discord

from db import Database


async def upsert_user(db: Database, user: discord.User | discord.Member):
    """Insert or update a single user record."""
    await db.execute(
        """
        INSERT INTO users (id, name, discriminator, display_name, bot, avatar_hash)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (id) DO UPDATE SET
            name            = EXCLUDED.name,
            discriminator   = EXCLUDED.discriminator,
            display_name    = EXCLUDED.display_name,
            bot             = EXCLUDED.bot,
            avatar_hash     = EXCLUDED.avatar_hash
        """,
        user.id,
        user.name,
        user.discriminator,
        getattr(user, 'global_name', None),
        user.bot,
        user.avatar.key if user.avatar else None,
    )

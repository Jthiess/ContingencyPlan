"""
Contingency Plan — Discord Server Cloner

Connects to a Discord guild via bot token, reads every bit of data the bot
can access (metadata, roles, members, channels, threads, webhooks, emojis,
stickers, scheduled events, and full message history), and stores it all in
a PostgreSQL database whose schema is defined in DatabaseSetup.sql.

All configuration is read from environment variables (see .env.example).
"""

import argparse
import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler

import discord

from config import DISCORD_BOT_TOKEN, DOWNLOAD_DIR, GUILD_ID, fix_windows_encoding, gzip_rotator, gzip_namer
from db import Database
from cloner import ServerCloner


# ── logging ──────────────────────────────────────────────────────────────────

def setup_logging():
    fix_windows_encoding()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.DEBUG)
    sh.setFormatter(fmt)

    fh = RotatingFileHandler("clone.log", maxBytes=20 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    fh.rotator = gzip_rotator
    fh.namer = gzip_namer

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(sh)
    root.addHandler(fh)

    # Enable all discord.py internals
    for name in ("discord", "discord.gateway", "discord.http", "discord.client",
                 "discord.state", "discord.voice_client", "discord.webhook"):
        logging.getLogger(name).setLevel(logging.DEBUG)

    # Enable asyncpg and aiohttp at DEBUG too
    logging.getLogger("asyncpg").setLevel(logging.DEBUG)
    logging.getLogger("aiohttp").setLevel(logging.DEBUG)


# ── CLI ──────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Contingency Plan — Clone a Discord server into PostgreSQL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python main.py                     Clone entire server\n"
            "  python main.py --skip-messages      Clone everything except messages\n"
            "  python main.py --skip-downloads     Clone data but don't download files\n"
            "  python main.py --full-clone         Wipe existing guild data and re-clone from scratch\n"
            "  python main.py --init-db            Create DB schema and exit\n"
            "  python main.py --guild-id 12345     Override GUILD_ID from .env\n"
        ),
    )
    parser.add_argument(
        "--guild-id", type=int, default=0,
        help="Override GUILD_ID from .env",
    )
    parser.add_argument(
        "--skip-messages", action="store_true",
        help="Skip the (potentially very slow) message history clone",
    )
    parser.add_argument(
        "--skip-downloads", action="store_true",
        help="Skip downloading avatars, attachments, emoji images, etc.",
    )
    parser.add_argument(
        "--init-db", action="store_true",
        help="Create the database schema from DatabaseSetup.sql and exit",
    )
    parser.add_argument(
        "--full-clone", action="store_true",
        help="Delete all existing data for the guild before cloning (full overwrite)",
    )
    return parser.parse_args()


# ── main ─────────────────────────────────────────────────────────────────────

async def main():
    setup_logging()
    logger = logging.getLogger("contingencyplan")
    args = parse_args()

    guild_id = args.guild_id or GUILD_ID

    # ── validate config ──────────────────────────────────────────────────
    if not args.init_db:
        if not DISCORD_BOT_TOKEN:
            logger.error("DISCORD_BOT_TOKEN is not set — check your .env file")
            sys.exit(1)
        if not guild_id:
            logger.error("GUILD_ID is not set — use --guild-id or set it in .env")
            sys.exit(1)

    # ── database ─────────────────────────────────────────────────────────
    db = Database()
    try:
        await db.connect()
    except Exception as e:
        logger.error("Failed to connect to database: %s", e)
        logger.error("Check your DB_* environment variables in .env")
        sys.exit(1)

    await db.init_schema()

    if args.init_db:
        logger.info("Database schema ready. Exiting.")
        await db.close()
        return

    # ── discord client ───────────────────────────────────────────────────
    intents = discord.Intents.all()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        try:
            logger.info("Logged in as %s (ID: %s)", client.user, client.user.id)

            guild = client.get_guild(guild_id)
            if guild is None:
                try:
                    guild = await client.fetch_guild(guild_id)
                except discord.NotFound:
                    logger.error("Guild %d not found — is the bot a member?", guild_id)
                    return
                except discord.Forbidden:
                    logger.error("No access to guild %d — check bot permissions", guild_id)
                    return

            # Make sure the full member list is cached
            if not guild.chunked:
                logger.info("Requesting full member list...")
                await guild.chunk()

            cloner = ServerCloner(db, guild)
            await cloner.clone_all(
                skip_messages=args.skip_messages,
                skip_downloads=args.skip_downloads,
                download_dir=DOWNLOAD_DIR,
                full_clone=args.full_clone,
            )

        except asyncio.CancelledError:
            logger.info("Shutting down cleanly — re-run to resume from where it stopped")
        except Exception:
            logger.exception("Fatal error during clone")
        finally:
            await client.close()
            await db.close()

    try:
        await client.start(DISCORD_BOT_TOKEN)
    except discord.LoginFailure:
        logger.error("Invalid bot token — check DISCORD_BOT_TOKEN in .env")
        await db.close()
        sys.exit(1)
    except discord.PrivilegedIntentsRequired:
        logger.error(
            "Privileged intents are required but not enabled.\n"
            "Go to the Discord Developer Portal → your app → Bot →\n"
            "Privileged Gateway Intents → enable all three toggles."
        )
        await db.close()
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

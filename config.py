import os

from dotenv import load_dotenv

load_dotenv()

# ── Discord ──────────────────────────────────────────────────────────────────
DISCORD_BOT_TOKEN: str = os.getenv("DISCORD_BOT_TOKEN", "")
GUILD_ID: int = int(os.getenv("GUILD_ID", "0"))

# ── PostgreSQL ───────────────────────────────────────────────────────────────
DB_HOST: str = os.getenv("DB_HOST", "localhost")
DB_PORT: int = int(os.getenv("DB_PORT", "5432"))
DB_NAME: str = os.getenv("DB_NAME", "contingencyplan")
DB_USER: str = os.getenv("DB_USER", "postgres")
DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
DB_SCHEMA: str = os.getenv("DB_SCHEMA", "public")

# ── Downloads ────────────────────────────────────────────────────────────────
DOWNLOAD_DIR: str = os.getenv("DOWNLOAD_DIR", "./downloads")

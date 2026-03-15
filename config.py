import gzip
import os
import shutil
import sys

from dotenv import load_dotenv

load_dotenv()


# ── Flask ────────────────────────────────────────────────────────────────────
FLASK_PORT: int = int(os.getenv("FLASK_PORT", "5000"))

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


# ── Shared helpers ───────────────────────────────────────────────────────────

def fix_windows_encoding():
    """Fix Windows console encoding so Unicode characters don't crash stream handlers."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def gzip_rotator(source, dest):
    """Compress rotated log files with gzip."""
    with open(source, "rb") as f_in, gzip.open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    os.remove(source)


def gzip_namer(name):
    return name + ".gz"

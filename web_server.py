"""Flask web server for the Contingency Plan Discord archive viewer."""

import gzip
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlencode

import psycopg2
import psycopg2.extras
import requests as http_requests
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request, send_from_directory, session

load_dotenv()

# Fix Windows console encoding so Unicode characters don't crash the stream
# handler with a charmap error.
import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(_sys.stderr, "reconfigure"):
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_log_fmt = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_sh = logging.StreamHandler(_sys.stdout)
_sh.setFormatter(_log_fmt)


def _gzip_rotator(source, dest):
    with open(source, "rb") as f_in, gzip.open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    os.remove(source)


def _gzip_namer(name):
    return name + ".gz"


_fh = RotatingFileHandler("web.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
_fh.setFormatter(_log_fmt)
_fh.rotator = _gzip_rotator
_fh.namer = _gzip_namer

_root = logging.getLogger()
_root.addHandler(_sh)
_root.addHandler(_fh)

log = logging.getLogger("contingency")
# Keep werkzeug/urllib3/requests at INFO to reduce noise
logging.getLogger("werkzeug").setLevel(logging.INFO)
logging.getLogger("urllib3").setLevel(logging.INFO)
logging.getLogger("requests").setLevel(logging.INFO)


def _apply_log_level(debug: bool):
    level = logging.DEBUG if debug else logging.INFO
    _root.setLevel(level)
    _sh.setLevel(level)
    _fh.setLevel(level)


_debug_on = os.getenv("LOG_DEBUG", "true").lower() not in ("0", "false", "no")
_apply_log_level(_debug_on)

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "./downloads")
DB_SCHEMA = os.getenv("DB_SCHEMA", "public")
ENV_PATH = Path(".env")

# Auth config
_secret_key_env = os.getenv("SECRET_KEY", "")
if not _secret_key_env:
    SECRET_KEY = secrets.token_hex(32)
    log.warning(
        "SECRET_KEY is not set in .env — a random key was generated. "
        "All sessions will be invalidated on every restart. "
        "Set SECRET_KEY to a stable value: python -c \"import secrets; print(secrets.token_hex(32))\""
    )
else:
    SECRET_KEY = _secret_key_env
AUTHENTIK_BASE_URL = os.getenv("AUTHENTIK_BASE_URL", "").rstrip("/")
OAUTH_CLIENT_ID = os.getenv("OAUTH_CLIENT_ID", "")
OAUTH_CLIENT_SECRET = os.getenv("OAUTH_CLIENT_SECRET", "")
BASE_URL = os.getenv("BASE_URL", "http://localhost:5000")
ADMIN_USERS = [u.strip() for u in os.getenv("ADMIN_USERS", "").split(",") if u.strip()]

CONFIG_KEYS = [
    "DISCORD_BOT_TOKEN",
    "GUILD_ID",
    "DB_HOST",
    "DB_PORT",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "DB_SCHEMA",
    "DOWNLOAD_DIR",
    "LOG_DEBUG",
]

# guild_id -> {"process": Popen, "full": bool, "log": [str], "done": bool, "error": str|None, "pid": int}
_clone_jobs: dict = {}
_clone_lock = threading.Lock()

SCHEDULES_FILE = Path("schedules.json")
_schedules: list = []
_schedules_lock = threading.Lock()

app = Flask(__name__, static_folder="static", static_url_path="")
app.secret_key = SECRET_KEY


def get_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "contingencyplan"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
        options=f"-c search_path={DB_SCHEMA}",
    )


def _row(row):
    """Convert a psycopg2 row to a plain dict, serialising datetimes and large IDs."""
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
        elif isinstance(v, int) and (k == "id" or k.endswith("_id")):
            d[k] = str(v)
    return d


def ensure_auth_tables():
    """Create authentication/permission tables if they don't already exist."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS app_users (
                id              SERIAL PRIMARY KEY,
                authentik_sub   TEXT UNIQUE NOT NULL,
                username        TEXT NOT NULL,
                email           TEXT,
                is_admin        BOOLEAN DEFAULT FALSE,
                created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                last_login      TIMESTAMP WITH TIME ZONE
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_discord_links (
                id              SERIAL PRIMARY KEY,
                app_user_id     INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
                discord_user_id BIGINT NOT NULL,
                UNIQUE (app_user_id, discord_user_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_guild_permissions (
                app_user_id     INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
                guild_id        BIGINT NOT NULL,
                can_access      BOOLEAN DEFAULT TRUE,
                PRIMARY KEY (app_user_id, guild_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_channel_permissions (
                app_user_id     INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
                channel_id      BIGINT NOT NULL,
                can_access      BOOLEAN DEFAULT TRUE,
                PRIMARY KEY (app_user_id, channel_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_hidden_authors (
                app_user_id     INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
                discord_user_id BIGINT NOT NULL,
                PRIMARY KEY (app_user_id, discord_user_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS default_guild_permissions (
                guild_id        BIGINT PRIMARY KEY,
                can_access      BOOLEAN DEFAULT TRUE
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS default_channel_permissions (
                channel_id      BIGINT PRIMARY KEY,
                can_access      BOOLEAN DEFAULT TRUE
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Warning: Could not create auth tables: {e}", flush=True)


# ── Auth helpers ──────────────────────────────────────────────────────────────

def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT id, authentik_sub, username, email, is_admin FROM app_users WHERE id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not get_current_user():
            return jsonify({"error": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({"error": "Authentication required"}), 401
        if not user["is_admin"]:
            return jsonify({"error": "Admin required"}), 403
        return f(*args, **kwargs)
    return decorated


# ── Permission helpers ────────────────────────────────────────────────────────

def _get_accessible_guild_ids(user_id: int) -> list:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT guild_id FROM user_guild_permissions WHERE app_user_id = %s AND can_access = TRUE",
        (user_id,),
    )
    ids = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return ids


def _get_accessible_channel_ids(user_id: int, guild_id: int):
    """Returns list of accessible channel IDs, or None if no per-channel restrictions are set."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT ucp.channel_id, ucp.can_access
        FROM user_channel_permissions ucp
        JOIN channels c ON c.id = ucp.channel_id
        WHERE ucp.app_user_id = %s AND c.guild_id = %s
        """,
        (user_id, guild_id),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    if not rows:
        return None  # No restrictions — all channels accessible
    return [row[0] for row in rows if row[1]]


def _check_guild_access(user: dict, guild_id) -> bool:
    if user["is_admin"]:
        return True
    return int(guild_id) in _get_accessible_guild_ids(user["id"])


def _check_channel_access(user: dict, channel_id) -> bool:
    if user["is_admin"]:
        return True
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT guild_id FROM channels WHERE id = %s", (int(channel_id),))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return False
    guild_id = row[0]
    if not _check_guild_access(user, guild_id):
        return False
    accessible = _get_accessible_channel_ids(user["id"], guild_id)
    if accessible is None:
        return True
    return int(channel_id) in accessible


def _get_hidden_author_ids(user_id: int) -> list:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT discord_user_id FROM user_hidden_authors WHERE app_user_id = %s",
        (user_id,),
    )
    ids = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return ids


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/login")
def login():
    log.debug("LOGIN: request from %s", request.remote_addr)
    if not AUTHENTIK_BASE_URL or not OAUTH_CLIENT_ID:
        log.error("LOGIN: SSO not configured — AUTHENTIK_BASE_URL=%r OAUTH_CLIENT_ID=%r", AUTHENTIK_BASE_URL, OAUTH_CLIENT_ID)
        return "SSO not configured. Set AUTHENTIK_BASE_URL and OAUTH_CLIENT_ID in .env", 503
    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    params = {
        "response_type": "code",
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": f"{BASE_URL}/callback",
        "scope": "openid profile email",
        "state": state,
    }
    authorize_url = f"{AUTHENTIK_BASE_URL}/application/o/authorize/?{urlencode(params)}"
    log.debug("LOGIN: redirecting to Authentik — url=%s", authorize_url)
    return redirect(authorize_url)


@app.route("/callback")
def callback():
    log.debug("CALLBACK: received — args=%s", dict(request.args))

    error = request.args.get("error")
    if error:
        error_desc = request.args.get("error_description", "")
        log.error("CALLBACK: OAuth error from Authentik — error=%r description=%r", error, error_desc)
        return f"OAuth error: {error}", 400

    received_state = request.args.get("state")
    expected_state = session.pop("oauth_state", None)
    log.debug("CALLBACK: state check — received=%r expected=%r match=%s", received_state, expected_state, received_state == expected_state)
    if received_state != expected_state:
        log.error("CALLBACK: state mismatch — possible CSRF or stale session")
        return "Invalid state parameter", 400

    code = request.args.get("code")
    if not code:
        log.error("CALLBACK: no authorization code in response")
        return "No authorization code received", 400
    log.debug("CALLBACK: got authorization code (len=%d)", len(code))

    token_url = f"{AUTHENTIK_BASE_URL}/application/o/token/"
    token_payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": f"{BASE_URL}/callback",
        "client_id": OAUTH_CLIENT_ID,
        "client_secret": OAUTH_CLIENT_SECRET,
    }
    log.debug("CALLBACK: exchanging code for token — url=%s client_id=%r redirect_uri=%r", token_url, OAUTH_CLIENT_ID, f"{BASE_URL}/callback")
    try:
        token_resp = http_requests.post(token_url, data=token_payload, timeout=10)
        log.debug("CALLBACK: token endpoint HTTP status=%d", token_resp.status_code)
        log.debug("CALLBACK: token response body=%s", token_resp.text)
        token_data = token_resp.json()
    except Exception as e:
        log.exception("CALLBACK: token exchange exception")
        return f"Token exchange failed: {e}", 500

    if "error" in token_data:
        log.error("CALLBACK: token error — %r: %r", token_data.get("error"), token_data.get("error_description"))
        return f"Token exchange failed: {token_data.get('error_description', token_data['error'])}", 500

    access_token = token_data.get("access_token")
    log.debug("CALLBACK: token_data keys=%s token_type=%r expires_in=%r scope=%r",
              list(token_data.keys()), token_data.get("token_type"), token_data.get("expires_in"), token_data.get("scope"))
    if not access_token:
        log.error("CALLBACK: no access_token in token response")
        return "Token exchange failed: no access_token", 500

    userinfo_url = f"{AUTHENTIK_BASE_URL}/application/o/userinfo/"
    log.debug("CALLBACK: fetching userinfo — url=%s", userinfo_url)
    try:
        userinfo_resp = http_requests.get(
            userinfo_url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        log.debug("CALLBACK: userinfo HTTP status=%d", userinfo_resp.status_code)
        log.debug("CALLBACK: userinfo response body=%s", userinfo_resp.text)
        userinfo = userinfo_resp.json()
    except Exception as e:
        log.exception("CALLBACK: userinfo fetch exception")
        return f"Failed to get user info: {e}", 500

    log.debug("CALLBACK: userinfo keys=%s", list(userinfo.keys()))
    log.debug("CALLBACK: userinfo full=%s", userinfo)

    sub = userinfo.get("sub")
    if not sub:
        log.error("CALLBACK: no 'sub' in userinfo — cannot identify user")
        return "No user identifier received from SSO", 500

    username = userinfo.get("preferred_username") or userinfo.get("name") or sub
    email = userinfo.get("email")
    discord_user_id_raw = userinfo.get("discordUserId")

    # Fall back to id_token claims if userinfo doesn't include discordUserId
    if discord_user_id_raw is None:
        id_token = token_data.get("id_token")
        if id_token:
            try:
                import base64, json as _json
                payload_b64 = id_token.split(".")[1]
                # Add padding if needed
                payload_b64 += "=" * (-len(payload_b64) % 4)
                id_token_claims = _json.loads(base64.urlsafe_b64decode(payload_b64))
                discord_user_id_raw = id_token_claims.get("discordUserId")
                if discord_user_id_raw is not None:
                    log.debug("CALLBACK: discordUserId found in id_token claims: %r", discord_user_id_raw)
            except Exception as e:
                log.warning("CALLBACK: failed to decode id_token for discordUserId — %s", e)

    log.debug("CALLBACK: parsed — sub=%r username=%r email=%r discordUserId=%r", sub, username, email, discord_user_id_raw)
    if discord_user_id_raw is None:
        log.warning("CALLBACK: 'discordUserId' attribute not present in userinfo or id_token — check Authentik property mapping")

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT COUNT(*) AS count FROM app_users")
    is_first = cur.fetchone()["count"] == 0
    force_admin = is_first or (username in ADMIN_USERS)
    log.debug("CALLBACK: is_first_user=%s force_admin=%s", is_first, force_admin)

    cur.execute(
        """
        INSERT INTO app_users (authentik_sub, username, email, is_admin, last_login)
        VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (authentik_sub) DO UPDATE
        SET username = EXCLUDED.username,
            email = EXCLUDED.email,
            last_login = NOW(),
            is_admin = CASE WHEN %s THEN TRUE ELSE app_users.is_admin END
        RETURNING id, (xmax = 0) AS is_new_user
        """,
        (sub, username, email, force_admin, force_admin),
    )
    user = cur.fetchone()
    app_user_id = user["id"]
    is_new_user = user["is_new_user"]
    log.debug("CALLBACK: upserted app_user id=%d is_new_user=%s", app_user_id, is_new_user)

    if is_new_user:
        cur.execute(
            """
            SELECT g.id, COALESCE(dgp.can_access, TRUE) AS can_access
            FROM guilds g
            LEFT JOIN default_guild_permissions dgp ON dgp.guild_id = g.id
            """
        )
        guild_rows = cur.fetchall()
        for guild_row in guild_rows:
            cur.execute(
                """
                INSERT INTO user_guild_permissions (app_user_id, guild_id, can_access)
                VALUES (%s, %s, %s)
                ON CONFLICT (app_user_id, guild_id) DO NOTHING
                """,
                (app_user_id, guild_row["id"], guild_row["can_access"]),
            )
            # Apply default channel permissions for accessible guilds
            if guild_row["can_access"]:
                # Check if any channel defaults are configured for this guild
                cur.execute(
                    """
                    SELECT COUNT(*) AS count FROM default_channel_permissions dcp
                    JOIN channels c ON c.id = dcp.channel_id
                    WHERE c.guild_id = %s
                    """,
                    (guild_row["id"],),
                )
                has_channel_defaults = cur.fetchone()["count"] > 0
                if has_channel_defaults:
                    # Whitelist mode: channels not listed in default_channel_permissions default to denied
                    cur.execute(
                        """
                        SELECT c.id, COALESCE(dcp.can_access, FALSE) AS can_access
                        FROM channels c
                        LEFT JOIN default_channel_permissions dcp ON dcp.channel_id = c.id
                        WHERE c.guild_id = %s AND c.type NOT IN ('category')
                        """,
                        (guild_row["id"],),
                    )
                else:
                    # No defaults configured: only insert explicit entries (all channels remain accessible)
                    cur.execute(
                        """
                        SELECT c.id, dcp.can_access
                        FROM channels c
                        JOIN default_channel_permissions dcp ON dcp.channel_id = c.id
                        WHERE c.guild_id = %s AND c.type NOT IN ('category')
                        """,
                        (guild_row["id"],),
                    )
                chan_rows = cur.fetchall()
                for chan_row in chan_rows:
                    cur.execute(
                        """
                        INSERT INTO user_channel_permissions (app_user_id, channel_id, can_access)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (app_user_id, channel_id) DO NOTHING
                        """,
                        (app_user_id, chan_row["id"], chan_row["can_access"]),
                    )
        log.debug("CALLBACK: applied default guild permissions (%d guilds) for new user id=%d", len(guild_rows), app_user_id)

    if discord_user_id_raw:
        try:
            discord_user_id = int(discord_user_id_raw)
            log.debug("CALLBACK: upserting discord link app_user_id=%d discord_user_id=%d", app_user_id, discord_user_id)
            cur.execute(
                """
                INSERT INTO user_discord_links (app_user_id, discord_user_id)
                VALUES (%s, %s)
                ON CONFLICT (app_user_id, discord_user_id) DO NOTHING
                """,
                (app_user_id, discord_user_id),
            )
            log.debug("CALLBACK: discord link upsert complete (rowcount=%d)", cur.rowcount)
        except (ValueError, TypeError) as e:
            log.error("CALLBACK: discordUserId=%r could not be parsed as int — %s", discord_user_id_raw, e)
    else:
        log.warning("CALLBACK: skipping discord link — discordUserId not set for user %r", username)

    conn.commit()
    cur.close()
    conn.close()

    log.debug("CALLBACK: login complete — app_user_id=%d username=%r redirecting to /", app_user_id, username)
    session["user_id"] = app_user_id
    return redirect("/")


@app.route("/logout")
def logout():
    session.clear()
    if AUTHENTIK_BASE_URL:
        return redirect(f"{AUTHENTIK_BASE_URL}/application/o/discordarchive/end-session/")
    return redirect("/")


@app.route("/api/me")
def api_me():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Not authenticated"}), 401
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT udl.discord_user_id, u.name, u.display_name, u.avatar_hash
        FROM user_discord_links udl
        JOIN users u ON u.id = udl.discord_user_id
        WHERE udl.app_user_id = %s
        """,
        (user["id"],),
    )
    discord_links = [_row(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify({
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "is_admin": user["is_admin"],
        "discord_links": discord_links,
    })


# ── Static / downloads ────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/admin")
def admin_page():
    """Serve the admin panel. Redirect non-admins before any HTML is sent."""
    user = get_current_user()
    if not user:
        return redirect("/")
    if not user["is_admin"]:
        return redirect("/")
    return send_from_directory("static", "admin.html")


@app.route("/settings")
def settings_page():
    user = get_current_user()
    if not user:
        return redirect("/")
    return send_from_directory("static", "settings.html")


@app.route("/downloads/<path:filepath>")
@login_required
def serve_download(filepath):
    return send_from_directory(os.path.abspath(DOWNLOAD_DIR), filepath)


# ── Guilds ────────────────────────────────────────────────────────────────────

@app.route("/api/guilds")
@login_required
def get_guilds():
    user = get_current_user()
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if user["is_admin"]:
        cur.execute(
            "SELECT id, name, icon_hash, description, premium_tier, owner_id FROM guilds ORDER BY name"
        )
    else:
        accessible = _get_accessible_guild_ids(user["id"])
        if not accessible:
            cur.close()
            conn.close()
            return jsonify([])
        cur.execute(
            "SELECT id, name, icon_hash, description, premium_tier, owner_id "
            "FROM guilds WHERE id = ANY(%s) ORDER BY name",
            (accessible,),
        )
    guilds = [_row(g) for g in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(guilds)


# ── Channels ──────────────────────────────────────────────────────────────────

@app.route("/api/guilds/<guild_id>/channels")
@login_required
def get_channels(guild_id):
    user = get_current_user()
    if not _check_guild_access(user, guild_id):
        return jsonify({"error": "Access denied"}), 403
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT c.id, c.name, c.type, c.topic, c.position, c.category_id,
               c.nsfw, c.bitrate, c.user_limit, t.parent_id
        FROM channels c
        LEFT JOIN threads t ON t.id = c.id
        WHERE c.guild_id = %s ORDER BY c.position
        """,
        (guild_id,),
    )
    channels = [_row(c) for c in cur.fetchall()]
    cur.close()
    conn.close()
    if not user["is_admin"]:
        accessible = _get_accessible_channel_ids(user["id"], int(guild_id))
        if accessible is not None:
            accessible_set = set(accessible)
            # Always keep categories (structural); hide restricted channels
            channels = [c for c in channels if c["type"] == "category" or int(c["id"]) in accessible_set]
    return jsonify(channels)


# ── Members ───────────────────────────────────────────────────────────────────

@app.route("/api/guilds/<guild_id>/members")
@login_required
def get_members(guild_id):
    user = get_current_user()
    if not _check_guild_access(user, guild_id):
        return jsonify({"error": "Access denied"}), 403
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT u.id, u.name, u.display_name, u.avatar_hash, u.bot,
               m.nickname, m.joined_at,
               COALESCE(
                   ARRAY_AGG(mr.role_id::TEXT ORDER BY mr.role_id)
                   FILTER (WHERE mr.role_id IS NOT NULL),
                   ARRAY[]::TEXT[]
               ) AS role_ids
        FROM members m
        JOIN users u ON u.id = m.user_id
        LEFT JOIN member_roles mr
            ON mr.user_id = m.user_id AND mr.guild_id = m.guild_id
        WHERE m.guild_id = %s
        GROUP BY u.id, u.name, u.display_name, u.avatar_hash, u.bot,
                 m.nickname, m.joined_at
        ORDER BY u.name
        """,
        (guild_id,),
    )
    members = []
    for row in cur.fetchall():
        d = _row(row)
        d["role_ids"] = list(d.get("role_ids") or [])
        members.append(d)
    cur.close()
    conn.close()
    return jsonify(members)


# ── Roles ─────────────────────────────────────────────────────────────────────

@app.route("/api/guilds/<guild_id>/roles")
@login_required
def get_roles(guild_id):
    user = get_current_user()
    if not _check_guild_access(user, guild_id):
        return jsonify({"error": "Access denied"}), 403
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT id, name, color, position, hoist, mentionable "
        "FROM roles WHERE guild_id = %s ORDER BY position DESC",
        (guild_id,),
    )
    roles = [_row(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(roles)


# ── Messages ──────────────────────────────────────────────────────────────────

@app.route("/api/channels/<channel_id>/messages")
@login_required
def get_messages(channel_id):
    user = get_current_user()
    if not _check_channel_access(user, channel_id):
        return jsonify({"error": "Access denied"}), 403

    before = request.args.get("before")
    limit = min(int(request.args.get("limit", 50)), 100)

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    base_select = """
        SELECT m.id, m.channel_id, m.content, m.created_at, m.edited_at, m.pinned,
               m.mention_everyone, m.type, m.reference_id,
               u.id   AS author_id,
               u.name AS author_name,
               COALESCE(mem.nickname, u.display_name, u.name) AS author_display,
               u.avatar_hash AS author_avatar,
               u.bot  AS author_bot
        FROM messages m
        LEFT JOIN users u ON u.id = m.author_id
        LEFT JOIN members mem ON mem.user_id = m.author_id AND mem.guild_id = m.guild_id
    """
    hidden = _get_hidden_author_ids(user["id"])
    if hidden:
        hidden_clause = (
            " AND (m.author_id IS NULL OR m.author_id != ALL(%s::bigint[]))"
            " AND (m.reference_id IS NULL OR m.reference_id NOT IN ("
            "SELECT id FROM messages WHERE author_id = ANY(%s::bigint[])))"
        )
    else:
        hidden_clause = ""

    if before:
        params = (channel_id, before, limit) if not hidden else (channel_id, before, hidden, hidden, limit)
        cur.execute(
            base_select + f"WHERE m.channel_id = %s AND m.id < %s{hidden_clause} ORDER BY m.id DESC LIMIT %s",
            params,
        )
    else:
        params = (channel_id, limit) if not hidden else (channel_id, hidden, hidden, limit)
        cur.execute(
            base_select + f"WHERE m.channel_id = %s{hidden_clause} ORDER BY m.id DESC LIMIT %s",
            params,
        )

    messages = [_row(m) for m in cur.fetchall()]

    if messages:
        msg_ids = [m["id"] for m in messages]
        cur.execute(
            "SELECT message_id, id, filename, url, proxy_url, width, height, content_type, size "
            "FROM attachments WHERE message_id = ANY(%s::bigint[])",
            (msg_ids,),
        )
        attachments: dict = {}
        for a in cur.fetchall():
            row = _row(a)
            attachments.setdefault(row["message_id"], []).append(row)

        cur.execute(
            "SELECT message_id, emoji_name, emoji_id, count "
            "FROM reactions WHERE message_id = ANY(%s::bigint[])",
            (msg_ids,),
        )
        reactions: dict = {}
        for r in cur.fetchall():
            row = _row(r)
            reactions.setdefault(row["message_id"], []).append(row)

        cur.execute(
            "SELECT id, message_id, title, description, url, color, "
            "footer_text, author_name, image_url, thumbnail_url "
            "FROM embeds WHERE message_id = ANY(%s::bigint[])",
            (msg_ids,),
        )
        embeds: dict = {}
        for e in cur.fetchall():
            row = _row(e)
            embeds.setdefault(row["message_id"], []).append(row)

        for m in messages:
            m["attachments"] = attachments.get(m["id"], [])
            m["reactions"] = reactions.get(m["id"], [])
            m["embeds"] = embeds.get(m["id"], [])

    cur.close()
    conn.close()

    messages.reverse()  # return chronological order
    return jsonify(messages)


# ── Single message ────────────────────────────────────────────────────────────

@app.route("/api/messages/<message_id>")
@login_required
def get_message(message_id):
    user = get_current_user()
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT m.id, m.channel_id, m.content, m.created_at,
               u.id   AS author_id,
               u.name AS author_name,
               COALESCE(mem.nickname, u.display_name, u.name) AS author_display,
               u.avatar_hash AS author_avatar
        FROM messages m
        LEFT JOIN users u ON u.id = m.author_id
        LEFT JOIN members mem ON mem.user_id = m.author_id AND mem.guild_id = m.guild_id
        WHERE m.id = %s
        """,
        (message_id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return jsonify(None), 404
    msg = _row(row)
    if not _check_channel_access(user, msg["channel_id"]):
        return jsonify({"error": "Access denied"}), 403
    return jsonify(msg)


# ── Search ────────────────────────────────────────────────────────────────────

@app.route("/api/guilds/<guild_id>/search")
@login_required
def search_messages(guild_id):
    user = get_current_user()
    if not _check_guild_access(user, guild_id):
        return jsonify({"error": "Access denied"}), 403
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    base_q = """
        SELECT m.id, m.content, m.created_at, m.channel_id,
               c.name AS channel_name,
               COALESCE(mem.nickname, u.display_name, u.name) AS author_display,
               u.avatar_hash AS author_avatar
        FROM messages m
        LEFT JOIN users u ON u.id = m.author_id
        LEFT JOIN channels c ON c.id = m.channel_id
        LEFT JOIN members mem ON mem.user_id = m.author_id AND mem.guild_id = m.guild_id
    """
    hidden = _get_hidden_author_ids(user["id"])
    hidden_clause = (
        " AND (m.author_id IS NULL OR m.author_id != ALL(%s::bigint[]))"
        " AND (m.reference_id IS NULL OR m.reference_id NOT IN ("
        "SELECT id FROM messages WHERE author_id = ANY(%s::bigint[])))"
    ) if hidden else ""

    if user["is_admin"]:
        params = (guild_id, f"%{q}%", hidden, hidden) if hidden else (guild_id, f"%{q}%")
        cur.execute(
            base_q + f"WHERE m.guild_id = %s AND m.content ILIKE %s{hidden_clause} ORDER BY m.created_at DESC LIMIT 50",
            params,
        )
    else:
        accessible = _get_accessible_channel_ids(user["id"], int(guild_id))
        if accessible is None:
            params = (guild_id, f"%{q}%", hidden, hidden) if hidden else (guild_id, f"%{q}%")
            cur.execute(
                base_q + f"WHERE m.guild_id = %s AND m.content ILIKE %s{hidden_clause} ORDER BY m.created_at DESC LIMIT 50",
                params,
            )
        else:
            params = (guild_id, f"%{q}%", accessible, hidden, hidden) if hidden else (guild_id, f"%{q}%", accessible)
            cur.execute(
                base_q + f"WHERE m.guild_id = %s AND m.content ILIKE %s AND m.channel_id = ANY(%s){hidden_clause} ORDER BY m.created_at DESC LIMIT 50",
                params,
            )

    results = [_row(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(results)


# ── Schedule helpers ──────────────────────────────────────────────────────────

def _load_schedules():
    global _schedules
    if SCHEDULES_FILE.exists():
        try:
            with open(SCHEDULES_FILE, "r", encoding="utf-8") as f:
                _schedules = json.load(f)
        except Exception:
            _schedules = []


def _save_schedules():
    with open(SCHEDULES_FILE, "w", encoding="utf-8") as f:
        json.dump(_schedules, f, indent=2, default=str)


def _run_scheduled_clone(guild_id: str, full: bool, skip_downloads: bool):
    with _clone_lock:
        job = _clone_jobs.get(guild_id)
        if job and not job["done"]:
            log.info("Scheduled clone for guild %s skipped — already running", guild_id)
            return
        cmd = [sys.executable, "main.py", "--guild-id", guild_id]
        if full:
            cmd.append("--full-clone")
        if skip_downloads:
            cmd.append("--skip-downloads")
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        job = {
            "process": process,
            "full": full,
            "log": [],
            "done": False,
            "error": None,
            "pid": process.pid,
        }
        _clone_jobs[guild_id] = job

    def _stream(j, proc):
        try:
            for line in proc.stdout:
                with _clone_lock:
                    j["log"].append(line.rstrip())
                    if len(j["log"]) > 500:
                        j["log"] = j["log"][-500:]
            proc.wait()
            with _clone_lock:
                j["done"] = True
                if proc.returncode != 0:
                    j["error"] = f"Exited with code {proc.returncode}"
        except Exception as exc:
            log.exception("Scheduled clone stream thread crashed for guild %s", guild_id)
            with _clone_lock:
                j["done"] = True
                j["error"] = str(exc)

    threading.Thread(target=_stream, args=(job, process), daemon=True).start()
    log.info("Scheduled clone started for guild %s (PID %s)", guild_id, process.pid)


def _scheduler_loop():
    while True:
        try:
            now = datetime.now(timezone.utc)
            with _schedules_lock:
                changed = False
                for sched in _schedules:
                    if not sched.get("enabled", True):
                        continue
                    next_run = sched.get("next_run")
                    if next_run and datetime.fromisoformat(next_run) <= now:
                        guild_id = sched["guild_id"]
                        full = sched.get("full", False)
                        skip_downloads = sched.get("skip_downloads", False)
                        interval_hours = sched.get("interval_hours", 24)
                        sched["last_run"] = now.isoformat()
                        sched["next_run"] = (now + timedelta(hours=interval_hours)).isoformat()
                        changed = True
                        threading.Thread(
                            target=_run_scheduled_clone,
                            args=(guild_id, full, skip_downloads),
                            daemon=True,
                        ).start()
                        log.info("Scheduler triggered clone for guild %s", guild_id)
                if changed:
                    _save_schedules()
        except Exception:
            log.exception("Scheduler loop error")
        time.sleep(60)


# ── Admin helpers ─────────────────────────────────────────────────────────────

def _read_env_file() -> dict:
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" in stripped:
                k, _, v = stripped.partition("=")
                env[k.strip()] = v.strip()
    return env


def _write_env_file(values: dict):
    lines = []
    written_keys: set = set()
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                lines.append(line)
                continue
            if "=" in stripped:
                k = stripped.partition("=")[0].strip()
                if k in values:
                    lines.append(f"{k}={values[k]}")
                    written_keys.add(k)
                else:
                    lines.append(line)
    for k, v in values.items():
        if k not in written_keys:
            lines.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── Admin: config ─────────────────────────────────────────────────────────────

@app.route("/api/admin/config")
@admin_required
def admin_get_config():
    env = _read_env_file()
    return jsonify({k: env.get(k, "") for k in CONFIG_KEYS})


@app.route("/api/admin/config", methods=["POST"])
@admin_required
def admin_set_config():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400
    filtered = {k: str(v) for k, v in data.items() if k in CONFIG_KEYS}
    _write_env_file(filtered)
    load_dotenv(override=True)
    return jsonify({"ok": True})


# ── Admin: guilds list ────────────────────────────────────────────────────────

@app.route("/api/admin/guilds")
@admin_required
def admin_get_guilds():
    """Full guild list for the admin panel (all guilds, not permission-filtered)."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT id, name, icon_hash, description, premium_tier, owner_id FROM guilds ORDER BY name"
    )
    guilds = [_row(g) for g in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(guilds)


# ── Admin: clone ──────────────────────────────────────────────────────────────

@app.route("/api/admin/clone", methods=["POST"])
@admin_required
def admin_start_clone():
    data = request.get_json() or {}
    guild_id = str(data.get("guild_id", "")).strip()
    full = bool(data.get("full", False))
    skip_downloads = bool(data.get("skip_downloads", False))

    if not guild_id:
        return jsonify({"error": "guild_id required"}), 400

    with _clone_lock:
        job = _clone_jobs.get(guild_id)
        if job and not job["done"]:
            return jsonify({"error": "Clone already running for this guild"}), 409

        cmd = [sys.executable, "main.py", "--guild-id", guild_id]
        if full:
            cmd.append("--full-clone")
        if skip_downloads:
            cmd.append("--skip-downloads")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        job = {
            "process": process,
            "full": full,
            "log": [],
            "done": False,
            "error": None,
            "pid": process.pid,
        }
        _clone_jobs[guild_id] = job

    def _stream(j, proc):
        try:
            for line in proc.stdout:
                with _clone_lock:
                    j["log"].append(line.rstrip())
                    if len(j["log"]) > 500:
                        j["log"] = j["log"][-500:]
            proc.wait()
            with _clone_lock:
                j["done"] = True
                if proc.returncode != 0:
                    j["error"] = f"Exited with code {proc.returncode}"
        except Exception as exc:
            log.exception("Clone stream thread crashed for guild %s", guild_id)
            with _clone_lock:
                j["done"] = True
                j["error"] = str(exc)

    threading.Thread(target=_stream, args=(job, process), daemon=True).start()
    return jsonify({"ok": True, "pid": process.pid})


@app.route("/api/admin/clone/<guild_id>/status")
@admin_required
def admin_clone_status(guild_id):
    with _clone_lock:
        job = _clone_jobs.get(str(guild_id))
        if not job:
            return jsonify({"running": False, "log": [], "error": None})
        return jsonify({
            "running": not job["done"],
            "full": job["full"],
            "log": list(job["log"]),
            "error": job["error"],
            "pid": job["pid"],
        })


@app.route("/api/admin/clone/<guild_id>/stop", methods=["POST"])
@admin_required
def admin_stop_clone(guild_id):
    with _clone_lock:
        job = _clone_jobs.get(str(guild_id))
        if not job or job["done"]:
            return jsonify({"error": "No running job"}), 404
        job["process"].terminate()
    return jsonify({"ok": True})


# ── Admin: user management ────────────────────────────────────────────────────

@app.route("/api/admin/users")
@admin_required
def admin_list_users():
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT u.id, u.username, u.email, u.is_admin, u.created_at, u.last_login,
               COALESCE(
                   JSON_AGG(
                       JSON_BUILD_OBJECT(
                           'discord_user_id', udl.discord_user_id::TEXT,
                           'name', du.name,
                           'display_name', du.display_name,
                           'avatar_hash', du.avatar_hash
                       )
                   ) FILTER (WHERE udl.id IS NOT NULL),
                   '[]'::json
               ) AS discord_links
        FROM app_users u
        LEFT JOIN user_discord_links udl ON udl.app_user_id = u.id
        LEFT JOIN users du ON du.id = udl.discord_user_id
        GROUP BY u.id
        ORDER BY u.created_at
        """
    )
    users = []
    for row in cur.fetchall():
        d = dict(row)
        for k in ("created_at", "last_login"):
            if isinstance(d.get(k), datetime):
                d[k] = d[k].isoformat()
        users.append(d)
    cur.close()
    conn.close()
    return jsonify(users)


@app.route("/api/admin/users/<int:user_id>/admin", methods=["POST"])
@admin_required
def admin_set_admin(user_id):
    data = request.get_json() or {}
    is_admin = bool(data.get("is_admin", False))
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "UPDATE app_users SET is_admin = %s WHERE id = %s RETURNING id, username, is_admin",
        (is_admin, user_id),
    )
    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    if not row:
        return jsonify({"error": "User not found"}), 404
    return jsonify(dict(row))


@app.route("/api/admin/users/<int:user_id>/discord-links", methods=["POST"])
@admin_required
def admin_add_discord_link(user_id):
    data = request.get_json() or {}
    discord_user_id = data.get("discord_user_id")
    if not discord_user_id:
        return jsonify({"error": "discord_user_id required"}), 400
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO user_discord_links (app_user_id, discord_user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (user_id, int(discord_user_id)),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({"error": str(e)}), 400
    cur.close()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/admin/users/<int:user_id>/discord-links/<discord_user_id>", methods=["DELETE"])
@admin_required
def admin_remove_discord_link(user_id, discord_user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM user_discord_links WHERE app_user_id = %s AND discord_user_id = %s",
        (user_id, int(discord_user_id)),
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/admin/users/<int:user_id>/guild-access")
@admin_required
def admin_get_guild_access(user_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT g.id::TEXT AS guild_id, g.name, g.icon_hash,
               COALESCE(ugp.can_access, FALSE) AS can_access
        FROM guilds g
        LEFT JOIN user_guild_permissions ugp ON ugp.guild_id = g.id AND ugp.app_user_id = %s
        ORDER BY g.name
        """,
        (user_id,),
    )
    perms = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(perms)


@app.route("/api/admin/users/<int:user_id>/guild-access", methods=["POST"])
@admin_required
def admin_set_guild_access(user_id):
    data = request.get_json() or {}
    guild_id = data.get("guild_id")
    can_access = bool(data.get("can_access", True))
    if not guild_id:
        return jsonify({"error": "guild_id required"}), 400
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO user_guild_permissions (app_user_id, guild_id, can_access)
        VALUES (%s, %s, %s)
        ON CONFLICT (app_user_id, guild_id) DO UPDATE SET can_access = EXCLUDED.can_access
        """,
        (user_id, int(guild_id), can_access),
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/admin/users/<int:user_id>/channel-access/<guild_id>")
@admin_required
def admin_get_channel_access(user_id, guild_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT c.id::TEXT AS channel_id, c.name, c.type, c.category_id::TEXT, c.position,
               ucp.can_access
        FROM channels c
        LEFT JOIN user_channel_permissions ucp ON ucp.channel_id = c.id AND ucp.app_user_id = %s
        WHERE c.guild_id = %s AND c.type NOT IN ('category')
        ORDER BY c.position
        """,
        (user_id, int(guild_id)),
    )
    perms = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(perms)


@app.route("/api/admin/users/<int:user_id>/channel-access", methods=["POST"])
@admin_required
def admin_set_channel_access(user_id):
    data = request.get_json() or {}
    channel_id = data.get("channel_id")
    can_access = data.get("can_access")  # None means "remove restriction"
    if not channel_id:
        return jsonify({"error": "channel_id required"}), 400
    conn = get_db()
    cur = conn.cursor()
    if can_access is None:
        cur.execute(
            "DELETE FROM user_channel_permissions WHERE app_user_id = %s AND channel_id = %s",
            (user_id, int(channel_id)),
        )
    else:
        cur.execute(
            """
            INSERT INTO user_channel_permissions (app_user_id, channel_id, can_access)
            VALUES (%s, %s, %s)
            ON CONFLICT (app_user_id, channel_id) DO UPDATE SET can_access = EXCLUDED.can_access
            """,
            (user_id, int(channel_id), bool(can_access)),
        )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/admin/users/<int:user_id>/hidden-authors")
@admin_required
def admin_get_hidden_authors(user_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT u.id, u.name, u.display_name, u.avatar_hash
        FROM user_hidden_authors uha
        JOIN users u ON u.id = uha.discord_user_id
        WHERE uha.app_user_id = %s
        ORDER BY u.name
        """,
        (user_id,),
    )
    authors = [_row(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(authors)


@app.route("/api/admin/users/<int:user_id>/hidden-authors", methods=["POST"])
@admin_required
def admin_add_hidden_author(user_id):
    data = request.get_json() or {}
    discord_user_id = data.get("discord_user_id")
    if not discord_user_id:
        return jsonify({"error": "discord_user_id required"}), 400
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO user_hidden_authors (app_user_id, discord_user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (user_id, int(discord_user_id)),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({"error": str(e)}), 400
    cur.close()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/admin/users/<int:user_id>/hidden-authors/<discord_user_id>", methods=["DELETE"])
@admin_required
def admin_remove_hidden_author(user_id, discord_user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM user_hidden_authors WHERE app_user_id = %s AND discord_user_id = %s",
        (user_id, int(discord_user_id)),
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/admin/default-permissions")
@admin_required
def admin_get_default_permissions():
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT g.id::TEXT AS guild_id, g.name, g.icon_hash,
               COALESCE(dgp.can_access, TRUE) AS can_access
        FROM guilds g
        LEFT JOIN default_guild_permissions dgp ON dgp.guild_id = g.id
        ORDER BY g.name
        """
    )
    perms = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(perms)


@app.route("/api/admin/default-permissions", methods=["POST"])
@admin_required
def admin_set_default_permission():
    data = request.get_json() or {}
    guild_id = data.get("guild_id")
    can_access = bool(data.get("can_access", True))
    if not guild_id:
        return jsonify({"error": "guild_id required"}), 400
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO default_guild_permissions (guild_id, can_access)
        VALUES (%s, %s)
        ON CONFLICT (guild_id) DO UPDATE SET can_access = EXCLUDED.can_access
        """,
        (int(guild_id), can_access),
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/admin/default-permissions/<int:guild_id>/channels")
@admin_required
def admin_get_default_channel_permissions(guild_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT c.id::TEXT AS channel_id, c.name, c.type, c.category_id::TEXT, c.position,
               dcp.can_access
        FROM channels c
        LEFT JOIN default_channel_permissions dcp ON dcp.channel_id = c.id
        WHERE c.guild_id = %s AND c.type NOT IN ('category')
        ORDER BY c.position
        """,
        (guild_id,),
    )
    perms = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(perms)


@app.route("/api/admin/default-permissions/channel", methods=["POST"])
@admin_required
def admin_set_default_channel_permission():
    data = request.get_json() or {}
    channel_id = data.get("channel_id")
    can_access = data.get("can_access")  # None means remove the override
    if not channel_id:
        return jsonify({"error": "channel_id required"}), 400
    conn = get_db()
    cur = conn.cursor()
    if can_access is None:
        cur.execute(
            "DELETE FROM default_channel_permissions WHERE channel_id = %s",
            (int(channel_id),),
        )
    else:
        cur.execute(
            """
            INSERT INTO default_channel_permissions (channel_id, can_access)
            VALUES (%s, %s)
            ON CONFLICT (channel_id) DO UPDATE SET can_access = EXCLUDED.can_access
            """,
            (int(channel_id), bool(can_access)),
        )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/admin/discord-users/search")
@admin_required
def admin_search_discord_users():
    q = request.args.get("q", "").strip()
    guild_id = request.args.get("guild_id")
    if not q:
        return jsonify([])
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if guild_id:
        cur.execute(
            """
            SELECT DISTINCT u.id, u.name, u.display_name, u.avatar_hash
            FROM users u
            JOIN members m ON m.user_id = u.id
            WHERE m.guild_id = %s AND (u.name ILIKE %s OR u.display_name ILIKE %s)
            ORDER BY u.name LIMIT 20
            """,
            (int(guild_id), f"%{q}%", f"%{q}%"),
        )
    else:
        cur.execute(
            """
            SELECT id, name, display_name, avatar_hash FROM users
            WHERE name ILIKE %s OR display_name ILIKE %s
            ORDER BY name LIMIT 20
            """,
            (f"%{q}%", f"%{q}%"),
        )
    users = [_row(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(users)


# ── Admin: logging ────────────────────────────────────────────────────────────

@app.route("/api/admin/logging")
@admin_required
def admin_get_logging():
    rotated = []
    for i in range(1, 6):
        p = Path(f"web.log.{i}.gz")
        if p.exists():
            rotated.append({"name": p.name, "size": p.stat().st_size})
    log_path = Path("web.log")
    return jsonify({
        "debug": _root.level == logging.DEBUG,
        "current_size": log_path.stat().st_size if log_path.exists() else 0,
        "rotated": rotated,
        "max_bytes": 10 * 1024 * 1024,
        "backup_count": 5,
    })


@app.route("/api/admin/logging", methods=["POST"])
@admin_required
def admin_set_logging():
    data = request.get_json() or {}
    debug = bool(data.get("debug", True))
    _apply_log_level(debug)
    _write_env_file({"LOG_DEBUG": "true" if debug else "false"})
    load_dotenv(override=True)
    log.info("Debug logging %s by admin", "enabled" if debug else "disabled")
    return jsonify({"ok": True, "debug": debug})


@app.route("/api/admin/logging/rotate", methods=["POST"])
@admin_required
def admin_rotate_log():
    _fh.doRollover()
    log.info("Log rotated manually by admin")
    return jsonify({"ok": True})


# ── Admin: schedules ──────────────────────────────────────────────────────────

@app.route("/api/admin/schedules")
@admin_required
def admin_list_schedules():
    with _schedules_lock:
        return jsonify(list(_schedules))


@app.route("/api/admin/schedules", methods=["POST"])
@admin_required
def admin_create_schedule():
    data = request.get_json() or {}
    guild_id = str(data.get("guild_id", "")).strip()
    interval_hours = int(data.get("interval_hours", 24))
    full = bool(data.get("full", False))
    skip_downloads = bool(data.get("skip_downloads", False))
    if not guild_id:
        return jsonify({"error": "guild_id required"}), 400
    if interval_hours < 1:
        return jsonify({"error": "interval_hours must be >= 1"}), 400
    now = datetime.now(timezone.utc)
    sched = {
        "id": str(uuid.uuid4()),
        "guild_id": guild_id,
        "interval_hours": interval_hours,
        "full": full,
        "skip_downloads": skip_downloads,
        "enabled": True,
        "last_run": None,
        "next_run": (now + timedelta(hours=interval_hours)).isoformat(),
        "created_at": now.isoformat(),
    }
    with _schedules_lock:
        _schedules.append(sched)
        _save_schedules()
    return jsonify(sched), 201


@app.route("/api/admin/schedules/<schedule_id>", methods=["DELETE"])
@admin_required
def admin_delete_schedule(schedule_id):
    with _schedules_lock:
        before = len(_schedules)
        _schedules[:] = [s for s in _schedules if s["id"] != schedule_id]
        if len(_schedules) == before:
            return jsonify({"error": "Not found"}), 404
        _save_schedules()
    return jsonify({"ok": True})


@app.route("/api/admin/schedules/<schedule_id>", methods=["PATCH"])
@admin_required
def admin_update_schedule(schedule_id):
    data = request.get_json() or {}
    with _schedules_lock:
        sched = next((s for s in _schedules if s["id"] == schedule_id), None)
        if not sched:
            return jsonify({"error": "Not found"}), 404
        if "enabled" in data:
            sched["enabled"] = bool(data["enabled"])
        if "interval_hours" in data:
            hours = int(data["interval_hours"])
            if hours < 1:
                return jsonify({"error": "interval_hours must be >= 1"}), 400
            sched["interval_hours"] = hours
            sched["next_run"] = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        _save_schedules()
    return jsonify(sched)


# Initialize auth tables and scheduler at startup
_load_schedules()
threading.Thread(target=_scheduler_loop, daemon=True).start()
ensure_auth_tables()

if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, port=5000)

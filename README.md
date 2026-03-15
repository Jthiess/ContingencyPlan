# Contingency Plan

A Discord server backup and archival system. Clones an entire Discord guild into a PostgreSQL database — metadata, roles, members, channels, threads, messages, attachments, emojis, stickers, webhooks, and scheduled events — and serves the archive through a web interface with SSO authentication.

---

## Table of Contents

- [Overview](#overview)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Running the Cloner](#running-the-cloner)
- [Running the Web Server](#running-the-web-server)
- [Deployment](#deployment)
- [Authentication (Authentik SSO)](#authentication-authentik-sso)
- [Scheduled Clones](#scheduled-clones)
- [Project Structure](#project-structure)

---

## Overview

Contingency Plan has two independent components:

| Component | Entry Point | Purpose |
|-----------|-------------|---------|
| **Cloner** | `main.py` | Discord bot that reads guild data and writes it to PostgreSQL |
| **Web Server** | `web_server.py` | Flask app that serves the archived data with authentication |

Both components share the same PostgreSQL database. The cloner and web server can run on the same machine or separately.

---

## Requirements

- **Python 3.10+** (uses union type hints: `X | Y`)
- **PostgreSQL 13+**
- A **Discord bot token** with all privileged gateway intents enabled
- An **Authentik** instance for SSO authentication (required by the web server)

---

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/NerdAler1/ContingencyPlan.git
cd ContingencyPlan
```

### 2. Install dependencies

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows
.venv\Scripts\activate

pip install -r requirements.txt
```

For production, also install the WSGI server:

```bash
pip install gunicorn
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your actual values. See [Configuration](#configuration) for details.

### 4. Initialize the database

```bash
python main.py --init-db
```

This creates all tables defined in `DatabaseSetup.sql`. Safe to run multiple times.

### 5. Run the cloner

```bash
python main.py
```

### 6. Start the web server

```bash
python web_server.py
# or, for production:
gunicorn -w 4 -b 0.0.0.0:5000 "web_server:app"
```

---

## Configuration

All configuration is loaded from a `.env` file in the project root. Copy `.env.example` to `.env` and fill in your values.

### Discord

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_BOT_TOKEN` | Yes | Bot token from the Discord Developer Portal |
| `GUILD_ID` | Yes | Numeric ID of the guild to clone |

**Bot setup:**
1. Create an application at [discord.com/developers](https://discord.com/developers/applications)
2. Add a Bot, copy the token
3. Under **Bot → Privileged Gateway Intents**, enable all three toggles (Presence, Server Members, Message Content)
4. Invite the bot to your guild with sufficient permissions (Read Messages, Read Message History)

### PostgreSQL

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_HOST` | `localhost` | Database host |
| `DB_PORT` | `5432` | Database port |
| `DB_NAME` | `contingencyplan` | Database name |
| `DB_USER` | `postgres` | Database user |
| `DB_PASSWORD` | *(none)* | Database password |
| `DB_SCHEMA` | `public` | PostgreSQL schema |

### Web Server

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | **Yes** | Flask session secret. Generate with: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `BASE_URL` | Yes | Public URL of the web server, e.g. `https://contingency.example.com` |
| `ADMIN_USERS` | No | Comma-separated Authentik usernames with permanent admin rights |

> **Important:** Always set `SECRET_KEY` explicitly. If omitted, a random key is generated at startup, which invalidates all sessions on every restart.

### Authentication (Authentik SSO)

| Variable | Required | Description |
|----------|----------|-------------|
| `AUTHENTIK_BASE_URL` | Yes | Base URL of your Authentik instance, e.g. `https://auth.example.com` |
| `OAUTH_CLIENT_ID` | Yes | OAuth2 client ID from Authentik |
| `OAUTH_CLIENT_SECRET` | Yes | OAuth2 client secret from Authentik |

### Downloads

| Variable | Default | Description |
|----------|---------|-------------|
| `DOWNLOAD_DIR` | `./downloads` | Directory where avatars, attachments, and media are saved |

---

## Running the Cloner

```
python main.py [options]
```

| Flag | Description |
|------|-------------|
| *(none)* | Clone entire server incrementally (skips already-stored messages) |
| `--full-clone` | Wipe all existing guild data and re-clone from scratch |
| `--skip-messages` | Clone everything except message history |
| `--skip-downloads` | Clone data but do not download media files |
| `--init-db` | Initialize the database schema and exit |
| `--guild-id ID` | Override `GUILD_ID` from `.env` |

**Resuming an interrupted clone:** The message fetcher tracks per-channel progress. Re-running `main.py` without `--full-clone` resumes from where it left off.

Logs are written to `clone.log` (rotated, gzip-compressed) and stdout.

---

## Running the Web Server

**Development:**

```bash
python web_server.py
```

**Production (gunicorn):**

```bash
gunicorn -w 4 -b 0.0.0.0:5000 "web_server:app"
```

Logs are written to `web.log` (rotated, gzip-compressed) and stdout.

---

## Deployment

### Systemd (Linux)

Create `/etc/systemd/system/contingency-web.service`:

```ini
[Unit]
Description=Contingency Plan Web Server
After=network.target postgresql.service

[Service]
Type=simple
User=contingency
WorkingDirectory=/opt/contingencyplan
EnvironmentFile=/opt/contingencyplan/.env
ExecStart=/opt/contingencyplan/.venv/bin/gunicorn -w 4 -b 127.0.0.1:5000 "web_server:app"
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now contingency-web
```

### Nginx Reverse Proxy

```nginx
server {
    listen 443 ssl;
    server_name contingency.example.com;

    ssl_certificate     /etc/ssl/certs/contingency.crt;
    ssl_certificate_key /etc/ssl/private/contingency.key;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Serve downloaded media files directly
    location /downloads/ {
        alias /opt/contingencyplan/downloads/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
```

### Running the Cloner on a Schedule

The web server's admin panel supports scheduling clones through its UI. Alternatively, use cron:

```cron
# Incremental clone every 6 hours
0 */6 * * * /opt/contingencyplan/.venv/bin/python /opt/contingencyplan/main.py --skip-downloads >> /var/log/contingency-clone.log 2>&1
```

---

## Authentication (Authentik SSO)

The web server requires Authentik for user authentication. No other SSO provider is supported.

### Authentik Setup

1. In Authentik, go to **Applications → Providers → Create → OAuth2/OpenID Provider**
2. Set the **Redirect URI** to `{BASE_URL}/auth/callback`
3. Copy the **Client ID** and **Client Secret** into your `.env`
4. Create an Application linked to this provider

### Admin Access

- The first user to sign in is automatically granted admin rights
- Additional permanent admins can be listed in `ADMIN_USERS` (comma-separated Authentik usernames)
- Admins can manage user permissions, schedules, and view logs from the admin panel (`/admin`)

### User Permissions

Permissions are managed per-guild and per-channel from the admin panel. By default, authenticated users can see all guilds and channels. Admins can restrict access at guild or channel level.

---

## Scheduled Clones

The admin panel (`/admin`) lets you create recurring clone schedules with configurable intervals and options (full clone, skip downloads, etc.). Schedule state is stored in `schedules.json` in the project root.

---

## Project Structure

```
ContingencyPlan/
├── main.py              # Cloner entry point (Discord bot)
├── web_server.py        # Flask web server
├── config.py            # Environment variable loader
├── db.py                # Async PostgreSQL wrapper (asyncpg)
├── downloader.py        # Rate-limited media downloader
├── DatabaseSetup.sql    # Full PostgreSQL schema
├── requirements.txt     # Python dependencies
├── .env.example         # Environment variable template
├── cloner/              # Cloner submodules
│   ├── __init__.py      # ServerCloner orchestrator
│   ├── channels.py      # Channel and permission cloning
│   ├── guild.py         # Guild metadata, roles, emojis, stickers
│   ├── helpers.py       # Shared utilities
│   ├── members.py       # Member and avatar cloning
│   ├── messages.py      # Message history fetcher
│   └── webhooks.py      # Webhook cloning
└── static/              # Web UI assets
    ├── index.html        # Main archive viewer
    ├── admin.html        # Admin panel
    ├── settings.html     # User settings
    ├── app.js            # Viewer client logic
    ├── admin.js          # Admin panel logic
    └── style.css         # Styles
```

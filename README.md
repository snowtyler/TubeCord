# TubeCord

[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](./VERSION)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

TubeCord monitors a YouTube channel for uploads, livestreams, and community posts, then relays the updates to Discord via webhooks. Real-time events arrive through YouTube WebSub callbacks while community posts are polled and deduplicated locally.

## Highlights
- WebSub-compatible Flask webhook with automatic subscription renewal
- Optional WebSub callback signing with shared secrets
- Discord delivery through rich embeds and optional role mentions per content type
- Community post polling backed by a SQL datastore (SQLite by default) to avoid duplicate sends
- Flexible message templates for uploads, livestreams, and community posts
- Health and diagnostics endpoints for monitoring deployments

## How It Works
1. At startup the app subscribes to the YouTube WebSub hub for the configured channel.
2. Webhook callbacks are validated, parsed, and converted into Discord payloads.
3. The community post scheduler polls YouTube, records seen posts in SQLite, and emits new entries to Discord.
4. Waitress serves the Flask app in production, keeping the webhook endpoint available.
5. SQLAlchemy persists state to SQLite, PostgreSQL, MySQL, or MariaDB depending on `DATABASE_URL`.

## Prerequisites
- Python 3.11 or newer
- A publicly reachable HTTPS endpoint for receiving WebSub callbacks (use ngrok or similar during development)
- Discord webhook URLs for each destination channel
- (Optional) YouTube Data API key to improve livestream detection accuracy

## Quick Start
```bash
git clone https://github.com/snowtyler/TubeCord.git
cd TubeCord
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# edit .env with your channel, webhook, and API credentials
python main.py
```

The service starts the webhook server, registers with YouTube WebSub, and begins forwarding notifications to Discord.

## Configuration

### Environment Variables
The app reads settings from environment variables (or `.env`). The most important options are listed below.

| Variable | Description |
| --- | --- |
| `YOUTUBE_CHANNEL_ID` | Channel ID to monitor (required). |
| `YOUTUBE_API_KEY` | Key used to enhance livestream detection via the YouTube Data API. |
| `CALLBACK_URL` | Public HTTPS URL that YouTube will call for WebSub notifications (required). |
| `CALLBACK_PORT` | Optional port override when the externally visible port differs from the callback URL. |
| `CALLBACK_SECRET` | Optional secret for verifying WebSub signatures. |
| `PORT` | Local port for the Flask server (defaults to 8000). |
| `DATABASE_URL` | SQLAlchemy connection string for the persistence layer (defaults to a local SQLite database under `data/`). |
| `DATABASE_ECHO` | Set to `true` to enable SQLAlchemy statement logging for troubleshooting. |

### Database Configuration
TubeCord uses SQLAlchemy for persistence and supports multiple engines out of the box:
- SQLite (default, file stored under `data/`)
- PostgreSQL (`postgresql+psycopg://` DSN)
- MySQL or MariaDB via the pure-Python PyMySQL driver (`mysql+pymysql://` DSN)

Set the `DATABASE_URL` environment variable to point at your external database. When left unset, the app falls back to the bundled SQLite file. Use `DATABASE_ECHO=true` if you need to inspect generated SQL during troubleshooting.

### Discord Routing
Provide comma-separated lists to target multiple webhooks or roles, keeping the order aligned between URLs and role IDs.

| Variable | Purpose |
| --- | --- |
| `UPLOAD_WEBHOOK_URLS` | Webhooks that receive new upload alerts. |
| `UPLOAD_ROLE_IDS` | Role IDs to mention for uploads (optional). |
| `LIVESTREAM_WEBHOOK_URLS` | Webhooks that receive livestream alerts. |
| `LIVESTREAM_ROLE_IDS` | Role IDs to mention for livestreams (optional). |
| `COMMUNITY_WEBHOOK_URLS` | Webhooks for community post notifications (optional). |
| `COMMUNITY_ROLE_IDS` | Role IDs to mention for community posts (optional). |

### Message Templates
Edit `app/config/messages.py` to tailor the message titles, bodies, and embeds. Each content type can be enabled, disabled, or reformatted without touching the core delivery flow.

## Running Tests
Execute the automated test suite with:
```bash
pytest
```

## Operational Endpoints
- `GET /webhook` verifies WebSub challenges.
- `POST /webhook` accepts YouTube notifications.
- `GET /subscribe` and `GET /unsubscribe` force subscription churn.
- `GET /health` reports subscription status and webhook counts.
- `GET /websub/status` returns detailed subscription metadata.
- `GET /version` exposes the running application version.

## Versioning
The current release number lives in `VERSION` and is surfaced through `app/version.py` and the `/version` endpoint. Changes follow semantic versioning and are tracked in `CHANGELOG.md`.
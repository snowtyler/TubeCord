# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

TubeCord monitors one YouTube channel and relays uploads, livestreams, and community posts to Discord webhooks. Uploads and livestreams arrive in real time via YouTube WebSub (PubSubHubbub) push callbacks; community posts are polled on a schedule and deduplicated in a database. It runs as a Flask app served by Waitress.

Requires Python 3.11+. `context7` MCP is preferred for library/API docs when generating code.

## Commands

```bash
# Setup (Windows / PowerShell)
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env    # then edit credentials

# Run the service (initializes WebSub subscription, starts Waitress on settings.HOST:PORT)
python main.py

# Tests
pytest                                        # whole suite
pytest tests/test_livestream_detection.py     # single file
pytest tests/test_livestream_detection.py::test_livestream_detection  # single test

# Version bump (updates VERSION and app/version.py together)
python bump_version.py [major|minor|patch]
```

Community post scraping shells out to the `yp-dl` CLI (`pip install yp-dl`, pinned in requirements). If `yp-dl` is not on PATH, community polling logs an error and is skipped — the rest of the app still runs.

## Architecture

Data flows through two independent pipelines that converge on the same Discord delivery path.

**WebSub pipeline (uploads / livestreams, real-time):**
1. `main.py` `WebSubSubscriptionManager` subscribes to the YouTube hub at startup and schedules renewal 1 hour before the 5-day lease expires (daemon thread).
2. Flask `/webhook` (in `main.py`) handles both the GET challenge verification and POST notifications. `WebSubHandler` (`app/webhooks/websub.py`) verifies the optional HMAC signature (`CALLBACK_SECRET`), parses the Atom XML, and returns a notification dict (or a `deleted` marker for removed/privated videos).
3. `process_youtube_notification()` in `main.py` is the orchestration hub: builds a `YouTubeNotification`, drops completed livestreams and notifications older than 24h, looks up the per-type config, formats the message, and fans out to every configured Discord server.

**Community pipeline (polled):** `CommunityPostScheduler` (`app/utils/scheduler.py`) runs a background thread every `COMMUNITY_CHECK_INTERVAL_MINUTES`; `CommunityPostScraper` (`app/utils/community_scraper.py`) invokes `yp-dl`, parses its JSON, and persists posts to the DB with a `notified` flag so each post sends once. Only initialized when at least one community webhook is configured.

**Notification classification is API-driven.** `YouTubeNotification._determine_notification_type()` (`app/models/notification.py`) is the core dispatch logic: with a `YOUTUBE_API_KEY` it queries the YouTube Data API (after a 3s propagation delay) and classifies by `liveStreamingDetails` + `liveBroadcastContent` into UPLOAD / LIVESTREAM (upcoming) / LIVESTREAM_LIVE / LIVESTREAM_COMPLETED. Without a key (or on API failure) it falls back to the `live_broadcast_content` value from the WebSub payload. The API call also back-fills `scheduled_start_time` / `actual_start_time` used for Discord timestamps.

**Configuration & routing:**
- `app/config/settings.py` — the single `settings` instance, built from env vars / `.env` at import time. Validates required vars (raises on missing `YOUTUBE_CHANNEL_ID` / `CALLBACK_URL` / no webhooks), parses comma-separated webhook & role lists, normalizes SQLite paths, and clamps the poll interval. Importing this module can raise — that's intentional fail-fast.
- `app/models/discord_config.py` — `DiscordConfiguration.from_settings()` turns the flat env lists into `DiscordServer` objects keyed by content type; `get_servers_for_type()` is what the delivery loop iterates. `DiscordServer` validates that webhook URLs start with `https://discord.com/api/webhooks/`.
- `app/config/messages.py` — `NOTIFICATION_CONFIG` (per-type: `enabled`, `template_type`, `use_rich_embed`) and `MessageTemplates.format_message()`. Edit templates here; no core code changes needed.

**Delivery:** `DiscordClient` (`app/discord/client.py`) enforces a 5 req/sec-per-webhook rate limit and builds embeds. Role mentions render as `<@&role_id>`.

**Persistence:** `app/db/engine.py` `get_engine()` is an `lru_cache`'d SQLAlchemy engine factory driven by `DATABASE_URL` (SQLite default under `data/`; also PostgreSQL via `psycopg`, MySQL/MariaDB via `pymysql`). The community scraper defines its tables (`community_posts`, `channel_handles`) with SQLAlchemy Core. There is no migration framework — tables are created from metadata.

## Operational endpoints (defined in `main.py`)

Beyond `/webhook`: `/health`, `/websub/status`, `/version`, `/config` (dumps Discord + WebSub config), `/subscribe` & `/unsubscribe` (force subscription churn), `/community/status` & `/community/check`, and `/test-notification` / `/test-livestream` / `/test-community` (POST, inject synthetic payloads for end-to-end testing without waiting on YouTube).

## Release process

Follows SemVer; changes tracked in `CHANGELOG.md` (Keep a Changelog format). To release: summarize changes → `python bump_version.py <part>` → update `CHANGELOG.md` under the new version → commit `chore: bump version to X.Y.Z` → `git tag -a vX.Y.Z -m "Release vX.Y.Z"` → `git push origin main --tags`. `VERSION` and `app/version.py` are the source of truth; `.github/workflows/update-readme-badges.yml` refreshes README badges.

## Notes

- `.github/copilot-instructions.md` predates the current layout and references files that no longer exist (`app/discord/formatters.py`, `app/security/`, `app/main.py`). Trust the tree over that doc.
- Local WebSub testing needs a public HTTPS callback — use ngrok and set `CALLBACK_URL`; `GET /ngrok-setup` prints the steps.

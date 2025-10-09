# Copilot Instructions for TubeCord

## Project Overview
TubeCord is a Python-based YouTube channel monitoring service that consumes WebSub (PubSubHubbub) notifications for uploads and livestreams, formats the payloads, and delivers Discord webhook messages with configurable role mentions. It also polls for YouTube Community posts and forwards them to Discord when configured. The production entry point runs a Flask app behind the Waitress WSGI server.

## Architecture Guidelines

### Core Components
- **WebSub Webhook Server**: Flask blueprint (`app/webhooks/websub.py`) handling subscription management, challenge verification, and notification parsing
- **Discord Integration**: Discord webhook client with rich-embed formatting defined in `app/discord`
- **Configuration System**: Settings loader (`app/config/settings.py`) and message templates (`app/config/messages.py`)
- **Community Post Monitor**: Scheduler and scraper in `app/utils` that poll community posts and dispatch Discord notifications

### Technology Stack
- **Runtime**: Python 3.11+
- **Web Server**: Flask served by Waitress in production (`main.py`)
- **YouTube Integration**: WebSub subscription flow plus YouTube API helpers inside the notification models
- **Discord**: Webhook-based delivery with role mentions and optional rich embeds
- **Storage**: SQLite used by the community post scraper for deduplication and history tracking

## Development Patterns

### Project Structure
```
app/
├── webhooks/
│   └── websub.py            # WebSub webhook handler, verification, and parsing
├── discord/
│   ├── client.py            # Discord webhook client
│   └── formatters.py        # Message template formatting
├── config/
│   ├── messages.py          # Discord message templates and configurations
│   └── settings.py          # Environment variable loading
├── models/
│   ├── notification.py      # YouTube notification data models
│   └── discord_config.py    # Discord server/role configuration
├── utils/
│   ├── logging.py           # Structured logging setup
│   ├── community_scraper.py # Community post scraping and persistence
│   └── scheduler.py         # Community post polling scheduler
├── security/                # Placeholder for future auth/signed request helpers
└── main.py                  # Flask application entry point and Waitress bootstrap
```

### Configuration Management
- **Environment Variables**: `CALLBACK_URL`, `YOUTUBE_CHANNEL_ID`, Discord webhook URLs/role IDs per content type, logging level, and optional community poll interval
- **Message Config File**: Template strings for uploads, livestreams (scheduled/live), and community posts in `app/config/messages.py`
- **Local Overrides**: `.env` supported via `python-dotenv`; production relies on hosting-platform environment variables
- **WebSub Subscription**: Automatic subscription renewal and challenge verification handled in `main.py`
- **SQLite Storage**: Community scraper persists state in an embedded SQLite database under `data/`

### Error Handling
- WebSub subscription failures with retry logic
- Discord webhook rate limiting (5 requests per second per webhook)
- Graceful handling of malformed YouTube notifications
- Comprehensive logging for WebSub challenge verification issues

### Data Flow (WebSub-based)
1. **Subscription Setup**: Subscribe to YouTube channel's WebSub hub
2. **Challenge Verification**: Handle WebSub verification challenges
3. **Push Notifications**: Receive real-time notifications from YouTube
4. **Message Formatting**: Apply configured templates with video metadata
5. **Discord Delivery**: Send formatted embeds with role mentions to specified channels

## Key Integration Points

### WebSub (PubSubHubbub) Protocol
- **Hub URL**: `https://pubsubhubbub.appspot.com/subscribe` for YouTube
- **Topic URL**: `https://www.youtube.com/xml/feeds/videos.xml?channel_id={CHANNEL_ID}`
- **Callback URL**: Set via `CALLBACK_URL` environment variable for receiving notifications
- **Challenge Verification**: Echo back hub.challenge parameter during subscription
- **Subscription Renewal**: Handle lease expiration and automatic resubscription

### Discord Webhooks
- **Rich Embeds**: Video thumbnails, titles, descriptions, and channel branding
- **Role Mentions**: Configurable role pings per server using role IDs from environment
- **Rate Limiting**: 5 requests per second per webhook URL (not per bot)
- **Message Templates**: Separate config file for customizable notification formats

### Simplified Hosting Deployment
- **Auto-Deploy**: Hosting service automatically pulls from GitHub repo and runs main script
- **Callback Configuration**: WebSub callback URL is configured via `CALLBACK_URL` environment variable
- **Environment Variables**: Load sensitive data (webhook URLs, role IDs) from hosting platform env
- **No Container Control**: No access to port mapping or volume mounts - use embedded SQLite

## Development Workflow
- **Local Development**: Use ngrok or similar for WebSub callback testing
- **Environment Setup**: `.env` file for local development, hosting platform env for production
- **Database Migrations**: SQLite schema for subscription tracking and notification history
- **Deployment**: Push to GitHub repo triggers automatic deployment on hosting service
- **Entry Point**: Ensure main script can run standalone without Docker wrapper

## Versioning
- **Semantic Versioning**: Follows Semantic Versioning 2.0.0 (MAJOR.MINOR.PATCH)
  - **MAJOR**: Breaking changes or significant rewrites
  - **MINOR**: New features, backward-compatible
  - **PATCH**: Bug fixes, backward-compatible
- **Version File**: `VERSION` in project root contains current version
- **Changelog**: `CHANGELOG.md` documents all notable changes following Keep a Changelog format
- **Version Module**: `app/version.py` provides programmatic access to version info
- **Version Endpoints**: `/version` and `/health` expose version information
- **Release Process**:
  1. Check and summarize every change made since the last release
  2. Run `bump_version.py` with the argument major, minor or patch depending on the update needed
  3. Update `CHANGELOG.md` with changes under new version heading
  4. Update `app/version.py` `__version__` to match
  5. Commit changes with message: `chore: bump version to X.Y.Z`
  6. Create git tag: `git tag -a vX.Y.Z -m "Release vX.Y.Z"`
  6. Push to GitHub: `git push origin main --tags`

## Testing Strategy
- **WebSub Simulation**: Mock YouTube hub notifications for integration tests
- **Discord Webhook Testing**: Use Discord test servers to validate message formatting
- **Challenge Verification**: Unit tests for WebSub subscription handshake
- **Production Testing**: Test directly against your production endpoint configured in `CALLBACK_URL`
- **GitHub Integration**: Validate auto-deployment triggers work correctly
- **Test Suite**: Run `pytest` against the `tests/` package (community posts, livestream detection, role mentions, timestamps, YouTube API helpers)

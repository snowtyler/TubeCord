# Changelog

All notable changes to TubeCord will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Upload polling fallback: the channel's recent uploads are polled on a schedule
  (`UPLOAD_CHECK_INTERVAL_MINUTES`, default 15) via the YouTube Data API and
  anything WebSub push missed is delivered anyway — resilience against the
  ongoing YouTube WebSub delivery degradation (Google Issue Tracker 554905105).
  A shared `notified_videos` table deduplicates across the push and poll paths so
  a video is never sent twice; only uploads newer than `UPLOAD_MAX_AGE_HOURS`
  (default 48) are announced so a long backlog isn't blasted. New `/upload/status`
  and `/upload/check` endpoints and dashboard tile. (The public RSS feed is not
  used — it returns an empty placeholder to server-side requests.)
- WebSub subscription resilience: failed subscribe requests are retried with
  exponential backoff + jitter (rides out the hub's intermittent HTTP 503s), and
  a background watchdog re-subscribes when the subscription is unverified, near
  lease expiry, or was accepted but never verified — so a single flaky-hub moment
  can no longer silently stop delivery for days.
- Operator dashboard at `/` and `/dashboard`, and separate test destinations
  (`TEST_*_WEBHOOK_URLS`) so `/test-*` injections never post to public channels.

### Changed
- `subscription_active` now means "last subscribe accepted"; a new
  `subscription_confirmed` flag reflects actual hub verification. `/health` and
  `/websub/status` expose it, and lease-expiry math is anchored on the last
  *verified* subscription instead of the last subscribe request.

### Fixed
- Subscription could be reported active while actually expired, because the
  active flag was set on the hub's `202` accept before verification completed.
- `/test-*` injections were delivered to production webhooks and dropped by the
  recency gate; they now route to test channels and always deliver.

### Removed
- Cloudflare Tunnel bootstrap (`TUNNEL_MODE`/`TUNNEL_TOKEN`). Investigation
  confirmed the outage was Google's hub, not the callback transport — HTTP works
  fine — so the tunnel added failure modes (quick-tunnel 530s / URL churn)
  without value. Point `CALLBACK_URL` directly at your reachable HTTP/HTTPS host.

## [1.0.0] - 2025-10-08

### Added
- TubeCord initial release

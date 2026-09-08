# Changelog

All notable changes to TubeCord will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- WebSub subscription resilience: failed subscribe requests are now retried with
  exponential backoff + jitter (rides out the hub's intermittent HTTP 503s), and
  a background watchdog re-subscribes when the subscription is unverified, near
  lease expiry, or was accepted but never verified — so a single flaky-hub moment
  can no longer silently stop delivery for days.
- Optional Cloudflare Tunnel bootstrap (`TUNNEL_MODE=quick|named`) to serve the
  WebSub callback over HTTPS on hosts that only expose plain HTTP / a non-standard
  port. `cloudflared` is auto-downloaded; quick tunnels are re-subscribed on each
  restart. New env vars documented in `.env.example`.

### Changed
- `subscription_active` now means "last subscribe accepted"; a new
  `subscription_confirmed` flag reflects actual hub verification. `/health` and
  `/websub/status` expose it, and lease-expiry math is anchored on the last
  *verified* subscription instead of the last subscribe request.

### Fixed
- Subscription could be reported active while actually expired, because the
  active flag was set on the hub's `202` accept before verification completed.

## [1.0.0] - 2025-10-08

### Added
- TubeCord initial release

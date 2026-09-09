"""Tests for the upload polling fallback: API parsing, dedup, and catch-up window."""

from datetime import datetime, timedelta, timezone

import main
from sqlalchemy import create_engine

from app.utils.upload_poller import UploadFeedScraper, UploadNotificationStore


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _api_payload(videos):
    return {"items": [
        {"snippet": {"title": t, "videoOwnerChannelId": c, "videoOwnerChannelTitle": "Chan"},
         "contentDetails": {"videoId": v, "videoPublishedAt": p}}
        for (v, c, t, p) in videos
    ]}


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = ""

    def json(self):
        return self._payload


def _store():
    return UploadNotificationStore(engine=create_engine("sqlite://"))


def test_api_parsing(monkeypatch):
    ch = "UCTESTCHANNELIDAAAAAAAA"
    payload = _api_payload([
        ("VIDNEWEST001", ch, "Newest", "2026-09-09T10:00:00Z"),
        ("VIDOLDER0002", ch, "Older", "2026-09-08T10:00:00Z"),
    ])
    monkeypatch.setattr("app.utils.upload_poller.requests.get", lambda *a, **k: _Resp(payload))
    scraper = UploadFeedScraper(api_key="test-key")
    entries = scraper.fetch_entries(ch)
    assert [e["video_id"] for e in entries] == ["VIDNEWEST001", "VIDOLDER0002"]
    assert entries[0]["title"] == "Newest"
    assert entries[0]["url"] == "https://www.youtube.com/watch?v=VIDNEWEST001"


def test_uploads_playlist_id():
    assert UploadFeedScraper._uploads_playlist_id("UCabc123") == "UUabc123"
    assert UploadFeedScraper._uploads_playlist_id("bogus") is None


def test_store_dedup(monkeypatch):
    store = _store()
    assert store.seen("VID1") is False
    assert store.mark("VID1", "UCx", "t", "poll") is True
    assert store.mark("VID1", "UCx", "t", "poll") is False  # idempotent
    assert store.seen("VID1") is True
    assert store.count() == 1


def test_poll_delivers_recent_skips_old_and_dedupes(monkeypatch):
    ch = main.settings.YOUTUBE_CHANNEL_ID
    store = _store()
    monkeypatch.setattr(main, "upload_notification_store", store)
    monkeypatch.setattr(main, "upload_feed_scraper", UploadFeedScraper(api_key="k"))
    monkeypatch.setattr(main.settings, "UPLOAD_MAX_AGE_HOURS", 48)

    now = datetime.now(timezone.utc)
    recent = _iso(now - timedelta(hours=1))     # within window -> deliver
    old = _iso(now - timedelta(hours=100))       # outside window -> record silently
    payload = _api_payload([
        ("VIDRECENT001", ch, "Recent upload", recent),
        ("VIDOLD000002", ch, "Old backlog", old),
    ])
    monkeypatch.setattr("app.utils.upload_poller.requests.get", lambda *a, **k: _Resp(payload))

    sent = []
    monkeypatch.setattr(main.discord_client, "send_youtube_notification",
                        lambda webhook_url, **kw: sent.append(kw.get("notification_data", {}).get("video_id")) or True)

    from app.models.discord_config import DiscordConfiguration
    cfg = DiscordConfiguration()
    cfg.add_server("https://discord.com/api/webhooks/1/t", [], "upload", "u")
    monkeypatch.setattr(main, "discord_config", cfg)
    # Skip the classification API call: build the notification directly.
    monkeypatch.setattr(main.YouTubeNotification, "from_websub_data",
                        classmethod(lambda cls, d: cls(
                            video_id=d["video_id"], channel_id=d["channel_id"], title=d["title"],
                            author=d.get("author", "a"), url=d["url"],
                            published=d.get("published"), updated=d.get("updated"))))

    delivered = main._poll_uploads_once()
    assert delivered == 1
    assert sent == ["VIDRECENT001"]          # recent delivered
    assert store.seen("VIDRECENT001") is True
    assert store.seen("VIDOLD000002") is True  # old one recorded, not sent

    # Second poll: nothing new -> nothing sent (deduped).
    sent.clear()
    assert main._poll_uploads_once() == 0
    assert sent == []

"""Tests for the upload polling fallback: feed parsing, seeding, and dedup."""

import main
from sqlalchemy import create_engine

from app.utils.upload_poller import UploadFeedScraper, UploadNotificationStore

SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
  <title>YouTube video feed</title>
  <entry>
    <id>yt:video:VID000000001</id>
    <yt:videoId>VID000000001</yt:videoId>
    <yt:channelId>UCTESTCHANNELIDAAAAAAAA</yt:channelId>
    <title>Newest video</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=VID000000001"/>
    <author><name>Test Author</name></author>
    <published>2026-09-08T10:00:00+00:00</published>
    <updated>2026-09-08T10:05:00+00:00</updated>
  </entry>
  <entry>
    <id>yt:video:VID000000002</id>
    <yt:videoId>VID000000002</yt:videoId>
    <yt:channelId>UCTESTCHANNELIDAAAAAAAA</yt:channelId>
    <title>Older video</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=VID000000002"/>
    <author><name>Test Author</name></author>
    <published>2026-09-07T10:00:00+00:00</published>
    <updated>2026-09-07T10:05:00+00:00</updated>
  </entry>
</feed>"""


class _Resp:
    def __init__(self, content):
        self.content = content.encode("utf-8")

    def raise_for_status(self):
        pass


def _store():
    # Isolated in-memory DB per test.
    return UploadNotificationStore(engine=create_engine("sqlite://"))


def test_feed_parsing(monkeypatch):
    scraper = UploadFeedScraper()
    monkeypatch.setattr("app.utils.upload_poller.requests.get", lambda *a, **k: _Resp(SAMPLE_FEED))
    entries = scraper.fetch_entries("UCTESTCHANNELIDAAAAAAAA")
    assert [e["video_id"] for e in entries] == ["VID000000001", "VID000000002"]
    assert entries[0]["title"] == "Newest video"
    assert entries[0]["author"] == "Test Author"
    assert entries[0]["url"] == "https://www.youtube.com/watch?v=VID000000001"
    assert entries[0]["channel_id"] == "UCTESTCHANNELIDAAAAAAAA"


def test_seed_then_dedup(monkeypatch):
    store = _store()
    ch = "UCTESTCHANNELIDAAAAAAAA"
    assert store.is_seeded(ch) is False

    entries = [
        {"video_id": "VID000000001", "channel_id": ch, "title": "a"},
        {"video_id": "VID000000002", "channel_id": ch, "title": "b"},
    ]
    store.seed(ch, entries)
    assert store.is_seeded(ch) is True
    assert store.seen("VID000000001") is True
    assert store.seen("VID000000002") is True
    assert store.seen("VID999999999") is False

    # mark is idempotent (no duplicate-key crash)
    assert store.mark("VID000000003", ch, "c", "poll") is True
    assert store.mark("VID000000003", ch, "c", "poll") is False
    assert store.seen("VID000000003") is True


def test_poll_seeds_first_run_then_delivers_new(monkeypatch):
    ch = main.settings.YOUTUBE_CHANNEL_ID
    store = _store()
    monkeypatch.setattr(main, "upload_notification_store", store)
    monkeypatch.setattr(main, "upload_feed_scraper", UploadFeedScraper())

    feed = {"content": SAMPLE_FEED.replace("UCTESTCHANNELIDAAAAAAAA", ch)}
    monkeypatch.setattr("app.utils.upload_poller.requests.get",
                        lambda *a, **k: _Resp(feed["content"]))

    sent = []
    monkeypatch.setattr(main.discord_client, "send_youtube_notification",
                        lambda webhook_url, **kw: sent.append(kw.get("notification_data", {}).get("video_id")) or True)
    # Ensure there's an upload destination and no API calls during classify.
    from app.models.discord_config import DiscordConfiguration
    cfg = DiscordConfiguration()
    cfg.add_server("https://discord.com/api/webhooks/1/t", [], "upload", "u")
    monkeypatch.setattr(main, "discord_config", cfg)
    monkeypatch.setattr(main.YouTubeNotification, "from_websub_data",
                        classmethod(lambda cls, d: cls(
                            video_id=d["video_id"], channel_id=d["channel_id"], title=d["title"],
                            author=d.get("author", "a"), url=d["url"],
                            published=d.get("published"), updated=d.get("updated"))))

    # First run: seeds, sends nothing.
    assert main._poll_uploads_once() == 0
    assert sent == []
    assert store.is_seeded(ch) is True

    # A brand-new upload appears at the top of the feed.
    new_feed = feed["content"].replace(
        "<yt:videoId>VID000000001</yt:videoId>", "<yt:videoId>VIDNEW00000A</yt:videoId>")
    new_feed = new_feed.replace("watch?v=VID000000001", "watch?v=VIDNEW00000A")
    feed["content"] = new_feed

    delivered = main._poll_uploads_once()
    assert delivered == 1
    assert sent == ["VIDNEW00000A"]

    # Running again delivers nothing (deduped).
    sent.clear()
    assert main._poll_uploads_once() == 0
    assert sent == []

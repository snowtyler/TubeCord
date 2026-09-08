"""Tests: /test-* injections route to the test channel, never to production,
and are not dropped by the recency/completed gates."""

import main
from app.models.discord_config import DiscordConfiguration
from app.models.notification import NotificationType

PROD = "https://discord.com/api/webhooks/1000/prod-token"
TESTCH = "https://discord.com/api/webhooks/2000/test-token"


def _configs():
    prod = DiscordConfiguration()
    prod.add_server(PROD, [], "upload", "prod-upload")
    prod.add_server(PROD, [], "livestream", "prod-live")
    test = DiscordConfiguration()
    test.add_server(TESTCH, [], "upload", "test-upload")
    test.add_server(TESTCH, [], "livestream", "test-live")
    return prod, test


def test_test_upload_routes_to_test_channel_only(monkeypatch):
    prod, test = _configs()
    monkeypatch.setattr(main, "discord_config", prod)
    monkeypatch.setattr(main, "test_discord_config", test)

    sent = []
    monkeypatch.setattr(main.discord_client, "send_youtube_notification",
                        lambda webhook_url, **kw: sent.append(webhook_url) or True)

    client = main.app.test_client()
    resp = client.post("/test-notification")
    assert resp.status_code == 200
    assert sent == [TESTCH]          # only the test channel
    assert PROD not in sent          # never production


def test_test_livestream_routes_to_test_and_classifies_as_livestream(monkeypatch):
    prod, test = _configs()
    monkeypatch.setattr(main, "discord_config", prod)
    monkeypatch.setattr(main, "test_discord_config", test)

    seen = []
    def fake_send(webhook_url, notification_type=None, **kw):
        seen.append((webhook_url, notification_type))
        return True
    monkeypatch.setattr(main.discord_client, "send_youtube_notification", fake_send)

    resp = main.app.test_client().post("/test-livestream")
    assert resp.status_code == 200
    assert seen == [(TESTCH, "livestream")]


def test_test_upload_fails_cleanly_when_no_test_webhook(monkeypatch):
    prod, _ = _configs()
    monkeypatch.setattr(main, "discord_config", prod)
    monkeypatch.setattr(main, "test_discord_config", DiscordConfiguration())  # empty

    sent = []
    monkeypatch.setattr(main.discord_client, "send_youtube_notification",
                        lambda webhook_url, **kw: sent.append(webhook_url) or True)

    resp = main.app.test_client().post("/test-notification")
    assert resp.status_code == 500          # explicit failure, not a silent prod post
    assert sent == []


def test_process_bypasses_recency_gate_in_test_mode(monkeypatch):
    _, test = _configs()
    monkeypatch.setattr(main, "test_discord_config", test)
    sent = []
    monkeypatch.setattr(main.discord_client, "send_youtube_notification",
                        lambda webhook_url, **kw: sent.append(webhook_url) or True)

    # A deliberately ancient published date would be skipped in normal mode.
    data = {
        "video_id": "v1", "channel_id": "c1", "title": "old", "author": "a",
        "url": "https://youtu.be/v1", "published": "2000-01-01T00:00:00Z",
        "updated": "2000-01-01T00:00:00Z",
    }
    ok = main.process_youtube_notification(
        data, config_source=test, test_mode=True, force_type=NotificationType.UPLOAD)
    assert ok is True
    assert sent == [TESTCH]

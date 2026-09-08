"""Tests for WebSub subscription resilience: watchdog renewal + retry/backoff."""

from datetime import datetime, timezone, timedelta

import main
from app.config.settings import settings


def _mgr():
    return main.WebSubSubscriptionManager()


def test_needs_renewal_when_never_verified():
    mgr = _mgr()
    needed, reason = mgr._needs_renewal()
    assert needed is True
    assert "no verified" in reason


def test_no_renewal_when_freshly_verified():
    mgr = _mgr()
    mgr.last_verification_time = datetime.now(timezone.utc)
    needed, _ = mgr._needs_renewal()
    assert needed is False


def test_renewal_when_verified_lease_near_expiry():
    mgr = _mgr()
    # Verified so long ago that we're inside the renewal lead window.
    age = settings.WEBSUB_LEASE_SECONDS - settings.WEBSUB_RENEWAL_LEAD_SECONDS + 60
    mgr.last_verification_time = datetime.now(timezone.utc) - timedelta(seconds=age)
    needed, reason = mgr._needs_renewal()
    assert needed is True
    assert "expiry" in reason


def test_renewal_when_accepted_but_never_verified():
    mgr = _mgr()
    now = datetime.now(timezone.utc)
    # Verified a while ago, then a newer subscribe was accepted but not verified.
    mgr.last_verification_time = now - timedelta(hours=1)
    mgr.last_subscription_time = now - timedelta(seconds=settings.WEBSUB_VERIFY_TIMEOUT_SECONDS + 30)
    needed, reason = mgr._needs_renewal()
    assert needed is True
    assert "never verified" in reason


def test_subscribe_with_retry_succeeds_after_transient_failures(monkeypatch):
    mgr = _mgr()
    # Make backoff effectively instant.
    monkeypatch.setattr(settings, "WEBSUB_SUBSCRIBE_MAX_RETRIES", 4)
    monkeypatch.setattr(settings, "WEBSUB_SUBSCRIBE_RETRY_BASE_SECONDS", 0)
    monkeypatch.setattr(settings, "WEBSUB_SUBSCRIBE_RETRY_MAX_SECONDS", 0)

    calls = {"n": 0}

    def fake_subscribe():
        calls["n"] += 1
        return calls["n"] >= 3  # fail twice (simulated 503), then succeed

    monkeypatch.setattr(mgr, "subscribe_to_channel", fake_subscribe)
    assert mgr.subscribe_with_retry() is True
    assert calls["n"] == 3


def test_subscribe_with_retry_gives_up_after_max(monkeypatch):
    mgr = _mgr()
    monkeypatch.setattr(settings, "WEBSUB_SUBSCRIBE_MAX_RETRIES", 3)
    monkeypatch.setattr(settings, "WEBSUB_SUBSCRIBE_RETRY_BASE_SECONDS", 0)
    monkeypatch.setattr(settings, "WEBSUB_SUBSCRIBE_RETRY_MAX_SECONDS", 0)

    calls = {"n": 0}

    def always_fail():
        calls["n"] += 1
        return False

    monkeypatch.setattr(mgr, "subscribe_to_channel", always_fail)
    assert mgr.subscribe_with_retry() is False
    assert calls["n"] == 3


def test_stop_short_circuits_retry(monkeypatch):
    mgr = _mgr()
    monkeypatch.setattr(settings, "WEBSUB_SUBSCRIBE_MAX_RETRIES", 5)
    monkeypatch.setattr(mgr, "subscribe_to_channel", lambda: False)
    mgr.stop()  # signal shutdown before we start
    assert mgr.subscribe_with_retry() is False


def test_subscribe_records_attempt_time_even_on_failure(monkeypatch):
    """Regression: `datetime` must resolve at module scope (no local shadowing)."""
    import requests as _requests

    mgr = _mgr()

    def boom(*a, **k):
        raise _requests.exceptions.RequestException("blocked")

    monkeypatch.setattr(main.requests, "post", boom)
    assert mgr.subscribe_to_channel() is False           # no UnboundLocalError
    assert mgr.last_subscribe_attempt_time is not None    # reached datetime.now()


def test_get_challenge_confirms_subscription():
    """A hub GET challenge is echoed and marks the subscription confirmed."""
    client = main.app.test_client()
    resp = client.get('/webhook', query_string={
        'hub.mode': 'subscribe',
        'hub.topic': settings.youtube_topic_url,
        'hub.challenge': 'CHAL42',
        'hub.lease_seconds': '432000',
    })
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == 'CHAL42'
    assert main.subscription_manager.subscription_confirmed is True

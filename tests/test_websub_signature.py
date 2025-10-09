"""Tests for WebSub signature verification."""

import hashlib
import hmac

from app.webhooks.websub import WebSubHandler


def test_verify_signature_valid_sha256():
    handler = WebSubHandler()
    secret = "supersecret"
    body = b"<xml>payload</xml>"
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    headers = {"X-Hub-Signature-256": f"sha256={signature}"}

    assert handler.verify_signature(headers, body, secret) is True


def test_verify_signature_invalid_signature():
    handler = WebSubHandler()
    secret = "supersecret"
    body = b"<xml>payload</xml>"
    headers = {"X-Hub-Signature-256": "sha256=deadbeef"}

    assert handler.verify_signature(headers, body, secret) is False


def test_verify_signature_missing_header():
    handler = WebSubHandler()
    secret = "supersecret"
    body = b"<xml>payload</xml>"

    assert handler.verify_signature({}, body, secret) is False


def test_verify_signature_no_secret():
    handler = WebSubHandler()
    body = b"<xml>payload</xml>"

    assert handler.verify_signature({}, body, "") is True

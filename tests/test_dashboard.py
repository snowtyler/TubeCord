"""Tests for the operator WebSub dashboard page."""

import main
from app.web.dashboard import render_dashboard


def test_render_dashboard_injects_version():
    html = render_dashboard("9.9.9")
    assert "v9.9.9" in html
    assert "__VERSION__" not in html


def test_dashboard_routes_serve_html():
    client = main.app.test_client()
    for path in ("/", "/dashboard"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.headers["Content-Type"].startswith("text/html")
        body = resp.get_data(as_text=True)
        assert "WebSub Dashboard" in body
        # references the endpoints it drives
        assert "/websub/status" in body
        assert "/subscribe" in body
        assert "/unsubscribe" in body

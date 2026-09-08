"""Tests for the Cloudflare Tunnel helper."""

import app.utils.tunnel as tunnel


def test_quick_tunnel_url_regex_matches_cloudflared_banner():
    line = (
        "2026-09-08T18:00:00Z INF +--------------------------------------+ "
        "|  https://random-words-here.trycloudflare.com  | +----------------+"
    )
    match = tunnel._TRYCLOUDFLARE_RE.search(line)
    assert match is not None
    assert match.group(0) == "https://random-words-here.trycloudflare.com"


def test_quick_tunnel_url_regex_ignores_unrelated_lines():
    assert tunnel._TRYCLOUDFLARE_RE.search("INF Registered tunnel connection") is None


def test_asset_name_is_known_for_this_platform():
    # On CI/dev (linux/windows amd64/arm64) an asset must resolve.
    asset = tunnel._asset_name()
    assert asset is None or asset.startswith("cloudflared-")


def test_manager_start_is_noop_when_disabled():
    mgr = tunnel.TunnelManager(mode="off", local_port=26970)
    assert mgr.start() is None


def test_named_mode_requires_token():
    mgr = tunnel.TunnelManager(mode="named", local_port=26970, token="")
    assert mgr.start() is None

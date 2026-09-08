"""Optional Cloudflare Tunnel bootstrap.

Runs a ``cloudflared`` tunnel as a child process so the WebSub callback can be
served over HTTPS on port 443 even when the host only exposes a plain-HTTP,
non-standard port (e.g. a Pterodactyl/BisectHosting Python egg that can launch
only a single Python file). The binary is auto-downloaded if not present.

Two modes (see ``TUNNEL_MODE`` in settings):

* ``quick`` — ephemeral ``https://<random>.trycloudflare.com`` tunnel. The URL
  is discovered from ``cloudflared`` output at startup and changes on every
  restart; the caller re-subscribes with the new URL each boot.
* ``named`` — a stable named tunnel driven by ``TUNNEL_TOKEN``; the hostname is
  configured in the Cloudflare dashboard, so the caller keeps using the
  configured ``CALLBACK_URL``.

Everything here is best-effort: any failure is logged and surfaced as ``None``
so the application can fall back to the configured callback instead of crashing.
"""

from __future__ import annotations

import os
import platform
import re
import stat
import subprocess
import threading
import time
from typing import Callable, Optional

import requests

from app.utils.logging import get_logger

logger = get_logger(__name__)

_TRYCLOUDFLARE_RE = re.compile(r"https://[-a-z0-9]+\.trycloudflare\.com")
_RELEASE_BASE = "https://github.com/cloudflare/cloudflared/releases/latest/download"


def _asset_name() -> Optional[str]:
    """Return the cloudflared release asset for the current OS/arch, or None."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {
        'x86_64': 'amd64', 'amd64': 'amd64',
        'aarch64': 'arm64', 'arm64': 'arm64',
        'armv7l': 'arm', 'armv6l': 'arm',
        'i386': '386', 'i686': '386',
    }.get(machine)
    if arch is None:
        return None
    if system == 'linux':
        return f"cloudflared-linux-{arch}"
    if system == 'windows':
        # cloudflared ships windows binaries for amd64/386 only.
        return f"cloudflared-windows-{arch if arch in ('amd64', '386') else 'amd64'}.exe"
    # macOS ships a .tgz which needs extraction; ask the user to install it.
    return None


def _download_cloudflared(dest_dir: str) -> Optional[str]:
    """Download the cloudflared binary into ``dest_dir``; return its path."""
    asset = _asset_name()
    if not asset:
        logger.error(
            "No prebuilt cloudflared for %s/%s; install it manually and set "
            "CLOUDFLARED_PATH.", platform.system(), platform.machine())
        return None

    os.makedirs(dest_dir, exist_ok=True)
    target = os.path.join(dest_dir, 'cloudflared.exe' if asset.endswith('.exe') else 'cloudflared')
    url = f"{_RELEASE_BASE}/{asset}"
    try:
        logger.info("Downloading cloudflared from %s", url)
        with requests.get(url, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            tmp = target + '.part'
            with open(tmp, 'wb') as fh:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    if chunk:
                        fh.write(chunk)
            os.replace(tmp, target)
        if not target.endswith('.exe'):
            os.chmod(target, os.stat(target).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        logger.info("cloudflared downloaded to %s", target)
        return target
    except (requests.RequestException, OSError) as exc:
        logger.error("Failed to download cloudflared: %s", exc)
        return None


def _resolve_binary(explicit_path: str = '') -> Optional[str]:
    """Find a usable cloudflared: explicit path, then PATH, then download."""
    from shutil import which

    if explicit_path:
        if os.path.isfile(explicit_path) and os.access(explicit_path, os.X_OK):
            return explicit_path
        logger.warning("CLOUDFLARED_PATH %s is not an executable file", explicit_path)

    on_path = which('cloudflared')
    if on_path:
        return on_path

    return _download_cloudflared(os.path.join(os.getcwd(), 'bin'))


class TunnelManager:
    """Manages a cloudflared child process and (for quick tunnels) its URL."""

    def __init__(
        self,
        mode: str,
        local_port: int,
        token: str = '',
        binary_path: str = '',
        on_url_change: Optional[Callable[[str], None]] = None,
    ):
        self.mode = mode
        self.local_port = local_port
        self.token = token
        self.binary_path = binary_path
        self.on_url_change = on_url_change

        self.public_url: Optional[str] = None
        self._binary: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None
        self._url_event = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # -- process management -------------------------------------------------
    def _command(self) -> list:
        base = [self._binary, 'tunnel', '--no-autoupdate']
        if self.mode == 'named':
            return base + ['run', '--token', self.token]
        # quick tunnel
        return base + ['--url', f'http://127.0.0.1:{self.local_port}']

    def _spawn(self) -> bool:
        try:
            self._proc = subprocess.Popen(
                self._command(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            logger.error("Failed to launch cloudflared: %s", exc)
            return False

        threading.Thread(target=self._read_output, daemon=True).start()
        return True

    def _read_output(self) -> None:
        """Drain cloudflared output and capture the quick-tunnel URL."""
        proc = self._proc
        if not proc or not proc.stdout:
            return
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                logger.debug("cloudflared: %s", line)
            if self.mode == 'quick' and not self._url_event.is_set():
                match = _TRYCLOUDFLARE_RE.search(line)
                if match:
                    new_url = f"{match.group(0)}/webhook"
                    with self._lock:
                        changed = new_url != self.public_url
                        self.public_url = new_url
                    self._url_event.set()
                    logger.info("Quick tunnel URL: %s", new_url)
                    if changed and self.on_url_change:
                        # Fire on restarts (not the very first discovery, which
                        # the caller handles synchronously via start()).
                        try:
                            self.on_url_change(new_url)
                        except Exception as exc:  # noqa: BLE001 - never kill reader
                            logger.error("on_url_change callback failed: %s", exc)

    # -- public API ---------------------------------------------------------
    def start(self, url_timeout: float = 45.0) -> Optional[str]:
        """Start the tunnel. Returns the public callback URL for quick mode,
        or None for named/off (caller keeps the configured CALLBACK_URL)."""
        if self.mode not in {'quick', 'named'}:
            return None
        if self.mode == 'named' and not self.token:
            logger.error("TUNNEL_MODE=named requires TUNNEL_TOKEN; tunnel disabled")
            return None

        self._binary = _resolve_binary(self.binary_path)
        if not self._binary:
            logger.error("cloudflared unavailable; continuing without a tunnel")
            return None

        if not self._spawn():
            return None

        threading.Thread(target=self._monitor, daemon=True).start()

        if self.mode == 'quick':
            if not self._url_event.wait(timeout=url_timeout):
                logger.error("Timed out waiting for quick-tunnel URL after %ss", url_timeout)
                return None
            return self.public_url
        logger.info("Named cloudflared tunnel started")
        return None

    def _monitor(self) -> None:
        """Restart cloudflared if it exits unexpectedly."""
        backoff = 5
        while not self._stop.is_set():
            proc = self._proc
            if proc is None:
                return
            proc.wait()
            if self._stop.is_set():
                return
            logger.warning("cloudflared exited (code %s); restarting in %ss",
                           proc.returncode, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
            # Force re-discovery of the quick URL on restart.
            self._url_event.clear()
            if self._spawn():
                backoff = 5

    def stop(self) -> None:
        self._stop.set()
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass

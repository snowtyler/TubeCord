"""Upload polling fallback.

YouTube's WebSub/PubSubHubbub push pipeline periodically degrades — subscribe
requests 503, verification challenges never arrive, and upload notifications go
missing for hours (Google Issue Tracker 554905105). To stay resilient, TubeCord
polls the channel's public upload feed on a schedule and delivers anything the
push pipeline missed. A shared ``notified_videos`` table deduplicates against the
WebSub path so a video is never announced twice, whichever path sees it first.
"""

import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Callable, List, Dict, Any, Optional

import requests
from sqlalchemy import Column, MetaData, String, Table, Text, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.config.settings import settings
from app.db import get_engine
from app.utils.logging import get_logger

logger = get_logger(__name__)

metadata = MetaData()

# Shared dedup store for delivered/handled videos (WebSub + polling).
notified_videos_table = Table(
    "notified_videos",
    metadata,
    Column("video_id", String(64), primary_key=True),
    Column("channel_id", String(255)),
    Column("title", Text),
    Column("source", String(32)),
    Column("notified_at", String(64)),
)


class UploadNotificationStore:
    """Tracks which videos have already been announced, across both paths."""

    def __init__(self, engine: Optional[Engine] = None):
        self.engine = engine or get_engine(settings.DATABASE_URL, echo=settings.DATABASE_ECHO)
        metadata.create_all(self.engine, checkfirst=True)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _seed_marker(channel_id: str) -> str:
        return f"__seeded__:{channel_id}"

    def seen(self, video_id: str) -> bool:
        stmt = select(notified_videos_table.c.video_id).where(
            notified_videos_table.c.video_id == video_id
        )
        try:
            with self.engine.connect() as conn:
                return conn.execute(stmt).first() is not None
        except SQLAlchemyError as exc:
            logger.error("notified_videos lookup failed: %s", exc)
            return False

    def mark(self, video_id: str, channel_id: str, title: str, source: str) -> bool:
        stmt = notified_videos_table.insert().values(
            video_id=video_id,
            channel_id=channel_id,
            title=title or "",
            source=source,
            notified_at=self._now(),
        )
        try:
            with self.engine.begin() as conn:
                conn.execute(stmt)
            return True
        except IntegrityError:
            return False  # already recorded
        except SQLAlchemyError as exc:
            logger.error("notified_videos insert failed: %s", exc)
            return False

    def is_seeded(self, channel_id: str) -> bool:
        return self.seen(self._seed_marker(channel_id))

    def seed(self, channel_id: str, entries: List[Dict[str, Any]]) -> None:
        """Record the current feed as already-seen so the first poll after a
        fresh install doesn't blast the backlog. New uploads notify normally."""
        for entry in entries:
            self.mark(entry["video_id"], entry.get("channel_id", channel_id),
                      entry.get("title", ""), "seed")
        self.mark(self._seed_marker(channel_id), channel_id, "seed marker", "seed")
        logger.info("Upload poller seeded %d existing videos for %s", len(entries), channel_id)


class UploadFeedScraper:
    """Fetches and parses a channel's public upload Atom feed."""

    FEED_URL = "https://www.youtube.com/xml/feeds/videos.xml?channel_id={cid}"
    NS = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }

    def fetch_entries(self, channel_id: str) -> List[Dict[str, Any]]:
        url = self.FEED_URL.format(cid=channel_id)
        try:
            resp = requests.get(
                url,
                timeout=15,
                headers={
                    # A browser-ish UA avoids YouTube's empty placeholder feed.
                    "User-Agent": "Mozilla/5.0 (compatible; TubeCord/1.0; +https://github.com/snowtyler/TubeCord)",
                    "Accept": "application/atom+xml,application/xml,text/xml",
                },
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Failed to fetch upload feed for %s: %s", channel_id, exc)
            return []

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            logger.error("Failed to parse upload feed for %s: %s", channel_id, exc)
            return []

        entries: List[Dict[str, Any]] = []
        for entry in root.findall("atom:entry", self.NS):
            vid = entry.find("yt:videoId", self.NS)
            cid = entry.find("yt:channelId", self.NS)
            if vid is None or not vid.text or cid is None:
                continue
            title = entry.find("atom:title", self.NS)
            author = entry.find("atom:author/atom:name", self.NS)
            published = entry.find("atom:published", self.NS)
            updated = entry.find("atom:updated", self.NS)
            link = entry.find("atom:link[@rel='alternate']", self.NS)
            entries.append({
                "video_id": vid.text,
                "channel_id": cid.text or channel_id,
                "title": title.text if title is not None else "Unknown Title",
                "author": author.text if author is not None else "Unknown Author",
                "published": published.text if published is not None else None,
                "updated": updated.text if updated is not None else None,
                "url": link.get("href") if link is not None
                       else f"https://www.youtube.com/watch?v={vid.text}",
            })
        return entries


class UploadPollScheduler:
    """Runs ``on_tick`` immediately, then every ``interval_minutes``."""

    def __init__(self, interval_minutes: int, on_tick: Callable[[], None]):
        self.interval_seconds = max(60, interval_minutes * 60)
        self.on_tick = on_tick
        self._stop = threading.Event()
        self.last_check_time: Optional[datetime] = None

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="upload-poller").start()
        logger.info("Upload poll scheduler started (every %ds)", self.interval_seconds)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.last_check_time = datetime.now(timezone.utc)
                self.on_tick()
            except Exception as exc:  # noqa: BLE001 - never kill the poll thread
                logger.error("Upload poll tick failed: %s", exc)
            self._stop.wait(self.interval_seconds)

    def force_check(self) -> None:
        self.on_tick()

    def stop(self) -> None:
        self._stop.set()

"""
Data models for YouTube notifications and processing.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any
import os


class NotificationType(Enum):
    UPLOAD = "upload"
    LIVESTREAM = "livestream"           # Scheduled livestream
    LIVESTREAM_LIVE = "livestream_live"  # Currently live
    LIVESTREAM_COMPLETED = "livestream_completed"  # Ended stream
    COMMUNITY_POST = "community"
    UNKNOWN = "unknown"


@dataclass
class YouTubeNotification:
    video_id: str
    channel_id: str
    title: str
    author: str
    url: str
    published: Optional[str] = None
    updated: Optional[str] = None
    notification_type: NotificationType = NotificationType.UPLOAD
    scheduled_start_time: Optional[str] = None
    actual_start_time: Optional[str] = None

    @classmethod
    def from_websub_data(cls, data: Dict[str, Any]) -> "YouTubeNotification":
        ntype = cls._determine_notification_type(data)
        return cls(
            video_id=data['video_id'],
            channel_id=data['channel_id'],
            title=data['title'],
            author=data['author'],
            url=data['url'],
            published=data.get('published'),
            updated=data.get('updated'),
            notification_type=ntype,
            scheduled_start_time=data.get('scheduled_start_time'),
            actual_start_time=data.get('actual_start_time'),
        )

    @classmethod
    def from_community_post(cls, post_data: Dict[str, Any]) -> "YouTubeNotification":
        return cls(
            video_id=post_data.get('post_id', ''),
            channel_id=post_data['channel_id'],
            title=post_data.get('title', f"Community Post from {post_data.get('author', 'Unknown')}"),
            author=post_data['author'],
            url=post_data['url'],
            published=post_data.get('published'),
            updated=post_data.get('published'),
            notification_type=NotificationType.COMMUNITY_POST,
        )

    @staticmethod
    def _determine_notification_type(data: Dict[str, Any]) -> NotificationType:
        """Classify using simplified rules with a single YouTube API query.

        Rules:
        - If liveStreamingDetails exists: treat as livestream and map snippet.liveBroadcastContent
          -> 'live' => LIVESTREAM_LIVE, 'upcoming' => LIVESTREAM, else => LIVESTREAM_COMPLETED
        - If liveStreamingDetails missing: treat as regular UPLOAD
        """
        import requests
        import logging

        logger = logging.getLogger(__name__)

        video_id = data.get('video_id')
        if not video_id:
            logger.warning("No video_id found in notification data")
            return NotificationType.UPLOAD

        api_key = os.getenv('YOUTUBE_API_KEY')
        if not api_key:
            logger.warning("YOUTUBE_API_KEY not set; falling back to WebSub metadata")
            lbc = (data.get('live_broadcast_content') or '').lower()
            if lbc == 'live':
                return NotificationType.LIVESTREAM_LIVE
            if lbc == 'upcoming':
                return NotificationType.LIVESTREAM
            if lbc in ('none', 'completed'):
                return NotificationType.LIVESTREAM_COMPLETED
            return NotificationType.UPLOAD

        try:
            api_url = "https://www.googleapis.com/youtube/v3/videos"
            params = {
                'part': 'snippet,liveStreamingDetails',
                'id': video_id,
                'key': api_key,
                'fields': 'items(snippet(liveBroadcastContent),liveStreamingDetails(actualStartTime,actualEndTime,scheduledStartTime,scheduledEndTime))'
            }
            # Fixed delay to allow YouTube to populate API state after WebSub push
            import time
            logger.debug("Delaying 3s before YouTube API fetch for %s", video_id)
            time.sleep(3)
            logger.debug("Fetching video state from YouTube API for %s", video_id)
            resp = requests.get(api_url, params=params, timeout=10)
            if resp.status_code != 200:
                logger.error("YouTube API request failed: %s - %s", resp.status_code, resp.text[:200])
                # Conservative fallback
                lbc = (data.get('live_broadcast_content') or '').lower()
                if lbc == 'live':
                    return NotificationType.LIVESTREAM_LIVE
                if lbc == 'upcoming':
                    return NotificationType.LIVESTREAM
                if lbc in ('none', 'completed'):
                    return NotificationType.LIVESTREAM_COMPLETED
                return NotificationType.UPLOAD

            payload = resp.json()
            items = payload.get('items', [])
            if not items:
                logger.warning("No items returned for video %s; treating as upload", video_id)
                return NotificationType.UPLOAD

            item = items[0]
            snippet = item.get('snippet') or {}
            live_details = item.get('liveStreamingDetails') or {}

            if live_details:
                lbc = (snippet.get('liveBroadcastContent') or '').lower()

                # Propagate times for formatting downstream
                if live_details.get('scheduledStartTime'):
                    data['scheduled_start_time'] = live_details['scheduledStartTime']
                if live_details.get('actualStartTime'):
                    data['actual_start_time'] = live_details['actualStartTime']

                if lbc == 'live':
                    return NotificationType.LIVESTREAM_LIVE
                if lbc == 'upcoming':
                    return NotificationType.LIVESTREAM
                return NotificationType.LIVESTREAM_COMPLETED

            return NotificationType.UPLOAD

        except requests.exceptions.RequestException as exc:
            logger.error("Network error calling YouTube API: %s", exc)
            lbc = (data.get('live_broadcast_content') or '').lower()
            if lbc == 'live':
                return NotificationType.LIVESTREAM_LIVE
            if lbc == 'upcoming':
                return NotificationType.LIVESTREAM
            if lbc in ('none', 'completed'):
                return NotificationType.LIVESTREAM_COMPLETED
            return NotificationType.UPLOAD

    def to_dict(self) -> Dict[str, Any]:
        return {
            'video_id': self.video_id,
            'channel_id': self.channel_id,
            'title': self.title,
            'author': self.author,
            'url': self.url,
            'published': self.published,
            'updated': self.updated,
            'notification_type': self.notification_type.value,
            'scheduled_start_time': self.scheduled_start_time,
            'actual_start_time': self.actual_start_time,
        }

    @property
    def thumbnail_url(self) -> str:
        return f"https://img.youtube.com/vi/{self.video_id}/maxresdefault.jpg"

    @property
    def channel_url(self) -> str:
        return f"https://www.youtube.com/channel/{self.channel_id}"

    def is_recent(self, hours: int = 1) -> bool:
        if not self.published:
            return True
        try:
            published_dt = datetime.fromisoformat(self.published.replace('Z', '+00:00'))
            now = datetime.now(published_dt.tzinfo)
            return (now - published_dt).total_seconds() <= hours * 3600
        except (ValueError, AttributeError):
            return True

    def get_discord_timestamp(self, format_type: str = 'R') -> str:
        if not self.scheduled_start_time:
            return "soon"
        try:
            scheduled_dt = datetime.fromisoformat(self.scheduled_start_time.replace('Z', '+00:00'))
            return f"<t:{int(scheduled_dt.timestamp())}:{format_type}>"
        except (ValueError, AttributeError):
            return "soon"


@dataclass
class ProcessedNotification:
    video_id: str
    channel_id: str
    processed_at: str
    sent_to_discord: bool
    webhook_urls: list
    error_message: Optional[str] = None

    @classmethod
    def from_youtube_notification(
        cls,
        notification: YouTubeNotification,
        sent_to_discord: bool,
        webhook_urls: list,
        error_message: Optional[str] = None,
    ) -> "ProcessedNotification":
        return cls(
            video_id=notification.video_id,
            channel_id=notification.channel_id,
            processed_at=datetime.utcnow().isoformat() + 'Z',
            sent_to_discord=sent_to_discord,
            webhook_urls=webhook_urls,
            error_message=error_message,
        )
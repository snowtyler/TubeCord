"""
Data models for YouTube notifications and processing.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum
import os


class NotificationType(Enum):
    """Types of YouTube notifications."""
    UPLOAD = "upload"
    LIVESTREAM = "livestream"  # Scheduled livestream
    LIVESTREAM_LIVE = "livestream_live"  # Currently live stream
    LIVESTREAM_COMPLETED = "livestream_completed"  # Completed/ended stream (no notification)
    COMMUNITY_POST = "community"
    UNKNOWN = "unknown"


@dataclass
class YouTubeNotification:
    """Represents a YouTube notification from WebSub."""
    
    video_id: str
    channel_id: str
    title: str
    author: str
    url: str
    published: Optional[str] = None
    updated: Optional[str] = None
    notification_type: NotificationType = NotificationType.UPLOAD
    scheduled_start_time: Optional[str] = None
    actual_start_time: Optional[str] = None  # When stream actually went live
    
    @classmethod
    def from_websub_data(cls, data: Dict[str, Any]) -> 'YouTubeNotification':
        """
        Create a YouTubeNotification from parsed WebSub data.
        
        Args:
            data: Dictionary containing parsed notification data
            
        Returns:
            YouTubeNotification instance
        """
        # Determine notification type based on available data
        notification_type = cls._determine_notification_type(data)
        
        return cls(
            video_id=data['video_id'],
            channel_id=data['channel_id'],
            title=data['title'],
            author=data['author'],
            url=data['url'],
            published=data.get('published'),
            updated=data.get('updated'),
            notification_type=notification_type,
            scheduled_start_time=data.get('scheduled_start_time'),
            actual_start_time=data.get('actual_start_time')
        )
    
    @classmethod
    def from_community_post(cls, post_data: Dict[str, Any]) -> 'YouTubeNotification':
        """
        Create a YouTubeNotification from community post data.
        
        Args:
            post_data: Dictionary containing community post data
            
        Returns:
            YouTubeNotification instance for community post
        """
        return cls(
            video_id=post_data.get('post_id', ''),  # Use post_id as video_id for community posts
            channel_id=post_data['channel_id'],
            title=post_data.get('title', f"Community Post from {post_data.get('author', 'Unknown')}"),
            author=post_data['author'],
            url=post_data['url'],
            published=post_data.get('published'),
            updated=post_data.get('published'),  # Use published time as updated for community posts
            notification_type=NotificationType.COMMUNITY_POST,
            scheduled_start_time=None  # Community posts don't have scheduled times
        )
    
    @classmethod
    def from_community_post(cls, post_data: Dict[str, Any]) -> 'YouTubeNotification':
        """
        Create a YouTubeNotification from community post data.
        
        Args:
            post_data: Dictionary containing community post data
            
        Returns:
            YouTubeNotification instance for community post
        """
        return cls(
            video_id=post_data.get('post_id', ''),  # Use post_id as video_id for community posts
            channel_id=post_data['channel_id'],
            title=post_data.get('title', f"Community Post from {post_data.get('author', 'Unknown')}"),
            author=post_data['author'],
            url=post_data['url'],
            published=post_data.get('published'),
            updated=post_data.get('published'),  # Use published time as updated for community posts
            notification_type=NotificationType.COMMUNITY_POST,
            scheduled_start_time=None  # Community posts don't have scheduled times
        )
    
    @staticmethod
    def _determine_notification_type(data: Dict[str, Any]) -> NotificationType:
        """Determine the type of notification using the YouTube Data API v3."""
        import logging
        import requests

        logger = logging.getLogger(__name__)

        api_key = os.getenv('YOUTUBE_API_KEY')
        if not api_key:
            logger.warning("YOUTUBE_API_KEY not found, using metadata fallback for livestream detection")
            return YouTubeNotification._fallback_title_detection(data)

        video_id = data.get('video_id')
        if not video_id:
            logger.warning("No video_id found in notification data")
            return NotificationType.UPLOAD

        try:
            api_url = "https://www.googleapis.com/youtube/v3/videos"
            params = {
                'part': 'liveStreamingDetails,snippet,status',
                'id': video_id,
                'key': api_key,
                'fields': (
                    'items('
                    'snippet(title,liveBroadcastContent),'
                    'liveStreamingDetails(actualStartTime,actualEndTime,scheduledStartTime,scheduledEndTime,activeLiveChatId),'
                    'status(uploadStatus)'
                    ')'
                )
            }

            logger.debug("Checking YouTube API for video: %s", video_id)
            response = requests.get(api_url, params=params, timeout=10)

            if response.status_code != 200:
                if response.status_code == 403:
                    logger.error("YouTube API quota exceeded or invalid API key")
                else:
                    logger.error(
                        "YouTube API request failed: %s - %s",
                        response.status_code,
                        response.text
                    )
                return YouTubeNotification._fallback_title_detection(data)

            api_data = response.json()
            items = api_data.get('items', [])
            if not items:
                logger.warning("No video data found for video_id: %s", video_id)
                return YouTubeNotification._fallback_title_detection(data)

            video_data = items[0]
            live_details = video_data.get('liveStreamingDetails') or {}
            snippet = video_data.get('snippet') or {}
            status_info = video_data.get('status') or {}

            live_broadcast_content = snippet.get('liveBroadcastContent', 'none')
            upload_status = (status_info.get('uploadStatus') or '').lower()

            scheduled_start_time = live_details.get('scheduledStartTime')
            if scheduled_start_time:
                data['scheduled_start_time'] = scheduled_start_time

            scheduled_end_time = live_details.get('scheduledEndTime')
            if scheduled_end_time:
                data['scheduled_end_time'] = scheduled_end_time

            actual_start_time = live_details.get('actualStartTime')
            actual_end_time = live_details.get('actualEndTime')
            active_live_chat_id = live_details.get('activeLiveChatId')

            if actual_start_time:
                data['actual_start_time'] = actual_start_time

            if actual_end_time:
                logger.info("Detected completed livestream via actualEndTime")
                return NotificationType.LIVESTREAM_COMPLETED

            if upload_status == 'processed' and actual_start_time:
                logger.info("Detected completed livestream via uploadStatus processed")
                return NotificationType.LIVESTREAM_COMPLETED

            # If YouTube says the broadcast content is 'none' and we have live details,
            # treat it as a completed livestream to avoid misclassifying as an upload.
            # This covers cases where end flags propagate before actualEndTime.
            if live_broadcast_content == 'none' and live_details:
                logger.info("Detected completed livestream via broadcast content 'none'")
                return NotificationType.LIVESTREAM_COMPLETED

            if actual_start_time and not active_live_chat_id and live_broadcast_content != 'live':
                logger.info("Detected completed livestream (no active live chat)")
                return NotificationType.LIVESTREAM_COMPLETED

            if live_broadcast_content == 'upcoming' or (scheduled_start_time and not actual_start_time):
                logger.info(
                    "Detected scheduled livestream: %s (start: %s)",
                    data.get('title', 'Unknown'),
                    scheduled_start_time or 'unknown'
                )
                return NotificationType.LIVESTREAM

            if actual_start_time and not actual_end_time:
                if not active_live_chat_id and live_broadcast_content != 'live':
                    logger.info("Detected completed livestream (started but chat offline)")
                    return NotificationType.LIVESTREAM_COMPLETED

                logger.info("Detected LIVE stream: %s", data.get('title', 'Unknown'))
                return NotificationType.LIVESTREAM_LIVE

            if live_broadcast_content == 'live':
                if not active_live_chat_id and actual_start_time:
                    logger.info("Broadcast marked complete despite 'live' flag (no active chat)")
                    return NotificationType.LIVESTREAM_COMPLETED

                logger.info("Detected LIVE stream from broadcast content")
                return NotificationType.LIVESTREAM_LIVE

            if scheduled_start_time and not data.get('actual_start_time'):
                logger.info("Detected scheduled livestream via scheduled start time only")
                return NotificationType.LIVESTREAM

            title = data.get('title', '').lower()
            live_indicators = ['live', 'streaming', 'stream', 'livestream', '🔴']
            if any(indicator in title for indicator in live_indicators):
                logger.info(
                    "Detected potentially completed livestream (will skip notification): %s",
                    data.get('title', 'Unknown')
                )
                return NotificationType.LIVESTREAM_COMPLETED

            logger.info("Detected regular upload: %s", data.get('title', 'Unknown'))
            return NotificationType.UPLOAD

        except requests.exceptions.RequestException as exc:
            logger.error("Network error calling YouTube API: %s", exc)
            return YouTubeNotification._fallback_title_detection(data)
        except Exception as exc:
            logger.error("Unexpected error in YouTube API detection: %s", exc)
            return YouTubeNotification._fallback_title_detection(data)
    
    @staticmethod
    def _fallback_title_detection(data: Dict[str, Any]) -> NotificationType:
        """
        Fallback method for livestream detection based on broadcast metadata and title keywords.
        Used when YouTube API is unavailable or fails.
        
        Args:
            data: Parsed notification data
            
        Returns:
            NotificationType enum value
        """
        import logging
        
        logger = logging.getLogger(__name__)
        logger.info("Using fallback title-based livestream detection")
        
        live_broadcast_content = data.get('live_broadcast_content')
        if live_broadcast_content:
            live_broadcast_content = live_broadcast_content.lower()
            if live_broadcast_content == 'live':
                logger.info("Fallback: Detected live broadcast from WebSub metadata")
                return NotificationType.LIVESTREAM_LIVE
            if live_broadcast_content == 'upcoming':
                logger.info("Fallback: Detected scheduled livestream from WebSub metadata")
                return NotificationType.LIVESTREAM
            if live_broadcast_content == 'completed':
                logger.info("Fallback: WebSub metadata indicates completed livestream")
                return NotificationType.LIVESTREAM_COMPLETED
            if live_broadcast_content == 'none':
                logger.info("Fallback: WebSub metadata indicates non-live content")
                # continue to title-based detection for standard uploads

        title = data.get('title', '').lower()
        live_indicators = [
            'live', 'streaming', 'stream', 'livestream', '🔴', 
            'going live', 'now live', 'live now', 'live stream',
            'chat', 'q&a', 'qa', 'talk', 'discussion'
        ]
        
        if any(indicator in title for indicator in live_indicators):
            # In fallback mode, we can't distinguish between scheduled/live/completed
            # So we assume it's completed to avoid sending notifications for old streams
            logger.info(f"Fallback: Detected livestream from title keywords (assuming completed to avoid duplicate notifications): {title}")
            return NotificationType.LIVESTREAM_COMPLETED
        
        logger.info(f"Fallback: Detected upload from title: {title}")
        return NotificationType.UPLOAD
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert notification to dictionary format."""
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
            'actual_start_time': self.actual_start_time
        }
    
    @property
    def thumbnail_url(self) -> str:
        """Get the YouTube thumbnail URL for this video."""
        return f"https://img.youtube.com/vi/{self.video_id}/maxresdefault.jpg"
    
    @property
    def channel_url(self) -> str:
        """Get the YouTube channel URL."""
        return f"https://www.youtube.com/channel/{self.channel_id}"
    
    def is_recent(self, hours: int = 1) -> bool:
        """
        Check if the notification is recent (within specified hours).
        
        Args:
            hours: Number of hours to consider as "recent"
            
        Returns:
            True if the notification is recent, False otherwise
        """
        if not self.published:
            return True  # Assume recent if no timestamp
        
        try:
            published_dt = datetime.fromisoformat(
                self.published.replace('Z', '+00:00')
            )
            now = datetime.now(published_dt.tzinfo)
            delta = now - published_dt
            
            return delta.total_seconds() <= (hours * 3600)
        except (ValueError, AttributeError):
            return True  # Assume recent if parsing fails
    
    def get_discord_timestamp(self, format_type: str = 'R') -> str:
        """
        Get Discord timestamp format for scheduled start time.
        
        Args:
            format_type: Discord timestamp format ('R' for relative, 'F' for full, etc.)
            
        Returns:
            Discord timestamp string or fallback text
        """
        if not self.scheduled_start_time:
            return "soon"
        
        try:
            # Parse ISO 8601 timestamp
            scheduled_dt = datetime.fromisoformat(
                self.scheduled_start_time.replace('Z', '+00:00')
            )
            # Convert to Unix timestamp
            unix_timestamp = int(scheduled_dt.timestamp())
            return f"<t:{unix_timestamp}:{format_type}>"
        except (ValueError, AttributeError):
            return "soon"


@dataclass
class ProcessedNotification:
    """Represents a notification that has been processed and sent."""
    
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
        error_message: Optional[str] = None
    ) -> 'ProcessedNotification':
        """
        Create a ProcessedNotification from a YouTubeNotification.
        
        Args:
            notification: The original YouTube notification
            sent_to_discord: Whether the message was successfully sent
            webhook_urls: List of webhook URLs used
            error_message: Optional error message if sending failed
            
        Returns:
            ProcessedNotification instance
        """
        return cls(
            video_id=notification.video_id,
            channel_id=notification.channel_id,
            processed_at=datetime.utcnow().isoformat() + 'Z',
            sent_to_discord=sent_to_discord,
            webhook_urls=webhook_urls,
            error_message=error_message
        )
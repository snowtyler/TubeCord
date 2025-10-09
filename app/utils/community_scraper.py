"""
YouTube Community Post scraper using yp-dl (YoutubeCommunityScraper).
Handles fetching, processing, and storing community posts.
"""

import logging
import json
import os
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import hashlib

import subprocess
import tempfile
import requests
import re

from sqlalchemy import (
    Boolean,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    delete,
    select,
    update,
    Index,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.engine import Engine

from app.config.settings import settings
from app.db import get_engine

logger = logging.getLogger(__name__)


metadata = MetaData()

community_posts_table = Table(
    "community_posts",
    metadata,
    Column("post_id", String(255), primary_key=True),
    Column("channel_id", String(255), nullable=False),
    Column("channel_name", String(255), nullable=False),
    Column("content", Text, nullable=False),
    Column("image_urls", Text),
    Column("video_attachments", Text),
    Column("poll_data", Text),
    Column("published_time", String(64), nullable=False),
    Column("like_count", Integer),
    Column("url", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("scraped_at", String(64), nullable=False),
    Column("notified", Boolean, nullable=False, default=False),
)

channel_handles_table = Table(
    "channel_handles",
    metadata,
    Column("channel_id", String(255), primary_key=True),
    Column("handle", String(255), nullable=False),
    Column("channel_name", String(255)),
    Column("resolved_at", String(64), nullable=False),
    Column("last_verified", String(64), nullable=False),
)

Index('idx_channel_published', community_posts_table.c.channel_id, community_posts_table.c.published_time)
Index('idx_notified', community_posts_table.c.notified, community_posts_table.c.published_time)


@dataclass
class CommunityPost:
    """Represents a YouTube community post."""
    
    post_id: str
    channel_id: str
    channel_name: str
    content: str
    image_urls: List[str]
    video_attachments: List[Dict[str, Any]]
    poll_data: Optional[Dict[str, Any]]
    published_time: str
    like_count: Optional[int]
    url: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return {
            'post_id': self.post_id,
            'channel_id': self.channel_id,
            'channel_name': self.channel_name,
            'content': self.content,
            'image_urls': self.image_urls,
            'video_attachments': self.video_attachments,
            'poll_data': self.poll_data,
            'published_time': self.published_time,
            'like_count': self.like_count,
            'url': self.url
        }
    
    @property
    def content_hash(self) -> str:
        """Generate a hash of the post content for deduplication."""
        content_str = f"{self.post_id}:{self.content}:{self.published_time}"
        return hashlib.md5(content_str.encode()).hexdigest()


class CommunityPostDatabase:
    """Database helper for storing and tracking community posts."""

    def __init__(self, database_url: Optional[str] = None, *, engine: Optional[Engine] = None):
        self.database_url = database_url or settings.DATABASE_URL
        self.engine = engine or get_engine(self.database_url)
        self._init_database()

    def _init_database(self) -> None:
        """Ensure database schema exists."""
        try:
            metadata.create_all(self.engine, checkfirst=True)
        except SQLAlchemyError as exc:
            logger.error("Database initialization error: %s", exc)
            raise

    def store_post(self, post: CommunityPost) -> bool:
        """Store a community post in the database."""
        record = {
            "post_id": post.post_id,
            "channel_id": post.channel_id,
            "channel_name": post.channel_name,
            "content": post.content,
            "image_urls": json.dumps(post.image_urls),
            "video_attachments": json.dumps(post.video_attachments),
            "poll_data": json.dumps(post.poll_data) if post.poll_data else None,
            "published_time": post.published_time,
            "like_count": post.like_count,
            "url": post.url,
            "content_hash": post.content_hash,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "notified": False,
        }

        try:
            with Session(self.engine) as session:
                try:
                    session.execute(community_posts_table.insert().values(**record))
                    session.commit()
                    return True
                except IntegrityError:
                    session.rollback()
                    return False
        except SQLAlchemyError as exc:
            logger.error("Database error storing post %s: %s", post.post_id, exc)
            return False

    def get_unnotified_posts(self, channel_id: Optional[str] = None) -> List[CommunityPost]:
        """Get community posts that haven't been notified yet."""
        stmt = select(community_posts_table).where(community_posts_table.c.notified.is_(False))

        if channel_id:
            stmt = stmt.where(community_posts_table.c.channel_id == channel_id)

        stmt = stmt.order_by(community_posts_table.c.published_time.desc())

        try:
            with Session(self.engine) as session:
                rows = session.execute(stmt).mappings().all()
                return [self._row_to_post(row) for row in rows]
        except SQLAlchemyError as exc:
            logger.error("Database error getting unnotified posts: %s", exc)
            return []

    def mark_notified(self, post_id: str) -> bool:
        """Mark a post as notified."""
        stmt = (
            update(community_posts_table)
            .where(community_posts_table.c.post_id == post_id)
            .values(notified=True)
        )

        try:
            with Session(self.engine) as session:
                result = session.execute(stmt)
                session.commit()
                return result.rowcount > 0 if result.rowcount is not None else True
        except SQLAlchemyError as exc:
            logger.error("Database error marking post %s as notified: %s", post_id, exc)
            return False

    def _row_to_post(self, row: Dict[str, Any]) -> CommunityPost:
        """Convert database row to CommunityPost object."""
        return CommunityPost(
            post_id=row["post_id"],
            channel_id=row["channel_id"],
            channel_name=row["channel_name"],
            content=row["content"],
            image_urls=json.loads(row["image_urls"]) if row["image_urls"] else [],
            video_attachments=json.loads(row["video_attachments"]) if row["video_attachments"] else [],
            poll_data=json.loads(row["poll_data"]) if row["poll_data"] else None,
            published_time=row["published_time"],
            like_count=row["like_count"],
            url=row["url"],
        )

    def cleanup_old_posts(self, days: int = 30) -> None:
        """Clean up old posts from the database."""
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        stmt = delete(community_posts_table).where(
            community_posts_table.c.published_time < cutoff_date,
            community_posts_table.c.notified.is_(True),
        )

        try:
            with Session(self.engine) as session:
                result = session.execute(stmt)
                session.commit()
                deleted = result.rowcount or 0
                if deleted > 0:
                    logger.info("Cleaned up %s old community posts", deleted)
        except SQLAlchemyError as exc:
            logger.error("Database error during cleanup: %s", exc)

    def get_cached_handle(self, channel_id: str) -> Optional[str]:
        """Get cached channel handle from database."""
        stmt = select(
            channel_handles_table.c.handle,
            channel_handles_table.c.last_verified,
        ).where(channel_handles_table.c.channel_id == channel_id)

        try:
            with Session(self.engine) as session:
                result = session.execute(stmt).first()
        except SQLAlchemyError as exc:
            logger.error("Database error getting cached handle: %s", exc)
            return None

        if not result:
            return None

        handle, last_verified = result
        candidate_values = [last_verified]

        if last_verified.endswith('Z'):
            candidate_values.append(last_verified[:-1] + '+00:00')
        if last_verified.endswith('+00:00Z'):
            candidate_values.append(last_verified[:-1])
        if last_verified.endswith('+00:00+00:00'):
            candidate_values.append(last_verified[:-6])

        last_verified_dt = None
        for candidate in candidate_values:
            try:
                last_verified_dt = datetime.fromisoformat(candidate)
                break
            except ValueError:
                continue

        if not last_verified_dt:
            logger.warning(
                "Invalid cached timestamp for channel %s, discarding handle cache",
                channel_id,
            )
            return None

        if (datetime.now(timezone.utc) - last_verified_dt).days < 30:
            return handle

        logger.debug("Cached handle for %s is stale, will refresh", channel_id)
        return None

    def cache_handle(self, channel_id: str, handle: str, channel_name: Optional[str] = None) -> bool:
        """Cache a channel handle in the database."""
        now = datetime.now(timezone.utc).isoformat()
        insert_stmt = channel_handles_table.insert().values(
            channel_id=channel_id,
            handle=handle,
            channel_name=channel_name,
            resolved_at=now,
            last_verified=now,
        )

        try:
            with Session(self.engine) as session:
                try:
                    session.execute(insert_stmt)
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    update_stmt = (
                        update(channel_handles_table)
                        .where(channel_handles_table.c.channel_id == channel_id)
                        .values(
                            handle=handle,
                            channel_name=channel_name,
                            last_verified=now,
                            resolved_at=now,
                        )
                    )
                    session.execute(update_stmt)
                    session.commit()
                logger.info("Cached handle for %s: %s", channel_id, handle)
                return True
        except SQLAlchemyError as exc:
            logger.error("Database error caching handle: %s", exc)
            return False

    def get_cached_channel_name(self, channel_id: str) -> Optional[str]:
        """Return cached channel name for a channel if available."""
        stmt = select(channel_handles_table.c.channel_name).where(
            channel_handles_table.c.channel_id == channel_id,
            channel_handles_table.c.channel_name.isnot(None),
        )

        try:
            with Session(self.engine) as session:
                return session.execute(stmt).scalar_one_or_none()
        except SQLAlchemyError as exc:
            logger.error("Database error getting cached channel name: %s", exc)
            return None


class CommunityPostScraper:
    """Main class for scraping YouTube community posts using yp-dl CLI tool."""
    
    _CHANNELS_API_URL = "https://youtube.googleapis.com/youtube/v3/channels"

    def __init__(
        self,
        *,
        database_url: Optional[str] = None,
        engine: Optional[Engine] = None,
    ):
        self.db = CommunityPostDatabase(database_url=database_url, engine=engine)
        self.yp_dl_available = self._check_yp_dl_availability()
    
    def _check_yp_dl_availability(self) -> bool:
        """Check if yp-dl command-line tool is available."""
        try:
            result = subprocess.run(['yp-dl', '--help'], 
                                  capture_output=True, 
                                  text=True, 
                                  timeout=10)
            return result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError, subprocess.TimeoutExpired):
            logger.error("yp-dl command-line tool not available. Please install with: pip install yp-dl")
            return False
    
    def _resolve_channel_handle(self, channel_id: str) -> Optional[str]:
        """
        Resolve a YouTube channel ID to its handle (@username).
        
        Args:
            channel_id: YouTube channel ID (e.g., UCaxsVeXsitJG4pkQkhfaScg)
            
        Returns:
            Channel handle with @ prefix (e.g., @3blue1brown) or None if not found
        """
        # Check cache first
        cached_handle = self.db.get_cached_handle(channel_id)
        if cached_handle:
            logger.debug(f"Using cached handle for {channel_id}: {cached_handle}")
            return cached_handle
        
        logger.info(f"Resolving handle for channel: {channel_id}")

        # First try the YouTube Data API if available
        api_handle, api_channel_name = self._resolve_handle_via_api(channel_id)
        if api_handle:
            self.db.cache_handle(channel_id, api_handle, api_channel_name)
            return api_handle

        # Fall back to HTML scraping if API didn't succeed
        html_handle, html_channel_name = self._resolve_handle_via_html(channel_id)
        if html_handle:
            self.db.cache_handle(channel_id, html_handle, html_channel_name)
            return html_handle

        logger.warning(f"Could not resolve handle for channel: {channel_id}")
        return None

    def _resolve_handle_via_api(self, channel_id: str) -> tuple[Optional[str], Optional[str]]:
        """Attempt to resolve a channel handle using the YouTube Data API."""
        api_key = getattr(settings, 'YOUTUBE_API_KEY', '')
        if not api_key:
            logger.debug("YouTube API key not configured; skipping API handle resolution")
            return None, None

        params = {
            'part': 'snippet',
            'id': channel_id,
            'fields': 'items/snippet/customUrl,items/snippet/title',
            'key': api_key
        }

        try:
            response = requests.get(self._CHANNELS_API_URL, params=params, timeout=10)
            if response.status_code != 200:
                logger.warning(
                    "YouTube API request failed for channel %s: %s %s",
                    channel_id,
                    response.status_code,
                    response.text[:200]
                )
                return None, None

            data = response.json()
            items = data.get('items', [])
            if not items:
                logger.debug("YouTube API returned no items for channel %s", channel_id)
                return None, None

            snippet = items[0].get('snippet', {})
            custom_url = snippet.get('customUrl')
            channel_name = snippet.get('title')

            if custom_url:
                handle = f"@{custom_url.lstrip('@')}"
                logger.info(f"Found handle via YouTube API: {handle}")
                return handle, channel_name

            logger.debug(
                "YouTube API response for channel %s missing customUrl field",
                channel_id
            )
            return None, channel_name

        except requests.exceptions.RequestException as exc:
            logger.error("YouTube API error resolving handle for %s: %s", channel_id, exc)
            return None, None
        except (ValueError, json.JSONDecodeError) as exc:
            logger.error("Failed to parse YouTube API response for %s: %s", channel_id, exc)
            return None, None

    def _resolve_handle_via_html(self, channel_id: str) -> tuple[Optional[str], Optional[str]]:
        """Fallback method to resolve a channel handle by scraping the channel page."""
        try:
            handle = None
            channel_name = None

            channel_url = f"https://www.youtube.com/channel/{channel_id}"
            response = requests.get(channel_url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })

            if response.status_code != 200:
                logger.warning(
                    "Channel page request failed for %s with status %s",
                    channel_id,
                    response.status_code
                )
                return None, None

            content = response.text

            canonical_match = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/@([^"]+)"', content)
            if canonical_match:
                handle = f"@{canonical_match.group(1)}"
                logger.info(f"Found handle via canonical URL: {handle}")

            name_match = re.search(r'<meta property="og:title" content="([^"]+)"', content)
            if name_match:
                channel_name = name_match.group(1)

            if not handle:
                handle_match = re.search(r'"webCommandMetadata":\{"url":"/(@[^/\"]+)', content)
                if handle_match:
                    handle = handle_match.group(1)
                    logger.info(f"Found handle via web command metadata: {handle}")

            if not handle:
                nav_match = re.search(r'"canonicalChannelUrl":"https://www\.youtube\.com/(@[^\"]+)"', content)
                if nav_match:
                    handle = nav_match.group(1)
                    logger.info(f"Found handle via navigation data: {handle}")

            if not handle:
                base_url_match = re.search(r'"canonicalBaseUrl":"/(@[^"\\]+)"', content)
                if base_url_match:
                    handle = base_url_match.group(1)
                    logger.info(f"Found handle via canonicalBaseUrl: {handle}")

            if not handle:
                vanity_match = re.search(r'"vanityChannelUrl":"https://www\.youtube\.com/(@[^\"]+)"', content)
                if vanity_match:
                    handle = vanity_match.group(1)
                    logger.info(f"Found handle via vanityChannelUrl: {handle}")

            if not handle:
                loose_match = re.search(r'(@[A-Za-z0-9_\.\-]+)"[^\n]+channelId":"' + re.escape(channel_id) + '"', content)
                if loose_match:
                    handle = loose_match.group(1)
                    logger.info(f"Found handle via loose pattern: {handle}")

            return handle, channel_name

        except requests.exceptions.RequestException as exc:
            logger.error("Network error resolving handle for %s: %s", channel_id, exc)
            return None, None
        except Exception as exc:
            logger.error("Unexpected error resolving handle for %s: %s", channel_id, exc)
            return None, None
    
    def scrape_channel_posts(self, channel_id: str, limit: int = 10) -> List[CommunityPost]:
        """
        Scrape community posts from a YouTube channel using yp-dl CLI tool.
        
        Args:
            channel_id: YouTube channel ID
            limit: Maximum number of posts to fetch
            
        Returns:
            List of new CommunityPost objects
        """
        if not self.yp_dl_available:
            logger.error("Cannot scrape: yp-dl command-line tool not available")
            return []
        
        logger.info(f"Scraping community posts for channel: {channel_id}")
        
        # Create temporary directory for yp-dl output
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                # Try to get the channel handle first (yp-dl works better with handles)
                channel_handle = self._resolve_channel_handle(channel_id)
                
                if channel_handle:
                    channel_url = f"https://www.youtube.com/{channel_handle}"
                    logger.info(f"Using channel handle: {channel_handle}")
                else:
                    # Fallback to channel ID format
                    channel_url = f"https://www.youtube.com/channel/{channel_id}"
                    logger.warning(f"Could not resolve handle for {channel_id}, using channel ID format")
                
                # Change to temp directory and run yp-dl there
                original_cwd = os.getcwd()
                os.chdir(temp_dir)
                
                # yp-dl seems to have a bug where it tries to create files in a 'channel/' subdirectory
                # Let's create this directory structure it expects
                os.makedirs('channel', exist_ok=True)
                
                # Run yp-dl command without --folder-path to avoid the path separator bug
                cmd = [
                    'yp-dl',
                    '--reverse',
                    '--verbose',
                    channel_url
                ]
                
                logger.debug(f"Running yp-dl command: {' '.join(cmd)}")
                
                try:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=300  # 5 minute timeout
                    )
                    
                    if result.returncode != 0:
                        logger.error(f"yp-dl command failed: {result.stderr}")
                        return []
                    
                    logger.debug(f"yp-dl output: {result.stdout}")
                    
                finally:
                    # Always restore the original working directory
                    os.chdir(original_cwd)
                
                # Find the generated JSON file(s) - yp-dl may create subdirectories
                json_files = []
                # Recursively search for JSON files in temp_dir and subdirectories
                for root, dirs, files in os.walk(temp_dir):
                    for file in files:
                        if file.endswith('.json'):
                            json_files.append(os.path.join(root, file))
                
                if not json_files:
                    logger.warning(f"No JSON files generated by yp-dl for channel: {channel_id}")
                    return []
                
                # Parse the JSON file(s)
                new_posts = []
                for json_file in json_files:
                    try:
                        posts_data = self._load_json_file(json_file)
                        if posts_data:
                            # With --reverse, yp-dl returns newest-first; slice takes newest items
                            for post_data in posts_data[:limit]:  # Limit the number of posts
                                try:
                                    post = self._parse_yp_dl_post_data(post_data, channel_id)
                                    
                                    # Store in database and check if it's new
                                    if self.db.store_post(post):
                                        new_posts.append(post)
                                        logger.info(f"Found new community post: {post.post_id}")
                                    else:
                                        logger.debug(f"Community post already exists: {post.post_id}")
                                        
                                except Exception as e:
                                    logger.error(f"Error parsing community post data: {e}")
                                    continue
                    except Exception as e:
                        logger.error(f"Error loading JSON file {json_file}: {e}")
                        continue
                
                logger.info(f"Scraped {len(new_posts)} new community posts for channel: {channel_id}")
                return new_posts
                
            except subprocess.TimeoutExpired:
                logger.error(f"yp-dl command timed out for channel: {channel_id}")
                return []
            except Exception as e:
                logger.error(f"Error scraping community posts for channel {channel_id}: {e}")
                return []
    
    def _load_json_file(self, json_file_path: str) -> List[Dict[str, Any]]:
        """
        Load and parse JSON file generated by yp-dl.
        
        Args:
            json_file_path: Path to the JSON file
            
        Returns:
            List of post data dictionaries
        """
        try:
            with open(json_file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # yp-dl generates JSON files with posts in a list
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                # Sometimes it might be wrapped in an object
                return data.get('posts', [data])  # Return single post as list if needed
            else:
                logger.warning(f"Unexpected JSON format in {json_file_path}")
                return []
                
        except Exception as e:
            logger.error(f"Error loading JSON file {json_file_path}: {e}")
            return []
    
    def _parse_yp_dl_post_data(self, post_data: Dict[str, Any], channel_id: str) -> CommunityPost:
        """
        Parse post data from yp-dl JSON format into CommunityPost object.
        
        Args:
            post_data: Raw post data from yp-dl JSON
            channel_id: YouTube channel ID
            
        Returns:
            CommunityPost object
        """
        # Extract post ID from post_link (yp-dl format: post_link)
        post_link = post_data.get('post_link', '')
        post_id = ''
        if '/post/' in post_link:
            post_id = post_link.split('/post/')[-1]
        
        if not post_id:
            # Generate a fallback ID based on content and timestamp
            content_hash = hashlib.md5(
                f"{post_data.get('text', '')}:{post_data.get('time_since', '')}".encode()
            ).hexdigest()[:12]
            post_id = f"community_{content_hash}"
        
        # Get channel name - try to use cached name from handle resolution
        channel_name = self.db.get_cached_channel_name(channel_id) or f"Channel {channel_id}"
        
        # Parse images (yp-dl format: images field can be null or array)
        image_urls = []
        images_data = post_data.get('images')
        if images_data and isinstance(images_data, list):
            for image in images_data:
                if isinstance(image, str):
                    image_urls.append(image)
                elif isinstance(image, dict) and 'url' in image:
                    image_urls.append(image['url'])
        
        # Parse video attachments (yp-dl format: video field)
        video_attachments = []
        video_data = post_data.get('video')
        if video_data:
            # video_data might be a URL or dict with video information
            if isinstance(video_data, str):
                video_url = video_data
            elif isinstance(video_data, dict):
                video_url = video_data.get('url', video_data.get('link', ''))
            else:
                video_url = ''
            
            if video_url:
                # Extract video ID from YouTube URL
                video_id = ''
                if 'watch?v=' in video_url:
                    video_id = video_url.split('watch?v=')[-1].split('&')[0]
                elif 'youtu.be/' in video_url:
                    video_id = video_url.split('youtu.be/')[-1].split('?')[0]
                
                if video_id:
                    video_attachments.append({
                        'video_id': video_id,
                        'title': 'Attached Video',  # yp-dl doesn't provide video title
                        'thumbnail': f'https://img.youtube.com/vi/{video_id}/default.jpg'
                    })
        
        # Convert time_since to approximate timestamp
        published_time = self._parse_time_since(post_data.get('time_since', ''))
        
        # Get text content (yp-dl format: text field)
        content = post_data.get('text', '')
        
        return CommunityPost(
            post_id=post_id,
            channel_id=channel_id,
            channel_name=channel_name,
            content=content,
            image_urls=image_urls,
            video_attachments=video_attachments,
            poll_data=None,  # yp-dl doesn't currently support polls according to docs
            published_time=published_time,
            like_count=None,  # yp-dl doesn't provide like counts
            url=post_link or f"https://www.youtube.com/post/{post_id}"
        )
    
    def _parse_time_since(self, time_since: str) -> str:
        """
        Convert yp-dl's time_since format to ISO timestamp.
        
        Args:
            time_since: Time string like "2 hours ago", "1 day ago", etc.
            
        Returns:
            ISO timestamp string
        """
        try:
            from datetime import datetime, timezone, timedelta
            import re
            
            now = datetime.now(timezone.utc)
            
            # Parse different time formats
            if 'minute' in time_since:
                match = re.search(r'(\d+)', time_since)
                if match:
                    minutes = int(match.group(1))
                    timestamp = now - timedelta(minutes=minutes)
                else:
                    timestamp = now - timedelta(minutes=1)
            elif 'hour' in time_since:
                match = re.search(r'(\d+)', time_since)
                if match:
                    hours = int(match.group(1))
                    timestamp = now - timedelta(hours=hours)
                else:
                    timestamp = now - timedelta(hours=1)
            elif 'day' in time_since:
                match = re.search(r'(\d+)', time_since)
                if match:
                    days = int(match.group(1))
                    timestamp = now - timedelta(days=days)
                else:
                    timestamp = now - timedelta(days=1)
            elif 'week' in time_since:
                match = re.search(r'(\d+)', time_since)
                if match:
                    weeks = int(match.group(1))
                    timestamp = now - timedelta(weeks=weeks)
                else:
                    timestamp = now - timedelta(weeks=1)
            elif 'month' in time_since:
                match = re.search(r'(\d+)', time_since)
                if match:
                    months = int(match.group(1))
                    timestamp = now - timedelta(days=months * 30)  # Approximate
                else:
                    timestamp = now - timedelta(days=30)
            elif 'year' in time_since:
                match = re.search(r'(\d+)', time_since)
                if match:
                    years = int(match.group(1))
                    timestamp = now - timedelta(days=years * 365)  # Approximate
                else:
                    timestamp = now - timedelta(days=365)
            else:
                # Default to current time if can't parse
                timestamp = now
            
            return timestamp.isoformat()
            
        except Exception as e:
            logger.error(f"Error parsing time_since '{time_since}': {e}")
            return datetime.now(timezone.utc).isoformat()
    

    
    def get_new_posts_for_notification(self, channel_id: str = None) -> List[CommunityPost]:
        """
        Get community posts that need to be sent as notifications.
        
        Args:
            channel_id: Optional channel ID to filter by
            
        Returns:
            List of CommunityPost objects ready for notification
        """
        return self.db.get_unnotified_posts(channel_id)
    
    def mark_post_notified(self, post_id: str) -> bool:
        """
        Mark a community post as notified.
        
        Args:
            post_id: ID of the post to mark
            
        Returns:
            True if marked successfully
        """
        return self.db.mark_notified(post_id)
    
    def cleanup_old_posts(self, days: int = 30):
        """Clean up old posts from the database."""
        self.db.cleanup_old_posts(days)
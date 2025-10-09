"""
YouTube Community Post scraper using yp-dl (YoutubeCommunityScraper).
Handles fetching, processing, and storing community posts.
"""

import logging
import json
import sqlite3
import os
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import hashlib

import subprocess
import tempfile
import requests

logger = logging.getLogger(__name__)


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
    """SQLite database for storing and tracking community posts."""
    
    def __init__(self, db_path: str = "community_posts.db"):
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self):
        """Initialize the database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS community_posts (
                    post_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    channel_name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    image_urls TEXT,
                    video_attachments TEXT,
                    poll_data TEXT,
                    published_time TEXT NOT NULL,
                    like_count INTEGER,
                    url TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    scraped_at TEXT NOT NULL,
                    notified BOOLEAN DEFAULT FALSE
                )
            """)
            
            # Create table for caching channel handles
            conn.execute("""
                CREATE TABLE IF NOT EXISTS channel_handles (
                    channel_id TEXT PRIMARY KEY,
                    handle TEXT NOT NULL,
                    channel_name TEXT,
                    resolved_at TEXT NOT NULL,
                    last_verified TEXT NOT NULL
                )
            """)
            
            # Create index for faster queries
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_channel_published 
                ON community_posts(channel_id, published_time DESC)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_notified 
                ON community_posts(notified, published_time DESC)
            """)
            
            # Create table for caching channel handles
            conn.execute("""
                CREATE TABLE IF NOT EXISTS channel_handles (
                    channel_id TEXT PRIMARY KEY,
                    handle TEXT NOT NULL,
                    channel_name TEXT,
                    resolved_at TEXT NOT NULL,
                    last_verified TEXT NOT NULL
                )
            """)
    
    def store_post(self, post: CommunityPost) -> bool:
        """
        Store a community post in the database.
        
        Args:
            post: CommunityPost to store
            
        Returns:
            True if stored successfully, False if already exists
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR IGNORE INTO community_posts (
                        post_id, channel_id, channel_name, content, image_urls,
                        video_attachments, poll_data, published_time, like_count,
                        url, content_hash, scraped_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    post.post_id,
                    post.channel_id,
                    post.channel_name,
                    post.content,
                    json.dumps(post.image_urls),
                    json.dumps(post.video_attachments),
                    json.dumps(post.poll_data) if post.poll_data else None,
                    post.published_time,
                    post.like_count,
                    post.url,
                    post.content_hash,
                    datetime.now(timezone.utc).isoformat()
                ))
                
                # Return True if a row was inserted
                return conn.total_changes > 0
                
        except sqlite3.Error as e:
            logger.error(f"Database error storing post {post.post_id}: {e}")
            return False
    
    def get_unnotified_posts(self, channel_id: str = None) -> List[CommunityPost]:
        """
        Get community posts that haven't been notified yet.
        
        Args:
            channel_id: Optional channel ID to filter by
            
        Returns:
            List of unnotified CommunityPost objects
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                if channel_id:
                    cursor = conn.execute("""
                        SELECT * FROM community_posts 
                        WHERE channel_id = ? AND notified = FALSE 
                        ORDER BY published_time DESC
                    """, (channel_id,))
                else:
                    cursor = conn.execute("""
                        SELECT * FROM community_posts 
                        WHERE notified = FALSE 
                        ORDER BY published_time DESC
                    """)
                
                posts = []
                for row in cursor.fetchall():
                    post = self._row_to_post(row)
                    posts.append(post)
                
                return posts
                
        except sqlite3.Error as e:
            logger.error(f"Database error getting unnotified posts: {e}")
            return []
    
    def mark_notified(self, post_id: str) -> bool:
        """
        Mark a post as notified.
        
        Args:
            post_id: ID of the post to mark as notified
            
        Returns:
            True if marked successfully
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    UPDATE community_posts 
                    SET notified = TRUE 
                    WHERE post_id = ?
                """, (post_id,))
                
                return conn.total_changes > 0
                
        except sqlite3.Error as e:
            logger.error(f"Database error marking post {post_id} as notified: {e}")
            return False
    
    def _row_to_post(self, row) -> CommunityPost:
        """Convert database row to CommunityPost object."""
        return CommunityPost(
            post_id=row[0],
            channel_id=row[1],
            channel_name=row[2],
            content=row[3],
            image_urls=json.loads(row[4]) if row[4] else [],
            video_attachments=json.loads(row[5]) if row[5] else [],
            poll_data=json.loads(row[6]) if row[6] else None,
            published_time=row[7],
            like_count=row[8],
            url=row[9]
        )
    
    def cleanup_old_posts(self, days: int = 30):
        """
        Clean up old posts from the database.
        
        Args:
            days: Number of days to keep posts
        """
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    DELETE FROM community_posts 
                    WHERE published_time < ? AND notified = TRUE
                """, (cutoff_date,))
                
                deleted_count = cursor.rowcount
                if deleted_count > 0:
                    logger.info(f"Cleaned up {deleted_count} old community posts")
                    
        except sqlite3.Error as e:
            logger.error(f"Database error during cleanup: {e}")
    
    def get_cached_handle(self, channel_id: str) -> Optional[str]:
        """Get cached channel handle from database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    SELECT handle, last_verified FROM channel_handles 
                    WHERE channel_id = ?
                """, (channel_id,))
                
                result = cursor.fetchone()
                if result:
                    handle, last_verified = result
                    # Check if cache is still valid (30 days)
                    from datetime import timezone
                    # Parse as timezone-aware datetime
                    last_verified_dt = datetime.fromisoformat(last_verified.replace('Z', '+00:00'))
                    if (datetime.now(timezone.utc) - last_verified_dt).days < 30:
                        return handle
                    else:
                        logger.debug(f"Cached handle for {channel_id} is stale, will refresh")
                
                return None
        except sqlite3.Error as e:
            logger.error(f"Database error getting cached handle: {e}")
            return None
    
    def cache_handle(self, channel_id: str, handle: str, channel_name: str = None) -> bool:
        """Cache a channel handle in the database."""
        try:
            from datetime import timezone
            now = datetime.now(timezone.utc).isoformat() + 'Z'
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO channel_handles 
                    (channel_id, handle, channel_name, resolved_at, last_verified)
                    VALUES (?, ?, ?, ?, ?)
                """, (channel_id, handle, channel_name, now, now))
                
                logger.info(f"Cached handle for {channel_id}: {handle}")
                return True
        except sqlite3.Error as e:
            logger.error(f"Database error caching handle: {e}")
            return False


class CommunityPostScraper:
    """Main class for scraping YouTube community posts using yp-dl CLI tool."""
    
    def __init__(self, db_path: str = "community_posts.db"):
        self.db = CommunityPostDatabase(db_path)
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
        
        try:
            # Try multiple approaches to find the handle
            handle = None
            channel_name = None
            
            # Method 1: Check channel page for canonical URL or handle
            channel_url = f"https://www.youtube.com/channel/{channel_id}"
            response = requests.get(channel_url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            
            if response.status_code == 200:
                content = response.text
                
                # Look for canonical URL with handle
                import re
                canonical_match = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/@([^"]+)"', content)
                if canonical_match:
                    handle = f"@{canonical_match.group(1)}"
                    logger.info(f"Found handle via canonical URL: {handle}")
                
                # Look for channel name while we're here
                name_match = re.search(r'<meta property="og:title" content="([^"]+)"', content)
                if name_match:
                    channel_name = name_match.group(1)
                
                # Alternative: Look for handle in page data
                if not handle:
                    handle_match = re.search(r'"webCommandMetadata":{\"url\":\"/(@[^/\"]+)', content)
                    if handle_match:
                        handle = handle_match.group(1)
                        logger.info(f"Found handle via web command metadata: {handle}")
                
                # Another alternative: Look for handle in navigation data
                if not handle:
                    nav_match = re.search(r'"canonicalChannelUrl":"https://www\.youtube\.com/(@[^"]+)"', content)
                    if nav_match:
                        handle = nav_match.group(1)
                        logger.info(f"Found handle via navigation data: {handle}")
            
            # Method 2: Try redirecting from /c/ or /user/ formats if they exist
            if not handle:
                logger.debug(f"Direct method failed, trying alternative approaches for {channel_id}")
                
                # Sometimes channels have custom URLs, but we can't easily guess them
                # We'll rely on the direct channel page approach above
            
            if handle:
                # Cache the result
                self.db.cache_handle(channel_id, handle, channel_name)
                return handle
            else:
                logger.warning(f"Could not resolve handle for channel: {channel_id}")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error resolving handle for {channel_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error resolving handle for {channel_id}: {e}")
            return None
    
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
        cached_handle_data = None
        try:
            with sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.execute("""
                    SELECT channel_name FROM channel_handles 
                    WHERE channel_id = ? AND channel_name IS NOT NULL
                """, (channel_id,))
                result = cursor.fetchone()
                if result:
                    channel_name = result[0]
                else:
                    channel_name = f"Channel {channel_id}"
        except Exception:
            channel_name = f"Channel {channel_id}"
        
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
            
            return timestamp.isoformat() + 'Z'
            
        except Exception as e:
            logger.error(f"Error parsing time_since '{time_since}': {e}")
            return datetime.now(timezone.utc).isoformat() + 'Z'
    

    
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
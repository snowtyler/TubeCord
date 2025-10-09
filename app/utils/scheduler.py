"""
Scheduler for periodic community post checking and notification.
Handles background task scheduling and coordination.
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Callable, List
import signal
import sys

logger = logging.getLogger(__name__)


class CommunityPostScheduler:
    """Scheduler for periodic community post checking."""
    
    def __init__(self, check_interval_minutes: int = 15):
        """
        Initialize the scheduler.
        
        Args:
            check_interval_minutes: How often to check for new community posts
        """
        self.check_interval_minutes = check_interval_minutes
        self.check_interval_seconds = check_interval_minutes * 60
        self.running = False
        self.scheduler_thread: Optional[threading.Thread] = None
        self.last_check_time: Optional[datetime] = None
        
        # Callbacks for different events
        self.on_posts_found: Optional[Callable] = None
        self.on_check_complete: Optional[Callable] = None
        self.on_error: Optional[Callable] = None
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {signum}, shutting down scheduler...")
        self.stop()
        sys.exit(0)
    
    def set_callbacks(
        self,
        on_posts_found: Callable = None,
        on_check_complete: Callable = None,
        on_error: Callable = None
    ):
        """
        Set callback functions for scheduler events.
        
        Args:
            on_posts_found: Called when new posts are found (posts_list)
            on_check_complete: Called after each check cycle (check_time, posts_count)
            on_error: Called when an error occurs (error_message)
        """
        self.on_posts_found = on_posts_found
        self.on_check_complete = on_check_complete
        self.on_error = on_error
    
    def start(self):
        """Start the scheduler in a background thread."""
        if self.running:
            logger.warning("Scheduler is already running")
            return
        
        self.running = True
        self.scheduler_thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.scheduler_thread.start()
        
        logger.info(f"Community post scheduler started (checking every {self.check_interval_minutes} minutes)")
    
    def stop(self):
        """Stop the scheduler."""
        if not self.running:
            return
        
        self.running = False
        
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            self.scheduler_thread.join(timeout=5)
        
        logger.info("Community post scheduler stopped")
    
    def _run_scheduler(self):
        """Main scheduler loop."""
        logger.info("Community post scheduler loop started")
        
        while self.running:
            try:
                from datetime import datetime, timezone
                check_start_time = datetime.now(timezone.utc)
                
                # Perform the community post check
                new_posts = self._check_community_posts()
                
                # Update last check time
                self.last_check_time = check_start_time
                
                # Call callbacks
                if new_posts and self.on_posts_found:
                    try:
                        self.on_posts_found(new_posts)
                    except Exception as e:
                        logger.error(f"Error in on_posts_found callback: {e}")
                
                if self.on_check_complete:
                    try:
                        self.on_check_complete(check_start_time, len(new_posts))
                    except Exception as e:
                        logger.error(f"Error in on_check_complete callback: {e}")
                
                # Wait for next check interval
                self._wait_for_next_check()
                
            except Exception as e:
                error_msg = f"Error in scheduler loop: {e}"
                logger.error(error_msg)
                
                if self.on_error:
                    try:
                        self.on_error(error_msg)
                    except Exception as callback_error:
                        logger.error(f"Error in on_error callback: {callback_error}")
                
                # Wait a bit before retrying to avoid tight error loops
                if self.running:
                    time.sleep(60)  # Wait 1 minute before retrying
    
    def _check_community_posts(self) -> List:
        """
        Check for new community posts.
        This method should be overridden or set via dependency injection.
        
        Returns:
            List of new community posts
        """
        from app.utils.community_scraper import CommunityPostScraper
        from app.config.settings import settings
        
        # Initialize scraper
        scraper = CommunityPostScraper()
        
        # Get the channel ID from settings
        channel_id = settings.YOUTUBE_CHANNEL_ID
        if not channel_id:
            logger.warning("No YouTube channel ID configured for community post checking")
            return []
        
        # Scrape new posts
        new_posts = scraper.scrape_channel_posts(channel_id, limit=20)
        
        if new_posts:
            logger.info(f"Found {len(new_posts)} new community posts")
        else:
            logger.debug("No new community posts found")
        
        return new_posts
    
    def _wait_for_next_check(self):
        """Wait for the next check interval, but allow for early termination."""
        wait_time = 0
        sleep_increment = 10  # Check every 10 seconds if we should stop
        
        while wait_time < self.check_interval_seconds and self.running:
            time.sleep(min(sleep_increment, self.check_interval_seconds - wait_time))
            wait_time += sleep_increment
    
    def force_check(self) -> List:
        """
        Force an immediate check for community posts.
        
        Returns:
            List of new community posts found
        """
        logger.info("Forcing immediate community post check")
        
        try:
            new_posts = self._check_community_posts()
            
            if self.on_posts_found and new_posts:
                self.on_posts_found(new_posts)
            
            return new_posts
            
        except Exception as e:
            error_msg = f"Error in forced check: {e}"
            logger.error(error_msg)
            
            if self.on_error:
                self.on_error(error_msg)
            
            return []
    
    def get_status(self) -> dict:
        """
        Get the current status of the scheduler.
        
        Returns:
            Dictionary with scheduler status information
        """
        return {
            'running': self.running,
            'check_interval_minutes': self.check_interval_minutes,
            'last_check_time': self.last_check_time.isoformat() if self.last_check_time else None,
            'next_check_in_seconds': self._get_seconds_until_next_check(),
            'thread_alive': self.scheduler_thread.is_alive() if self.scheduler_thread else False
        }
    
    def _get_seconds_until_next_check(self) -> Optional[int]:
        """Get seconds until the next scheduled check."""
        if not self.running or not self.last_check_time:
            return None
        
        from datetime import timezone
        next_check_time = self.last_check_time + timedelta(seconds=self.check_interval_seconds)
        now = datetime.now(timezone.utc)
        
        if next_check_time > now:
            return int((next_check_time - now).total_seconds())
        else:
            return 0  # Overdue for check


class CommunityPostNotificationHandler:
    """Handles notification of community posts to Discord."""
    
    def __init__(self):
        self.discord_client = None
        self.community_scraper = None
        
    def initialize(self):
        """Initialize the notification handler."""
        from app.discord.client import DiscordClient
        from app.utils.community_scraper import CommunityPostScraper
        
        self.discord_client = DiscordClient()
        self.community_scraper = CommunityPostScraper()
        
        logger.info("Community post notification handler initialized")
    
    def handle_new_posts(self, posts: List):
        """
        Handle a list of new community posts by sending Discord notifications.
        Only sends notification for the latest post.
        
        Args:
            posts: List of CommunityPost objects
        """
        if not self.discord_client or not self.community_scraper:
            logger.error("Notification handler not properly initialized")
            return
        
        if not posts:
            return
        
        from app.config.settings import settings
        from datetime import datetime
        
        # Get Discord configuration for community posts
        webhook_urls = settings.get_webhooks_for_type('community')
        role_ids = settings.get_roles_for_type('community')
        
        if not webhook_urls:
            logger.warning("No Discord webhook URLs configured for community posts")
            return
        
        # Sort posts by published_time (newest first) to ensure we get the latest post
        try:
            sorted_posts = sorted(
                posts,
                key=lambda p: datetime.fromisoformat(p.published_time.replace('Z', '+00:00')),
                reverse=True
            )
        except Exception as e:
            logger.warning(f"Failed to sort posts by published_time: {e}, using original order")
            sorted_posts = posts
        
        # Only process the latest post (first in the sorted list)
        latest_post = sorted_posts[0]
        logger.info(f"Processing only the latest community post: {latest_post.post_id} (published: {latest_post.published_time}, ignoring {len(sorted_posts) - 1} older posts)")
        
        # Mark all other posts as notified without sending notifications
        for post in sorted_posts[1:]:
            self.community_scraper.mark_post_notified(post.post_id)
            logger.debug(f"Marked older post as notified without notification: {post.post_id} (published: {post.published_time})")
        
        successful_notifications = 0
        
        try:
            # Convert community post to notification format
            notification_data = self._post_to_notification_data(latest_post)
            
            # Send to each configured webhook
            for webhook_url in webhook_urls:
                success = self.discord_client.send_youtube_notification(
                    webhook_url=webhook_url,
                    notification_data=notification_data,
                    role_mentions=role_ids,
                    notification_type='community',
                    use_rich_embed=settings.USE_RICH_EMBEDS
                )
                
                if success:
                    successful_notifications += 1
                    logger.info(f"Sent community post notification: {latest_post.post_id}")
                else:
                    logger.error(f"Failed to send community post notification: {latest_post.post_id}")
            
            # Mark post as notified if at least one notification succeeded
            if successful_notifications > 0:
                self.community_scraper.mark_post_notified(latest_post.post_id)
            
        except Exception as e:
            logger.error(f"Error handling community post {latest_post.post_id}: {e}")
        
        if successful_notifications > 0:
            logger.info(f"Successfully sent {successful_notifications} community post notifications for latest post")
    
    def _post_to_notification_data(self, post) -> dict:
        """
        Convert a CommunityPost to notification data format.
        
        Args:
            post: CommunityPost object
            
        Returns:
            Dictionary in notification data format
        """
        # Truncate long content for Discord
        content = post.content
        if len(content) > 300:
            content = content[:297] + "..."
        
        # Use first image as thumbnail if available
        thumbnail_url = post.image_urls[0] if post.image_urls else None
        
        return {
            'post_id': post.post_id,
            'channel_id': post.channel_id,
            'title': f"Community Post from {post.channel_name}",
            'author': post.channel_name,
            'url': post.url,
            'content': content,
            'published': post.published_time,
            'thumbnail_url': thumbnail_url,
            'image_count': len(post.image_urls),
            'video_attachments': post.video_attachments,
            'poll_data': post.poll_data,
            'like_count': post.like_count
        }
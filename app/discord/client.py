"""
Discord webhook client for sending formatted notifications.
Handles rate limiting, embeds, and role mentions.
"""

import logging
import requests
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DiscordEmbed:
    """Represents a Discord embed structure."""
    title: str
    description: str
    url: str
    color: int
    thumbnail_url: Optional[str] = None
    author_name: Optional[str] = None
    author_url: Optional[str] = None
    footer_text: Optional[str] = None
    timestamp: Optional[str] = None


class DiscordClient:
    """Discord webhook client with rate limiting and embed support."""
    
    def __init__(self):
        self.rate_limit_delay = 0.2  # 5 requests per second max
        self.last_request_time = 0
    
    def _enforce_rate_limit(self):
        """Enforce Discord webhook rate limiting."""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        
        if time_since_last < self.rate_limit_delay:
            sleep_time = self.rate_limit_delay - time_since_last
            time.sleep(sleep_time)
        
        self.last_request_time = time.time()
    
    def _build_embed_dict(self, embed: DiscordEmbed) -> Dict[str, Any]:
        """Convert DiscordEmbed to Discord API format."""
        embed_dict = {
            'title': embed.title,
            'description': embed.description,
            'url': embed.url,
            'color': embed.color,
        }
        
        if embed.thumbnail_url:
            embed_dict['thumbnail'] = {'url': embed.thumbnail_url}
        
        if embed.author_name:
            author_dict = {'name': embed.author_name}
            if embed.author_url:
                author_dict['url'] = embed.author_url
            embed_dict['author'] = author_dict
        
        if embed.footer_text:
            embed_dict['footer'] = {'text': embed.footer_text}
        
        if embed.timestamp:
            embed_dict['timestamp'] = embed.timestamp
        
        return embed_dict
    
    def send_webhook_message(
        self,
        webhook_url: str,
        content: Optional[str] = None,
        embed: Optional[DiscordEmbed] = None,
        role_mentions: Optional[List[str]] = None
    ) -> bool:
        """
        Send a message to Discord via webhook.
        
        Args:
            webhook_url: Discord webhook URL
            content: Text content of the message
            embed: Optional embed to include
            role_mentions: List of role IDs to mention
            
        Returns:
            True if message was sent successfully, False otherwise
        """
        self._enforce_rate_limit()
        
        payload = {}
        
        # Build message content with role mentions
        message_parts = []
        if role_mentions:
            mentions = [f'<@&{role_id}>' for role_id in role_mentions]
            message_parts.extend(mentions)
        
        if content:
            message_parts.append(content)
        
        if message_parts:
            payload['content'] = ' '.join(message_parts)
        
        # Add embed if provided
        if embed:
            payload['embeds'] = [self._build_embed_dict(embed)]
        
        try:
            response = requests.post(
                webhook_url,
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            
            if response.status_code == 204:
                logger.info("Discord message sent successfully")
                return True
            elif response.status_code == 429:
                # Rate limited
                retry_after = response.json().get('retry_after', 1)
                logger.warning(f"Discord rate limited, retrying after {retry_after}s")
                time.sleep(retry_after)
                return self.send_webhook_message(webhook_url, content, embed, role_mentions)
            else:
                logger.error(f"Discord webhook failed: {response.status_code} - {response.text}")
                return False
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send Discord webhook: {e}")
            return False
    
    def send_youtube_notification(
        self,
        webhook_url: str,
        notification_data: Dict[str, Any],
        role_mentions: Optional[List[str]] = None,
        custom_message: Optional[str] = None,
        use_rich_embed: bool = True,
        notification_type: str = 'upload'
    ) -> bool:
        """
        Send a YouTube notification to Discord with rich embed or simple message.
        
        Args:
            webhook_url: Discord webhook URL
            notification_data: Parsed YouTube notification data
            role_mentions: List of role IDs to mention
            custom_message: Custom message template
            use_rich_embed: Whether to use rich embed (True) or simple text (False)
            notification_type: Type of notification for color/styling
            
        Returns:
            True if message was sent successfully, False otherwise
        """
        # Extract video information
        video_id = notification_data.get('video_id')
        title = notification_data.get('title', 'Unknown Title')
        author = notification_data.get('author', 'Unknown Channel')
        url = notification_data.get('url', f'https://www.youtube.com/watch?v={video_id}')
        
        if use_rich_embed:
            # Build rich embed
            from app.config.messages import MessageTemplates
            color_map = {
                'upload': 0xFF0000,      # YouTube red
                'livestream': 0xFF4500,  # Orange red for scheduled
                'livestream_live': 0xFF0000,  # Bright red for LIVE
                'community': 0x1DA1F2    # Twitter blue for community posts
            }
            
            # Handle community posts differently
            if notification_type == 'community':
                return self._send_community_post_notification(
                    webhook_url, notification_data, role_mentions, use_rich_embed
                )
            
            embed = DiscordEmbed(
                title=title,
                description=f"New {notification_type} from {author}",
                url=url,
                color=color_map.get(notification_type, 0xFF0000),
                thumbnail_url=f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                author_name=author,
                author_url=f"https://www.youtube.com/channel/{notification_data.get('channel_id')}",
                footer_text=f"YouTube • {notification_type.title()}",
                timestamp=notification_data.get('published')
            )
            
            # Use custom message or default
            content = custom_message
            
            return self.send_webhook_message(webhook_url, content, embed, role_mentions)
        else:
            # Send simple text message
            if custom_message:
                # If custom message provided, use it directly with URL
                message_content = f"{custom_message}\n{url}"
            else:
                # Use the simple message format
                from app.config.messages import MessageTemplates
                message_content = MessageTemplates.format_simple_message(notification_type, notification_data, role_mentions)
            
            return self.send_webhook_message(webhook_url, message_content, None, None)  # role_mentions already included in message_content
    
    def _send_community_post_notification(
        self,
        webhook_url: str,
        notification_data: Dict[str, Any],
        role_mentions: Optional[List[str]] = None,
        use_rich_embed: bool = True
    ) -> bool:
        """
        Send a community post notification with specialized formatting.
        
        Args:
            webhook_url: Discord webhook URL
            notification_data: Community post notification data
            role_mentions: List of role IDs to mention
            use_rich_embed: Whether to use rich embed format
            
        Returns:
            True if message was sent successfully, False otherwise
        """
        post_id = notification_data.get('post_id', '')
        title = notification_data.get('title', 'Community Post')
        author = notification_data.get('author', 'Unknown Channel')
        url = notification_data.get('url', 'https://youtube.com')
        content = notification_data.get('content', '')
        image_count = notification_data.get('image_count', 0)
        video_attachments = notification_data.get('video_attachments', [])
        poll_data = notification_data.get('poll_data')
        like_count = notification_data.get('like_count')
        
        if use_rich_embed:
            # Build rich embed for community post
            description_parts = []
            
            if content:
                description_parts.append(content)
            
            # Add attachment info
            if image_count > 0:
                description_parts.append(f"📷 {image_count} image{'s' if image_count > 1 else ''}")
            
            if video_attachments:
                video_count = len(video_attachments)
                description_parts.append(f"🎥 {video_count} video attachment{'s' if video_count > 1 else ''}")
            
            if poll_data:
                description_parts.append("📊 Poll included")
            
            if like_count:
                description_parts.append(f"👍 {like_count} likes")
            
            description = '\n\n'.join(description_parts) if description_parts else f"New community post from {author}"
            
            # Use thumbnail if available
            thumbnail_url = notification_data.get('thumbnail_url')
            
            embed = DiscordEmbed(
                title=f"📝 {title}",
                description=description,
                url=url,
                color=0x1DA1F2,  # Twitter blue for community posts
                thumbnail_url=thumbnail_url,
                author_name=author,
                author_url=f"https://www.youtube.com/channel/{notification_data.get('channel_id')}",
                footer_text="YouTube • Community Post",
                timestamp=notification_data.get('published')
            )
            
            return self.send_webhook_message(webhook_url, None, embed, role_mentions)
        else:
            # Simple text message format for community posts
            message_parts = []
            
            if role_mentions:
                mentions = [f'<@&{role_id}>' for role_id in role_mentions]
                message_parts.extend(mentions)
            
            message_parts.append(f"📝 **{author}** posted in the community tab")
            
            if content:
                # Truncate content for simple message
                short_content = content[:150] + "..." if len(content) > 150 else content
                message_parts.append(f'"{short_content}"')
            
            message_parts.append(f"[View Post]({url})")
            
            message_content = '\n'.join(message_parts)
            
            return self.send_webhook_message(webhook_url, message_content, None, None)
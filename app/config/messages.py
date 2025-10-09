"""
Discord message templates and formatting configuration.
"""

from typing import Dict, Any, List
from datetime import datetime, timezone


class MessageTemplates:
    """Discord message templates for different notification types."""
    
    # Default message templates
    UPLOAD_MESSAGE = "📺 New video uploaded!"
    LIVESTREAM_MESSAGE = "📡 Livestream scheduled!"
    LIVESTREAM_LIVE_MESSAGE = "🔴 LIVE now!"
    COMMUNITY_POST_MESSAGE = "📝 New community post!"
    
    # Custom embed colors
    COLORS = {
        'upload': 0xFF0000,      # YouTube red
        'livestream': 0xFF4500,  # Orange red for scheduled
        'livestream_live': 0xFF0000,  # Bright red for LIVE
        'community': 0x1DA1F2,   # Twitter blue for community posts
        'default': 0xFF0000
    }
    
    # Message format templates with placeholders
    TEMPLATES = {
        'upload': {
            'message': "📺 {author} just uploaded a new video!",
            'embed_description': "Check out this new video from {author}",
            'embed_footer': "YouTube • Uploaded"
        },
        'livestream': {
            'message': "📡 {author} has scheduled a livestream",
            'embed_description': "{author} will be streaming soon",
            'embed_footer': "YouTube • Scheduled Stream"
        },
        'livestream_live': {
            'message': "🔴 {author} is LIVE!",
            'embed_description': "{author} is streaming now",
            'embed_footer': "YouTube • LIVE"
        },
        'community': {
            'message': "📝 {author} posted in the community tab",
            'embed_description': "New community post from {author}",
            'embed_footer': "YouTube • Community Post"
        }
    }
    
    @staticmethod
    def format_message(template_type: str, data: Dict[str, Any]) -> Dict[str, str]:
        """
        Format a message template with provided data.
        
        Args:
            template_type: Type of template ('upload', 'livestream', 'community')
            data: Dictionary containing video/channel data
            
        Returns:
            Dictionary with formatted message components
        """
        template = MessageTemplates.TEMPLATES.get(template_type, MessageTemplates.TEMPLATES['upload'])
        
        # Default values
        author = data.get('author', 'Unknown Channel')
        title = data.get('title', 'Unknown Title')
        
        # Format each template component
        formatted = {}
        for key, template_str in template.items():
            # Create format data with defaults, then update with actual data
            format_data = {
                'author': author,
                'title': title
            }
            format_data.update(data)
            formatted[key] = template_str.format(**format_data)
        
        return formatted
    
    @staticmethod
    def format_simple_message(template_type: str, data: Dict[str, Any], role_mentions: List[str] = None) -> str:
        """
        Format a simple text message with provided data.
        
        Args:
            template_type: Type of template ('upload', 'livestream', 'community')
            data: Dictionary containing video/channel data
            role_mentions: List of role IDs for mentions
            
        Returns:
            Formatted simple message string
        """
        from app.config.messages import SIMPLE_MESSAGE_TEMPLATES
        from app.models.notification import YouTubeNotification
        
        template = SIMPLE_MESSAGE_TEMPLATES.get(template_type, SIMPLE_MESSAGE_TEMPLATES['upload'])
        
        # Default values
        author = data.get('author', 'Unknown Channel')
        title = data.get('title', 'Unknown Title')
        url = data.get('url', 'https://youtube.com')
        
        # Format role mentions for Discord
        role_mentions_str = ""
        if role_mentions:
            role_mentions_str = " ".join([f"<@&{role_id}>" for role_id in role_mentions])
        
        # Format Discord timestamp for livestreams
        scheduled_time = "soon"
        if template_type == 'livestream' and data.get('scheduled_start_time'):
            # Create a temporary notification object to use the timestamp method
            temp_notification = YouTubeNotification(
                video_id=data.get('video_id', ''),
                channel_id=data.get('channel_id', ''),
                title=title,
                author=author,
                url=url,
                scheduled_start_time=data.get('scheduled_start_time')
            )
            scheduled_time = temp_notification.get_discord_timestamp()
        
        # Create format data with defaults, then update with actual data
        format_data = {
            'author': author,
            'title': title,
            'url': url,
            'role_mentions': role_mentions_str,
            'scheduled_time': scheduled_time
        }
        format_data.update(data)
        
        return template.format(**format_data)
    
    @staticmethod
    def get_embed_color(template_type: str) -> int:
        """Get the embed color for a specific template type."""
        return MessageTemplates.COLORS.get(template_type, MessageTemplates.COLORS['default'])
    
    @staticmethod
    def format_timestamp(timestamp_str: str = None) -> str:
        """
        Format a timestamp for Discord embed.
        
        Args:
            timestamp_str: ISO timestamp string, uses current time if None
            
        Returns:
            Formatted timestamp string
        """
        if timestamp_str:
            try:
                # Parse the timestamp and return ISO format
                dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                return dt.isoformat()
            except (ValueError, AttributeError):
                pass
        
        # Fall back to current time
        return datetime.now(timezone.utc).isoformat() + 'Z'


def get_notification_config():
    """Get notification configuration with environment variable override."""
    from app.config.settings import settings
    
    return {
        'upload': {
            'enabled': True,
            'template_type': 'upload',
            'use_rich_embed': settings.USE_RICH_EMBEDS,
            'include_thumbnail': True,
            'include_author': True,
            'include_timestamp': True
        },
        'livestream': {
            'enabled': True,
            'template_type': 'livestream',
            'use_rich_embed': settings.USE_RICH_EMBEDS,
            'include_thumbnail': True,
            'include_author': True,
            'include_timestamp': True
        },
        'livestream_live': {
            'enabled': True,
            'template_type': 'livestream_live',
            'use_rich_embed': settings.USE_RICH_EMBEDS,
            'include_thumbnail': True,
            'include_author': True,
            'include_timestamp': True
        },
        'community': {
            'enabled': True,  # Enabled when community monitoring is configured
            'template_type': 'community',
            'use_rich_embed': settings.USE_RICH_EMBEDS,
            'include_thumbnail': True,
            'include_author': True,
            'include_timestamp': True
        }
    }

# Configuration for different notification types
NOTIFICATION_CONFIG = get_notification_config()

# Simple text message templates (used when use_rich_embed = False)
SIMPLE_MESSAGE_TEMPLATES = {
    'upload': "{role_mentions} [{title}]({url})",
    'livestream': "{role_mentions} starting {scheduled_time}: [{title}]({url})",
    'livestream_live': "{role_mentions} 🔴 LIVE: [{title}]({url})",
    'community': "{role_mentions} {author} has made a new post! [View here.]({url})"
}
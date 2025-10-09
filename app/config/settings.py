"""Application settings and environment variable management."""

import os
from pathlib import Path
from typing import List
from urllib.parse import urlsplit

from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()


class Settings:
    """Application configuration settings."""

    # WebSub Configuration
    CALLBACK_URL: str
    CALLBACK_PORT: int
    WEBSUB_HUB_URL: str = "https://pubsubhubbub.appspot.com/subscribe"
    CALLBACK_SECRET: str = os.getenv('CALLBACK_SECRET', '').strip()
    
    # YouTube Configuration
    YOUTUBE_CHANNEL_ID: str = os.getenv('YOUTUBE_CHANNEL_ID', '')
    YOUTUBE_API_KEY: str = os.getenv('YOUTUBE_API_KEY', '').strip()
    
    # Discord Configuration - Per Content Type
    # Upload notifications
    UPLOAD_WEBHOOK_URLS: List[str] = []
    UPLOAD_ROLE_IDS: List[str] = []
    
    # Livestream notifications
    LIVESTREAM_WEBHOOK_URLS: List[str] = []
    LIVESTREAM_ROLE_IDS: List[str] = []
    
    # Community post notifications
    COMMUNITY_WEBHOOK_URLS: List[str] = []
    COMMUNITY_ROLE_IDS: List[str] = []

    # Database configuration
    DATABASE_URL: str = os.getenv('DATABASE_URL', 'sqlite:///data/community_posts.db')
    DATABASE_ECHO: bool = os.getenv('DATABASE_ECHO', 'False').lower() == 'true'
    
    # Server Configuration
    HOST: str = os.getenv('HOST', '0.0.0.0')
    PORT: int = int(os.getenv('PORT', '8000'))
    DEBUG: bool = os.getenv('DEBUG', 'False').lower() == 'true'
    
    # Logging Configuration
    LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
    
    # Discord Message Format Configuration
    USE_RICH_EMBEDS: bool = os.getenv('USE_RICH_EMBEDS', 'True').lower() == 'true'
    
    # Community Posts
    COMMUNITY_CHECK_INTERVAL_MINUTES: int = int(os.getenv('COMMUNITY_CHECK_INTERVAL_MINUTES', '15'))
    
    def __init__(self):
        """Initialize settings from environment variables."""
        self.CALLBACK_URL, self.CALLBACK_PORT = self._resolve_callback_settings()
        self._load_discord_config()
        self._load_database_settings()
        self._validate_required_settings()
        # Normalize and validate community check interval
        self._load_community_settings()

    def _load_community_settings(self) -> None:
        """Load and validate community post related settings."""
        raw = os.getenv('COMMUNITY_CHECK_INTERVAL_MINUTES', str(self.COMMUNITY_CHECK_INTERVAL_MINUTES)).strip()
        try:
            value = int(raw)
        except ValueError:
            value = 15
        # Clamp to sensible bounds (1 minute to 1 day)
        if value < 1:
            value = 1
        if value > 24 * 60:
            value = 24 * 60
        self.COMMUNITY_CHECK_INTERVAL_MINUTES = value

    def _load_database_settings(self) -> None:
        """Normalize database configuration settings."""
        raw_url = os.getenv('DATABASE_URL', self.DATABASE_URL).strip()
        if not raw_url:
            raw_url = 'sqlite:///data/community_posts.db'

        if raw_url.startswith('sqlite:///') and not raw_url.startswith('sqlite:///:'):
            db_path = raw_url.replace('sqlite:///', '', 1)
            resolved = Path(db_path).expanduser()
            if not resolved.is_absolute():
                resolved = (Path.cwd() / resolved).resolve()
            self.DATABASE_URL = f"sqlite:///{resolved.as_posix()}"
        else:
            self.DATABASE_URL = raw_url

        raw_echo = os.getenv('DATABASE_ECHO', 'false').strip().lower()
        self.DATABASE_ECHO = raw_echo in {'1', 'true', 'yes', 'on'}

    def _resolve_callback_settings(self) -> tuple[str, int]:
        """Read and validate the WebSub callback configuration from the environment."""
        raw_url = os.getenv('CALLBACK_URL', '').strip()
        if not raw_url:
            raise ValueError("CALLBACK_URL environment variable must be set.")

        parsed = urlsplit(raw_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("CALLBACK_URL must include a scheme and hostname (e.g. https://example.com/webhook).")

        default_ports = {'http': 80, 'https': 443}
        derived_port = parsed.port or default_ports.get(parsed.scheme.lower())

        port_override = os.getenv('CALLBACK_PORT', '').strip()
        if port_override:
            try:
                port = int(port_override)
            except ValueError as exc:
                raise ValueError("CALLBACK_PORT must be an integer between 1 and 65535") from exc

            if not 1 <= port <= 65535:
                raise ValueError("CALLBACK_PORT must be between 1 and 65535")
        else:
            if derived_port is None:
                raise ValueError("Unable to determine callback port. Specify a port in CALLBACK_URL or set CALLBACK_PORT explicitly.")
            port = derived_port

        return raw_url, port
    
    def _load_discord_config(self):
        """Load Discord webhook URLs and role IDs from environment for each content type."""
        # Upload configuration
        upload_webhooks = os.getenv('UPLOAD_WEBHOOK_URLS', '')
        if upload_webhooks:
            self.UPLOAD_WEBHOOK_URLS = [url.strip() for url in upload_webhooks.split(',') if url.strip()]
        
        upload_roles = os.getenv('UPLOAD_ROLE_IDS', '')
        if upload_roles:
            self.UPLOAD_ROLE_IDS = [role_id.strip() for role_id in upload_roles.split(',') if role_id.strip()]
        
        # Livestream configuration
        livestream_webhooks = os.getenv('LIVESTREAM_WEBHOOK_URLS', '')
        if livestream_webhooks:
            self.LIVESTREAM_WEBHOOK_URLS = [url.strip() for url in livestream_webhooks.split(',') if url.strip()]
        
        livestream_roles = os.getenv('LIVESTREAM_ROLE_IDS', '')
        if livestream_roles:
            self.LIVESTREAM_ROLE_IDS = [role_id.strip() for role_id in livestream_roles.split(',') if role_id.strip()]
        
        # Community post configuration
        community_webhooks = os.getenv('COMMUNITY_WEBHOOK_URLS', '')
        if community_webhooks:
            self.COMMUNITY_WEBHOOK_URLS = [url.strip() for url in community_webhooks.split(',') if url.strip()]
        
        community_roles = os.getenv('COMMUNITY_ROLE_IDS', '')
        if community_roles:
            self.COMMUNITY_ROLE_IDS = [role_id.strip() for role_id in community_roles.split(',') if role_id.strip()]
    
    def _validate_required_settings(self):
        """Validate that required settings are present."""
        required_settings = [
            ('YOUTUBE_CHANNEL_ID', self.YOUTUBE_CHANNEL_ID),
        ]
        
        missing_settings = []
        for setting_name, setting_value in required_settings:
            if not setting_value:
                missing_settings.append(setting_name)
        
        if missing_settings:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_settings)}")
        
        # Check if at least one webhook URL is configured
        total_webhooks = len(self.UPLOAD_WEBHOOK_URLS) + len(self.LIVESTREAM_WEBHOOK_URLS) + len(self.COMMUNITY_WEBHOOK_URLS)
        if total_webhooks == 0:
            raise ValueError("At least one Discord webhook URL must be configured (UPLOAD_WEBHOOK_URLS, LIVESTREAM_WEBHOOK_URLS, or COMMUNITY_WEBHOOK_URLS)")
    
    def get_webhooks_for_type(self, content_type: str) -> List[str]:
        """Get webhook URLs for a specific content type."""
        type_mapping = {
            'upload': self.UPLOAD_WEBHOOK_URLS,
            'livestream': self.LIVESTREAM_WEBHOOK_URLS,
            'livestream_live': self.LIVESTREAM_WEBHOOK_URLS,  # Use same webhooks as scheduled livestream
            'community': self.COMMUNITY_WEBHOOK_URLS
        }
        return type_mapping.get(content_type, [])
    
    def get_roles_for_type(self, content_type: str) -> List[str]:
        """Get role IDs for a specific content type."""
        type_mapping = {
            'upload': self.UPLOAD_ROLE_IDS,
            'livestream': self.LIVESTREAM_ROLE_IDS,
            'livestream_live': self.LIVESTREAM_ROLE_IDS,  # Use same roles as scheduled livestream
            'community': self.COMMUNITY_ROLE_IDS
        }
        return type_mapping.get(content_type, [])
    
    @property
    def youtube_topic_url(self) -> str:
        """Generate the YouTube WebSub topic URL."""
        return f"https://www.youtube.com/xml/feeds/videos.xml?channel_id={self.YOUTUBE_CHANNEL_ID}"


# Global settings instance
settings = Settings()
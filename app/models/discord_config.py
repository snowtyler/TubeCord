"""
Discord configuration and server management models.
"""

from dataclasses import dataclass
from typing import List, Optional, Dict, Any


@dataclass
class DiscordServer:
    """Represents a Discord server configuration for a specific content type."""
    
    webhook_url: str
    role_ids: List[str]
    content_type: str  # 'upload', 'livestream', 'livestream_live', or 'community'
    server_name: Optional[str] = None
    enabled: bool = True
    
    def __post_init__(self):
        """Validate the configuration after initialization."""
        if not self.webhook_url:
            raise ValueError("webhook_url is required")
        
        if not self.webhook_url.startswith('https://discord.com/api/webhooks/'):
            raise ValueError("Invalid Discord webhook URL format")
    
    @property
    def role_mentions(self) -> List[str]:
        """Get formatted role mentions for Discord."""
        return [f'<@&{role_id}>' for role_id in self.role_ids if role_id]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return {
            'webhook_url': self.webhook_url,
            'role_ids': self.role_ids,
            'content_type': self.content_type,
            'server_name': self.server_name,
            'enabled': self.enabled
        }


class DiscordConfiguration:
    """Manages Discord server configurations."""
    
    def __init__(self):
        self.servers: List[DiscordServer] = []
    
    def add_server(self, webhook_url: str, role_ids: List[str], content_type: str, server_name: Optional[str] = None):
        """
        Add a Discord server configuration.
        
        Args:
            webhook_url: Discord webhook URL
            role_ids: List of role IDs to mention
            content_type: Type of content ('upload', 'livestream', 'livestream_live', 'community')
            server_name: Optional server name for identification
        """
        server = DiscordServer(
            webhook_url=webhook_url,
            role_ids=role_ids,
            content_type=content_type,
            server_name=server_name
        )
        self.servers.append(server)
    
    def get_enabled_servers(self) -> List[DiscordServer]:
        """Get all enabled Discord servers."""
        return [server for server in self.servers if server.enabled]
    
    def get_servers_for_type(self, content_type: str) -> List[DiscordServer]:
        """Get enabled servers for a specific content type."""
        # Map livestream_live to livestream so both use the same servers
        if content_type == 'livestream_live':
            content_type = 'livestream'
        return [server for server in self.servers if server.enabled and server.content_type == content_type]
    
    def get_all_webhook_urls(self) -> List[str]:
        """Get all webhook URLs from enabled servers."""
        return [server.webhook_url for server in self.get_enabled_servers()]
    
    def get_all_role_ids(self) -> List[str]:
        """Get all role IDs from enabled servers."""
        role_ids = []
        for server in self.get_enabled_servers():
            role_ids.extend(server.role_ids)
        return list(set(role_ids))  # Remove duplicates
    
    def disable_server(self, webhook_url: str):
        """Disable a server by webhook URL."""
        for server in self.servers:
            if server.webhook_url == webhook_url:
                server.enabled = False
                break
    
    def enable_server(self, webhook_url: str):
        """Enable a server by webhook URL."""
        for server in self.servers:
            if server.webhook_url == webhook_url:
                server.enabled = True
                break
    
    def remove_server(self, webhook_url: str):
        """Remove a server configuration."""
        self.servers = [s for s in self.servers if s.webhook_url != webhook_url]
    
    @classmethod
    def from_settings(cls, settings) -> 'DiscordConfiguration':
        """
        Create configuration from settings object with per-content-type configurations.
        
        Args:
            settings: Settings instance with per-content-type webhook URLs and roles
            
        Returns:
            DiscordConfiguration instance
        """
        config = cls()
        
        # Add upload servers
        for i, webhook_url in enumerate(settings.UPLOAD_WEBHOOK_URLS):
            server_name = f"Upload_Server_{i+1}"
            config.add_server(webhook_url, settings.UPLOAD_ROLE_IDS, 'upload', server_name)
        
        # Add livestream servers (handles both scheduled and live notifications)
        for i, webhook_url in enumerate(settings.LIVESTREAM_WEBHOOK_URLS):
            server_name = f"Livestream_Server_{i+1}"
            config.add_server(webhook_url, settings.LIVESTREAM_ROLE_IDS, 'livestream', server_name)
        
        # Add community servers
        for i, webhook_url in enumerate(settings.COMMUNITY_WEBHOOK_URLS):
            server_name = f"Community_Server_{i+1}"
            config.add_server(webhook_url, settings.COMMUNITY_ROLE_IDS, 'community', server_name)
        
        return config
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary format."""
        return {
            'servers': [server.to_dict() for server in self.servers]
        }
    
    def __len__(self) -> int:
        """Get the number of configured servers."""
        return len(self.servers)
    
    def __bool__(self) -> bool:
        """Check if any servers are configured."""
        return len(self.servers) > 0
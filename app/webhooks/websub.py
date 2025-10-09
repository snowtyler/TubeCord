"""
WebSub (PubSubHubbub) webhook handler for YouTube notifications.
Handles challenge verification and incoming push notifications.
"""

import logging
import xml.etree.ElementTree as ET
import hmac
import hashlib
from flask import Blueprint, request, abort
from typing import Dict, Any, Optional
from urllib.parse import parse_qs

logger = logging.getLogger(__name__)

websub_bp = Blueprint('websub', __name__)


class WebSubHandler:
    """Handles WebSub protocol for YouTube channel notifications."""
    
    def __init__(self):
        self.namespace = {
            'atom': 'http://www.w3.org/2005/Atom',
            'yt': 'http://www.youtube.com/xml/schemas/2015'
        }
    
    def verify_challenge(self, args: Dict[str, Any]) -> str:
        """
        Handle WebSub challenge verification during subscription.
        
        Args:
            args: Query parameters from the GET request
            
        Returns:
            Challenge string to echo back to hub
            
        Raises:
            ValueError: If required parameters are missing
        """
        # Log all received parameters for debugging
        logger.debug(f"Received challenge parameters: {dict(args)}")
        
        hub_mode = args.get('hub.mode')
        hub_topic = args.get('hub.topic')
        hub_challenge = args.get('hub.challenge')
        hub_lease_seconds = args.get('hub.lease_seconds')
        
        # Debug log each parameter
        logger.debug(f"hub.mode: {hub_mode}")
        logger.debug(f"hub.topic: {hub_topic}")
        logger.debug(f"hub.challenge: {hub_challenge}")
        logger.debug(f"hub.lease_seconds: {hub_lease_seconds}")
        
        if not all([hub_mode, hub_topic, hub_challenge]):
            missing = []
            if not hub_mode:
                missing.append('hub.mode')
            if not hub_topic:
                missing.append('hub.topic')
            if not hub_challenge:
                missing.append('hub.challenge')
            raise ValueError(f"Missing required WebSub challenge parameters: {', '.join(missing)}")
        
        if hub_mode not in ['subscribe', 'unsubscribe']:
            raise ValueError(f"Invalid hub.mode: {hub_mode}")
        
        logger.info(f"WebSub challenge verification: mode={hub_mode}, topic={hub_topic}, lease={hub_lease_seconds}")
        
        return hub_challenge

    def verify_signature(self, headers: Dict[str, Any], body: bytes, secret: str) -> bool:
        """Validate WebSub notification signature using shared secret."""
        if not secret:
            return True

        signature_header = headers.get('X-Hub-Signature-256')

        if not signature_header:
            signature_header = headers.get('X-Hub-Signature')

        if not signature_header:
            logger.warning("Missing WebSub signature header with configured callback secret")
            return False

        try:
            algo, provided_signature = signature_header.split('=', 1)
        except ValueError:
            logger.warning("Malformed WebSub signature header")
            return False

        algo = algo.lower()
        if algo not in ('sha1', 'sha256'):
            logger.warning(f"Unsupported WebSub signature algorithm: {algo}")
            return False

        digestmod = hashlib.sha256 if algo == 'sha256' else hashlib.sha1
        computed = hmac.new(secret.encode('utf-8'), body, digestmod=digestmod).hexdigest()

        if hmac.compare_digest(computed, provided_signature):
            logger.debug("WebSub signature verification succeeded")
            return True

        logger.warning("WebSub signature verification failed")
        return False
    
    def parse_notification(self, xml_content: str) -> Optional[Dict[str, Any]]:
        """
        Parse YouTube notification XML into structured data.
        
        Args:
            xml_content: Raw XML content from YouTube WebSub notification
            
        Returns:
            Dictionary containing notification data or None if parsing fails.
            Returns a dict with 'deleted': True for deleted/privated videos.
        """
        try:
            root = ET.fromstring(xml_content)
            logger.debug(f"Root element: {root.tag}, attributes: {root.attrib}")
            logger.debug(f"Root namespace: {root.tag.split('}')[0] if '}' in root.tag else 'No namespace'}")
            
            # Check for deleted-entry (video was deleted or made private)
            deleted_entry = root.find('{http://purl.org/atompub/tombstones/1.0}deleted-entry')
            if deleted_entry is not None:
                logger.info("Detected deleted/privated video notification")
                
                # Extract what information we can from deleted-entry
                ref_attr = deleted_entry.get('ref')  # Contains the video URL
                when_attr = deleted_entry.get('when')  # Deletion timestamp
                
                # Try to extract video ID from ref attribute
                video_id = None
                if ref_attr and 'watch?v=' in ref_attr:
                    video_id = ref_attr.split('watch?v=')[-1].split('&')[0]
                
                # Look for at:by element which contains channel info
                by_elem = deleted_entry.find('{http://purl.org/atompub/tombstones/1.0}by')
                channel_id = None
                channel_name = None
                
                if by_elem is not None:
                    # Check for name and URI in the by element
                    name_elem = by_elem.find('{http://www.w3.org/2005/Atom}name')
                    uri_elem = by_elem.find('{http://www.w3.org/2005/Atom}uri')
                    
                    if name_elem is not None:
                        channel_name = name_elem.text
                    
                    if uri_elem is not None and uri_elem.text:
                        # URI format: http://www.youtube.com/channel/CHANNEL_ID
                        if '/channel/' in uri_elem.text:
                            channel_id = uri_elem.text.split('/channel/')[-1]
                
                logger.info(f"Deleted video: ID={video_id}, Channel={channel_id}, Deleted at={when_attr}")
                
                return {
                    'deleted': True,
                    'video_id': video_id,
                    'channel_id': channel_id,
                    'channel_name': channel_name,
                    'deleted_at': when_attr,
                    'url': ref_attr
                }
            
            # Find the entry element (normal video notification)
            entry = root.find('atom:entry', self.namespace)
            if entry is None:
                # Try without namespace
                entry = root.find('entry')
            if entry is None:
                logger.warning("No entry found in notification XML")
                logger.debug(f"Available child elements: {[child.tag for child in root]}")
                logger.debug(f"Root tag: {root.tag}")
                logger.debug(f"Trying to find entry with all namespaces...")
                
                # Try to find any element with 'entry' in the tag name
                for child in root:
                    logger.debug(f"  - {child.tag}")
                    if 'entry' in child.tag.lower():
                        entry = child
                        logger.info(f"Found entry element with tag: {child.tag}")
                        break
                
                if entry is None:
                    logger.error("Could not find entry element in XML")
                    return None
            
            logger.debug(f"Entry found: {entry.tag}")
            logger.debug(f"Entry children: {[child.tag for child in entry]}")
            
            # Extract video information
            video_id_elem = entry.find('yt:videoId', self.namespace)
            channel_id_elem = entry.find('yt:channelId', self.namespace)
            
            logger.debug(f"video_id_elem: {video_id_elem}")
            logger.debug(f"channel_id_elem: {channel_id_elem}")
            
            if video_id_elem is not None:
                logger.debug(f"Video ID: {video_id_elem.text}")
            if channel_id_elem is not None:
                logger.debug(f"Channel ID: {channel_id_elem.text}")
            
            title_elem = entry.find('atom:title', self.namespace)
            link_elem = entry.find('atom:link[@rel="alternate"]', self.namespace)
            author_elem = entry.find('atom:author/atom:name', self.namespace)
            published_elem = entry.find('atom:published', self.namespace)
            updated_elem = entry.find('atom:updated', self.namespace)
            live_broadcast_elem = entry.find('yt:liveBroadcastContent', self.namespace)
            
            if video_id_elem is None or channel_id_elem is None:
                logger.warning("Missing required video or channel ID in notification")
                return None
            
            if not video_id_elem.text or not channel_id_elem.text:
                logger.warning("Video or channel ID elements found but have no text content")
                return None
            
            notification_data = {
                'video_id': video_id_elem.text,
                'channel_id': channel_id_elem.text,
                'title': title_elem.text if title_elem is not None else 'Unknown Title',
                'url': link_elem.get('href') if link_elem is not None else f"https://www.youtube.com/watch?v={video_id_elem.text}",
                'author': author_elem.text if author_elem is not None else 'Unknown Author',
                'published': published_elem.text if published_elem is not None else None,
                'updated': updated_elem.text if updated_elem is not None else None,
            }

            if live_broadcast_elem is not None and live_broadcast_elem.text:
                notification_data['live_broadcast_content'] = live_broadcast_elem.text.strip().lower()
            
            logger.info(f"Parsed notification for video: {notification_data['video_id']} - {notification_data['title']}")
            logger.info(f"Notification details - Published: {notification_data['published']}, Updated: {notification_data['updated']}")
            logger.info(f"Video URL: {notification_data['url']}")
            
            # Log the full XML for debugging livestream detection
            logger.debug(f"Full notification XML: {xml_content}")
            
            return notification_data
            
        except ET.ParseError as e:
            logger.error(f"Failed to parse XML notification: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error parsing notification: {e}")
            return None


# Note: The webhook route is now handled in main.py to integrate with notification processing
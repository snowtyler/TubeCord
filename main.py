"""
Main Flask application entry point for TubeCord.
Handles WebSub subscriptions and YouTube notifications.
"""

import sys
import os
import requests
import threading
import time
from flask import Flask

# Add the app directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from app.version import VERSION, VERSION_INFO
from app.config.settings import settings
from app.utils.logging import setup_logging, get_logger, log_websub_event, log_discord_event, log_notification_processing
from app.webhooks.websub import WebSubHandler
from app.discord.client import DiscordClient
from app.models.notification import YouTubeNotification, NotificationType
from app.models.discord_config import DiscordConfiguration
from app.config.messages import MessageTemplates, NOTIFICATION_CONFIG

# Initialize logging
setup_logging(settings.LOG_LEVEL, use_colors=True)
logger = get_logger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Initialize components
discord_client = DiscordClient()
discord_config = DiscordConfiguration.from_settings(settings)

# Initialize community post monitoring
community_scheduler = None
community_handler = None


class WebSubSubscriptionManager:
    """Manages WebSub subscriptions to YouTube channels."""
    
    def __init__(self):
        self.subscription_active = False
        self.lease_seconds = 432000  # 5 days
        self.last_subscription_time = None
        self.last_verification_time = None
        self.last_notification_time = None
    
    def subscribe_to_channel(self) -> bool:
        """
        Subscribe to YouTube channel WebSub notifications.
        
        Returns:
            True if subscription was successful, False otherwise
        """
        try:
            subscription_data = {
                'hub.callback': settings.CALLBACK_URL,
                'hub.topic': settings.youtube_topic_url,
                'hub.mode': 'subscribe',
                'hub.lease_seconds': str(self.lease_seconds)
            }

            if settings.CALLBACK_SECRET:
                subscription_data['hub.secret'] = settings.CALLBACK_SECRET
            
            logger.info(f"Subscribing to WebSub for channel: {settings.YOUTUBE_CHANNEL_ID}")
            logger.debug(f"Subscription data: {subscription_data}")
            
            response = requests.post(
                settings.WEBSUB_HUB_URL,
                data=subscription_data,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=10
            )
            
            if response.status_code in [202, 204]:
                from datetime import datetime, timezone
                self.last_subscription_time = datetime.now(timezone.utc)
                logger.info(f"WebSub subscription request accepted at {self.last_subscription_time.isoformat()}")
                log_websub_event(logger, 'subscription_requested', {
                    'channel_id': settings.YOUTUBE_CHANNEL_ID,
                    'callback_url': settings.CALLBACK_URL,
                    'lease_seconds': self.lease_seconds
                })
                self.subscription_active = True
                return True
            else:
                logger.error(f"WebSub subscription failed: {response.status_code} - {response.text}")
                return False
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to subscribe to WebSub: {e}")
            return False
    
    def unsubscribe_from_channel(self) -> bool:
        """
        Unsubscribe from YouTube channel WebSub notifications.
        
        Returns:
            True if unsubscription was successful, False otherwise
        """
        try:
            unsubscription_data = {
                'hub.callback': settings.CALLBACK_URL,
                'hub.topic': settings.youtube_topic_url,
                'hub.mode': 'unsubscribe'
            }
            
            logger.info(f"Unsubscribing from WebSub for channel: {settings.YOUTUBE_CHANNEL_ID}")
            
            response = requests.post(
                settings.WEBSUB_HUB_URL,
                data=unsubscription_data,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=10
            )
            
            if response.status_code in [202, 204]:
                logger.info("WebSub unsubscription request accepted")
                log_websub_event(logger, 'unsubscription_requested', {
                    'channel_id': settings.YOUTUBE_CHANNEL_ID
                })
                self.subscription_active = False
                return True
            else:
                logger.error(f"WebSub unsubscription failed: {response.status_code} - {response.text}")
                return False
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to unsubscribe from WebSub: {e}")
            return False
    
    def schedule_renewal(self):
        """Schedule subscription renewal before lease expires."""
        def renewal_task():
            # Renew subscription 1 hour before it expires
            renewal_delay = self.lease_seconds - 3600
            time.sleep(renewal_delay)
            
            if self.subscription_active:
                logger.info("Renewing WebSub subscription")
                self.subscribe_to_channel()
                self.schedule_renewal()  # Schedule next renewal
        
        if self.subscription_active:
            thread = threading.Thread(target=renewal_task, daemon=True)
            thread.start()


# Global subscription manager
subscription_manager = WebSubSubscriptionManager()


def process_youtube_notification(notification_data: dict) -> bool:
    """
    Process a YouTube notification and send to Discord.
    
    Args:
        notification_data: Parsed notification data from WebSub
        
    Returns:
        True if notification was processed successfully, False otherwise
    """
    try:
        # Create notification model
        notification = YouTubeNotification.from_websub_data(notification_data)
        
        logger.info(f"Processing notification: {notification.title} by {notification.author}")
        
        # Skip completed livestreams (they've already been notified when they went live)
        if notification.notification_type == NotificationType.LIVESTREAM_COMPLETED:
            logger.info(f"Skipping completed livestream notification: {notification.title}")
            return True
        
        # Check if notification is recent (avoid spam from old videos)
        if not notification.is_recent(hours=24):
            logger.info(f"Skipping old notification: {notification.title}")
            return True
        
        # Get notification configuration
        notification_type = notification.notification_type.value
        logger.info(f"Notification type detected: {notification_type}")
        config = NOTIFICATION_CONFIG.get(notification_type, NOTIFICATION_CONFIG['upload'])
        
        if not config['enabled']:
            logger.info(f"Notifications disabled for type: {notification_type}")
            return True
        
        # Format message using templates
        formatted_messages = MessageTemplates.format_message(
            config['template_type'],
            notification.to_dict()
        )
        
        # Send to Discord servers configured for this content type
        content_type_servers = discord_config.get_servers_for_type(notification_type)
        success_count = 0
        total_servers = len(content_type_servers)
        
        logger.info(f"Found {total_servers} servers for notification type '{notification_type}'")
        if total_servers > 0:
            server_names = [s.server_name or s.content_type for s in content_type_servers]
            logger.info(f"Servers: {', '.join(server_names)}")
        
        if total_servers == 0:
            logger.info(f"No Discord servers configured for content type: {notification_type}")
            return True  # Not an error if no servers configured for this type
        
        for server in content_type_servers:
            try:
                # Only use custom message for rich embeds
                custom_msg = formatted_messages.get('message') if config['use_rich_embed'] else None
                
                success = discord_client.send_youtube_notification(
                    webhook_url=server.webhook_url,
                    notification_data=notification.to_dict(),
                    role_mentions=server.role_ids,
                    custom_message=custom_msg,
                    use_rich_embed=config['use_rich_embed'],
                    notification_type=notification_type
                )
                
                if success:
                    success_count += 1
                    log_discord_event(
                        logger,
                        'notification_sent',
                        server.webhook_url,
                        True,
                        {'video_id': notification.video_id, 'title': notification.title}
                    )
                else:
                    log_discord_event(
                        logger,
                        'notification_failed',
                        server.webhook_url,
                        False,
                        {'video_id': notification.video_id, 'title': notification.title}
                    )
                    
            except Exception as e:
                logger.error(f"Error sending to Discord server: {e}")
                log_discord_event(
                    logger,
                    'notification_error',
                    server.webhook_url,
                    False,
                    {'error': str(e)}
                )
        
        # Log overall processing result
        overall_success = success_count > 0
        log_notification_processing(
            logger,
            notification.video_id,
            notification.title,
            overall_success,
            f"Sent to {success_count}/{total_servers} servers" if overall_success else "Failed to send to any servers"
        )
        
        return overall_success
        
    except Exception as e:
        logger.error(f"Error processing YouTube notification: {e}")
        return False


# Enhanced webhook handler that processes notifications
@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    """Enhanced webhook handler that processes notifications."""
    from flask import request
    from app.webhooks.websub import WebSubHandler
    
    handler = WebSubHandler()
    
    if request.method == 'GET':
        # Handle WebSub challenge verification
        try:
            # Log the full request URL and parameters for debugging
            logger.info(f"Received WebSub challenge request")
            logger.debug(f"Request URL: {request.url}")
            logger.debug(f"Query string: {request.query_string.decode('utf-8')}")
            logger.debug(f"Request args type: {type(request.args)}")
            logger.debug(f"Request args: {dict(request.args)}")
            
            challenge = handler.verify_challenge(request.args)
            from datetime import datetime, timezone
            subscription_manager.last_verification_time = datetime.now(timezone.utc)
            logger.info(f"WebSub challenge verification successful at {subscription_manager.last_verification_time.isoformat()}")
            return challenge, 200
        except ValueError as e:
            logger.error(f"Challenge verification failed: {e}")
            logger.error(f"Request URL: {request.url}")
            logger.error(f"Query parameters received: {dict(request.args)}")
            return '', 400
        except Exception as e:
            logger.error(f"Unexpected error in challenge verification: {e}")
            logger.error(f"Request URL: {request.url}")
            return '', 500
    
    elif request.method == 'POST':
        # Handle incoming notification
        raw_body = request.get_data()

        if not raw_body:
            logger.warning("Received empty notification")
            return '', 400

        if settings.CALLBACK_SECRET:
            if not handler.verify_signature(request.headers, raw_body, settings.CALLBACK_SECRET):
                logger.warning("Rejected WebSub notification due to signature mismatch")
                return 'Invalid signature', 403

        xml_content = raw_body.decode('utf-8', errors='replace')
        
        logger.info("Received WebSub notification from YouTube")
        logger.debug("===== RECEIVED WEBSUB NOTIFICATION =====")
        logger.debug(f"Content-Type: {request.headers.get('Content-Type')}")
        logger.debug(f"Content-Length: {request.headers.get('Content-Length')}")
        logger.debug(f"Full XML content:\n{xml_content}")
        logger.debug("========================================")
        
        notification_data = handler.parse_notification(xml_content)
        if notification_data:
            from datetime import datetime, timezone
            subscription_manager.last_notification_time = datetime.now(timezone.utc)
            logger.info(f"Received WebSub notification at {subscription_manager.last_notification_time.isoformat()}")
            
            # Handle deleted/privated videos gracefully
            if notification_data.get('deleted'):
                logger.info(f"Video deleted/privated: {notification_data.get('video_id')} from channel {notification_data.get('channel_id')}")
                logger.debug(f"Deletion details: {notification_data}")
                # Acknowledge receipt without processing further
                return 'OK', 200
            
            success = process_youtube_notification(notification_data)
            if success:
                return 'OK', 200
            else:
                return 'Processing failed', 500
        else:
            logger.error("Failed to parse notification")
            return 'Parse error', 400
    
    return '', 405


@app.route('/health')
def health_check():
    """Health check endpoint for monitoring."""
    return {
        'status': 'healthy',
        'version': VERSION,
        'subscription_active': subscription_manager.subscription_active,
        'discord_servers': {
            'upload': len(discord_config.get_servers_for_type('upload')),
            'livestream': len(discord_config.get_servers_for_type('livestream')),
            'community': len(discord_config.get_servers_for_type('community')),
            'total': len(discord_config.get_enabled_servers())
        }
    }, 200


@app.route('/websub/status')
def websub_status():
    """Get detailed WebSub subscription status and diagnostics."""
    from datetime import datetime, timezone, timedelta
    
    status = {
        'subscription_active': subscription_manager.subscription_active,
        'lease_seconds': subscription_manager.lease_seconds,
        'last_subscription_time': subscription_manager.last_subscription_time.isoformat() if subscription_manager.last_subscription_time else None,
        'last_verification_time': subscription_manager.last_verification_time.isoformat() if subscription_manager.last_verification_time else None,
        'last_notification_time': subscription_manager.last_notification_time.isoformat() if subscription_manager.last_notification_time else None,
        'callback_url': settings.CALLBACK_URL,
        'topic_url': settings.youtube_topic_url,
        'hub_url': settings.WEBSUB_HUB_URL,
        'channel_id': settings.YOUTUBE_CHANNEL_ID
    }
    
    # Calculate time since last events
    now = datetime.now(timezone.utc)
    if subscription_manager.last_subscription_time:
        time_since_subscription = (now - subscription_manager.last_subscription_time).total_seconds()
        status['seconds_since_subscription'] = int(time_since_subscription)
        status['subscription_expires_in'] = int(subscription_manager.lease_seconds - time_since_subscription)
        status['subscription_expired'] = time_since_subscription > subscription_manager.lease_seconds
    
    if subscription_manager.last_verification_time:
        status['seconds_since_verification'] = int((now - subscription_manager.last_verification_time).total_seconds())
    
    if subscription_manager.last_notification_time:
        status['seconds_since_notification'] = int((now - subscription_manager.last_notification_time).total_seconds())
    
    # Add warnings if subscription might be stale
    warnings = []
    if not subscription_manager.subscription_active:
        warnings.append('Subscription is not active')
    elif not subscription_manager.last_verification_time:
        warnings.append('No verification challenge received yet - subscription may not be confirmed')
    elif subscription_manager.last_subscription_time:
        if (now - subscription_manager.last_subscription_time).total_seconds() > subscription_manager.lease_seconds:
            warnings.append('Subscription has expired and needs renewal')
    
    status['warnings'] = warnings
    
    return status, 200


@app.route('/subscribe')
def manual_subscribe():
    """Manually trigger WebSub subscription (for testing)."""
    success = subscription_manager.subscribe_to_channel()
    if success:
        subscription_manager.schedule_renewal()
        return {'status': 'subscription_requested'}, 200
    else:
        return {'status': 'subscription_failed'}, 500


@app.route('/unsubscribe')
def manual_unsubscribe():
    """Manually trigger WebSub unsubscription (for testing)."""
    success = subscription_manager.unsubscribe_from_channel()
    return {'status': 'unsubscription_requested' if success else 'unsubscription_failed'}, 200 if success else 500


@app.route('/config')
def show_config():
    """Show current Discord configuration (for debugging)."""
    return {
        'discord_configuration': discord_config.to_dict(),
        'notification_config': NOTIFICATION_CONFIG,
        'websub_config': {
            'callback_url': settings.CALLBACK_URL,
            'youtube_topic_url': settings.youtube_topic_url,
            'hub_url': settings.WEBSUB_HUB_URL
        }
    }, 200


@app.route('/version')
def version_info():
    """Get version information."""
    return {
        'version': VERSION,
        'version_info': VERSION_INFO,
        'python_version': sys.version,
    'application': 'TubeCord'
    }, 200


@app.route('/ngrok-setup')
def ngrok_setup():
    """Provide ngrok setup instructions for local development."""
    return {
        'message': 'ngrok setup instructions for local development',
        'steps': [
            '1. Install ngrok: https://ngrok.com/download',
            f'2. Run: ngrok http {settings.PORT}',
            '3. Copy the https URL (e.g., https://abc123.ngrok.io)',
            '4. Add to .env: CALLBACK_URL=https://abc123.ngrok.io/webhook',
            '5. Restart the application',
            '6. Visit /subscribe to refresh WebSub subscription'
        ],
        'current_callback_url': settings.CALLBACK_URL,
        'is_local_development': 'localhost' in settings.CALLBACK_URL or '127.0.0.1' in settings.CALLBACK_URL or 'ngrok' in settings.CALLBACK_URL
    }, 200


@app.route('/test-notification', methods=['POST'])
def test_notification():
    """Test endpoint to simulate a YouTube upload notification."""
    test_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
    <entry>
        <yt:videoId>test123456</yt:videoId>
        <yt:channelId>UCXXXXXXXXXXXXXXXXXXXXXX</yt:channelId>
    <title>TEST: TubeCord Upload Notification</title>
        <author>
            <name>Example Creator</name>
        </author>
        <published>2025-10-06T16:00:00Z</published>
        <updated>2025-10-06T16:00:00Z</updated>
        <link rel="alternate" href="https://www.youtube.com/watch?v=test123456"/>
    </entry>
</feed>'''
    
    from app.webhooks.websub import WebSubHandler
    handler = WebSubHandler()
    
    logger.debug(f"Test XML being parsed: {test_xml}")
    notification_data = handler.parse_notification(test_xml)
    logger.debug(f"Parsed notification data: {notification_data}")
    
    if notification_data:
        success = process_youtube_notification(notification_data)
        return {
            'status': 'success' if success else 'failed',
            'notification_data': notification_data,
            'message': 'Test upload notification processed'
        }, 200 if success else 500
    else:
        return {'status': 'failed', 'message': 'Failed to parse test notification'}, 400


@app.route('/test-livestream', methods=['POST'])
def test_livestream():
    """Test endpoint to simulate a YouTube livestream notification."""
    from datetime import datetime, timezone, timedelta
    
    # Calculate a scheduled time 30 minutes from now for testing
    scheduled_time = datetime.now(timezone.utc) + timedelta(minutes=30)
    scheduled_time_str = scheduled_time.isoformat().replace('+00:00', 'Z')
    
    test_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
    <entry>
        <yt:videoId>livestream789</yt:videoId>
        <yt:channelId>UCXXXXXXXXXXXXXXXXXXXXXX</yt:channelId>
        <title>🔴 LIVE: Test Livestream Going Live Now!</title>
        <author>
            <name>Example Creator</name>
        </author>
        <published>2025-10-06T16:00:00Z</published>
        <updated>2025-10-06T16:00:00Z</updated>
        <link rel="alternate" href="https://www.youtube.com/watch?v=livestream789"/>
    </entry>
</feed>'''
    
    from app.webhooks.websub import WebSubHandler
    handler = WebSubHandler()
    
    logger.debug(f"Test XML being parsed: {test_xml}")
    notification_data = handler.parse_notification(test_xml)
    
    # Manually add scheduled start time since WebSub XML doesn't contain it
    # (In real usage, this comes from the YouTube API)
    if notification_data:
        notification_data['scheduled_start_time'] = scheduled_time_str
        logger.info(f"Added test scheduled start time: {scheduled_time_str}")
    
    logger.debug(f"Parsed notification data: {notification_data}")
    
    if notification_data:
        success = process_youtube_notification(notification_data)
        return {
            'status': 'success' if success else 'failed',
            'notification_data': notification_data,
            'message': 'Test notification processed'
        }, 200 if success else 500
    else:
        return {'status': 'failed', 'message': 'Failed to parse test notification'}, 400


@app.route('/community/check', methods=['POST'])
def force_community_check():
    """Force an immediate check for community posts."""
    if not community_scheduler:
        return {'status': 'error', 'message': 'Community post monitoring not initialized'}, 400
    
    try:
        new_posts = community_scheduler.force_check()
        return {
            'status': 'success',
            'message': f'Community check completed',
            'new_posts_found': len(new_posts),
            'posts': [post.to_dict() if hasattr(post, 'to_dict') else str(post) for post in new_posts]
        }, 200
    except Exception as e:
        logger.error(f"Error in forced community check: {e}")
        return {'status': 'error', 'message': str(e)}, 500


@app.route('/community/status')
def community_status():
    """Get the status of community post monitoring."""
    if not community_scheduler:
        return {
            'enabled': False,
            'message': 'Community post monitoring not initialized'
        }, 200
    
    try:
        status = community_scheduler.get_status()
        
        # Add database stats
        from app.utils.community_scraper import CommunityPostScraper
        scraper = CommunityPostScraper()
        unnotified_posts = scraper.get_new_posts_for_notification(settings.YOUTUBE_CHANNEL_ID)
        
        status.update({
            'enabled': True,
            'unnotified_posts': len(unnotified_posts),
            'configured_servers': len(discord_config.get_servers_for_type('community'))
        })
        
        return status, 200
    except Exception as e:
        logger.error(f"Error getting community status: {e}")
        return {'status': 'error', 'message': str(e)}, 500


@app.route('/test-community', methods=['POST'])
def test_community_post():
    """Test endpoint to simulate a community post notification."""
    if not community_handler:
        return {'status': 'error', 'message': 'Community post handler not initialized'}, 400
    
    # Create a test community post
    from app.utils.community_scraper import CommunityPost
    from datetime import datetime, timezone
    
    test_post = CommunityPost(
        post_id='test_community_123',
        channel_id=settings.YOUTUBE_CHANNEL_ID,
        channel_name='Test Channel',
    content='This is a test community post from TubeCord! 🎉\n\nTesting the community post notification system with some sample content.',
        image_urls=['https://img.youtube.com/vi/dQw4w9WgXcQ/maxresdefault.jpg'],
        video_attachments=[{
            'video_id': 'dQw4w9WgXcQ',
            'title': 'Test Video Attachment',
            'thumbnail': 'https://img.youtube.com/vi/dQw4w9WgXcQ/default.jpg'
        }],
        poll_data=None,
        published_time=datetime.now(timezone.utc).isoformat() + 'Z',
        like_count=42,
        url=f'https://www.youtube.com/post/test_community_123'
    )
    
    try:
        # Process the test post
        community_handler.handle_new_posts([test_post])
        
        return {
            'status': 'success',
            'message': 'Test community post notification sent',
            'test_post': test_post.to_dict()
        }, 200
        
    except Exception as e:
        logger.error(f"Error sending test community post: {e}")
        return {'status': 'error', 'message': str(e)}, 500


def initialize_app():
    """Initialize the application and subscribe to WebSub."""
    global community_scheduler, community_handler
    
    logger.info(f"Initializing TubeCord v{VERSION}")
    
    # Log configuration
    logger.info(f"YouTube Channel ID: {settings.YOUTUBE_CHANNEL_ID}")
    logger.info(f"Callback URL: {settings.CALLBACK_URL}")
    logger.info(f"WebSub signature verification: {'enabled' if settings.CALLBACK_SECRET else 'disabled'}")
    logger.info(f"Discord servers configured:")
    logger.info(f"  - Upload: {len(discord_config.get_servers_for_type('upload'))} servers")
    logger.info(f"  - Livestream: {len(discord_config.get_servers_for_type('livestream'))} servers")
    logger.info(f"  - Community: {len(discord_config.get_servers_for_type('community'))} servers")
    logger.info(f"  - Total: {len(discord_config)} servers")
    
    # Initialize community post monitoring if configured
    community_servers = discord_config.get_servers_for_type('community')
    if len(community_servers) > 0:
        try:
            from app.utils.scheduler import CommunityPostScheduler, CommunityPostNotificationHandler
            
            # Initialize community post handler
            community_handler = CommunityPostNotificationHandler()
            community_handler.initialize()
            
            # Initialize and start scheduler
            check_interval = settings.COMMUNITY_CHECK_INTERVAL_MINUTES
            community_scheduler = CommunityPostScheduler(check_interval_minutes=check_interval)
            
            # Set up callbacks
            community_scheduler.set_callbacks(
                on_posts_found=community_handler.handle_new_posts,
                on_check_complete=lambda check_time, count: logger.info(f"Community post check completed: {count} new posts found"),
                on_error=lambda error: logger.error(f"Community post scheduler error: {error}")
            )
            
            # Start the scheduler
            community_scheduler.start()
            logger.info(f"Community post monitoring started (checking every {check_interval} minutes)")
            
        except ImportError as e:
            logger.warning(f"Community post monitoring disabled: {e}")
        except Exception as e:
            logger.error(f"Failed to initialize community post monitoring: {e}")
    else:
        logger.info("Community post monitoring disabled (no Discord servers configured)")
    
    # Subscribe to WebSub notifications
    if subscription_manager.subscribe_to_channel():
        subscription_manager.schedule_renewal()
        logger.info("Application initialized successfully")
    else:
        logger.warning("Failed to subscribe to WebSub, but application will continue")


if __name__ == '__main__':
    try:
        initialize_app()
        logger.info(f"Starting production server on {settings.HOST}:{settings.PORT}")
        print("successfully finished startup")
        
        # Use Waitress production WSGI server (works on Windows and Unix)
        from waitress import serve
        serve(
            app,
            host=settings.HOST,
            port=settings.PORT,
            threads=4,  # Handle multiple requests concurrently
            channel_timeout=300,  # 5 minute timeout for long-running requests
            _quiet=False  # Show request logs
        )
    except KeyboardInterrupt:
        logger.info("Shutting down application")
        subscription_manager.unsubscribe_from_channel()
        if community_scheduler:
            community_scheduler.stop()
    except Exception as e:
        logger.error(f"Application startup failed: {e}")
        if community_scheduler:
            community_scheduler.stop()
        sys.exit(1)
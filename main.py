"""
Main Flask application entry point for TubeCord.
Handles WebSub subscriptions and YouTube notifications.
"""

import sys
import os
import random
import requests
import threading
import time
from datetime import datetime, timezone, timedelta
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
# Separate destinations for /test-* injections so they never post to the
# public production channels.
test_discord_config = DiscordConfiguration.from_settings(settings, test=True)

# Initialize community post monitoring
community_scheduler = None
community_handler = None

# Upload polling fallback (safety net for WebSub push outages)
upload_poll_scheduler = None
upload_notification_store = None
upload_feed_scraper = None


class WebSubSubscriptionManager:
    """Manages WebSub subscriptions to YouTube channels.

    Resilience model:
    * ``subscribe_to_channel`` sends one subscribe request. The hub returning
      202/204 means *accepted*, not *verified*.
    * ``subscribe_with_retry`` rides out transient hub failures (e.g. the hub's
      intermittent HTTP 503) with exponential backoff.
    * A background watchdog re-subscribes when the subscription is unverified,
      near lease expiry, or was accepted but never verified within a timeout —
      so a single flaky-hub moment can't silently kill delivery for days.
    """

    def __init__(self):
        # ``subscription_active`` = the most recent subscribe POST was accepted.
        self.subscription_active = False
        # ``subscription_confirmed`` = the hub completed the GET challenge.
        self.subscription_confirmed = False
        self.lease_seconds = settings.WEBSUB_LEASE_SECONDS
        self.last_subscription_time = None        # last accepted subscribe request
        self.last_subscribe_attempt_time = None   # last subscribe POST (any outcome)
        self.last_verification_time = None        # last hub challenge verified
        self.last_notification_time = None
        self._stop = threading.Event()

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

            self.last_subscribe_attempt_time = datetime.now(timezone.utc)
            response = requests.post(
                settings.WEBSUB_HUB_URL,
                data=subscription_data,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=10
            )
            
            if response.status_code in [202, 204]:
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
    
    def subscribe_with_retry(self) -> bool:
        """Attempt to subscribe, retrying with exponential backoff + jitter.

        Handles transient hub failures (notably the hub's intermittent
        HTTP 503) that would otherwise let the subscription lapse. Returns
        True as soon as a subscribe request is accepted (202/204).
        """
        attempts = settings.WEBSUB_SUBSCRIBE_MAX_RETRIES
        base = settings.WEBSUB_SUBSCRIBE_RETRY_BASE_SECONDS
        cap = settings.WEBSUB_SUBSCRIBE_RETRY_MAX_SECONDS

        for attempt in range(1, attempts + 1):
            if self._stop.is_set():
                return False
            if self.subscribe_to_channel():
                logger.info(f"Subscribe request accepted (attempt {attempt}/{attempts})")
                return True
            if attempt < attempts:
                delay = min(cap, base * (2 ** (attempt - 1)))
                delay += random.uniform(0, delay * 0.25)  # jitter to avoid lockstep retries
                logger.warning(
                    f"Subscribe attempt {attempt}/{attempts} failed; retrying in {delay:.0f}s"
                )
                self._stop.wait(timeout=delay)

        logger.error(f"All {attempts} subscribe attempts failed")
        return False

    def _needs_renewal(self) -> tuple[bool, str]:
        """Decide whether the subscription needs (re)subscribing right now."""
        now = datetime.now(timezone.utc)

        if self.last_verification_time is None:
            return True, "no verified subscription yet"

        verified_age = (now - self.last_verification_time).total_seconds()
        if verified_age >= self.lease_seconds - settings.WEBSUB_RENEWAL_LEAD_SECONDS:
            return True, f"verified lease near/after expiry ({int(verified_age)}s old)"

        # Accepted a subscribe request but the hub never came back to verify it.
        if (self.last_subscription_time is not None
                and self.last_subscription_time > self.last_verification_time
                and (now - self.last_subscription_time).total_seconds()
                >= settings.WEBSUB_VERIFY_TIMEOUT_SECONDS):
            return True, "last subscribe accepted but never verified"

        return False, ""

    def run_watchdog(self):
        """Subscribe immediately, then periodically ensure the sub stays live."""
        def loop():
            self.subscribe_with_retry()
            interval = settings.WEBSUB_WATCHDOG_INTERVAL_SECONDS
            while not self._stop.wait(timeout=interval):
                needed, reason = self._needs_renewal()
                if needed:
                    logger.info(f"Watchdog renewing WebSub subscription: {reason}")
                    self.subscribe_with_retry()

        threading.Thread(target=loop, daemon=True, name="websub-watchdog").start()

    def stop(self):
        """Signal the watchdog / retry loops to exit."""
        self._stop.set()


# Global subscription manager
subscription_manager = WebSubSubscriptionManager()


def process_youtube_notification(notification_data: dict, *, config_source=None,
                                 test_mode: bool = False, force_type=None,
                                 source: str = 'websub') -> bool:
    """
    Process a YouTube notification and send to Discord.

    Args:
        notification_data: Parsed notification data from WebSub
        config_source: DiscordConfiguration to deliver through (defaults to the
            production ``discord_config``). ``/test-*`` passes ``test_discord_config``.
        test_mode: When True, bypass the recency/completed-livestream gates so a
            synthetic injection always delivers, and treat a missing destination
            as an error instead of a silent no-op.
        force_type: Optional ``NotificationType`` to classify as, skipping the
            YouTube API lookup (used by test injectors so a livestream test
            renders as a livestream rather than being misread as an upload).

    Returns:
        True if notification was processed successfully, False otherwise
    """
    try:
        config_source = config_source if config_source is not None else discord_config

        # Create notification model. A forced type skips the API classification.
        if force_type is not None:
            notification = YouTubeNotification(
                video_id=notification_data['video_id'],
                channel_id=notification_data['channel_id'],
                title=notification_data['title'],
                author=notification_data['author'],
                url=notification_data['url'],
                published=notification_data.get('published'),
                updated=notification_data.get('updated'),
                notification_type=force_type,
                scheduled_start_time=notification_data.get('scheduled_start_time'),
                actual_start_time=notification_data.get('actual_start_time'),
            )
        else:
            notification = YouTubeNotification.from_websub_data(notification_data)

        logger.info(f"Processing notification: {notification.title} by {notification.author}"
                    f"{' [TEST]' if test_mode else ''} (source={source})")

        # Cross-path dedup: if the other path (WebSub or poll) already announced
        # this video, don't send it again.
        if not test_mode and upload_notification_store is not None:
            if upload_notification_store.seen(notification.video_id):
                logger.info(f"Skipping already-notified video {notification.video_id} (dedup)")
                return True

        if not test_mode:
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
        content_type_servers = config_source.get_servers_for_type(notification_type)
        success_count = 0
        total_servers = len(content_type_servers)

        logger.info(f"Found {total_servers} servers for notification type '{notification_type}'")
        if total_servers > 0:
            server_names = [s.server_name or s.content_type for s in content_type_servers]
            logger.info(f"Servers: {', '.join(server_names)}")

        if total_servers == 0:
            if test_mode:
                logger.error(
                    f"No TEST webhook configured for '{notification_type}'. Set "
                    f"TEST_{notification_type.split('_')[0].upper()}_WEBHOOK_URLS to a "
                    f"test-channel webhook so tests don't post to production.")
                return False
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

        # Record delivered videos so the other path (WebSub/poll) won't re-send.
        if overall_success and not test_mode and upload_notification_store is not None:
            upload_notification_store.mark(
                notification.video_id, notification.channel_id, notification.title, source)

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
            subscription_manager.last_verification_time = datetime.now(timezone.utc)
            subscription_manager.subscription_confirmed = True
            subscription_manager.subscription_active = True
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


@app.route('/')
@app.route('/dashboard')
def dashboard():
    """Serve the operator WebSub dashboard (HTML)."""
    from app.web.dashboard import render_dashboard
    return render_dashboard(VERSION), 200, {'Content-Type': 'text/html; charset=utf-8'}


@app.route('/health')
def health_check():
    """Health check endpoint for monitoring."""
    return {
        'status': 'healthy',
        'version': VERSION,
        'subscription_active': subscription_manager.subscription_active,
        'subscription_confirmed': subscription_manager.subscription_confirmed,
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
    status = {
        'subscription_active': subscription_manager.subscription_active,
        'subscription_confirmed': subscription_manager.subscription_confirmed,
        'lease_seconds': subscription_manager.lease_seconds,
        'last_subscription_time': subscription_manager.last_subscription_time.isoformat() if subscription_manager.last_subscription_time else None,
        'last_subscribe_attempt_time': subscription_manager.last_subscribe_attempt_time.isoformat() if subscription_manager.last_subscribe_attempt_time else None,
        'last_verification_time': subscription_manager.last_verification_time.isoformat() if subscription_manager.last_verification_time else None,
        'last_notification_time': subscription_manager.last_notification_time.isoformat() if subscription_manager.last_notification_time else None,
        'callback_url': settings.CALLBACK_URL,
        'topic_url': settings.youtube_topic_url,
        'hub_url': settings.WEBSUB_HUB_URL,
        'channel_id': settings.YOUTUBE_CHANNEL_ID
    }

    # Time since last events. Lease expiry is anchored on the last *verified*
    # subscription (the only event that proves the hub will deliver), not on
    # the last accepted subscribe request.
    now = datetime.now(timezone.utc)
    if subscription_manager.last_subscription_time:
        status['seconds_since_subscription'] = int((now - subscription_manager.last_subscription_time).total_seconds())

    if subscription_manager.last_verification_time:
        verified_age = (now - subscription_manager.last_verification_time).total_seconds()
        status['seconds_since_verification'] = int(verified_age)
        status['subscription_expires_in'] = int(subscription_manager.lease_seconds - verified_age)
        status['subscription_expired'] = verified_age > subscription_manager.lease_seconds
    else:
        status['subscription_expired'] = True

    if subscription_manager.last_notification_time:
        status['seconds_since_notification'] = int((now - subscription_manager.last_notification_time).total_seconds())

    # Add warnings if subscription might be stale
    warnings = []
    if not subscription_manager.subscription_confirmed:
        warnings.append('Subscription not confirmed by hub verification yet')
    if not subscription_manager.last_verification_time:
        warnings.append('No verification challenge received yet - subscription may not be live')
    elif (now - subscription_manager.last_verification_time).total_seconds() > subscription_manager.lease_seconds:
        warnings.append('Verified lease has expired and needs renewal')
    if (subscription_manager.last_subscription_time and subscription_manager.last_verification_time
            and subscription_manager.last_subscription_time > subscription_manager.last_verification_time
            and (now - subscription_manager.last_subscription_time).total_seconds() > settings.WEBSUB_VERIFY_TIMEOUT_SECONDS):
        warnings.append('Last subscribe was accepted but never verified by the hub')

    status['warnings'] = warnings
    
    return status, 200


@app.route('/subscribe')
def manual_subscribe():
    """Manually trigger a WebSub subscription (with retry/backoff)."""
    success = subscription_manager.subscribe_with_retry()
    if success:
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
        # Stamp "now" so the rendered timestamp is sensible (delivery itself
        # bypasses the recency gate in test_mode).
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        notification_data['published'] = now
        notification_data['updated'] = now
        success = process_youtube_notification(
            notification_data,
            config_source=test_discord_config,
            test_mode=True,
            force_type=NotificationType.UPLOAD,
        )
        return {
            'status': 'success' if success else 'failed',
            'notification_data': notification_data,
            'message': 'Test upload notification sent to test channel' if success
                       else 'Failed — is TEST_UPLOAD_WEBHOOK_URLS configured?'
        }, 200 if success else 500
    else:
        return {'status': 'failed', 'message': 'Failed to parse test notification'}, 400


@app.route('/test-livestream', methods=['POST'])
def test_livestream():
    """Test endpoint to simulate a YouTube livestream notification."""
    
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
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        notification_data['published'] = now
        notification_data['updated'] = now
        logger.info(f"Added test scheduled start time: {scheduled_time_str}")

    logger.debug(f"Parsed notification data: {notification_data}")

    if notification_data:
        success = process_youtube_notification(
            notification_data,
            config_source=test_discord_config,
            test_mode=True,
            force_type=NotificationType.LIVESTREAM,
        )
        return {
            'status': 'success' if success else 'failed',
            'notification_data': notification_data,
            'message': 'Test livestream notification sent to test channel' if success
                       else 'Failed — is TEST_LIVESTREAM_WEBHOOK_URLS configured?'
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

    if not settings.TEST_COMMUNITY_WEBHOOK_URLS:
        return {'status': 'error',
                'message': 'No test channel configured. Set TEST_COMMUNITY_WEBHOOK_URLS '
                           'so tests do not post to your production community channel.'}, 400

    # Create a test community post
    from app.utils.community_scraper import CommunityPost
    
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
        # Route to the test channel and don't mark the fake post notified.
        community_handler.handle_new_posts(
            [test_post],
            webhook_urls=settings.TEST_COMMUNITY_WEBHOOK_URLS,
            role_ids=settings.TEST_COMMUNITY_ROLE_IDS,
            mark_notified=False,
        )

        return {
            'status': 'success',
            'message': 'Test community post notification sent to test channel',
            'test_post': test_post.to_dict()
        }, 200

    except Exception as e:
        logger.error(f"Error sending test community post: {e}")
        return {'status': 'error', 'message': str(e)}, 500


@app.route('/upload/status')
def upload_status():
    """Status of the upload polling fallback."""
    if not upload_poll_scheduler:
        return {'enabled': False, 'message': 'Upload polling fallback not initialized'}, 200
    seeded = bool(upload_notification_store and upload_notification_store.is_seeded(settings.YOUTUBE_CHANNEL_ID))
    last = upload_poll_scheduler.last_check_time
    return {
        'enabled': True,
        'seeded': seeded,
        'interval_minutes': settings.UPLOAD_CHECK_INTERVAL_MINUTES,
        'last_check_time': last.isoformat() if last else None,
        'configured_servers': len(discord_config.get_servers_for_type('upload')),
    }, 200


@app.route('/upload/check', methods=['POST'])
def force_upload_check():
    """Force an immediate upload-feed poll."""
    if not upload_poll_scheduler:
        return {'status': 'error', 'message': 'Upload polling fallback not initialized'}, 400
    try:
        delivered = _poll_uploads_once()
        return {'status': 'success', 'delivered': delivered}, 200
    except Exception as e:
        logger.error(f"Error in forced upload check: {e}")
        return {'status': 'error', 'message': str(e)}, 500


def _poll_uploads_once() -> int:
    """One upload-poll cycle: fetch the feed and deliver anything WebSub missed.

    On the first run for a channel the current feed is seeded as already-seen so
    the backlog isn't blasted; thereafter only new videos are delivered. Returns
    the number of videos delivered this cycle.
    """
    if upload_feed_scraper is None or upload_notification_store is None:
        return 0

    channel_id = settings.YOUTUBE_CHANNEL_ID
    entries = upload_feed_scraper.fetch_entries(channel_id)
    if not entries:
        logger.debug("Upload poll: feed returned no entries")
        return 0

    if not upload_notification_store.is_seeded(channel_id):
        upload_notification_store.seed(channel_id, entries)
        return 0

    # Oldest-first so multiple missed uploads arrive in publish order.
    delivered = 0
    for entry in reversed(entries):
        if upload_notification_store.seen(entry['video_id']):
            continue
        logger.info(f"Upload poll: delivering missed video {entry['video_id']} - {entry['title']}")
        if process_youtube_notification(entry, source='poll'):
            delivered += 1
        else:
            logger.warning(f"Upload poll: delivery failed for {entry['video_id']}, will retry next cycle")
    return delivered


def initialize_app():
    """Initialize the application and subscribe to WebSub."""
    global community_scheduler, community_handler
    global upload_poll_scheduler, upload_notification_store, upload_feed_scraper

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
    
    # Initialize the upload polling fallback (safety net for WebSub push
    # outages, e.g. Google Issue Tracker 554905105) if upload webhooks exist.
    upload_servers = discord_config.get_servers_for_type('upload')
    if settings.UPLOAD_POLL_ENABLED and len(upload_servers) > 0:
        try:
            from app.utils.upload_poller import UploadFeedScraper, UploadNotificationStore, UploadPollScheduler

            upload_notification_store = UploadNotificationStore()
            upload_feed_scraper = UploadFeedScraper()
            interval = settings.UPLOAD_CHECK_INTERVAL_MINUTES
            upload_poll_scheduler = UploadPollScheduler(interval, _poll_uploads_once)
            upload_poll_scheduler.start()
            logger.info(f"Upload polling fallback started (checking every {interval} minutes)")
        except Exception as e:
            logger.error(f"Failed to initialize upload polling fallback: {e}")
    elif not settings.UPLOAD_POLL_ENABLED:
        logger.info("Upload polling fallback disabled (UPLOAD_POLL_ENABLED=false)")
    else:
        logger.info("Upload polling fallback disabled (no upload webhooks configured)")

    # Subscribe to WebSub notifications via the resilient watchdog, which
    # performs an immediate subscribe (with retry/backoff) and then keeps the
    # subscription alive by re-subscribing on staleness / near-expiry.
    subscription_manager.run_watchdog()
    logger.info("Application initialized successfully")


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
        subscription_manager.stop()
        subscription_manager.unsubscribe_from_channel()
        if community_scheduler:
            community_scheduler.stop()
        if upload_poll_scheduler:
            upload_poll_scheduler.stop()
    except Exception as e:
        logger.error(f"Application startup failed: {e}")
        subscription_manager.stop()
        if community_scheduler:
            community_scheduler.stop()
        if upload_poll_scheduler:
            upload_poll_scheduler.stop()
        sys.exit(1)
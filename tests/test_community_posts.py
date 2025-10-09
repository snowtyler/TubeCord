"""
Test script for community post functionality using yp-dl.
Tests the community scraper, database storage, and notification system.
"""

import sys
from pathlib import Path
from datetime import datetime

# Ensure project root is on the Python path
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.utils.community_scraper import CommunityPostScraper, CommunityPost
from app.utils.scheduler import CommunityPostNotificationHandler
from app.config.settings import settings
from app.utils.logging import setup_logging, get_logger

# Initialize logging
setup_logging('DEBUG', use_colors=True)
logger = get_logger(__name__)


def test_community_scraper():
    """Test the community post scraper."""
    logger.info("Testing community post scraper...")

    scraper = CommunityPostScraper()

    channel_id = settings.YOUTUBE_CHANNEL_ID
    if not channel_id:
        logger.error("No YOUTUBE_CHANNEL_ID set in environment")
        return False

    logger.info(f"Scraping community posts for channel: {channel_id}")

    try:
        posts = scraper.scrape_channel_posts(channel_id, limit=5)

        if posts:
            logger.info(f"Found {len(posts)} new community posts:")
            for post in posts:
                logger.info(f"  - {post.post_id}: {post.content[:50]}...")
                logger.info(f"    Images: {len(post.image_urls)}, Videos: {len(post.video_attachments)}")
                logger.info(f"    URL: {post.url}")
        else:
            logger.info("No new community posts found")

        return True

    except Exception as exc:
        logger.error(f"Error testing community scraper: {exc}")
        return False


def test_database_operations():
    """Test database storage and retrieval."""
    logger.info("Testing database operations...")

    scraper = CommunityPostScraper()

    test_post = CommunityPost(
        post_id='test_db_123',
        channel_id=settings.YOUTUBE_CHANNEL_ID,
        channel_name='Example Channel',
        content='This is a test post for database operations.',
        image_urls=['https://example.com/test.jpg'],
        video_attachments=[],
        poll_data=None,
        published_time=datetime.utcnow().isoformat() + 'Z',
        like_count=10,
        url='https://www.youtube.com/post/test_db_123'
    )

    try:
        stored = scraper.db.store_post(test_post)
        logger.info(f"Post stored successfully: {stored}")

        unnotified = scraper.get_new_posts_for_notification()
        logger.info(f"Found {len(unnotified)} unnotified posts")

        if unnotified:
            post_id = unnotified[0].post_id
            marked = scraper.mark_post_notified(post_id)
            logger.info(f"Marked post {post_id} as notified: {marked}")

        return True

    except Exception as exc:
        logger.error(f"Error testing database operations: {exc}")
        return False


def test_notification_handler():
    """Test the notification handler."""
    logger.info("Testing notification handler...")

    try:
        handler = CommunityPostNotificationHandler()
        handler.initialize()

        test_post = CommunityPost(
            post_id='test_notification_456',
            channel_id=settings.YOUTUBE_CHANNEL_ID,
            channel_name='Example Channel Notifications',
            content='🎉 This is a test community post notification!\n\nTesting the Discord integration with community posts.',
            image_urls=['https://img.youtube.com/vi/dQw4w9WgXcQ/maxresdefault.jpg'],
            video_attachments=[{
                'video_id': 'dQw4w9WgXcQ',
                'title': 'Test Video Attachment',
                'thumbnail': 'https://img.youtube.com/vi/dQw4w9WgXcQ/default.jpg'
            }],
            poll_data={'question': 'Example poll question?', 'options': ['Option 1', 'Option 2']},
            published_time=datetime.utcnow().isoformat() + 'Z',
            like_count=123,
            url='https://www.youtube.com/post/test_notification_456'
        )

        handler.handle_new_posts([test_post])
        logger.info("Test notification sent successfully")

        return True

    except Exception as exc:
        logger.error(f"Error testing notification handler: {exc}")
        return False


def test_scheduler():
    """Test the scheduler functionality."""
    logger.info("Testing scheduler...")

    try:
        from app.utils.scheduler import CommunityPostScheduler

        scheduler = CommunityPostScheduler(check_interval_minutes=1)

        posts_found = []

        def on_posts_found(posts):
            posts_found.extend(posts)
            logger.info(f"Scheduler found {len(posts)} new posts")

        def on_check_complete(check_time, count):
            logger.info(f"Check completed at {check_time}: {count} posts found")

        def on_error(error_msg):
            logger.error(f"Scheduler error: {error_msg}")

        scheduler.set_callbacks(
            on_posts_found=on_posts_found,
            on_check_complete=on_check_complete,
            on_error=on_error
        )

        logger.info("Testing force check...")
        new_posts = scheduler.force_check()
        logger.info(f"Force check found {len(new_posts)} posts")

        status = scheduler.get_status()
        logger.info(f"Scheduler status: {status}")

        return True

    except Exception as exc:
        logger.error(f"Error testing scheduler: {exc}")
        return False


def main():
    """Run all tests."""
    logger.info("Starting community post functionality tests...")

    tests = [
        ("Community Scraper", test_community_scraper),
        ("Database Operations", test_database_operations),
        ("Notification Handler", test_notification_handler),
        ("Scheduler", test_scheduler)
    ]

    results = {}

    for test_name, test_func in tests:
        logger.info(f"\n{'=' * 20} {test_name} {'=' * 20}")
        try:
            results[test_name] = test_func()
        except Exception as exc:
            logger.error(f"Test {test_name} failed with exception: {exc}")
            results[test_name] = False

    logger.info(f"\n{'=' * 20} Test Results {'=' * 20}")
    passed = sum(1 for result in results.values() if result)

    for test_name, result in results.items():
        status = "PASS" if result else "FAIL"
        logger.info(f"{test_name}: {status}")

    logger.info(f"\nTests passed: {passed}/{len(tests)}")

    if passed == len(tests):
        logger.info("🎉 All tests passed! Community post functionality is working.")
    else:
        logger.warning("⚠️ Some tests failed. Check the logs above for details.")

    return passed == len(tests)


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)

#!/usr/bin/env python3
"""Test script for livestream detection logic."""

import sys
from pathlib import Path

# Ensure project root is importable
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.models.notification import YouTubeNotification, NotificationType


def test_livestream_detection():
    """Test the livestream detection with various scenarios."""

    test_cases = [
        {
            'name': 'Regular upload',
            'data': {
                'video_id': 'test123',
                'channel_id': 'test_channel',
                'title': 'My awesome video',
                'author': 'Test Channel',
                'url': 'https://www.youtube.com/watch?v=test123',
                'published': '2024-01-01T12:00:00Z'
            },
            'expected': NotificationType.UPLOAD
        },
        {
            'name': 'Livestream with "live" in title',
            'data': {
                'video_id': 'live123',
                'channel_id': 'test_channel',
                'title': 'Going live now!',
                'author': 'Test Channel',
                'url': 'https://www.youtube.com/watch?v=live123',
                'published': '2024-01-01T12:00:00Z'
            },
            'expected': NotificationType.LIVESTREAM
        },
        {
            'name': 'Livestream with red circle emoji',
            'data': {
                'video_id': 'emoji123',
                'channel_id': 'test_channel',
                'title': '🔴 Streaming now',
                'author': 'Test Channel',
                'url': 'https://www.youtube.com/watch?v=emoji123',
                'published': '2024-01-01T12:00:00Z'
            },
            'expected': NotificationType.LIVESTREAM
        },
        {
            'name': 'Placeholder livestream title',
            'data': {
                'video_id': 'stream_placeholder',
                'channel_id': 'test_channel',
                'title': 'STREAM TITLE PLACEHOLDER',
                'author': 'Example Channel',
                'url': 'https://www.youtube.com/watch?v=stream_placeholder',
                'published': '2024-01-01T12:00:00Z'
            },
            'expected': NotificationType.LIVESTREAM
        }
    ]

    print("Testing livestream detection logic...\n")

    for test_case in test_cases:
        print(f"Test: {test_case['name']}")
        print(f"Title: '{test_case['data']['title']}'")

        notification = YouTubeNotification.from_websub_data(test_case['data'])

        print(f"Detected type: {notification.notification_type}")
        print(f"Expected type: {test_case['expected']}")

        if notification.notification_type == test_case['expected']:
            print("✅ PASS")
        else:
            print("❌ FAIL")

        print('-' * 50)


if __name__ == '__main__':
    test_livestream_detection()

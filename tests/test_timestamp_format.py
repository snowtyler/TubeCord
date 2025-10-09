#!/usr/bin/env python3
"""Test script for Discord timestamp formatting in livestream messages."""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Ensure project root is importable
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.models.notification import YouTubeNotification, NotificationType
from app.config.messages import MessageTemplates


def test_discord_timestamp():
    """Test Discord timestamp formatting."""

    print("🕒 TESTING DISCORD TIMESTAMP FORMATTING")
    print('=' * 50)

    test_cases = [
        {
            'name': 'Stream starting in 30 minutes',
            'scheduled_time': (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat().replace('+00:00', 'Z')
        },
        {
            'name': 'Stream starting in 2 hours',
            'scheduled_time': (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat().replace('+00:00', 'Z')
        },
        {
            'name': 'Stream starting tomorrow',
            'scheduled_time': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace('+00:00', 'Z')
        }
    ]

    for test_case in test_cases:
        print(f"\n🧪 Test: {test_case['name']}")
        print(f"📅 Scheduled: {test_case['scheduled_time']}")

        notification = YouTubeNotification(
            video_id='test123',
            channel_id='test_channel',
            title='Test Livestream',
            author='Test Channel',
            url='https://www.youtube.com/watch?v=test123',
            notification_type=NotificationType.LIVESTREAM,
            scheduled_start_time=test_case['scheduled_time']
        )

        relative_time = notification.get_discord_timestamp('R')
        full_time = notification.get_discord_timestamp('F')

        print(f"⏰ Relative format: {relative_time}")
        print(f"📆 Full format: {full_time}")

        role_mentions = ['234567890123456789']
        message = MessageTemplates.format_simple_message(
            'livestream',
            notification.to_dict(),
            role_mentions
        )

        print('💬 Complete message:')
        print(f'   {message}')
        print('-' * 40)


def test_message_formatting():
    """Test complete message formatting with various scenarios."""

    print('\n🎯 TESTING COMPLETE MESSAGE FORMATTING')
    print('=' * 50)

    upload_data = {
        'video_id': 'upload123',
        'channel_id': 'test_channel',
        'title': 'My Awesome Video',
        'author': 'Test Channel',
        'url': 'https://www.youtube.com/watch?v=upload123'
    }

    upload_message = MessageTemplates.format_simple_message('upload', upload_data)
    print(f'📹 Upload message: {upload_message}')

    livestream_data = {
        'video_id': 'live123',
        'channel_id': 'test_channel',
        'title': 'Epic Gaming Stream',
        'author': 'Gaming Channel',
        'url': 'https://www.youtube.com/watch?v=live123'
    }

    role_mentions = ['234567890123456789']
    livestream_message = MessageTemplates.format_simple_message(
        'livestream',
        livestream_data,
        role_mentions
    )
    print(f'🔴 Livestream (no schedule): {livestream_message}')

    scheduled_time = datetime.now(timezone.utc) + timedelta(hours=1)
    livestream_data_scheduled = livestream_data.copy()
    livestream_data_scheduled['scheduled_start_time'] = scheduled_time.isoformat().replace('+00:00', 'Z')

    scheduled_message = MessageTemplates.format_simple_message(
        'livestream',
        livestream_data_scheduled,
        role_mentions
    )
    print(f'🔴 Livestream (scheduled): {scheduled_message}')


def test_edge_cases():
    """Test edge cases and error handling."""

    print('\n⚠️  TESTING EDGE CASES')
    print('=' * 50)

    invalid_data = {
        'video_id': 'invalid123',
        'channel_id': 'test_channel',
        'title': 'Test Stream',
        'author': 'Test Channel',
        'url': 'https://www.youtube.com/watch?v=invalid123',
        'scheduled_start_time': 'invalid-timestamp'
    }

    role_mentions = ['234567890123456789']
    message = MessageTemplates.format_simple_message('livestream', invalid_data, role_mentions)
    print(f'❌ Invalid timestamp: {message}')

    no_roles_message = MessageTemplates.format_simple_message('livestream', invalid_data)
    print(f'👥 No role mentions: {no_roles_message}')

    multiple_roles = ['234567890123456789', '345678901234567890', '456789012345678901']
    multi_role_message = MessageTemplates.format_simple_message('livestream', invalid_data, multiple_roles)
    print(f'👥 Multiple roles: {multi_role_message}')


def main():
    """Run all tests."""

    print('🚀 DISCORD TIMESTAMP TESTING')
    print('=' * 50)
    print('Testing new livestream message format with Discord timestamps')
    print("Expected format: '<role_mentions> starting <timestamp>: [<title>](<url>)'")
    print()

    test_discord_timestamp()
    test_message_formatting()
    test_edge_cases()

    print('\n' + '=' * 50)
    print('✅ TESTING COMPLETE')
    print('=' * 50)
    print('💡 The new format will show Discord timestamps like:')
    print("   • 'in 30 minutes' (relative)")
    print("   • 'Monday, October 6, 2025 2:00 PM' (absolute)")
    print('   • Role mentions will ping the specified roles')


if __name__ == '__main__':
    main()

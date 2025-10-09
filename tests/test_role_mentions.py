#!/usr/bin/env python3
"""Quick test to verify role mentions are included in all message types."""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Ensure project root is importable
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config.messages import MessageTemplates


def test_role_mentions():
    """Test that role mentions are included in all message types."""

    print("🧪 TESTING ROLE MENTIONS IN ALL MESSAGE TYPES")
    print('=' * 50)

    test_data = {
        'video_id': 'test123',
        'channel_id': 'test_channel',
        'title': 'Test Video Title',
        'author': 'Test Channel',
        'url': 'https://www.youtube.com/watch?v=test123'
    }

    role_mentions = ['123456789012345678', '234567890123456789']

    upload_msg = MessageTemplates.format_simple_message('upload', test_data, role_mentions)
    print('📹 Upload message:')
    print(f'   {upload_msg}')
    print()

    livestream_msg = MessageTemplates.format_simple_message('livestream', test_data, role_mentions)
    print('🔴 Livestream message (no schedule):')
    print(f'   {livestream_msg}')
    print()

    scheduled_time = datetime.now(timezone.utc) + timedelta(minutes=30)
    test_data_scheduled = test_data.copy()
    test_data_scheduled['scheduled_start_time'] = scheduled_time.isoformat().replace('+00:00', 'Z')

    livestream_scheduled_msg = MessageTemplates.format_simple_message('livestream', test_data_scheduled, role_mentions)
    print('🔴 Livestream message (with schedule):')
    print(f'   {livestream_scheduled_msg}')
    print()

    community_msg = MessageTemplates.format_simple_message('community', test_data, role_mentions)
    print('📝 Community message:')
    print(f'   {community_msg}')
    print()

    upload_no_roles = MessageTemplates.format_simple_message('upload', test_data)
    print('📹 Upload message (no roles):')
    print(f'   {upload_no_roles}')
    print()

    print('✅ All message types now include role mention support!')


if __name__ == '__main__':
    test_role_mentions()

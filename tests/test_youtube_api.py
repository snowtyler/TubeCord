#!/usr/bin/env python3
"""Test script for YouTube Data API v3 integration."""

import sys
import os
from pathlib import Path
import requests
from dotenv import load_dotenv

# Ensure project root is importable
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Load environment variables
load_dotenv()


def _mask_api_key(api_key: str) -> str:
    """Mask an API key so only a small portion is visible."""
    if not api_key:
        return "<missing>"
    if len(api_key) <= 8:
        return "****"
    return f"{api_key[:4]}...{api_key[-4:]}"


def test_api_key():
    """Test if the YouTube API key is valid and working."""

    api_key = os.getenv('YOUTUBE_API_KEY')
    if not api_key or api_key == 'your_youtube_api_key_here':
        print("❌ ERROR: YOUTUBE_API_KEY not set in .env file")
        print("Please follow the setup guide in YOUTUBE_API_SETUP.md")
        return False

    print(f"🔑 Testing API key: {_mask_api_key(api_key)}")

    test_video_id = "dQw4w9WgXcQ"

    try:
        api_url = "https://www.googleapis.com/youtube/v3/videos"
        params = {
            'part': 'snippet',
            'id': test_video_id,
            'key': api_key
        }

        response = requests.get(api_url, params=params, timeout=10)

        if response.status_code == 200:
            data = response.json()
            if data.get('items'):
                video = data['items'][0]
                title = video['snippet']['title']
                print("✅ API key is valid!")
                print(f"📹 Test video: {title}")
                return True
            print("❌ API key valid but no video data returned")
            return False
        if response.status_code == 403:
            error_data = response.json()
            error_reason = error_data.get('error', {}).get('errors', [{}])[0].get('reason', 'unknown')

            if 'quotaExceeded' in error_reason:
                print("❌ ERROR: YouTube API quota exceeded")
                print("Wait for quota reset or request increase in Google Cloud Console")
            elif 'keyInvalid' in error_reason:
                print("❌ ERROR: Invalid API key")
                print("Check your API key in .env file")
            else:
                print(f"❌ ERROR: API access denied - {error_reason}")
                print("Check API key restrictions in Google Cloud Console")
            return False

        print(f"❌ ERROR: API request failed with status {response.status_code}")
        print(f"Response: {response.text}")
        return False

    except requests.exceptions.RequestException as exc:
        print(f"❌ ERROR: Network error - {exc}")
        return False
    except Exception as exc:
        print(f"❌ ERROR: Unexpected error - {exc}")
        return False


def test_livestream_detection():
    """Test livestream detection with our notification model."""

    print("\n" + "=" * 50)
    print("🔴 TESTING LIVESTREAM DETECTION")
    print("=" * 50)

    from app.models.notification import YouTubeNotification, NotificationType

    test_cases = [
        {
            'name': 'Regular Upload',
            'data': {
                'video_id': 'dQw4w9WgXcQ',
                'channel_id': 'UCuAXFkgsw1L7xaCfnd5JJOw',
                'title': 'Rick Astley - Never Gonna Give You Up',
                'author': 'Rick Astley',
                'url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
            },
            'expected': NotificationType.UPLOAD
        },
        {
            'name': 'Title with "Live" keyword',
            'data': {
                'video_id': 'test_live_123',
                'channel_id': 'test_channel',
                'title': 'Going Live Now!',
                'author': 'Test Channel',
                'url': 'https://www.youtube.com/watch?v=test_live_123'
            },
            'expected': NotificationType.LIVESTREAM
        }
    ]

    for test_case in test_cases:
        print(f"\n🧪 Test: {test_case['name']}")
        print(f"📹 Title: {test_case['data']['title']}")
        print(f"🆔 Video ID: {test_case['data']['video_id']}")

        try:
            notification = YouTubeNotification.from_websub_data(test_case['data'])

            print(f"🎯 Detected: {notification.notification_type.value}")
            print(f"🎯 Expected: {test_case['expected'].value}")

            if notification.notification_type == test_case['expected']:
                print("✅ PASS")
            else:
                print("❌ FAIL")

        except Exception as exc:
            print(f"❌ ERROR: {exc}")

        print('-' * 30)


def test_quota_usage():
    """Display current quota usage information."""

    print("\n" + "=" * 50)
    print("📊 QUOTA USAGE INFORMATION")
    print("=" * 50)

    api_key = os.getenv('YOUTUBE_API_KEY')
    if not api_key or api_key == 'your_youtube_api_key_here':
        print("❌ API key not configured")
        return

    print("📈 YouTube Data API v3 Quota:")
    print("   • Default daily limit: 10,000 units")
    print("   • Cost per video check: 1 unit")
    print("   • Estimated daily usage: 50-100 units")
    print("   • Monitor usage at: https://console.cloud.google.com/apis/api/youtube.googleapis.com/quotas")
    print("\n💡 Tips:")
    print("   • Quota resets daily at midnight Pacific Time")
    print("   • Fallback detection activates if quota exceeded")
    print("   • Request quota increase if needed (usually auto-approved)")


def main():
    """Run all tests."""

    print("🚀 TUBECORD YOUTUBE API TESTER")
    print("=" * 50)

    print("\n1️⃣ TESTING API KEY...")
    api_valid = test_api_key()

    if api_valid:
        test_livestream_detection()
    else:
        print("\n⚠️  Skipping detection tests due to API key issues")
        print("Please fix API key configuration and try again")

    test_quota_usage()

    print("\n" + "=" * 50)
    print("🏁 TESTING COMPLETE")
    print("=" * 50)

    if api_valid:
        print("✅ YouTube API integration is ready!")
        print("🎯 Start your bot and test with real livestreams")
    else:
        print("❌ Please fix API configuration before using the bot")
        print("📖 See YOUTUBE_API_SETUP.md for detailed instructions")


if __name__ == '__main__':
    main()

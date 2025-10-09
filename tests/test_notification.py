#!/usr/bin/env python3
"""Simple script to send POST requests to the local test notification endpoints."""

import json
import os
import sys

import requests


def test_upload():
    """Send a test upload notification to the local server."""
    port = os.getenv('PORT', '8000')
    base_url = f"http://localhost:{port}"
    try:
        print(f"Sending test upload notification to {base_url}/test-notification...")
        response = requests.post(f'{base_url}/test-notification', timeout=10)

        print(f"Status Code: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")

        if response.status_code == 200:
            try:
                result = response.json()
                print(f"Success! Response: {json.dumps(result, indent=2)}")
                print("\n✅ Test upload notification sent successfully!")
                print("Check your Discord channels for the upload test message.")
            except ValueError:
                print(f"Response Text: {response.text}")
        else:
            print(f"❌ Test failed with status {response.status_code}")
            print(f"Response: {response.text}")

    except requests.exceptions.ConnectionError:
        print(f"❌ Connection failed - make sure the Flask server is running on port {port}")
    except requests.exceptions.Timeout:
        print("❌ Request timed out")
    except Exception as exc:
        print(f"❌ Error: {exc}")


def test_livestream():
    """Send a test livestream notification to the local server."""
    port = os.getenv('PORT', '8000')
    base_url = f"http://localhost:{port}"
    try:
        print(f"Sending test livestream notification to {base_url}/test-livestream...")
        response = requests.post(f'{base_url}/test-livestream', timeout=10)

        print(f"Status Code: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")

        if response.status_code == 200:
            try:
                result = response.json()
                print(f"Success! Response: {json.dumps(result, indent=2)}")
                print("\n✅ Test livestream notification sent successfully!")
                print("Check your Discord channels for the livestream test message.")
            except ValueError:
                print(f"Response Text: {response.text}")
        else:
            print(f"❌ Test failed with status {response.status_code}")
            print(f"Response: {response.text}")

    except requests.exceptions.ConnectionError:
        print(f"❌ Connection failed - make sure the Flask server is running on port {port}")
    except requests.exceptions.Timeout:
        print("❌ Request timed out")
    except Exception as exc:
        print(f"❌ Error: {exc}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "livestream":
        test_livestream()
    else:
        test_upload()

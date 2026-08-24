"""
Speak Example - Earth Rover SDK

Demonstrates the /speak endpoint, which converts text to speech and
plays it through the rover's speaker via the Agora RTC channel.

Usage:
    python 17_speak.py
    python 17_speak.py "Your custom message here"
"""

import sys
import time

BASE_URL = "http://localhost:8000"

from _client import rover_session

SESSION = rover_session()


def speak(text: str) -> dict:
    """Send text to the rover's speaker via TTS."""
    response = SESSION.post(f"{BASE_URL}/speak", json={"text": text})
    response.raise_for_status()
    return response.json()


def main():
    sentences = [
        "Hello from the rover!",
        "The quick brown fox jumps over the lazy dog.",
        "Navigation systems are online.",
        "Battery level is at eighty percent.",
    ]

    if len(sys.argv) > 1:
        sentences = [" ".join(sys.argv[1:])]

    for sentence in sentences:
        print(f"Speaking: {sentence!r}")
        result = speak(sentence)
        print(f"  Result: {result}")
        # Wait for rover to finish speaking before sending the next sentence.
        # Approximate: 0.08s per character is a rough TTS rate at normal speed.
        estimated_duration = max(2.0, len(sentence) * 0.08)
        time.sleep(estimated_duration + 1.5)


if __name__ == "__main__":
    main()
"""
Basic Movement Example - Earth Rover SDK

This example demonstrates the fundamental movement controls:
- Moving forward and backward
- Turning left and right
- Stopping the rover

The rover uses linear (-1 to 1) and angular (-1 to 1) values for control.
"""

import time

BASE_URL = "http://localhost:8000"

from _client import rover_session

SESSION = rover_session()


def send_command(linear: float, angular: float, lamp: int = 0):
    """Send a movement command to the rover."""
    response = SESSION.post(
        f"{BASE_URL}/control",
        json={"command": {"linear": linear, "angular": angular, "lamp": lamp}},
    )
    return response.json()


def stop():
    """Stop all movement."""
    print("Stopping...")
    send_command(0, 0)


def move_forward(speed: float = 0.5, duration: float = 1.0):
    """Move forward at specified speed for duration seconds."""
    print(f"Moving forward at speed {speed}...")
    send_command(speed, 0)
    time.sleep(duration)
    stop()


def move_backward(speed: float = 0.5, duration: float = 1.0):
    """Move backward at specified speed for duration seconds."""
    print(f"Moving backward at speed {speed}...")
    send_command(-speed, 0)
    time.sleep(duration)
    stop()


def turn_left(speed: float = 0.5, duration: float = 1.0):
    """Turn left at specified speed for duration seconds."""
    print(f"Turning left at speed {speed}...")
    send_command(0, -speed)
    time.sleep(duration)
    stop()


def turn_right(speed: float = 0.5, duration: float = 1.0):
    """Turn right at specified speed for duration seconds."""
    print(f"Turning right at speed {speed}...")
    send_command(0, speed)
    time.sleep(duration)
    stop()


def main():
    print("=== Basic Movement Demo ===\n")

    # Move forward
    move_forward(speed=0.5, duration=2.0)
    time.sleep(0.5)

    # Move backward
    move_backward(speed=0.5, duration=2.0)
    time.sleep(0.5)

    # Turn left
    turn_left(speed=0.5, duration=1.0)
    time.sleep(0.5)

    # Turn right
    turn_right(speed=0.5, duration=1.0)
    time.sleep(0.5)

    print("\n=== Demo Complete ===")


if __name__ == "__main__":
    main()

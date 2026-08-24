"""
Square Pattern Example - Earth Rover SDK

This example makes the rover drive in a square pattern
by alternating between forward movement and 90-degree turns.

Great for testing movement precision and calibration.
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
    send_command(0, 0)


def move_forward(speed: float = 0.5, duration: float = 2.0):
    """Move forward for specified duration."""
    print(f"  Moving forward...")
    send_command(speed, 0)
    time.sleep(duration)
    stop()


def turn_90_degrees(direction: str = "right", turn_speed: float = 0.5, turn_duration: float = 1.0):
    """
    Turn approximately 90 degrees.

    Note: You may need to calibrate turn_duration based on your rover's
    turning speed and surface conditions.
    """
    angular = turn_speed if direction == "right" else -turn_speed
    print(f"  Turning {direction} 90°...")
    send_command(0, angular)
    time.sleep(turn_duration)
    stop()


def drive_square(side_length_time: float = 2.0, speed: float = 0.5,
                 turn_speed: float = 0.5, turn_duration: float = 1.0):
    """
    Drive in a square pattern.

    Args:
        side_length_time: Duration to drive each side (seconds)
        speed: Forward movement speed (0-1)
        turn_speed: Turning speed (0-1)
        turn_duration: Duration for 90° turn (adjust for calibration)
    """
    print("=== Driving Square Pattern ===\n")

    for side in range(4):
        print(f"Side {side + 1}/4:")

        # Drive forward
        move_forward(speed=speed, duration=side_length_time)
        time.sleep(0.3)

        # Turn right 90 degrees
        turn_90_degrees(direction="right", turn_speed=turn_speed, turn_duration=turn_duration)
        time.sleep(0.3)

        print()

    print("Square complete!")


def drive_rectangle(length_time: float = 3.0, width_time: float = 1.5, speed: float = 0.5):
    """Drive in a rectangle pattern."""
    print("=== Driving Rectangle Pattern ===\n")

    for i in range(2):
        # Long side
        print(f"Long side {i * 2 + 1}:")
        move_forward(speed=speed, duration=length_time)
        time.sleep(0.3)
        turn_90_degrees("right")
        time.sleep(0.3)

        # Short side
        print(f"Short side {i * 2 + 2}:")
        move_forward(speed=speed, duration=width_time)
        time.sleep(0.3)
        turn_90_degrees("right")
        time.sleep(0.3)

    print("Rectangle complete!")


def main():
    print("=== Square Pattern Demo ===\n")

    print("This demo will drive the rover in a square pattern.")
    print("Adjust turn_duration if turns are not exactly 90 degrees.\n")

    time.sleep(2)  # Give time to prepare

    # Drive a square
    drive_square(
        side_length_time=2.0,  # 2 seconds per side
        speed=0.4,             # Moderate speed
        turn_speed=0.5,        # Turn speed
        turn_duration=1.0      # Adjust this for accurate 90° turns
    )

    print("\n=== Demo Complete ===")


if __name__ == "__main__":
    main()

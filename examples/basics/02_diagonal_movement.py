"""
Diagonal Movement Example - Earth Rover SDK

This example shows how to combine linear and angular controls
to achieve diagonal/curved movement patterns.

By combining forward/backward with left/right, the rover
can follow curved paths and arcs.
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


def curve_forward_left(linear: float = 0.5, angular: float = -0.3, duration: float = 2.0):
    """Move forward while curving left."""
    print(f"Curving forward-left (linear={linear}, angular={angular})...")
    send_command(linear, angular)
    time.sleep(duration)
    stop()


def curve_forward_right(linear: float = 0.5, angular: float = 0.3, duration: float = 2.0):
    """Move forward while curving right."""
    print(f"Curving forward-right (linear={linear}, angular={angular})...")
    send_command(linear, angular)
    time.sleep(duration)
    stop()


def curve_backward_left(linear: float = -0.5, angular: float = -0.3, duration: float = 2.0):
    """Move backward while curving left."""
    print(f"Curving backward-left (linear={linear}, angular={angular})...")
    send_command(linear, angular)
    time.sleep(duration)
    stop()


def curve_backward_right(linear: float = -0.5, angular: float = 0.3, duration: float = 2.0):
    """Move backward while curving right."""
    print(f"Curving backward-right (linear={linear}, angular={angular})...")
    send_command(linear, angular)
    time.sleep(duration)
    stop()


def slalom(repetitions: int = 3, speed: float = 0.5, turn_intensity: float = 0.4):
    """Perform a slalom pattern - weaving left and right while moving forward."""
    print(f"\nPerforming slalom with {repetitions} weaves...")

    for i in range(repetitions):
        print(f"  Weave {i + 1}: left...")
        send_command(speed, -turn_intensity)
        time.sleep(1.0)

        print(f"  Weave {i + 1}: right...")
        send_command(speed, turn_intensity)
        time.sleep(1.0)

    stop()
    print("Slalom complete!")


def main():
    print("=== Diagonal Movement Demo ===\n")

    # Demonstrate curved movements
    curve_forward_left(duration=2.0)
    time.sleep(0.5)

    curve_forward_right(duration=2.0)
    time.sleep(0.5)

    curve_backward_left(duration=1.5)
    time.sleep(0.5)

    curve_backward_right(duration=1.5)
    time.sleep(0.5)

    # Perform a slalom
    slalom(repetitions=3)

    print("\n=== Demo Complete ===")


if __name__ == "__main__":
    main()

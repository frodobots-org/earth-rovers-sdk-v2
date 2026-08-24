"""
Circle Pattern Example - Earth Rover SDK

This example makes the rover drive in circular patterns
by combining forward movement with continuous turning.

Demonstrates smooth curved motion control.
"""

import time
import math

BASE_URL = "http://localhost:8000"

from _client import rover_session

SESSION = rover_session()
CONTROL_RATE_HZ = 10.0


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


def stream_command(linear: float, angular: float, duration: float, lamp: int = 0):
    """Continuously refresh a time-bounded motion command at 10 Hz."""
    interval = 1.0 / CONTROL_RATE_HZ
    deadline = time.monotonic() + duration
    while True:
        send_command(linear, angular, lamp)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(interval, remaining))


def drive_circle(radius: str = "medium", direction: str = "right", duration: float = 5.0):
    """
    Drive in a circular pattern.

    Args:
        radius: "tight", "medium", or "wide" - affects the circle size
        direction: "left" or "right"
        duration: How long to drive (seconds)
    """
    # Map radius to linear/angular ratio
    radius_settings = {
        "tight":  {"linear": 0.3, "angular": 0.7},
        "medium": {"linear": 0.5, "angular": 0.4},
        "wide":   {"linear": 0.6, "angular": 0.2},
    }

    settings = radius_settings.get(radius, radius_settings["medium"])
    linear = settings["linear"]
    angular = settings["angular"] if direction == "right" else -settings["angular"]

    print(f"Driving {radius} circle to the {direction}...")
    print(f"  Linear: {linear}, Angular: {angular}")

    stream_command(linear, angular, duration)
    stop()

    print("Circle complete!")


def drive_figure_eight(loop_duration: float = 4.0, speed: float = 0.5, turn_intensity: float = 0.4):
    """
    Drive in a figure-8 pattern.

    Combines two circles in opposite directions.
    """
    print("Driving figure-8 pattern...")

    # First loop (right)
    print("  Loop 1: turning right...")
    stream_command(speed, turn_intensity, loop_duration)

    # Transition
    print("  Transitioning...")
    stream_command(speed, 0, 0.5)

    # Second loop (left)
    print("  Loop 2: turning left...")
    stream_command(speed, -turn_intensity, loop_duration)

    stop()
    print("Figure-8 complete!")


def spiral_outward(start_angular: float = 0.6, end_angular: float = 0.1,
                   speed: float = 0.5, duration: float = 8.0):
    """
    Drive in an expanding spiral pattern.

    Starts with tight turns and gradually widens.
    """
    print("Driving outward spiral...")

    steps = 50
    step_time = duration / steps

    for i in range(steps):
        # Interpolate angular velocity from start to end
        progress = i / steps
        angular = start_angular + (end_angular - start_angular) * progress

        send_command(speed, angular)
        time.sleep(step_time)

    stop()
    print("Spiral complete!")


def spiral_inward(start_angular: float = 0.1, end_angular: float = 0.6,
                  speed: float = 0.5, duration: float = 8.0):
    """
    Drive in a tightening spiral pattern.

    Starts with wide turns and gradually tightens.
    """
    print("Driving inward spiral...")

    steps = 50
    step_time = duration / steps

    for i in range(steps):
        progress = i / steps
        angular = start_angular + (end_angular - start_angular) * progress

        send_command(speed, angular)
        time.sleep(step_time)

    stop()
    print("Spiral complete!")


def sine_wave_path(amplitude: float = 0.4, frequency: float = 0.5,
                   speed: float = 0.5, duration: float = 10.0):
    """
    Drive in a sine wave pattern (oscillating left and right).
    """
    print("Driving sine wave path...")

    start_time = time.time()

    while time.time() - start_time < duration:
        elapsed = time.time() - start_time
        angular = amplitude * math.sin(2 * math.pi * frequency * elapsed)

        send_command(speed, angular)
        time.sleep(0.05)

    stop()
    print("Sine wave complete!")


def main():
    print("=== Circle Pattern Demo ===\n")

    # Basic circles
    print("1. Tight circle to the right:")
    drive_circle(radius="tight", direction="right", duration=4.0)
    time.sleep(1.0)

    print("\n2. Wide circle to the left:")
    drive_circle(radius="wide", direction="left", duration=4.0)
    time.sleep(1.0)

    # Figure-8
    print("\n3. Figure-8 pattern:")
    drive_figure_eight(loop_duration=3.0)
    time.sleep(1.0)

    # Spiral
    print("\n4. Outward spiral:")
    spiral_outward(duration=6.0)
    time.sleep(1.0)

    # Sine wave
    print("\n5. Sine wave path:")
    sine_wave_path(duration=6.0)

    print("\n=== Demo Complete ===")


if __name__ == "__main__":
    main()

"""
Speed Control Example - Earth Rover SDK

This example demonstrates different speed levels and
smooth acceleration/deceleration patterns.

Speed values range from -1.0 (full reverse) to 1.0 (full forward).
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


def demonstrate_speed_levels():
    """Show different speed levels from slow to fast."""
    print("Demonstrating speed levels...\n")

    speeds = [0.2, 0.4, 0.6, 0.8, 1.0]

    for speed in speeds:
        percent = f"{speed * 100:.0f}%"
        print(f"  Speed: {speed:.1f} ({percent})")
        send_command(speed, 0)
        time.sleep(1.5)

    stop()
    print("Speed demonstration complete!\n")


def smooth_acceleration(target_speed: float = 1.0, steps: int = 20, total_time: float = 2.0):
    """Gradually accelerate from 0 to target speed."""
    print(f"Smooth acceleration to {target_speed}...")

    step_delay = total_time / steps

    for i in range(steps + 1):
        current_speed = (i / steps) * target_speed
        send_command(current_speed, 0)
        time.sleep(step_delay)

    print("Acceleration complete!")


def smooth_deceleration(current_speed: float = 1.0, steps: int = 20, total_time: float = 2.0):
    """Gradually decelerate from current speed to 0."""
    print(f"Smooth deceleration from {current_speed}...")

    step_delay = total_time / steps

    for i in range(steps + 1):
        speed = current_speed * (1 - i / steps)
        send_command(speed, 0)
        time.sleep(step_delay)

    stop()
    print("Deceleration complete!")


def speed_ramp_cycle():
    """Complete acceleration and deceleration cycle."""
    print("\n=== Speed Ramp Cycle ===")

    smooth_acceleration(target_speed=0.8, steps=15, total_time=1.5)
    time.sleep(1.0)  # Cruise at top speed
    smooth_deceleration(current_speed=0.8, steps=15, total_time=1.5)

    print("Cycle complete!\n")


def pulse_movement(pulses: int = 5, speed: float = 0.6):
    """Quick start-stop pulses."""
    print(f"Performing {pulses} movement pulses...")

    for i in range(pulses):
        print(f"  Pulse {i + 1}")
        send_command(speed, 0)
        time.sleep(0.3)
        stop()
        time.sleep(0.3)

    print("Pulse movement complete!")


def main():
    print("=== Speed Control Demo ===\n")

    # Show different speed levels
    demonstrate_speed_levels()
    time.sleep(1.0)

    # Smooth acceleration and deceleration
    speed_ramp_cycle()
    time.sleep(1.0)

    # Pulse movements
    pulse_movement(pulses=5)

    print("\n=== Demo Complete ===")


if __name__ == "__main__":
    main()

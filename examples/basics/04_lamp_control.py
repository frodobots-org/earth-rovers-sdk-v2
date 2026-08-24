"""
Lamp Control Example - Earth Rover SDK

This example demonstrates how to control the rover's lamp
while stationary or while moving.

Lamp values: 0 = off, 1 = on
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


def lamp_on():
    """Turn the lamp on."""
    print("Lamp ON")
    send_command(0, 0, lamp=1)


def lamp_off():
    """Turn the lamp off."""
    print("Lamp OFF")
    send_command(0, 0, lamp=0)


def blink_lamp(times: int = 5, interval: float = 0.5):
    """Blink the lamp on and off."""
    print(f"Blinking lamp {times} times...")

    for i in range(times):
        lamp_on()
        time.sleep(interval)
        lamp_off()
        time.sleep(interval)

    print("Blink complete!")


def move_with_lamp(linear: float = 0.5, duration: float = 3.0):
    """Move forward with lamp on."""
    print(f"Moving forward with lamp on...")
    send_command(linear, 0, lamp=1)
    time.sleep(duration)
    send_command(0, 0, lamp=0)
    print("Movement complete, lamp off.")


def sos_signal():
    """Flash SOS in morse code (... --- ...)"""
    print("Flashing SOS signal...")

    dot = 0.2
    dash = 0.6
    gap = 0.2
    letter_gap = 0.6

    # S (...)
    for _ in range(3):
        lamp_on()
        time.sleep(dot)
        lamp_off()
        time.sleep(gap)

    time.sleep(letter_gap)

    # O (---)
    for _ in range(3):
        lamp_on()
        time.sleep(dash)
        lamp_off()
        time.sleep(gap)

    time.sleep(letter_gap)

    # S (...)
    for _ in range(3):
        lamp_on()
        time.sleep(dot)
        lamp_off()
        time.sleep(gap)

    print("SOS signal complete!")


def strobe_effect(duration: float = 3.0, frequency: float = 10):
    """Fast strobe light effect."""
    print(f"Strobe effect for {duration} seconds...")

    interval = 1.0 / (frequency * 2)  # On and off cycle
    cycles = int(duration * frequency)

    for _ in range(cycles):
        lamp_on()
        time.sleep(interval)
        lamp_off()
        time.sleep(interval)

    print("Strobe complete!")


def main():
    print("=== Lamp Control Demo ===\n")

    # Basic on/off
    print("Basic lamp control:")
    lamp_on()
    time.sleep(2.0)
    lamp_off()
    time.sleep(1.0)

    # Blink pattern
    print("\nBlink pattern:")
    blink_lamp(times=5, interval=0.3)
    time.sleep(1.0)

    # SOS signal
    print("\nSOS Signal:")
    sos_signal()
    time.sleep(1.0)

    # Move with lamp
    print("\nMoving with lamp:")
    move_with_lamp(linear=0.4, duration=2.0)

    print("\n=== Demo Complete ===")


if __name__ == "__main__":
    main()

"""
Keyboard Teleoperation Example - Earth Rover SDK

This example provides real-time keyboard control of the rover.
Uses the 'keyboard' library for responsive input.

Controls:
  W / Up Arrow    - Move forward
  S / Down Arrow  - Move backward
  A / Left Arrow  - Turn left
  D / Right Arrow - Turn right
  L              - Toggle lamp
  Q / Escape     - Quit

Install: pip install keyboard
Note: May require root/admin privileges on some systems.
"""

import requests
import time
import sys

try:
    import keyboard
except ImportError:
    print("This example requires the 'keyboard' library.")
    print("Install with: pip install keyboard")
    print("\nNote: On macOS/Linux, you may need to run with sudo.")
    sys.exit(1)

BASE_URL = "http://localhost:8000"

from _client import rover_session

SESSION = rover_session()


class RoverController:
    def __init__(self):
        self.linear = 0.0
        self.angular = 0.0
        self.lamp = 0
        self.speed = 0.6
        self.running = True

    def send_command(self):
        """Send current state to rover."""
        try:
            SESSION.post(
                f"{BASE_URL}/control",
                json={"command": {
                    "linear": self.linear,
                    "angular": self.angular,
                    "lamp": self.lamp
                }},
                timeout=0.5
            )
        except requests.exceptions.RequestException:
            pass  # Ignore connection errors during rapid updates

    def update_movement(self):
        """Update movement based on currently pressed keys."""
        # Reset to zero
        self.linear = 0.0
        self.angular = 0.0

        # Check forward/backward
        if keyboard.is_pressed('w') or keyboard.is_pressed('up'):
            self.linear = self.speed
        elif keyboard.is_pressed('s') or keyboard.is_pressed('down'):
            self.linear = -self.speed

        # Check left/right
        if keyboard.is_pressed('a') or keyboard.is_pressed('left'):
            self.angular = -self.speed
        elif keyboard.is_pressed('d') or keyboard.is_pressed('right'):
            self.angular = self.speed

    def toggle_lamp(self):
        """Toggle lamp state."""
        self.lamp = 1 - self.lamp
        print(f"Lamp: {'ON' if self.lamp else 'OFF'}")

    def quit(self):
        """Stop the controller."""
        self.running = False

    def display_status(self):
        """Display current control status."""
        direction = ""
        if self.linear > 0:
            direction = "Forward"
        elif self.linear < 0:
            direction = "Backward"

        if self.angular < 0:
            direction += " Left" if direction else "Left"
        elif self.angular > 0:
            direction += " Right" if direction else "Right"

        if not direction:
            direction = "Stopped"

        print(f"\rMovement: {direction:20} | Linear: {self.linear:+.1f} | Angular: {self.angular:+.1f} | Lamp: {'ON' if self.lamp else 'OFF'}  ", end="")


def main():
    print("=== Keyboard Teleoperation ===\n")
    print("Controls:")
    print("  W/↑ - Forward    S/↓ - Backward")
    print("  A/← - Left       D/→ - Right")
    print("  L   - Toggle lamp")
    print("  Q   - Quit")
    print("\nStarting in 2 seconds...\n")

    time.sleep(2)

    controller = RoverController()

    # Set up keyboard hooks
    keyboard.on_press_key('l', lambda _: controller.toggle_lamp())
    keyboard.on_press_key('q', lambda _: controller.quit())
    keyboard.on_press_key('escape', lambda _: controller.quit())

    print("Control active! Press Q to quit.\n")

    try:
        while controller.running:
            controller.update_movement()
            controller.send_command()
            controller.display_status()
            time.sleep(0.05)  # 20 Hz update rate
    except KeyboardInterrupt:
        pass
    finally:
        # Stop rover on exit
        SESSION.post(
            f"{BASE_URL}/control",
            json={"command": {"linear": 0, "angular": 0, "lamp": 0}}
        )
        print("\n\nRover stopped. Goodbye!")


if __name__ == "__main__":
    main()

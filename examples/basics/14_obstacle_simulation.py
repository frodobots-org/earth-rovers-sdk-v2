"""
Obstacle Avoidance Simulation Example - Earth Rover SDK

This example demonstrates a simple obstacle avoidance behavior
by monitoring telemetry and adjusting movement accordingly.

Note: This is a simulation/demonstration. Real obstacle detection
would require additional sensors (LIDAR, ultrasonic, etc.).
"""

import time
import random

BASE_URL = "http://localhost:8000"

from _client import rover_session

SESSION = rover_session()


def send_command(linear: float, angular: float, lamp: int = 0):
    """Send movement command."""
    SESSION.post(
        f"{BASE_URL}/control",
        json={"command": {"linear": linear, "angular": angular, "lamp": lamp}}
    )


def stop():
    """Stop all movement."""
    send_command(0, 0)


def get_telemetry() -> dict:
    """Get current telemetry."""
    response = SESSION.get(f"{BASE_URL}/data")
    return response.json()


def simulate_obstacle_detection() -> bool:
    """
    Simulate obstacle detection.

    In a real implementation, this would read from sensors.
    Here we randomly simulate obstacles for demonstration.
    """
    # 15% chance of detecting an obstacle
    return random.random() < 0.15


def avoid_obstacle(direction: str = "random"):
    """
    Execute obstacle avoidance maneuver.

    Args:
        direction: "left", "right", or "random"
    """
    if direction == "random":
        direction = random.choice(["left", "right"])

    print(f"  Avoiding obstacle - turning {direction}")

    angular = -0.6 if direction == "left" else 0.6

    # Stop first
    stop()
    time.sleep(0.2)

    # Turn away
    send_command(0, angular)
    time.sleep(0.8)

    # Move forward slightly while turning
    send_command(0.3, angular * 0.5)
    time.sleep(0.5)


def autonomous_explore(duration: float = 30.0, base_speed: float = 0.4):
    """
    Autonomous exploration with obstacle avoidance.

    Args:
        duration: How long to explore (seconds)
        base_speed: Normal forward speed
    """
    print(f"Starting autonomous exploration for {duration} seconds...")
    print("Press Ctrl+C to stop\n")

    start_time = time.time()
    obstacle_count = 0

    try:
        while time.time() - start_time < duration:
            # Check for obstacles
            if simulate_obstacle_detection():
                print(f"[{time.time() - start_time:.1f}s] Obstacle detected!")
                obstacle_count += 1

                # Flash lamp as warning
                send_command(0, 0, lamp=1)
                time.sleep(0.1)

                # Avoid the obstacle
                avoid_obstacle()

                # Turn lamp off
                send_command(0, 0, lamp=0)
            else:
                # Normal forward movement with slight random variation
                angular_drift = random.uniform(-0.1, 0.1)
                send_command(base_speed, angular_drift)

            time.sleep(0.2)

    except KeyboardInterrupt:
        print("\nExploration interrupted by user")
    finally:
        stop()

    elapsed = time.time() - start_time
    print(f"\nExploration complete!")
    print(f"  Duration: {elapsed:.1f} seconds")
    print(f"  Obstacles avoided: {obstacle_count}")


def wall_following_simulation(duration: float = 20.0):
    """
    Simulate wall-following behavior.

    The rover moves forward while maintaining a simulated
    distance from a wall on its right side.
    """
    print(f"Starting wall-following simulation for {duration} seconds...")

    start_time = time.time()
    target_distance = 0.5  # Simulated target distance from wall

    try:
        while time.time() - start_time < duration:
            # Simulate distance reading (random for demo)
            current_distance = target_distance + random.uniform(-0.3, 0.3)

            # Calculate correction
            error = current_distance - target_distance
            correction = error * 2.0  # Proportional control
            correction = max(-0.5, min(0.5, correction))  # Clamp

            # Apply movement
            send_command(0.4, correction)

            if abs(error) > 0.2:
                print(f"  Distance error: {error:+.2f}, correcting with angular: {correction:+.2f}")

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nWall following interrupted")
    finally:
        stop()

    print("Wall following complete!")


def random_walk(duration: float = 20.0, change_interval: float = 2.0):
    """
    Random walk exploration pattern.

    Changes direction randomly at regular intervals.
    """
    print(f"Starting random walk for {duration} seconds...")

    start_time = time.time()
    last_change = start_time

    # Initial random direction
    linear = random.uniform(0.3, 0.6)
    angular = random.uniform(-0.4, 0.4)

    try:
        while time.time() - start_time < duration:
            current_time = time.time()

            # Change direction periodically
            if current_time - last_change > change_interval:
                linear = random.uniform(0.2, 0.6)
                angular = random.uniform(-0.5, 0.5)
                last_change = current_time
                print(f"  New direction: linear={linear:.2f}, angular={angular:.2f}")

            send_command(linear, angular)
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nRandom walk interrupted")
    finally:
        stop()

    print("Random walk complete!")


def main():
    print("=== Obstacle Avoidance Simulation Demo ===\n")

    print("This demo simulates autonomous exploration with obstacle avoidance.")
    print("Obstacles are randomly generated for demonstration purposes.\n")

    print("Running short exploration demo (10 seconds)...\n")
    autonomous_explore(duration=10.0, base_speed=0.3)

    print("\n" + "=" * 40)
    print("\nAvailable behaviors:")
    print("  - autonomous_explore(duration, speed): Explore with obstacle avoidance")
    print("  - wall_following_simulation(duration): Follow a simulated wall")
    print("  - random_walk(duration, interval): Random exploration pattern")
    print("=" * 40)

    print("\n=== Demo Complete ===")


if __name__ == "__main__":
    main()

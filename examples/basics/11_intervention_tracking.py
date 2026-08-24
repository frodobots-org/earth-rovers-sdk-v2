"""
Intervention Tracking Example - Earth Rover SDK

This example demonstrates the intervention management system:
- Starting an intervention (human takeover period)
- Ending an intervention
- Viewing intervention history

Interventions track when human operators take control of the rover.
"""

import time
import json
from datetime import datetime

BASE_URL = "http://localhost:8000"

from _client import rover_session

SESSION = rover_session()


def start_intervention():
    """Start an intervention period."""
    print("Starting intervention...")
    response = SESSION.post(f"{BASE_URL}/interventions/start")
    result = response.json()
    print(f"  Response: {json.dumps(result, indent=2)}")
    return result


def end_intervention():
    """End the current intervention period."""
    print("Ending intervention...")
    response = SESSION.post(f"{BASE_URL}/interventions/end")
    result = response.json()
    print(f"  Response: {json.dumps(result, indent=2)}")
    return result


def get_intervention_history():
    """Get the history of interventions."""
    print("Fetching intervention history...")
    response = SESSION.get(f"{BASE_URL}/interventions/history")
    result = response.json()
    return result


def display_intervention_history(history: list):
    """Display intervention history in a formatted way."""
    print("\n=== Intervention History ===")

    if not history:
        print("  No interventions recorded")
        return

    total_duration = 0

    for i, intervention in enumerate(history):
        start_time = intervention.get("start_time", "N/A")
        end_time = intervention.get("end_time", "N/A")
        duration = intervention.get("duration_seconds", 0)
        reason = intervention.get("reason", "Not specified")

        total_duration += duration

        print(f"\n  Intervention {i + 1}:")
        print(f"    Started: {start_time}")
        print(f"    Ended: {end_time}")
        print(f"    Duration: {duration:.1f} seconds")
        print(f"    Reason: {reason}")

    print(f"\n  Total intervention time: {total_duration:.1f} seconds")
    print(f"  Number of interventions: {len(history)}")


def send_command(linear: float, angular: float, lamp: int = 0):
    """Send movement command."""
    SESSION.post(
        f"{BASE_URL}/control",
        json={"command": {"linear": linear, "angular": angular, "lamp": lamp}}
    )


def simulated_intervention():
    """
    Simulate an intervention scenario where human takes over control.
    """
    print("\n=== Simulated Intervention Scenario ===\n")

    print("Scenario: Autonomous mode encounters obstacle, human takes over.")
    print()

    # Autonomous operation
    print("1. Autonomous operation (moving forward)...")
    send_command(0.5, 0)
    time.sleep(2)

    # Obstacle detected - stop
    print("2. Obstacle detected! Stopping...")
    send_command(0, 0)
    time.sleep(1)

    # Human intervention starts
    print("3. Starting human intervention...")
    start_intervention()

    # Human takes manual control
    print("4. Human operator navigating around obstacle...")

    # Turn left
    print("   - Turning left...")
    send_command(0, -0.5)
    time.sleep(1)

    # Move forward
    print("   - Moving forward...")
    send_command(0.5, 0)
    time.sleep(1.5)

    # Turn right to resume original heading
    print("   - Turning right to resume heading...")
    send_command(0, 0.5)
    time.sleep(1)

    # Stop and end intervention
    print("5. Path clear, stopping...")
    send_command(0, 0)

    print("6. Ending human intervention...")
    end_intervention()

    print("\n7. Resuming autonomous operation...")
    send_command(0.3, 0)
    time.sleep(2)
    send_command(0, 0)

    print("\nIntervention scenario complete!")


def calculate_intervention_stats(history: list) -> dict:
    """Calculate statistics from intervention history."""
    if not history:
        return {
            "total_interventions": 0,
            "total_duration": 0,
            "average_duration": 0,
            "min_duration": 0,
            "max_duration": 0
        }

    durations = [i.get("duration_seconds", 0) for i in history]

    return {
        "total_interventions": len(history),
        "total_duration": sum(durations),
        "average_duration": sum(durations) / len(durations),
        "min_duration": min(durations),
        "max_duration": max(durations)
    }


def display_stats(stats: dict):
    """Display intervention statistics."""
    print("\n=== Intervention Statistics ===")
    print(f"  Total interventions: {stats['total_interventions']}")
    print(f"  Total duration: {stats['total_duration']:.1f} seconds")
    print(f"  Average duration: {stats['average_duration']:.1f} seconds")
    print(f"  Shortest: {stats['min_duration']:.1f} seconds")
    print(f"  Longest: {stats['max_duration']:.1f} seconds")


def main():
    print("=== Intervention Tracking Demo ===\n")

    # Check current intervention history
    try:
        history = get_intervention_history()
        display_intervention_history(history)

        stats = calculate_intervention_stats(history)
        display_stats(stats)
    except Exception as e:
        print(f"Note: Could not fetch history - {e}")
        print("The intervention endpoint may not be configured.\n")

    print("\n" + "=" * 50)
    print("To run a simulated intervention, call:")
    print("  simulated_intervention()")
    print("=" * 50)

    print("\n=== Demo Complete ===")


if __name__ == "__main__":
    main()

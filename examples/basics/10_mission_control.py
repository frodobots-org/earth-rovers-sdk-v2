"""
Mission Control Example - Earth Rover SDK

This example demonstrates the mission and checkpoint system:
- Starting and ending missions
- Retrieving checkpoint lists
- Marking checkpoints as reached
- Viewing mission history
"""

import time
import json

BASE_URL = "http://localhost:8000"

from _client import rover_session

SESSION = rover_session()


def start_mission():
    """Start a new mission."""
    print("Starting mission...")
    response = SESSION.post(f"{BASE_URL}/start-mission")
    result = response.json()
    print(f"  Response: {json.dumps(result, indent=2)}")
    return result


def end_mission():
    """End the current mission."""
    print("Ending mission...")
    response = SESSION.post(f"{BASE_URL}/end-mission")
    result = response.json()
    print(f"  Response: {json.dumps(result, indent=2)}")
    return result


def get_checkpoints():
    """Get the list of checkpoints for the current mission."""
    print("Fetching checkpoints...")
    response = SESSION.get(f"{BASE_URL}/checkpoints-list")
    result = response.json()
    if isinstance(result, dict) and "checkpoints_list" in result:
        return result.get("checkpoints_list", []), result.get("latest_scanned_checkpoint")
    if isinstance(result, list):
        return result, None
    return [], None


def display_checkpoints(checkpoints: list, latest_scanned=None):
    """Display checkpoints in a formatted way."""
    print("\n=== Checkpoints ===")
    if not checkpoints:
        print("  No checkpoints available")
        return

    for i, checkpoint in enumerate(checkpoints):
        sequence = checkpoint.get("sequence", i + 1)
        try:
            sequence_value = int(sequence)
        except (TypeError, ValueError):
            sequence_value = None

        reached = checkpoint.get("reached")
        if reached is None and latest_scanned is not None and sequence_value is not None:
            reached = sequence_value <= latest_scanned

        status = "✓" if reached else "○"
        name = checkpoint.get("name", f"Checkpoint {sequence_value or sequence}")
        lat = checkpoint.get("latitude", checkpoint.get("lat", "N/A"))
        lon = checkpoint.get("longitude", checkpoint.get("lon", "N/A"))
        print(f"  {status} {name}: ({lat}, {lon})")


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def mark_checkpoint_reached():
    """Mark the current checkpoint as reached."""
    print("Marking checkpoint as reached...")
    response = SESSION.post(f"{BASE_URL}/checkpoint-reached")
    result = response.json()
    print(f"  Response: {json.dumps(result, indent=2)}")
    return result


def get_mission_history():
    """Get the history of past missions."""
    print("Fetching mission history...")
    response = SESSION.get(f"{BASE_URL}/missions-history")
    result = response.json()
    return result


def display_mission_history(history: list):
    """Display mission history in a formatted way."""
    print("\n=== Mission History ===")
    if not history:
        print("  No mission history available")
        return

    for mission in history:
        mission_id = mission.get("id", "N/A")
        status = mission.get("status", "N/A")
        start_time = mission.get("start_time", "N/A")
        end_time = mission.get("end_time", "N/A")
        checkpoints_completed = mission.get("checkpoints_completed", 0)

        print(f"\n  Mission {mission_id}:")
        print(f"    Status: {status}")
        print(f"    Started: {start_time}")
        print(f"    Ended: {end_time}")
        print(f"    Checkpoints completed: {checkpoints_completed}")


def send_command(linear: float, angular: float):
    """Send movement command."""
    SESSION.post(
        f"{BASE_URL}/control",
        json={"command": {"linear": linear, "angular": angular, "lamp": 0}}
    )


def navigate_to_checkpoint(checkpoint: dict):
    """
    Simple navigation toward a checkpoint.

    Note: This is a simplified example. Real navigation would use
    GPS data and more sophisticated path planning.
    """
    print(f"\nNavigating to checkpoint: {checkpoint.get('name', 'Unknown')}")

    # Get current position
    response = SESSION.get(f"{BASE_URL}/data")
    data = response.json()
    current_lat = _to_float(data.get("latitude", data.get("lat", 0)))
    current_lon = _to_float(data.get("longitude", data.get("lon", 0)))

    target_lat = _to_float(checkpoint.get("latitude", checkpoint.get("lat", 0)))
    target_lon = _to_float(checkpoint.get("longitude", checkpoint.get("lon", 0)))

    print(f"  Current: ({current_lat}, {current_lon})")
    print(f"  Target:  ({target_lat}, {target_lon})")

    # Simple forward movement (in real use, calculate heading and distance)
    print("  Moving forward (simulated navigation)...")
    send_command(0.5, 0)
    time.sleep(2)
    send_command(0, 0)

    print("  Checkpoint area reached!")


def run_simple_mission():
    """Run through a simple mission workflow."""
    print("=== Simple Mission Workflow ===\n")

    # Start mission
    start_mission()
    time.sleep(1)

    # Get checkpoints
    checkpoints, latest_scanned = get_checkpoints()
    display_checkpoints(checkpoints, latest_scanned)

    # If there are checkpoints, navigate to first one
    if checkpoints and len(checkpoints) > 0:
        navigate_to_checkpoint(checkpoints[0])
        mark_checkpoint_reached()

    time.sleep(1)

    # End mission
    end_mission()

    # Show history
    history = get_mission_history()
    display_mission_history(history)


def main():
    print("=== Mission Control Demo ===\n")

    print("This demo shows the mission control capabilities.")
    print("Make sure you have MISSION_SLUG configured in your environment.\n")

    # Check current mission status
    try:
        checkpoints, latest_scanned = get_checkpoints()
        display_checkpoints(checkpoints, latest_scanned)
    except Exception as e:
        print(f"Note: Could not fetch checkpoints - {e}")
        print("This may be normal if no mission is configured.\n")

    # Show mission history
    try:
        history = get_mission_history()
        display_mission_history(history)
    except Exception as e:
        print(f"Note: Could not fetch history - {e}")

    print("\n" + "=" * 40)
    print("To run a full mission workflow, call: run_simple_mission()")
    print("=" * 40)

    print("\n=== Demo Complete ===")


if __name__ == "__main__":
    main()

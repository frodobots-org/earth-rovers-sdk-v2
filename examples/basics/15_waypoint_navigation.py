"""
Waypoint Navigation Example - Earth Rover SDK

This example demonstrates GPS-based waypoint navigation:
- Define a list of GPS waypoints
- Navigate between waypoints
- Calculate bearing and distance
- Track navigation progress

Note: Requires accurate GPS data from the rover.
"""

import time
import math

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


def get_current_position() -> tuple:
    """Get current GPS position."""
    data = get_telemetry()
    lat = _to_float(data.get("latitude", data.get("lat", 0)))
    lon = _to_float(data.get("longitude", data.get("lon", 0)))
    heading = data.get("orientation", 0)
    return lat, lon, heading


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance between two points
    on the earth (specified in decimal degrees).

    Returns distance in meters.
    """
    R = 6371000  # Earth's radius in meters

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (math.sin(delta_lat / 2) ** 2 +
         math.cos(lat1_rad) * math.cos(lat2_rad) *
         math.sin(delta_lon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def calculate_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the bearing from point 1 to point 2.

    Returns bearing in degrees (0-360).
    """
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lon = math.radians(lon2 - lon1)

    x = math.sin(delta_lon) * math.cos(lat2_rad)
    y = (math.cos(lat1_rad) * math.sin(lat2_rad) -
         math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lon))

    bearing = math.atan2(x, y)
    bearing = math.degrees(bearing)
    bearing = (bearing + 360) % 360

    return bearing


def normalize_angle(angle: float) -> float:
    """Normalize angle to -180 to 180 range."""
    while angle > 180:
        angle -= 360
    while angle < -180:
        angle += 360
    return angle


class WaypointNavigator:
    """Navigate through a series of GPS waypoints."""

    def __init__(self, waypoints: list, arrival_threshold: float = 2.0):
        """
        Initialize navigator.

        Args:
            waypoints: List of (lat, lon) tuples
            arrival_threshold: Distance (meters) to consider waypoint reached
        """
        self.waypoints = waypoints
        self.current_waypoint_idx = 0
        self.arrival_threshold = arrival_threshold
        self.navigation_active = False

    def get_current_waypoint(self) -> tuple:
        """Get current target waypoint."""
        if self.current_waypoint_idx < len(self.waypoints):
            return self.waypoints[self.current_waypoint_idx]
        return None

    def navigate_to_waypoint(self, target_lat: float, target_lon: float,
                             speed: float = 0.4, timeout: float = 60.0) -> bool:
        """
        Navigate to a single waypoint.

        Args:
            target_lat, target_lon: Target coordinates
            speed: Forward speed (0-1)
            timeout: Maximum time to reach waypoint

        Returns:
            True if waypoint reached, False if timeout
        """
        print(f"\nNavigating to waypoint: ({target_lat:.6f}, {target_lon:.6f})")

        start_time = time.time()

        while time.time() - start_time < timeout:
            # Get current position
            current_lat, current_lon, current_heading = get_current_position()

            # Calculate distance to target
            distance = haversine_distance(current_lat, current_lon,
                                         target_lat, target_lon)

            # Check if arrived
            if distance < self.arrival_threshold:
                stop()
                print(f"  Waypoint reached! (distance: {distance:.1f}m)")
                return True

            # Calculate bearing to target
            target_bearing = calculate_bearing(current_lat, current_lon,
                                              target_lat, target_lon)

            # Calculate heading error
            heading_error = normalize_angle(target_bearing - current_heading)

            # Calculate angular correction (proportional control)
            kp = 0.02  # Proportional gain
            angular = heading_error * kp
            angular = max(-0.5, min(0.5, angular))  # Clamp

            # Reduce speed when far off heading
            linear = speed * max(0.3, 1 - abs(heading_error) / 180)

            # Send command
            send_command(linear, angular)

            # Status update
            print(f"\r  Distance: {distance:.1f}m | Heading error: {heading_error:+.1f}° | Speed: {linear:.2f}  ", end="")

            time.sleep(0.1)

        stop()
        print(f"\n  Timeout reaching waypoint")
        return False

    def start_navigation(self, speed: float = 0.4):
        """
        Start navigating through all waypoints.
        """
        print(f"=== Starting Waypoint Navigation ===")
        print(f"Total waypoints: {len(self.waypoints)}")
        print(f"Arrival threshold: {self.arrival_threshold}m\n")

        self.navigation_active = True
        self.current_waypoint_idx = 0

        while self.current_waypoint_idx < len(self.waypoints) and self.navigation_active:
            waypoint = self.waypoints[self.current_waypoint_idx]
            print(f"\n--- Waypoint {self.current_waypoint_idx + 1}/{len(self.waypoints)} ---")

            success = self.navigate_to_waypoint(waypoint[0], waypoint[1], speed=speed)

            if success:
                self.current_waypoint_idx += 1
                # Pause briefly at waypoint
                time.sleep(1.0)
            else:
                print("Navigation failed, stopping")
                break

        stop()

        if self.current_waypoint_idx >= len(self.waypoints):
            print("\n=== All waypoints reached! ===")
        else:
            print(f"\n=== Navigation stopped at waypoint {self.current_waypoint_idx + 1} ===")

    def stop_navigation(self):
        """Stop navigation."""
        self.navigation_active = False
        stop()


def demo_navigation():
    """
    Demonstrate waypoint navigation with sample waypoints.

    Note: Replace these coordinates with actual nearby GPS coordinates
    for real testing.
    """
    # Sample waypoints (replace with real coordinates near your rover)
    # These are example coordinates - adjust for your location!
    sample_waypoints = [
        (37.7749, -122.4194),  # Point A
        (37.7750, -122.4195),  # Point B
        (37.7751, -122.4193),  # Point C
        (37.7749, -122.4194),  # Back to A
    ]

    print("=== Waypoint Navigation Demo ===\n")
    print("NOTE: This demo uses sample coordinates.")
    print("Replace with real GPS coordinates near your rover.\n")

    # Get current position
    lat, lon, heading = get_current_position()
    print(f"Current position: ({lat}, {lon})")
    print(f"Current heading: {heading}°\n")

    # Create navigator
    navigator = WaypointNavigator(
        waypoints=sample_waypoints,
        arrival_threshold=2.0  # 2 meters
    )

    # Start navigation (uncomment to run)
    # navigator.start_navigation(speed=0.3)

    print("To start navigation, call:")
    print("  navigator.start_navigation(speed=0.3)")


def main():
    demo_navigation()

    print("\n=== Demo Complete ===")


if __name__ == "__main__":
    main()

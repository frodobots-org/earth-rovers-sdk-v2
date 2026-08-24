"""
Telemetry Monitoring Example - Earth Rover SDK

This example shows how to read and monitor rover telemetry data:
- Battery level
- GPS position
- Signal strength
- Orientation
- Speed
- IMU data (accelerometer, gyroscope, magnetometer)
- Motor RPMs
"""

import time
import json

BASE_URL = "http://localhost:8000"

from _client import rover_session

SESSION = rover_session()


def get_telemetry():
    """Fetch current telemetry data from the rover."""
    response = SESSION.get(f"{BASE_URL}/data")
    return response.json()


def display_basic_info(data: dict):
    """Display basic rover information."""
    print("\n=== Basic Status ===")
    print(f"  Battery:     {data.get('battery', 'N/A')}%")
    signal = data.get("signal_level", data.get("signal", "N/A"))
    print(f"  Signal:      {signal}/5")
    print(f"  GPS Signal:  {data.get('gps_signal', 'N/A')}%")
    print(f"  Speed:       {data.get('speed', 'N/A')}")
    print(f"  Orientation: {data.get('orientation', 'N/A')}°")
    print(f"  Lamp:        {'ON' if data.get('lamp') else 'OFF'}")


def display_position(data: dict):
    """Display GPS position."""
    print("\n=== Position ===")
    print(f"  Latitude:  {data.get('latitude', data.get('lat', 'N/A'))}")
    print(f"  Longitude: {data.get('longitude', data.get('lon', 'N/A'))}")


def _latest_xyz(samples):
    """Extract latest (x, y, z) from dict or list formats."""
    if isinstance(samples, dict):
        return samples.get("x"), samples.get("y"), samples.get("z")
    if isinstance(samples, list) and samples:
        last = samples[-1]
        if isinstance(last, dict):
            return last.get("x"), last.get("y"), last.get("z")
        if isinstance(last, (list, tuple)) and len(last) >= 3:
            return last[0], last[1], last[2]
    return None, None, None


def display_imu_data(data: dict):
    """Display IMU sensor readings."""
    print("\n=== IMU Data ===")

    # Accelerometer
    accels = data.get("accels", {})
    ax, ay, az = _latest_xyz(accels)
    if ax is not None:
        print(f"  Accelerometer:")
        print(f"    X: {ax}")
        print(f"    Y: {ay}")
        print(f"    Z: {az}")

    # Gyroscope
    gyros = data.get("gyros", {})
    gx, gy, gz = _latest_xyz(gyros)
    if gx is not None:
        print(f"  Gyroscope:")
        print(f"    X: {gx}")
        print(f"    Y: {gy}")
        print(f"    Z: {gz}")

    # Magnetometer
    mags = data.get("mags", {})
    mx, my, mz = _latest_xyz(mags)
    if mx is not None:
        print(f"  Magnetometer:")
        print(f"    X: {mx}")
        print(f"    Y: {my}")
        print(f"    Z: {mz}")


def display_motor_data(data: dict):
    """Display motor RPM readings."""
    print("\n=== Motor Data ===")

    rpms = data.get("rpms", {})
    if isinstance(rpms, dict):
        print(f"  Motor 1: {rpms.get('m1', 'N/A')} RPM")
        print(f"  Motor 2: {rpms.get('m2', 'N/A')} RPM")
        print(f"  Motor 3: {rpms.get('m3', 'N/A')} RPM")
        print(f"  Motor 4: {rpms.get('m4', 'N/A')} RPM")
    elif isinstance(rpms, list) and rpms:
        last = rpms[-1]
        if isinstance(last, (list, tuple)) and len(last) >= 4:
            print(f"  Motor 1: {last[0]} RPM")
            print(f"  Motor 2: {last[1]} RPM")
            print(f"  Motor 3: {last[2]} RPM")
            print(f"  Motor 4: {last[3]} RPM")


def display_all_telemetry(data: dict):
    """Display all telemetry in a formatted way."""
    display_basic_info(data)
    display_position(data)
    display_imu_data(data)
    display_motor_data(data)


def monitor_continuous(duration: float = 10.0, interval: float = 1.0):
    """Continuously monitor telemetry for specified duration."""
    print(f"\n=== Continuous Monitoring ({duration}s) ===")

    start_time = time.time()

    while time.time() - start_time < duration:
        data = get_telemetry()

        # Clear screen effect (print separator)
        print("\n" + "=" * 40)
        print(f"Time: {time.time() - start_time:.1f}s")

        print(f"Battery: {data.get('battery', 'N/A')}% | "
              f"Signal: {data.get('signal', 'N/A')}/5 | "
              f"Speed: {data.get('speed', 'N/A')}")

        time.sleep(interval)

    print("\nMonitoring complete!")


def log_telemetry_to_file(filename: str = "telemetry_log.json", samples: int = 10, interval: float = 1.0):
    """Log telemetry samples to a JSON file."""
    print(f"\nLogging {samples} telemetry samples to {filename}...")

    logs = []

    for i in range(samples):
        data = get_telemetry()
        data['sample_number'] = i + 1
        data['timestamp'] = time.time()
        logs.append(data)
        print(f"  Sample {i + 1}/{samples} captured")
        time.sleep(interval)

    with open(filename, 'w') as f:
        json.dump(logs, f, indent=2)

    print(f"Telemetry logged to {filename}")


def main():
    print("=== Telemetry Monitoring Demo ===")

    # Get and display current telemetry
    print("\nFetching current telemetry...")
    data = get_telemetry()
    display_all_telemetry(data)

    # Continuous monitoring
    print("\nStarting continuous monitoring (5 seconds)...")
    monitor_continuous(duration=5.0, interval=1.0)

    print("\n=== Demo Complete ===")


if __name__ == "__main__":
    main()

"""
Async Control Example - Earth Rover SDK

This example demonstrates asynchronous control patterns:
- Non-blocking movement commands
- Concurrent telemetry monitoring
- Combined video + control operations

Uses asyncio and aiohttp for efficient async operations.
"""

import asyncio
import aiohttp
import base64
import numpy as np
import cv2

BASE_URL = "http://localhost:8000"

from _client import rover_auth_headers

HEADERS = rover_auth_headers()


async def send_command_async(session: aiohttp.ClientSession,
                              linear: float, angular: float, lamp: int = 0):
    """Send movement command asynchronously."""
    async with session.post(
        f"{BASE_URL}/control",
        json={"command": {"linear": linear, "angular": angular, "lamp": lamp}}
    ) as response:
        return await response.json()


async def get_telemetry_async(session: aiohttp.ClientSession) -> dict:
    """Get telemetry data asynchronously."""
    async with session.get(f"{BASE_URL}/data") as response:
        return await response.json()


async def get_frames_async(session: aiohttp.ClientSession) -> tuple:
    """Get camera frames asynchronously."""
    async with session.get(f"{BASE_URL}/v2/screenshot") as response:
        if response.status == 200:
            data = await response.json()
            return data.get("front_frame"), data.get("rear_frame")
    return None, None


def decode_frame(base64_image: str):
    """Decode base64 image."""
    if not base64_image:
        return None
    image_bytes = base64.b64decode(base64_image)
    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    return cv2.imdecode(image_array, cv2.IMREAD_COLOR)


async def telemetry_monitor(session: aiohttp.ClientSession,
                            stop_event: asyncio.Event,
                            interval: float = 0.5):
    """Continuously monitor telemetry in the background."""
    print("Telemetry monitor started")

    while not stop_event.is_set():
        try:
            data = await get_telemetry_async(session)
            battery = data.get('battery', 'N/A')
            speed = data.get('speed', 'N/A')
            orientation = data.get('orientation', 'N/A')

            print(f"\r[Telemetry] Battery: {battery}% | Speed: {speed} | Heading: {orientation}°    ", end="")
        except Exception as e:
            print(f"\r[Telemetry] Error: {e}    ", end="")

        await asyncio.sleep(interval)

    print("\nTelemetry monitor stopped")


async def movement_sequence(session: aiohttp.ClientSession):
    """Execute a movement sequence."""
    print("\nExecuting movement sequence...")

    movements = [
        (0.5, 0, "Moving forward"),
        (0.5, 0.3, "Curving right"),
        (0.5, -0.3, "Curving left"),
        (0, 0.5, "Spinning right"),
        (-0.3, 0, "Moving backward"),
        (0, 0, "Stopped"),
    ]

    for linear, angular, description in movements:
        print(f"\n  {description}...")
        await send_command_async(session, linear, angular)
        await asyncio.sleep(1.5)

    print("\nMovement sequence complete!")


async def video_stream_task(session: aiohttp.ClientSession,
                            stop_event: asyncio.Event):
    """Stream video in the background."""
    print("Video stream started")

    while not stop_event.is_set():
        try:
            front_b64, rear_b64 = await get_frames_async(session)

            front_frame = decode_frame(front_b64)
            if front_frame is not None:
                cv2.imshow("Front Camera (Async)", front_frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                stop_event.set()
                break

        except Exception as e:
            pass  # Ignore frame errors

        await asyncio.sleep(0.05)  # ~20 FPS

    cv2.destroyAllWindows()
    print("Video stream stopped")


async def concurrent_control_and_monitor():
    """
    Demonstrate concurrent operations:
    - Video streaming
    - Telemetry monitoring
    - Movement control

    All running simultaneously.
    """
    print("=== Concurrent Control Demo ===\n")

    stop_event = asyncio.Event()

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        # Create background tasks
        telemetry_task = asyncio.create_task(
            telemetry_monitor(session, stop_event, interval=1.0)
        )

        video_task = asyncio.create_task(
            video_stream_task(session, stop_event)
        )

        # Run movement sequence
        await asyncio.sleep(1)  # Let monitors start
        await movement_sequence(session)

        # Let it run a bit more
        await asyncio.sleep(2)

        # Stop background tasks
        stop_event.set()

        # Wait for tasks to complete
        await asyncio.gather(telemetry_task, video_task, return_exceptions=True)


async def parallel_data_fetch():
    """Fetch multiple data sources in parallel."""
    print("=== Parallel Data Fetch Demo ===\n")

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        # Fetch telemetry and frames in parallel
        telemetry_coro = get_telemetry_async(session)
        frames_coro = get_frames_async(session)

        # Execute both simultaneously
        telemetry, frames = await asyncio.gather(telemetry_coro, frames_coro)

        print("Telemetry:")
        print(f"  Battery: {telemetry.get('battery', 'N/A')}%")
        latitude = telemetry.get("latitude", telemetry.get("lat", "N/A"))
        longitude = telemetry.get("longitude", telemetry.get("lon", "N/A"))
        print(f"  Position: ({latitude}, {longitude})")

        print("\nFrames:")
        front_b64, rear_b64 = frames
        print(f"  Front: {'Available' if front_b64 else 'Not available'}")
        print(f"  Rear: {'Available' if rear_b64 else 'Not available'}")


async def timed_movement(session: aiohttp.ClientSession,
                         linear: float, angular: float,
                         duration: float):
    """Execute a timed movement with precise async timing."""
    await send_command_async(session, linear, angular)
    await asyncio.sleep(duration)
    await send_command_async(session, 0, 0)


async def complex_maneuver():
    """Execute a complex maneuver with precise timing."""
    print("=== Complex Maneuver Demo ===\n")

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        # Execute multiple timed movements
        print("Executing complex maneuver...")

        await timed_movement(session, 0.5, 0, 1.0)
        await timed_movement(session, 0, 0.5, 0.5)
        await timed_movement(session, 0.5, 0, 1.0)
        await timed_movement(session, 0, -0.5, 0.5)
        await timed_movement(session, 0.5, 0, 1.0)

        print("Maneuver complete!")


async def main():
    print("=== Async Control Demo ===\n")

    # Parallel data fetch
    await parallel_data_fetch()

    print("\n" + "=" * 40 + "\n")

    # Complex maneuver
    await complex_maneuver()

    print("\n" + "=" * 40)
    print("\nTo run the full concurrent demo with video, call:")
    print("  asyncio.run(concurrent_control_and_monitor())")

    print("\n=== Demo Complete ===")


if __name__ == "__main__":
    asyncio.run(main())

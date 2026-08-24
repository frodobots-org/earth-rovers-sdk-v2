"""
Video Recording Example - Earth Rover SDK

This example demonstrates how to record video from the rover's cameras
and save it to a file.

Records both front and rear camera feeds to separate video files.
"""

import asyncio
import aiohttp
import base64
import numpy as np
import cv2
import time
from datetime import datetime

BASE_URL = "http://localhost:8000"

from _client import rover_auth_headers

HEADERS = rover_auth_headers()


async def fetch_frames(session: aiohttp.ClientSession) -> tuple:
    """Fetch camera frames."""
    async with session.get(f"{BASE_URL}/v2/screenshot") as response:
        if response.status == 200:
            data = await response.json()
            return data.get("front_frame"), data.get("rear_frame")
    return None, None


def decode_frame(base64_image: str):
    """Decode base64 image to OpenCV format."""
    if not base64_image:
        return None
    image_bytes = base64.b64decode(base64_image)
    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    return cv2.imdecode(image_array, cv2.IMREAD_COLOR)


async def record_video(
    duration: float = 10.0,
    filename_prefix: str = "rover_recording",
    fps: int = 15,
    show_preview: bool = True
):
    """
    Record video from rover cameras.

    Args:
        duration: Recording duration in seconds
        filename_prefix: Prefix for output filenames
        fps: Frames per second for the output video
        show_preview: Whether to show live preview while recording
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    front_filename = f"{filename_prefix}_front_{timestamp}.avi"
    rear_filename = f"{filename_prefix}_rear_{timestamp}.avi"

    print(f"Recording for {duration} seconds...")
    print(f"Output files: {front_filename}, {rear_filename}")

    front_writer = None
    rear_writer = None
    frame_count = 0

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        start_time = time.time()

        while time.time() - start_time < duration:
            front_b64, rear_b64 = await fetch_frames(session)

            front_frame = decode_frame(front_b64)
            rear_frame = decode_frame(rear_b64)

            # Initialize video writers on first frame
            if front_frame is not None and front_writer is None:
                h, w = front_frame.shape[:2]
                fourcc = cv2.VideoWriter_fourcc(*'XVID')
                front_writer = cv2.VideoWriter(front_filename, fourcc, fps, (w, h))
                print(f"Front camera: {w}x{h}")

            if rear_frame is not None and rear_writer is None:
                h, w = rear_frame.shape[:2]
                fourcc = cv2.VideoWriter_fourcc(*'XVID')
                rear_writer = cv2.VideoWriter(rear_filename, fourcc, fps, (w, h))
                print(f"Rear camera: {w}x{h}")

            # Write frames
            if front_frame is not None and front_writer is not None:
                front_writer.write(front_frame)

            if rear_frame is not None and rear_writer is not None:
                rear_writer.write(rear_frame)

            frame_count += 1

            # Show preview
            if show_preview:
                if front_frame is not None:
                    # Add recording indicator
                    cv2.circle(front_frame, (30, 30), 10, (0, 0, 255), -1)
                    elapsed = time.time() - start_time
                    cv2.putText(front_frame, f"REC {elapsed:.1f}s", (50, 35),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                    cv2.imshow("Recording - Front", front_frame)

                if rear_frame is not None:
                    cv2.circle(rear_frame, (30, 30), 10, (0, 0, 255), -1)
                    cv2.imshow("Recording - Rear", rear_frame)

                if cv2.waitKey(1) & 0xFF == ord('q'):
                    print("\nRecording stopped by user")
                    break

            # Control frame rate
            await asyncio.sleep(1.0 / fps)

    # Clean up
    if front_writer:
        front_writer.release()
    if rear_writer:
        rear_writer.release()

    if show_preview:
        cv2.destroyAllWindows()

    actual_duration = time.time() - start_time
    actual_fps = frame_count / actual_duration

    print(f"\nRecording complete!")
    print(f"  Frames captured: {frame_count}")
    print(f"  Duration: {actual_duration:.1f}s")
    print(f"  Actual FPS: {actual_fps:.1f}")


async def timelapse_capture(
    duration: float = 60.0,
    interval: float = 2.0,
    filename_prefix: str = "timelapse"
):
    """
    Capture timelapse images at regular intervals.

    Args:
        duration: Total capture duration in seconds
        interval: Time between captures in seconds
        filename_prefix: Prefix for output filenames
    """
    print(f"Capturing timelapse for {duration}s with {interval}s intervals...")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    frame_num = 0

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        start_time = time.time()

        while time.time() - start_time < duration:
            front_b64, rear_b64 = await fetch_frames(session)

            front_frame = decode_frame(front_b64)
            rear_frame = decode_frame(rear_b64)

            # Save frames
            if front_frame is not None:
                filename = f"{filename_prefix}_front_{timestamp}_{frame_num:04d}.jpg"
                cv2.imwrite(filename, front_frame)
                print(f"  Captured: {filename}")

            if rear_frame is not None:
                filename = f"{filename_prefix}_rear_{timestamp}_{frame_num:04d}.jpg"
                cv2.imwrite(filename, rear_frame)

            frame_num += 1
            await asyncio.sleep(interval)

    print(f"\nTimelapse complete! {frame_num} frames captured.")


async def main():
    print("=== Video Recording Demo ===\n")

    print("This demo will record 5 seconds of video.")
    print("Press 'q' to stop early.\n")

    await asyncio.sleep(2)

    # Record video
    await record_video(duration=5.0, fps=10, show_preview=True)

    print("\n=== Demo Complete ===")
    print("\nOther recording options:")
    print("  - timelapse_capture(): Capture still images at intervals")
    print("  - Adjust fps, duration, and filename_prefix as needed")


if __name__ == "__main__":
    asyncio.run(main())

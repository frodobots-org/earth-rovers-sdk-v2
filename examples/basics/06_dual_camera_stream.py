"""
Dual Camera Stream Example - Earth Rover SDK

This example demonstrates how to capture and display
both front and rear camera feeds simultaneously.

Uses the optimized /v2/screenshot endpoint for best performance.
"""

import asyncio
import aiohttp
import base64
import numpy as np
import cv2

BASE_URL = "http://localhost:8000"

from _client import rover_auth_headers

HEADERS = rover_auth_headers()


async def fetch_dual_frames(session: aiohttp.ClientSession) -> tuple:
    """Fetch both camera frames in a single request (optimized)."""
    async with session.get(f"{BASE_URL}/v2/screenshot") as response:
        if response.status == 200:
            data = await response.json()
            return data.get("front_frame"), data.get("rear_frame"), data.get("timestamp")
    return None, None, None


def decode_frame(base64_image: str):
    """Decode a base64 image to OpenCV format."""
    if not base64_image:
        return None
    image_bytes = base64.b64decode(base64_image)
    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    return cv2.imdecode(image_array, cv2.IMREAD_COLOR)


async def display_dual_stream():
    """Display both camera feeds side by side."""
    print("Starting dual camera stream...")
    print("Press 'q' to quit")

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        frame_count = 0
        start_time = asyncio.get_event_loop().time()

        while True:
            try:
                front_b64, rear_b64, timestamp = await fetch_dual_frames(session)

                front_frame = decode_frame(front_b64)
                rear_frame = decode_frame(rear_b64)

                # Display front camera
                if front_frame is not None:
                    cv2.imshow("Front Camera", front_frame)

                # Display rear camera
                if rear_frame is not None:
                    cv2.imshow("Rear Camera", rear_frame)

                # Create side-by-side view if both available
                if front_frame is not None and rear_frame is not None:
                    # Resize to same height if needed
                    h1, w1 = front_frame.shape[:2]
                    h2, w2 = rear_frame.shape[:2]

                    if h1 != h2:
                        # Resize rear to match front height
                        scale = h1 / h2
                        rear_frame = cv2.resize(rear_frame, (int(w2 * scale), h1))

                    combined = np.hstack([front_frame, rear_frame])

                    # Add FPS counter
                    frame_count += 1
                    elapsed = asyncio.get_event_loop().time() - start_time
                    fps = frame_count / elapsed if elapsed > 0 else 0
                    cv2.putText(combined, f"FPS: {fps:.1f}", (10, 30),
                               cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

                    cv2.imshow("Dual Camera View", combined)

                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

            except Exception as e:
                print(f"Error: {e}")

            await asyncio.sleep(0.01)  # ~100 FPS max

    cv2.destroyAllWindows()


async def capture_snapshot():
    """Capture and save a single snapshot from both cameras."""
    print("Capturing snapshot...")

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        front_b64, rear_b64, timestamp = await fetch_dual_frames(session)

        front_frame = decode_frame(front_b64)
        rear_frame = decode_frame(rear_b64)

        if front_frame is not None:
            filename = f"front_snapshot_{int(timestamp or 0)}.png"
            cv2.imwrite(filename, front_frame)
            print(f"  Saved: {filename}")

        if rear_frame is not None:
            filename = f"rear_snapshot_{int(timestamp or 0)}.png"
            cv2.imwrite(filename, rear_frame)
            print(f"  Saved: {filename}")


async def main():
    print("=== Dual Camera Stream Demo ===\n")

    # Option 1: Capture a single snapshot
    await capture_snapshot()

    print("\nStarting live stream in 2 seconds...")
    await asyncio.sleep(2)

    # Option 2: Live streaming display
    await display_dual_stream()

    print("\n=== Demo Complete ===")


if __name__ == "__main__":
    asyncio.run(main())

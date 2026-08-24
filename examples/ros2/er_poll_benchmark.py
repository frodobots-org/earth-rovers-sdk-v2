#!/usr/bin/env python3
"""ROS2 frame-acquisition benchmark for the Earth Rovers SDK.

Replicates the pattern researchers use — polling the SDK's snapshot
endpoints at a fixed rate from a ROS node — and measures what that client
actually experiences: per-request wall time (logged in the familiar
`Image (SDK): <seconds>` format), rolling percentiles, error counts, and an
optional CSV for before/after comparison. Also supports consuming the MJPEG
/feed stream, measuring inter-frame arrival gaps, for an apples-to-apples
comparison of the two integration styles.

Frames are published as sensor_msgs/CompressedImage (JPEG bytes straight
from the SDK, no decode), so the node needs no cv_bridge/OpenCV.

Usage (SDK running on the host, node inside the ros:humble container):
    python3 er_poll_benchmark.py --ros-args \
        -p sdk_url:=http://host.docker.internal:8000 \
        -p mode:=v2_front -p rate_hz:=10.0

    # /feed comparison at 15 fps:
    python3 er_poll_benchmark.py --ros-args \
        -p sdk_url:=http://host.docker.internal:8000 -p mode:=feed

Parameters:
    sdk_url        SDK base URL (default http://localhost:8000)
    mode           v2_front | v2_screenshot | feed (default v2_front)
    rate_hz        poll rate for v2 modes (default 10.0)
    feed_fps       requested fps for feed mode (default 15)
    use_session    reuse one TCP connection (default True; False opens a new
                   connection per request, like a naive client)
    http_timeout_s per-request timeout, generous on purpose so server stalls
                   are observed rather than clipped (default 10.0)
    csv_path       write per-sample rows to this file (default: disabled)

Dependencies: rclpy + requests (both present in the ros:humble image).
"""

import base64
import csv
import os
import threading
import time

import rclpy
import requests
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CompressedImage

SUMMARY_INTERVAL_S = 30.0


class Stats:
    def __init__(self):
        self.samples = []
        self.errors = {}
        self.empty = 0
        self.duplicate_timestamps = 0
        self._last_frame_timestamp = None
        self.started = time.monotonic()
        self.lock = threading.Lock()

    def record(self, seconds, frame_timestamp=None):
        with self.lock:
            self.samples.append(seconds)
            if frame_timestamp is not None:
                if frame_timestamp == self._last_frame_timestamp:
                    self.duplicate_timestamps += 1
                self._last_frame_timestamp = frame_timestamp

    def record_error(self, kind):
        with self.lock:
            self.errors[kind] = self.errors.get(kind, 0) + 1

    @staticmethod
    def _percentile(ordered, fraction):
        index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
        return ordered[index]

    def summary(self):
        with self.lock:
            ordered = sorted(self.samples)
            errors = dict(self.errors)
            duplicates = self.duplicate_timestamps
            empty = self.empty
        elapsed = time.monotonic() - self.started
        if not ordered:
            return f"no samples in {elapsed:.0f}s, errors={errors}"
        return (
            f"{len(ordered)} samples over {elapsed:.0f}s | "
            f"p50={self._percentile(ordered, 0.50):.4f}s "
            f"p90={self._percentile(ordered, 0.90):.4f}s "
            f"p99={self._percentile(ordered, 0.99):.4f}s "
            f"max={ordered[-1]:.4f}s | "
            f">0.5s: {sum(1 for s in ordered if s > 0.5)} "
            f">1s: {sum(1 for s in ordered if s > 1.0)} | "
            f"dup_timestamps: {duplicates} empty: {empty} errors: {errors}"
        )


class ErPollBenchmark(Node):
    def __init__(self):
        super().__init__("er_poll_benchmark")
        self.declare_parameter("sdk_url", "http://localhost:8000")
        self.declare_parameter("mode", "v2_front")
        self.declare_parameter("rate_hz", 10.0)
        self.declare_parameter("feed_fps", 15)
        self.declare_parameter("use_session", True)
        self.declare_parameter("http_timeout_s", 10.0)
        self.declare_parameter("csv_path", "")
        self.declare_parameter("rover_api_key", os.environ.get("ROVER_API_KEY", ""))

        self.rover_api_key = self.get_parameter("rover_api_key").value
        self._auth_headers = (
            {"Authorization": f"Bearer {self.rover_api_key}"} if self.rover_api_key else {}
        )
        self.sdk_url = self.get_parameter("sdk_url").value.rstrip("/")
        self.mode = self.get_parameter("mode").value
        self.rate_hz = float(self.get_parameter("rate_hz").value)
        self.feed_fps = int(self.get_parameter("feed_fps").value)
        self.use_session = bool(self.get_parameter("use_session").value)
        self.http_timeout_s = float(self.get_parameter("http_timeout_s").value)
        self.csv_path = self.get_parameter("csv_path").value

        if self.mode not in ("v2_front", "v2_screenshot", "feed"):
            raise ValueError(f"invalid mode: {self.mode}")

        sensor_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
        )
        self.image_pub = self.create_publisher(
            CompressedImage, "earth_rover/front/image_raw/compressed", sensor_qos
        )

        self.stats = Stats()
        self._csv_lock = threading.Lock()
        self._csv_handle = None
        self._csv = None
        if self.csv_path:
            self._csv_handle = open(self.csv_path, "w", newline="")
            self._csv = csv.writer(self._csv_handle)
            self._csv.writerow(["wall_time", "seconds", "status", "frame_timestamp"])

        self._running = True
        worker = self._feed_loop if self.mode == "feed" else self._poll_loop
        threading.Thread(target=worker, daemon=True).start()
        self.create_timer(SUMMARY_INTERVAL_S, self._log_summary)
        self.get_logger().info(
            f"Benchmarking {self.sdk_url} mode={self.mode} "
            + (f"fps={self.feed_fps}" if self.mode == "feed"
               else f"rate={self.rate_hz}Hz session={self.use_session}")
        )

    def _log_summary(self):
        self.get_logger().info(f"[summary] {self.stats.summary()}")

    def _write_csv(self, seconds, status, frame_timestamp):
        if not self._csv:
            return
        with self._csv_lock:
            self._csv.writerow(
                [time.time(), f"{seconds:.6f}", status, frame_timestamp]
            )

    def _publish_jpeg(self, jpeg_bytes):
        msg = CompressedImage()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "earth_rover_front_camera"
        msg.format = "jpeg"
        msg.data = jpeg_bytes
        self.image_pub.publish(msg)

    # ------------------------------------------------------------- v2 poll

    def _poll_loop(self):
        endpoint = "/v2/front" if self.mode == "v2_front" else "/v2/screenshot"
        url = self.sdk_url + endpoint
        http = requests.Session() if self.use_session else requests
        if self.use_session:
            http.headers.update(self._auth_headers)
        interval = 1.0 / self.rate_hz
        deadline = time.monotonic()
        while self._running and rclpy.ok():
            started = time.monotonic()
            status, frame_timestamp = "exc", None
            elapsed = 0.0
            try:
                response = http.get(
                    url,
                    timeout=self.http_timeout_s,
                    headers=None if self.use_session else self._auth_headers,
                )
                status = str(response.status_code)
                elapsed = time.monotonic() - started
                if response.status_code == 200:
                    body = response.json()
                    frame = body.get("front_frame")
                    frame_timestamp = body.get("timestamp")
                    if frame:
                        self.get_logger().info(f"Image (SDK): {elapsed:.6f}")
                        self.stats.record(elapsed, frame_timestamp)
                        self._publish_jpeg(base64.b64decode(frame))
                    else:
                        self.stats.empty += 1
                        self.get_logger().error(
                            f"No image data in response ({elapsed:.6f}s)"
                        )
                else:
                    # A failed HTTP response still consumed a polling tick.
                    # Include it in latency percentiles so slow failures cannot
                    # disappear from the benchmark summary.
                    self.stats.record(elapsed)
                    self.stats.record_error(status)
                    self.get_logger().error(
                        f"No image data in response "
                        f"(HTTP {status}, {elapsed:.6f}s)"
                    )
            except requests.RequestException as exc:
                elapsed = time.monotonic() - started
                self.stats.record(elapsed)
                self.stats.record_error(type(exc).__name__)
                self.get_logger().error(f"Request failed ({elapsed:.6f}s): {exc}")
            self._write_csv(elapsed, status, frame_timestamp)

            deadline += interval
            wait = deadline - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            else:
                # Drop missed ticks. An immediate retry after a slow request can
                # read that request's still-fresh cached frame and create a
                # duplicate publication.
                deadline = time.monotonic() + interval
                time.sleep(interval)

    # ---------------------------------------------------------------- feed

    def _feed_loop(self):
        url = f"{self.sdk_url}/feed?view=front&fps={self.feed_fps}"
        while self._running and rclpy.ok():
            try:
                with requests.get(
                    url,
                    stream=True,
                    timeout=(5, self.http_timeout_s),
                    headers=self._auth_headers,
                ) as response:
                    if response.status_code != 200:
                        self.stats.record_error(str(response.status_code))
                        self.get_logger().warning(
                            f"/feed HTTP {response.status_code}, retrying in 2s"
                        )
                        time.sleep(2)
                        continue
                    self.get_logger().info(f"Connected to {url}")
                    self._consume_mjpeg(response)
            except requests.RequestException as exc:
                self.stats.record_error(type(exc).__name__)
                self.get_logger().warning(f"/feed reconnecting: {exc}")
                time.sleep(2)

    def _consume_mjpeg(self, response):
        """Split the multipart stream on its boundary, timing frame arrivals."""
        boundary = b"--frame"
        buffer = b""
        previous = None
        for chunk in response.iter_content(chunk_size=16384):
            if not (self._running and rclpy.ok()):
                return
            buffer += chunk
            while True:
                start = buffer.find(boundary)
                next_part = buffer.find(boundary, start + len(boundary))
                if start < 0 or next_part < 0:
                    break
                part = buffer[start:next_part]
                buffer = buffer[next_part:]
                header_end = part.find(b"\r\n\r\n")
                if header_end < 0:
                    continue
                jpeg = part[header_end + 4 :].rstrip(b"\r\n")
                now = time.monotonic()
                if previous is not None:
                    gap = now - previous
                    self.get_logger().info(f"Image (SDK): {gap:.6f}")
                    self.stats.record(gap)
                    self._write_csv(gap, "frame", None)
                previous = now
                if jpeg:
                    self._publish_jpeg(jpeg)

    def destroy_node(self):
        self._running = False
        summary = f"[final] {self.stats.summary()}"
        if rclpy.ok():
            self.get_logger().info(summary)
        else:
            # SIGINT can invalidate rosout before destroy_node() runs.
            print(summary, flush=True)
        if self._csv_handle:
            with self._csv_lock:
                self._csv_handle.close()
            self.get_logger().info(f"CSV written to {self.csv_path}")
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = ErPollBenchmark()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        # SIGINT may have already shut the context down via rclpy's handler.
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()

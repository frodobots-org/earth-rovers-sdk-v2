#!/usr/bin/env python3
"""Frame-latency probe for the Earth Rovers SDK.

Measures what a researcher's polling client actually experiences, without ROS
or Docker in the way:

  v2_front       GET /v2/front at a fixed rate, wall time per request
  v2_screenshot  GET /v2/screenshot at a fixed rate, wall time per request
  feed           GET /feed MJPEG stream, inter-frame arrival gaps

Prints one line per sample (er_driver's format, so runs are directly
comparable with researcher logs), a rolling summary every 30 s, and a final
summary on Ctrl-C. Optionally dumps per-sample rows to CSV for before/after
comparison.

Usage:
    python3 examples/benchmarks/latency_probe.py --mode v2_front --rate 10
    python3 examples/benchmarks/latency_probe.py --mode feed --fps 15 \
        --csv baseline_feed.csv
"""

import argparse
import csv
import os
import signal
import sys
import time

import requests

ROVER_API_KEY = os.environ.get("ROVER_API_KEY", "")
AUTH_HEADERS = {"Authorization": f"Bearer {ROVER_API_KEY}"} if ROVER_API_KEY else {}

SUMMARY_INTERVAL_S = 30.0


class Stats:
    def __init__(self, label: str):
        self.label = label
        self.samples: list[float] = []
        self.errors: dict[str, int] = {}
        self.empty = 0
        self.duplicate_timestamps = 0
        self._last_frame_timestamp = None
        self.started = time.monotonic()

    def record(self, seconds: float, frame_timestamp=None):
        self.samples.append(seconds)
        if frame_timestamp is not None:
            if frame_timestamp == self._last_frame_timestamp:
                self.duplicate_timestamps += 1
            self._last_frame_timestamp = frame_timestamp

    def record_error(self, kind: str):
        self.errors[kind] = self.errors.get(kind, 0) + 1

    @staticmethod
    def _percentile(ordered: list[float], fraction: float) -> float:
        index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
        return ordered[index]

    def summary(self) -> str:
        elapsed = time.monotonic() - self.started
        if not self.samples:
            return f"[{self.label}] no samples in {elapsed:.0f}s, errors={self.errors}"
        ordered = sorted(self.samples)
        lines = [
            f"[{self.label}] {len(ordered)} samples over {elapsed:.0f}s",
            f"  p50={self._percentile(ordered, 0.50):.4f}s"
            f"  p90={self._percentile(ordered, 0.90):.4f}s"
            f"  p99={self._percentile(ordered, 0.99):.4f}s"
            f"  max={ordered[-1]:.4f}s",
            f"  >0.5s: {sum(1 for s in ordered if s > 0.5)}"
            f"  >1s: {sum(1 for s in ordered if s > 1.0)}"
            f"  duplicate_timestamps: {self.duplicate_timestamps}"
            f"  empty: {self.empty}",
        ]
        if self.errors:
            lines.append(f"  errors: {self.errors}")
        return "\n".join(lines)


def open_csv(path):
    handle = open(path, "w", newline="")
    writer = csv.writer(handle)
    writer.writerow(["wall_time", "seconds", "status", "frame_timestamp"])
    return handle, writer


def poll_v2(args, stats: Stats, writer):
    """Fixed-rate polling with deadline pacing and missed-tick dropping."""
    endpoint = "/v2/front" if args.mode == "v2_front" else "/v2/screenshot"
    frame_key = "front_frame"
    url = args.sdk_url.rstrip("/") + endpoint
    session = requests.Session() if args.session else requests
    if args.session:
        session.headers.update(AUTH_HEADERS)
    interval = 1.0 / args.rate
    deadline = time.monotonic()
    while True:
        started = time.monotonic()
        status, frame_timestamp = "exc", None
        try:
            response = session.get(
                url,
                timeout=args.timeout,
                headers=None if args.session else AUTH_HEADERS,
            )
            status = str(response.status_code)
            elapsed = time.monotonic() - started
            if response.status_code == 200:
                body = response.json()
                frame_timestamp = body.get("timestamp")
                if not body.get(frame_key):
                    stats.empty += 1
                    print(f"Image (SDK): {elapsed:.6f}  [empty body]", flush=True)
                else:
                    print(f"Image (SDK): {elapsed:.6f}", flush=True)
                stats.record(elapsed, frame_timestamp)
            else:
                # Error responses are still completed requests and must count
                # toward latency percentiles; excluding them can hide the exact
                # slow-failure spikes this probe is intended to detect.
                stats.record(elapsed)
                stats.record_error(status)
                detail = response.text[:120].replace("\n", " ")
                print(
                    f"Image (SDK): {elapsed:.6f}  [ERROR] No image data in response"
                    f" (HTTP {status}: {detail})",
                    flush=True,
                )
        except requests.RequestException as exc:
            elapsed = time.monotonic() - started
            stats.record(elapsed)
            stats.record_error(type(exc).__name__)
            print(f"Image (SDK): {elapsed:.6f}  [EXC] {exc}", flush=True)
        if writer:
            writer.writerow([time.time(), f"{elapsed:.6f}", status, frame_timestamp])

        deadline += interval
        sleep = deadline - time.monotonic()
        if sleep > 0:
            time.sleep(sleep)
        else:
            # Fell behind: drop missed ticks and wait a full interval. Starting
            # the next request immediately would be a one-request burst and can
            # return the same fresh cached frame as the slow request.
            deadline = time.monotonic() + interval
            time.sleep(interval)


def stream_feed(args, stats: Stats, writer):
    """Consume the MJPEG stream and measure inter-frame arrival gaps."""
    url = f"{args.sdk_url.rstrip('/')}/feed?view=front&fps={args.fps}"
    boundary = b"--frame"
    while True:
        try:
            with requests.get(
                url, stream=True, timeout=(5, args.timeout), headers=AUTH_HEADERS
            ) as response:
                if response.status_code != 200:
                    stats.record_error(str(response.status_code))
                    print(f"/feed HTTP {response.status_code}: {response.text[:120]}")
                    time.sleep(2)
                    continue
                print(f"Connected to {url}")
                previous = None
                buffer = b""
                for chunk in response.iter_content(chunk_size=16384):
                    buffer += chunk
                    while True:
                        start = buffer.find(boundary)
                        next_part = buffer.find(boundary, start + len(boundary))
                        if start < 0 or next_part < 0:
                            break
                        buffer = buffer[next_part:]
                        now = time.monotonic()
                        if previous is not None:
                            gap = now - previous
                            print(f"Frame gap: {gap:.6f}", flush=True)
                            stats.record(gap)
                            if writer:
                                writer.writerow(
                                    [time.time(), f"{gap:.6f}", "frame", ""]
                                )
                        previous = now
        except requests.RequestException as exc:
            stats.record_error(type(exc).__name__)
            print(f"/feed error, reconnecting: {exc}")
            time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sdk-url", default="http://localhost:8000", help="SDK base URL"
    )
    parser.add_argument(
        "--mode", choices=["v2_front", "v2_screenshot", "feed"], default="v2_front"
    )
    parser.add_argument("--rate", type=float, default=10.0, help="poll rate (Hz)")
    parser.add_argument("--fps", type=int, default=15, help="feed mode: requested fps")
    parser.add_argument(
        "--timeout", type=float, default=10.0, help="HTTP timeout (generous on"
        " purpose: we want to observe server stalls, not clip them)"
    )
    parser.add_argument(
        "--no-session", dest="session", action="store_false",
        help="new TCP connection per request instead of a reused Session",
    )
    parser.add_argument("--csv", help="dump per-sample rows to this CSV file")
    parser.add_argument(
        "--duration", type=float, default=0,
        help="stop after N seconds (default: run until Ctrl-C)",
    )
    args = parser.parse_args()

    stats = Stats(args.mode)
    handle, writer = (None, None)
    if args.csv:
        handle, writer = open_csv(args.csv)

    def finish(*_):
        print("\n" + stats.summary())
        if handle:
            handle.close()
            print(f"CSV written to {args.csv}")
        sys.exit(0)

    signal.signal(signal.SIGINT, finish)
    signal.signal(signal.SIGTERM, finish)
    if args.duration:
        signal.signal(signal.SIGALRM, finish)
        signal.setitimer(signal.ITIMER_REAL, args.duration)

    def periodic_summary():
        print(stats.summary(), flush=True)

    # Piggyback the rolling summary on SIGALRM only when no duration is set;
    # otherwise print it from the sampling loops via a simple time check.
    last_summary = time.monotonic()
    original_record = stats.record

    def record_with_summary(seconds, frame_timestamp=None):
        nonlocal last_summary
        original_record(seconds, frame_timestamp)
        if time.monotonic() - last_summary >= SUMMARY_INTERVAL_S:
            last_summary = time.monotonic()
            periodic_summary()

    stats.record = record_with_summary

    try:
        if args.mode == "feed":
            stream_feed(args, stats, writer)
        else:
            poll_v2(args, stats, writer)
    except KeyboardInterrupt:
        finish()


if __name__ == "__main__":
    main()

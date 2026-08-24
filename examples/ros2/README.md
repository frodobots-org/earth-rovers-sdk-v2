# ROS2 Bridge for the Earth Rovers SDK

A single-node bridge (`earth_rover_bridge.py`) that maps the SDK's API onto
standard ROS2 topics, so your stack talks plain ROS:

| ROS2 topic | Type | Direction | SDK source |
|---|---|---|---|
| `/cmd_vel` | `geometry_msgs/Twist` | subscribe | `POST /control` |
| `/earth_rover/front/image_raw` | `sensor_msgs/Image` | publish | `GET /feed` (MJPEG) |
| `/earth_rover/gps` | `sensor_msgs/NavSatFix` | publish | `WS /ws/data` |
| `/earth_rover/imu` | `sensor_msgs/Imu` | publish | `WS /ws/data` |
| `/earth_rover/battery` | `sensor_msgs/BatteryState` | publish | `WS /ws/data` |
| `/earth_rover/heading` | `std_msgs/Float32` (degrees) | publish | `WS /ws/data` |

## Behavior notes

- **`cmd_vel` is latest-wins at 10 Hz**: the bridge keeps only the newest Twist
  and posts it to `/control` at a fixed rate — bursts of commands never queue up.
- **Safety stop**: if no `cmd_vel` arrives for 0.5 s, stop commands are retried
  until the SDK acknowledges one. Shutdown also makes three best-effort stop
  attempts. Standard teleop/nav stacks that publish continuously work unchanged.
- **Twist mapping**: `linear.x` and `angular.z` are passed through clamped to
  the SDK's `-1..1` range. Treat them as normalized effort, not m/s / rad/s.
- Video, telemetry and control run concurrently. HTTP control has its own fixed-
  rate worker, so a slow request cannot block the ROS executor or `cmd_vel`.
- Camera and sensor topics use best-effort, depth-one QoS to prevent stale data
  from accumulating behind a slow subscriber.
- Everything reconnects automatically if the SDK restarts.

## Setup

Requires a ROS2 distro (Humble or newer recommended) with `rclpy` and
`cv_bridge`, plus:

```bash
pip install requests websocket-client opencv-python
```

### No ROS on your machine? (macOS / Windows)

Native ROS2 isn't practical outside Linux — use the provided Docker image
instead. The SDK server stays on the host; the container reaches it at
`host.docker.internal`:

```bash
docker build -t er-ros2 examples/ros2
docker run -it --rm -v "$PWD/examples/ros2:/ws" er-ros2 \
  python3 /ws/earth_rover_bridge.py --ros-args \
    -p sdk_url:=http://host.docker.internal:8000
```

(Don't use `--network host` on macOS/Windows — Docker runs in a VM there, so
host networking doesn't reach the host's `localhost`.)

## Run

1. Start the SDK (and the mission, if you use one):

```bash
hypercorn main:app
curl -X POST http://localhost:8000/start-mission -H "Authorization: Bearer $ROVER_API_KEY"   # if MISSION_SLUG is set
```

2. Run the bridge (every request needs the server's `ROVER_API_KEY`; the bridge reads it from the environment or the `rover_api_key` ROS param):

```bash
export ROVER_API_KEY="<the key printed by the server on startup, or set in its .env>"
python3 earth_rover_bridge.py
# SDK on another machine:
python3 earth_rover_bridge.py --ros-args -p sdk_url:=http://192.168.1.50:8000 -p rover_api_key:=$ROVER_API_KEY
```

3. Drive and watch:

```bash
ros2 run teleop_twist_keyboard teleop_twist_keyboard
ros2 run rqt_image_view rqt_image_view /earth_rover/front/image_raw
ros2 topic echo /earth_rover/gps
```

## Consuming the feed without ROS

`/feed` is a plain MJPEG stream — one line in OpenCV from any process:

```python
import cv2
# cv2.VideoCapture can't set custom headers, so pass the key as ?key= (the
# server accepts query authentication only on the read-only /feed endpoint).
cap = cv2.VideoCapture("http://localhost:8000/feed?view=front&fps=15&key=YOUR_ROVER_API_KEY")
while True:
    ok, frame = cap.read()
```

## Polling `/v2` from ROS — known-good pattern

Prefer `/feed` (above) for continuous video: frames are pushed as they're
captured, with no per-frame HTTP overhead. If your pipeline needs
request/response polling instead, `/v2/front` at a steady rate is supported —
the SDK keeps the capture loop warm between polls — as long as the client
follows three rules:

1. **Reuse one `requests.Session()`** (connection reuse; no TCP+TLS handshake
   per frame).
2. **Pace with a deadline, not `sleep(interval)`**, so a slow request doesn't
   shift the schedule.
3. **Treat 404/503 as "skip this tick"**: during a transient camera blip the
   SDK fails fast (within `V2_FRAME_TIMEOUT_S`, default 2 s) rather than
   stalling; the next polls succeed once capture recovers.

`er_poll_benchmark.py` in this directory implements exactly that and doubles
as a diagnostic: it logs each request's wall time (`Image (SDK): <seconds>`),
publishes the frames as `sensor_msgs/CompressedImage`, and prints rolling
p50/p90/p99/max latency summaries with error counts:

```bash
# poll /v2/front at 10 Hz (inside the Docker container: use host.docker.internal)
python3 er_poll_benchmark.py --ros-args \
  -p sdk_url:=http://localhost:8000 -p mode:=v2_front -p rate_hz:=10.0

# same measurement for the MJPEG feed (inter-frame arrival gaps)
python3 er_poll_benchmark.py --ros-args \
  -p sdk_url:=http://localhost:8000 -p mode:=feed -p feed_fps:=15

# dump per-request rows for before/after comparison
python3 er_poll_benchmark.py --ros-args \
  -p sdk_url:=http://localhost:8000 -p csv_path:=/ws/latency.csv
```

If you see latency spikes with this node, check `GET /status` → `video`:
`failures_total` climbing and `last_error` tell you the server-side capture
(not your client or ROS) is the bottleneck.

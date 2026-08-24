#!/usr/bin/env python3
"""ROS2 bridge for the Earth Rovers SDK.

Bridges the SDK's HTTP/WebSocket API into standard ROS2 topics:

  Subscribes
    /cmd_vel (geometry_msgs/Twist)   -> POST /control (latest-wins at 10 Hz,
                                        automatic stop when cmd_vel goes quiet)

  Publishes
    /earth_rover/front/image_raw (sensor_msgs/Image)  <- GET /feed (MJPEG)
    /earth_rover/gps (sensor_msgs/NavSatFix)          <- WS /ws/data
    /earth_rover/imu (sensor_msgs/Imu)                <- WS /ws/data
    /earth_rover/battery (sensor_msgs/BatteryState)   <- WS /ws/data
    /earth_rover/heading (std_msgs/Float32)           <- WS /ws/data

Usage:
    # SDK running on localhost:8000 (mission started if required)
    ros2 run <your_pkg> earth_rover_bridge.py
    # or directly:
    python3 earth_rover_bridge.py --ros-args -p sdk_url:=http://localhost:8000

Dependencies (besides a ROS2 distro with rclpy + cv_bridge):
    pip install requests websocket-client opencv-python
"""

import json
import math
import os
import threading
import time

import cv2
import rclpy
import requests
import websocket
from cv_bridge import CvBridge
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import BatteryState, Image, Imu, NavSatFix
from std_msgs.msg import Float32

CONTROL_RATE_HZ = 10.0
CMD_VEL_TIMEOUT_S = 0.5  # no cmd_vel for this long -> send stop
# /control dispatches without waiting for the rover's acknowledgement, so
# responses are fast; 1s leaves margin without stalling the 10 Hz loop.
CONTROL_HTTP_TIMEOUT_S = 1.0


class EarthRoverBridge(Node):
    def __init__(self):
        super().__init__("earth_rover_bridge")
        self.declare_parameter("sdk_url", "http://localhost:8000")
        self.declare_parameter("feed_fps", 15)
        self.sdk_url = self.get_parameter("sdk_url").value.rstrip("/")
        self.feed_fps = int(self.get_parameter("feed_fps").value)

        self.bridge = CvBridge()
        sensor_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
        )
        command_qos = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=1)
        self.image_pub = self.create_publisher(
            Image, "earth_rover/front/image_raw", sensor_qos
        )
        self.gps_pub = self.create_publisher(NavSatFix, "earth_rover/gps", sensor_qos)
        self.imu_pub = self.create_publisher(Imu, "earth_rover/imu", sensor_qos)
        self.battery_pub = self.create_publisher(
            BatteryState, "earth_rover/battery", sensor_qos
        )
        self.heading_pub = self.create_publisher(
            Float32, "earth_rover/heading", sensor_qos
        )

        # cmd_vel -> /control: keep only the latest command, send at a fixed
        # rate, and stop the rover if commands stop arriving.
        self._latest_cmd = None
        self._last_cmd_at = 0.0
        self._stopped = True
        self._cmd_lock = threading.Lock()
        self.create_subscription(Twist, "cmd_vel", self._on_cmd_vel, command_qos)

        self.declare_parameter("rover_api_key", os.environ.get("ROVER_API_KEY", ""))
        self.rover_api_key = self.get_parameter("rover_api_key").value

        self._session = requests.Session()
        if self.rover_api_key:
            self._session.headers.update(
                {"Authorization": f"Bearer {self.rover_api_key}"}
            )
        self._running = True
        self._stop_event = threading.Event()
        self._control_thread = threading.Thread(target=self._control_loop, daemon=True)
        self._control_thread.start()
        threading.Thread(target=self._feed_loop, daemon=True).start()
        threading.Thread(target=self._telemetry_loop, daemon=True).start()

        self.get_logger().info(f"Bridging Earth Rovers SDK at {self.sdk_url}")

    # ------------------------------------------------------------- control

    def _on_cmd_vel(self, msg: Twist):
        with self._cmd_lock:
            self._latest_cmd = {
                # SDK expects -1..1; Twist for this rover is already normalized.
                "linear": max(-1.0, min(1.0, msg.linear.x)),
                "angular": max(-1.0, min(1.0, msg.angular.z)),
            }
            self._last_cmd_at = time.monotonic()
            self._stopped = False

    def _control_tick(self):
        with self._cmd_lock:
            quiet = time.monotonic() - self._last_cmd_at > CMD_VEL_TIMEOUT_S
            if self._latest_cmd is None or (quiet and self._stopped):
                return
            command = {"linear": 0, "angular": 0} if quiet else dict(self._latest_cmd)
            last_cmd_at = self._last_cmd_at
        try:
            response = self._session.post(
                f"{self.sdk_url}/control",
                json={"command": command},
                timeout=CONTROL_HTTP_TIMEOUT_S,
            )
            response.raise_for_status()
            if quiet:
                # A successful HTTP response means the stop was dispatched.
                # The SDK watchdog independently tracks peer confirmation and
                # retries zero if this asynchronous delivery later fails.
                with self._cmd_lock:
                    if (
                        self._last_cmd_at == last_cmd_at
                        and time.monotonic() - self._last_cmd_at > CMD_VEL_TIMEOUT_S
                    ):
                        self._stopped = True
        except requests.RequestException as e:
            self.get_logger().warning(f"/control failed: {e}", throttle_duration_sec=5)

    def _control_loop(self):
        interval = 1.0 / CONTROL_RATE_HZ
        deadline = time.monotonic()
        while self._running and rclpy.ok():
            self._control_tick()
            deadline += interval
            wait = max(0.0, deadline - time.monotonic())
            if self._stop_event.wait(wait):
                break
            if time.monotonic() - deadline > interval:
                deadline = time.monotonic()

    # ---------------------------------------------------------------- feed

    def _feed_loop(self):
        url = f"{self.sdk_url}/feed?view=front&fps={self.feed_fps}"
        if self.rover_api_key:
            # cv2.VideoCapture can't set custom headers, so the key rides
            # along as a query param (the server accepts either for GET).
            url += f"&key={self.rover_api_key}"
        while self._running and rclpy.ok():
            capture = cv2.VideoCapture(url)
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not capture.isOpened():
                self.get_logger().warning(
                    "/feed not available, retrying in 3s", throttle_duration_sec=10
                )
                time.sleep(3)
                continue
            self.get_logger().info("Connected to /feed")
            while self._running and rclpy.ok():
                ok, frame = capture.read()
                if not ok:
                    break
                msg = self.bridge.cv2_to_imgmsg(frame, encoding="bgr8")
                msg.header.stamp = self.get_clock().now().to_msg()
                msg.header.frame_id = "earth_rover_front_camera"
                self.image_pub.publish(msg)
            capture.release()
            time.sleep(1)

    # ----------------------------------------------------------- telemetry

    def _telemetry_loop(self):
        ws_url = self.sdk_url.replace("http", "ws", 1) + "/ws/data"
        if self.rover_api_key:
            ws_url += f"?key={self.rover_api_key}"
        while self._running and rclpy.ok():
            ws = None
            try:
                ws = websocket.create_connection(ws_url, timeout=10)
                self.get_logger().info("Connected to /ws/data")
                while self._running and rclpy.ok():
                    msg = json.loads(ws.recv())
                    if msg.get("type") in ("snapshot", "telemetry") and msg.get("data"):
                        self._publish_telemetry(msg["data"])
            except Exception as e:
                self.get_logger().warning(
                    f"/ws/data reconnecting: {e}", throttle_duration_sec=10
                )
                time.sleep(2)
            finally:
                if ws is not None:
                    ws.close()

    def _publish_telemetry(self, data: dict):
        now = self.get_clock().now().to_msg()

        lat, lng = data.get("latitude"), data.get("longitude")
        if lat is not None and lng is not None:
            gps = NavSatFix()
            gps.header.stamp = now
            gps.header.frame_id = "earth_rover_gps"
            gps.latitude = float(lat)
            gps.longitude = float(lng)
            self.gps_pub.publish(gps)

        orientation = data.get("orientation")
        if orientation is not None:
            heading = Float32()
            heading.data = float(orientation)
            self.heading_pub.publish(heading)

        battery = data.get("battery")
        if battery is not None:
            batt = BatteryState()
            batt.header.stamp = now
            batt.percentage = float(battery) / 100.0
            batt.present = True
            self.battery_pub.publish(batt)

        # accels/gyros/mags are arrays of samples: [x, y, z, ..., unix_ts]
        accels, gyros = data.get("accels") or [], data.get("gyros") or []
        if accels or gyros:
            imu = Imu()
            imu.header.stamp = now
            imu.header.frame_id = "earth_rover_imu"
            if accels:
                sample = accels[-1]
                imu.linear_acceleration.x = float(sample[0])
                imu.linear_acceleration.y = float(sample[1])
                imu.linear_acceleration.z = float(sample[2])
            if gyros:
                sample = gyros[-1]
                imu.angular_velocity.x = math.radians(float(sample[0]))
                imu.angular_velocity.y = math.radians(float(sample[1]))
                imu.angular_velocity.z = math.radians(float(sample[2]))
            self.imu_pub.publish(imu)

    def destroy_node(self):
        self._running = False
        self._stop_event.set()
        self._control_thread.join(timeout=1.0)
        # Do not leave the last motion command active when the bridge exits.
        for _ in range(3):
            try:
                response = self._session.post(
                    f"{self.sdk_url}/control",
                    json={"command": {"linear": 0, "angular": 0}},
                    timeout=CONTROL_HTTP_TIMEOUT_S,
                )
                response.raise_for_status()
                break
            except requests.RequestException:
                continue
        self._session.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = EarthRoverBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        # ROS signal handling may already have shut the context down.
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()

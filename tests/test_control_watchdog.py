import asyncio
import os
import time
import unittest
from unittest.mock import AsyncMock, patch

import main
from fastapi import HTTPException


class ControlWatchdogTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main.auth_response_data = {"BOT_UID": "bot"}
        main.cancel_control_watchdog()

    async def asyncTearDown(self):
        main.cancel_control_watchdog()
        main.auth_response_data = {}

    async def test_motion_without_followup_delivers_confirmed_stop(self):
        send = AsyncMock()
        with (
            patch.object(main, "CONTROL_WATCHDOG_S", 0.05),
            patch.object(main.browser_service, "send_message", send),
            patch.object(main, "_rover_channel_is_live", AsyncMock(return_value=True)),
        ):
            main.arm_control_watchdog({"linear": 1, "angular": 0, "lamp": 1})
            await asyncio.sleep(0.2)

        send.assert_awaited_once_with({"linear": 0, "angular": 0, "lamp": 1})

    async def test_watchdog_arms_even_if_dispatch_would_fail(self):
        # Ambiguous delivery: /control arms BEFORE dispatching, so a motion
        # command whose send errors still gets a trailing safety stop.
        send = AsyncMock()
        with (
            patch.object(main, "CONTROL_WATCHDOG_S", 0.05),
            patch.object(main.browser_service, "send_message", send),
            patch.object(main, "_rover_channel_is_live", AsyncMock(return_value=True)),
        ):
            main.arm_control_watchdog({"linear": 0.7, "angular": 0})
            # (the dispatch itself failing changes nothing for the watchdog)
            await asyncio.sleep(0.2)

        send.assert_awaited_once()

    async def test_zero_command_still_ends_with_confirmed_stop(self):
        # A dispatched zero is not proof of receipt: the watchdog stays armed
        # and later delivers a stop confirmed by rover liveness.
        send = AsyncMock()
        with (
            patch.object(main, "CONTROL_WATCHDOG_S", 0.05),
            patch.object(main.browser_service, "send_message", send),
            patch.object(main, "_rover_channel_is_live", AsyncMock(return_value=True)),
        ):
            main.arm_control_watchdog({"linear": 1, "angular": 0})
            main.arm_control_watchdog({"linear": 0, "angular": 0})
            await asyncio.sleep(0.2)

        send.assert_awaited_once()

    async def test_zero_command_alone_does_not_arm(self):
        send = AsyncMock()
        with (
            patch.object(main, "CONTROL_WATCHDOG_S", 0.05),
            patch.object(main.browser_service, "send_message", send),
            patch.object(main, "_rover_channel_is_live", AsyncMock(return_value=True)),
        ):
            main.arm_control_watchdog({"linear": 0, "angular": 0, "lamp": 1})
            await asyncio.sleep(0.2)

        send.assert_not_awaited()

    async def test_recent_confirmed_delivery_extends_the_timer(self):
        send = AsyncMock()
        delivery = {"at": None}

        async def health():
            return {"last_delivered_at": delivery["at"]}

        with (
            patch.object(main, "CONTROL_WATCHDOG_S", 0.2),
            patch.object(main.browser_service, "rtm_health", side_effect=health),
            patch.object(main.browser_service, "send_message", send),
            patch.object(main, "_rover_channel_is_live", AsyncMock(return_value=True)),
        ):
            main.arm_control_watchdog({"linear": 1, "angular": 0})
            await asyncio.sleep(0.12)
            delivery["at"] = time.time()
            await asyncio.sleep(0.12)
            send.assert_not_awaited()
            await asyncio.sleep(0.2)

        send.assert_awaited_once()

    async def test_continuous_failed_traffic_does_not_refresh_deadline(self):
        send = AsyncMock()
        with (
            patch.object(main, "CONTROL_WATCHDOG_S", 0.05),
            patch.object(main.browser_service, "rtm_health", AsyncMock(return_value=None)),
            patch.object(main.browser_service, "send_message", send),
            patch.object(main, "_rover_channel_is_live", AsyncMock(return_value=True)),
        ):
            for _ in range(6):
                main.arm_control_watchdog({"linear": 1, "angular": 0})
                await asyncio.sleep(0.02)

        # At least one stop must break through before the failed request stream
        # ends. Further ambiguous motion attempts may correctly start another
        # safety cycle after the first stop has been confirmed.
        self.assertGreaterEqual(send.await_count, 1)

    async def test_legacy_motion_finishes_before_watchdog_stop(self):
        legacy_started = asyncio.Event()
        release_legacy = asyncio.Event()
        send = AsyncMock()

        async def slow_to_thread(*_args, **_kwargs):
            legacy_started.set()
            await release_legacy.wait()
            return {"ok": True}

        with (
            patch.object(main, "CONTROL_WATCHDOG_S", 0.05),
            patch.object(main.browser_service, "rtm_health", AsyncMock(return_value=None)),
            patch.object(main.browser_service, "send_message", send),
            patch.object(main, "_rover_channel_is_live", AsyncMock(return_value=True)),
            patch.object(main.asyncio, "to_thread", side_effect=slow_to_thread),
        ):
            command = {"linear": 1, "angular": 0}
            main.arm_control_watchdog(command)
            legacy = asyncio.create_task(main._dispatch_legacy_control(command))
            await legacy_started.wait()
            await asyncio.sleep(0.1)
            send.assert_not_awaited()
            late_motion = asyncio.create_task(
                main._dispatch_legacy_control(command)
            )
            await asyncio.sleep(0)  # let it queue behind the in-flight legacy send
            release_legacy.set()
            await legacy
            with self.assertRaisesRegex(RuntimeError, "safety stop"):
                await late_motion
            for _ in range(20):
                if send.await_count:
                    break
                await asyncio.sleep(0.01)

        send.assert_awaited_once()

    async def test_unconfirmed_stop_retries_and_rebuilds_session(self):
        # First liveness check raises, second returns False (channel not live),
        # third confirms. reset() must be called after WATCHDOG_RESET_EVERY
        # consecutive failures.
        send = AsyncMock()
        live = AsyncMock(side_effect=[RuntimeError("rtm down"), False, True])
        reset = AsyncMock()
        with (
            patch.object(main, "CONTROL_WATCHDOG_S", 0.05),
            patch.object(main, "WATCHDOG_RETRY_DELAY_S", 0.02),
            patch.object(main, "WATCHDOG_RESET_EVERY", 2),
            patch.object(main.browser_service, "send_message", send),
            patch.object(main, "_rover_channel_is_live", live),
            patch.object(main.browser_service, "reset", reset),
        ):
            main.arm_control_watchdog({"linear": 1, "angular": 0})
            for _ in range(100):
                await asyncio.sleep(0.02)
                if live.await_count >= 3:
                    break
            await asyncio.sleep(0.05)

        self.assertEqual(live.await_count, 3)
        reset.assert_awaited_once()

    async def test_watchdog_skips_when_session_cleared(self):
        send = AsyncMock()
        with (
            patch.object(main, "CONTROL_WATCHDOG_S", 0.05),
            patch.object(main.browser_service, "send_message", send),
            patch.object(main, "_rover_channel_is_live", AsyncMock(return_value=True)),
        ):
            main.arm_control_watchdog({"linear": 1, "angular": 0})
            main.auth_response_data = {}
            await asyncio.sleep(0.2)

        send.assert_not_awaited()


class RoverLivenessTest(unittest.IsolatedAsyncioTestCase):
    """_rover_channel_is_live() is the Fix 2 confirmation: RTM connected AND the
    rover actively streaming telemetry proves the command path is reachable."""

    async def asyncTearDown(self):
        main.telemetry_hub.last_update = None

    async def test_live_when_rtm_ready_and_telemetry_fresh(self):
        main.telemetry_hub.last_update = time.monotonic()
        with patch.object(
            main.browser_service, "rtm_health", AsyncMock(return_value={"ready": True})
        ):
            self.assertTrue(await main._rover_channel_is_live())

    async def test_not_live_when_rtm_not_ready(self):
        main.telemetry_hub.last_update = time.monotonic()
        with patch.object(
            main.browser_service, "rtm_health", AsyncMock(return_value={"ready": False})
        ):
            self.assertFalse(await main._rover_channel_is_live())

    async def test_not_live_when_rtm_health_missing(self):
        main.telemetry_hub.last_update = time.monotonic()
        with patch.object(
            main.browser_service, "rtm_health", AsyncMock(return_value=None)
        ):
            self.assertFalse(await main._rover_channel_is_live())

    async def test_not_live_when_telemetry_stale(self):
        with patch.object(main, "ROVER_LIVENESS_MAX_AGE_S", 0.05):
            main.telemetry_hub.last_update = time.monotonic() - 1.0
            with patch.object(
                main.browser_service,
                "rtm_health",
                AsyncMock(return_value={"ready": True}),
            ):
                self.assertFalse(await main._rover_channel_is_live())

    async def test_not_live_when_no_telemetry_yet(self):
        main.telemetry_hub.last_update = None
        with patch.object(
            main.browser_service, "rtm_health", AsyncMock(return_value={"ready": True})
        ):
            self.assertFalse(await main._rover_channel_is_live())


class CheckpointSafetyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main.cancel_control_watchdog()
        main.auth_response_data = {"BOT_UID": "bot"}
        main.checkpoints_list_data = {
            "checkpoints_list": [
                {"sequence": 1},
                {"sequence": 2},
                {"sequence": 3},
            ],
            "latest_scanned_checkpoint": 2,
        }

    async def asyncTearDown(self):
        main.cancel_control_watchdog()
        main.auth_response_data = {}
        main.checkpoints_list_data = {}

    async def test_final_checkpoint_confirms_stop_before_backend_teardown(self):
        order = []

        async def require_stop(*_args, **_kwargs):
            order.append("stop")

        async def report_checkpoint(*_args, **_kwargs):
            order.append("checkpoint")
            return 200, {"next_checkpoint_sequence": ""}

        env = {
            "SDK_API_TOKEN": "token",
            "BOT_SLUG": "bot",
            "MISSION_SLUG": "mission",
        }
        with (
            patch.dict(os.environ, env),
            patch.object(main, "latest_rover_data", AsyncMock(return_value={"latitude": 1, "longitude": 2})),
            patch.object(main, "_require_confirmed_stop", side_effect=require_stop),
            patch.object(main, "external_request", side_effect=report_checkpoint),
            patch.object(main.browser_service, "close", AsyncMock()),
            patch.object(main.feed_broadcasters["front"], "close", AsyncMock()),
            patch.object(main.feed_broadcasters["rear"], "close", AsyncMock()),
        ):
            response = await main.checkpoint_reached(None)

        self.assertEqual(order, ["stop", "checkpoint"])
        self.assertEqual(response.status_code, 200)

    async def test_final_checkpoint_is_not_reported_without_confirmed_stop(self):
        external = AsyncMock()
        env = {
            "SDK_API_TOKEN": "token",
            "BOT_SLUG": "bot",
            "MISSION_SLUG": "mission",
        }
        with (
            patch.dict(os.environ, env),
            patch.object(main, "latest_rover_data", AsyncMock(return_value={"latitude": 1, "longitude": 2})),
            patch.object(
                main,
                "_require_confirmed_stop",
                AsyncMock(side_effect=HTTPException(status_code=503, detail="no stop")),
            ),
            patch.object(main, "external_request", external),
        ):
            with self.assertRaises(HTTPException):
                await main.checkpoint_reached(None)

        external.assert_not_awaited()

    async def test_nonfinal_checkpoint_advances_cached_progress(self):
        main.checkpoints_list_data["latest_scanned_checkpoint"] = 1
        require_stop = AsyncMock()
        env = {
            "SDK_API_TOKEN": "token",
            "BOT_SLUG": "bot",
            "MISSION_SLUG": "mission",
        }
        with (
            patch.dict(os.environ, env),
            patch.object(
                main,
                "latest_rover_data",
                AsyncMock(return_value={"latitude": 1, "longitude": 2}),
            ),
            patch.object(main, "_require_confirmed_stop", require_stop),
            patch.object(
                main,
                "external_request",
                AsyncMock(return_value=(200, {"next_checkpoint_sequence": 3})),
            ),
        ):
            response = await main.checkpoint_reached(None)

        require_stop.assert_not_awaited()
        self.assertEqual(
            main.checkpoints_list_data["latest_scanned_checkpoint"], 2
        )
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()

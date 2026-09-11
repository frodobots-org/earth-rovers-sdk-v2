import asyncio
import json
import os
import time
import unittest
from unittest.mock import AsyncMock, patch

import main


def credentials(**overrides):
    return {"APP_ID": "venue", "CHANNEL_NAME": "arena", "RTC_TOKEN": "fresh",
            "USERID": 42, "CAMERAS": {"1": 1001},
            "EXPIRES_AT": int(time.time()) + 3600, **overrides}


class ArenaLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main.arena_auth_data = {}
        main.auth_response_data = {"BOT_UID": "bot"}

    async def asyncTearDown(self):
        main.arena_auth_data = {}
        main.auth_response_data = {}

    async def test_rover_page_never_waits_for_arena_backend(self):
        fetch = AsyncMock(side_effect=AssertionError("arena must not block page"))
        with patch.object(main, "retrieve_arena_tokens", fetch), patch.dict(
            os.environ, {"MISSION_SLUG": ""}
        ):
            response = await main.render_index_html(False)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"arenaCameras.js", response.body)
        fetch.assert_not_awaited()

    async def test_expired_cache_is_refreshed_before_reuse(self):
        main.arena_auth_data = credentials(RTC_TOKEN="expired", EXPIRES_AT=1)
        with patch.object(main, "retrieve_arena_tokens", AsyncMock(return_value=credentials())) as fetch:
            arena = await main.arena_auth()
        self.assertEqual(arena.rtc_token, "fresh")
        fetch.assert_awaited_once()

    async def test_failed_renewal_never_returns_old_token_as_success(self):
        main.arena_auth_data = credentials(RTC_TOKEN="old")
        with patch.object(main, "retrieve_arena_tokens", AsyncMock(return_value=None)):
            with self.assertRaises(main.HTTPException) as ctx:
                await main.get_arena_token()
        self.assertEqual(ctx.exception.status_code, 503)

    async def test_unknown_expiry_requires_refetch(self):
        main.arena_auth_data = credentials(EXPIRES_AT=None)
        with patch.object(main, "retrieve_arena_tokens", AsyncMock(return_value=credentials())) as fetch:
            await main.arena_auth()
        fetch.assert_awaited_once()

    async def test_malformed_credentials_are_unavailable(self):
        for value in ["bad", [1], credentials(CAMERAS=[1]), credentials(RTC_TOKEN=""), credentials(EXPIRES_AT=1)]:
            with self.subTest(value=value), patch.object(main, "retrieve_arena_tokens", AsyncMock(return_value=value)):
                self.assertFalse((await main.arena_auth()).configured)

    async def test_concurrent_refreshes_share_new_credentials(self):
        async def fetch():
            await asyncio.sleep(0.01)
            return credentials()
        with patch.object(main, "retrieve_arena_tokens", AsyncMock(side_effect=fetch)) as request:
            responses = await asyncio.gather(*(main.get_arena_token() for _ in range(5)))
        request.assert_awaited_once()
        for response in responses:
            self.assertEqual(json.loads(response.body)["RTC_TOKEN"], "fresh")
            self.assertEqual(response.headers["cache-control"], "no-store")

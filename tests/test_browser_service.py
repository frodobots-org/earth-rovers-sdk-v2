import unittest
from unittest.mock import patch

from playwright.async_api import Error as PlaywrightError

from browser_service import BrowserService


class FakePage:
    def __init__(self, closed=False):
        self.closed = closed
        self.close_calls = 0

    def is_closed(self):
        return self.closed

    async def close(self):
        self.close_calls += 1
        self.closed = True


class FakeBrowser:
    def __init__(self, connected=True):
        self.connected = connected

    def is_connected(self):
        return self.connected

    async def close(self):
        self.connected = False


class BrowserRecoveryTest(unittest.IsolatedAsyncioTestCase):
    def test_sdk_page_url_strips_legacy_query_key(self):
        service = BrowserService()
        with patch(
            "browser_service.SDK_PAGE_URL",
            "http://127.0.0.1:8000/sdk?mode=drive&key=do-not-log-this",
        ):
            self.assertEqual(
                service._sdk_page_url(),
                "http://127.0.0.1:8000/sdk?mode=drive",
            )

    async def test_stale_failure_cannot_destroy_new_generation(self):
        service = BrowserService()
        stale = FakePage(closed=True)
        current = FakePage()
        service._page = current
        service._browser = FakeBrowser()
        service._ready = True

        await service._invalidate(stale)

        self.assertIs(service._page, current)
        self.assertEqual(current.close_calls, 0)

    async def test_application_error_does_not_destroy_healthy_browser(self):
        service = BrowserService()
        page = FakePage()
        service._page = page
        service._browser = FakeBrowser()
        service._ready = True

        async def fail(_page):
            raise PlaywrightError("JavaScript application error")

        with self.assertRaises(PlaywrightError):
            await service._run(fail)

        self.assertIs(service._page, page)
        self.assertEqual(page.close_calls, 0)

    async def test_page_is_not_ready_until_launch_finishes(self):
        service = BrowserService()
        service._page = FakePage()
        service._browser = FakeBrowser()

        self.assertFalse(service.is_ready)

        service._ready = True
        self.assertTrue(service.is_ready)


class RearCameraCacheTest(unittest.IsolatedAsyncioTestCase):
    # /v2/screenshot consults has_rear_camera() on every poll; the answer
    # must come from cache, not a page.evaluate round trip per request.

    def _service(self, result):
        service = BrowserService()
        service._page = FakePage()
        service._browser = FakeBrowser()
        service._ready = True
        service.run_calls = 0

        async def run(fn, **kwargs):
            service.run_calls += 1
            return result

        service._run = run
        return service

    async def test_positive_result_is_cached_until_teardown(self):
        service = self._service(True)
        self.assertTrue(await service.has_rear_camera())
        self.assertTrue(await service.has_rear_camera())
        self.assertEqual(service.run_calls, 1)

        await service._teardown()  # relaunch must re-detect capability
        self.assertTrue(await service.has_rear_camera())
        self.assertEqual(service.run_calls, 2)

    async def test_negative_result_expires_quickly(self):
        import time

        service = self._service(False)
        self.assertFalse(await service.has_rear_camera())
        self.assertFalse(await service.has_rear_camera())
        self.assertEqual(service.run_calls, 1)

        # The rear track can subscribe late after the Agora join, so a
        # negative answer only holds for a short TTL.
        service._rear_camera = (False, time.monotonic() - 1)
        self.assertFalse(await service.has_rear_camera())
        self.assertEqual(service.run_calls, 2)


class LockLoopBindingTest(unittest.IsolatedAsyncioTestCase):
    async def test_lock_is_rebound_when_the_event_loop_changes(self):
        # The service is constructed at import time, before hypercorn's loop
        # exists. On Python 3.9 a Lock made then is bound to the wrong loop
        # and raises "attached to a different loop" under contention.
        import asyncio

        from browser_service import BrowserService

        service = BrowserService()
        service._lock = asyncio.Lock()
        service._lock_loop = object()  # simulates a dead import-time loop
        stale = service._lock

        lock = service._get_lock()
        self.assertIsNot(lock, stale)
        # Stable within the same running loop
        self.assertIs(service._get_lock(), lock)
        async with service._get_lock():
            pass  # must be acquirable on this loop


if __name__ == "__main__":
    unittest.main()

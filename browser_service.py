import asyncio
import logging
import os
import time
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

load_dotenv()

logger = logging.getLogger("browser_service")

# Configuration from environment variables with defaults
FORMAT = os.getenv("IMAGE_FORMAT", "jpeg")
QUALITY = float(os.getenv("IMAGE_QUALITY", "0.8"))
FEED_QUALITY = float(os.getenv("FEED_JPEG_QUALITY", "0.8"))

if FORMAT not in ["png", "jpeg", "webp"]:
    raise ValueError("Invalid image format. Supported formats: png, jpeg, webp")

if QUALITY < 0 or QUALITY > 1:
    raise ValueError("Invalid image quality. Quality should be between 0 and 1")

if FEED_QUALITY < 0 or FEED_QUALITY > 1:
    raise ValueError("Invalid feed quality. Quality should be between 0 and 1")

SDK_PAGE_URL = os.getenv("SDK_PAGE_URL", "http://127.0.0.1:8000/sdk")


class BrowserService:
    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._ready = False
        self._lock = None
        self._lock_loop = None
        self.last_error = None
        # has_rear_camera() cache: (value, monotonic expiry). A positive
        # result holds for the page's lifetime; a negative one only briefly,
        # since the rear track can subscribe late after the Agora join.
        self._rear_camera: Optional[tuple[bool, float]] = None
        # Large enough for the legacy front/rear/map element captures without
        # paying for an 8.3-megapixel headless render surface on every frame.
        self._viewport = {"width": 1920, "height": 1200}

    def _get_lock(self) -> asyncio.Lock:
        # On Python 3.9 an asyncio.Lock binds to the loop present when it is
        # constructed. This service is instantiated at import time — before
        # hypercorn creates its serving loop — so the lock must be created
        # (and recreated after a --reload loop swap) inside the running loop.
        running_loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not running_loop:
            self._lock = asyncio.Lock()
            self._lock_loop = running_loop
        return self._lock

    @property
    def is_ready(self) -> bool:
        return bool(
            self._ready
            and self._page
            and not self._page.is_closed()
            and self._browser
            and self._browser.is_connected()
        )

    async def ensure_page(self):
        # Lock-free fast path: concurrent /control, /feed and /v2 calls must
        # not serialize on the init lock once the page is up.
        if self.is_ready:
            return self._page
        async with self._get_lock():
            if self.is_ready:
                return self._page
            await self._teardown()
            await self._launch()
            return self._page

    def _sdk_page_url(self) -> str:
        """Drop legacy URL credentials before the browser navigates."""
        parts = urlsplit(SDK_PAGE_URL)
        query = urlencode(
            [
                (name, value)
                for name, value in parse_qsl(parts.query, keep_blank_values=True)
                if name != "key"
            ]
        )
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))

    async def _launch(self):
        self._ready = False
        try:
            if self._playwright is None:
                self._playwright = await async_playwright().start()

            launch_kwargs = {
                "headless": True,
                "args": [
                    "--ignore-certificate-errors",
                    "--no-sandbox",
                    "--autoplay-policy=no-user-gesture-required",
                    "--use-fake-ui-for-media-stream",
                    "--disable-application-cache",
                    "--disk-cache-size=0",
                ],
            }
            executable_path = os.getenv("CHROME_EXECUTABLE_PATH") or None
            if executable_path:
                self._browser = await self._playwright.chromium.launch(
                    executable_path=executable_path, **launch_kwargs
                )
                logger.info("Using browser from CHROME_EXECUTABLE_PATH")
            else:
                # Prefer installed Google Chrome: it ships the H.264/AAC
                # codecs some rover streams need; Playwright's open-source
                # Chromium does not and decodes those streams as 0x0.
                try:
                    self._browser = await self._playwright.chromium.launch(
                        channel="chrome", **launch_kwargs
                    )
                    logger.info("Using installed Google Chrome (all codecs)")
                except PlaywrightError:
                    if not os.path.exists(
                        self._playwright.chromium.executable_path
                    ):
                        raise RuntimeError(
                            "No usable browser found. Run: python -m"
                            " playwright install chromium (or install Google"
                            " Chrome, or set CHROME_EXECUTABLE_PATH)"
                        )
                    self._browser = await self._playwright.chromium.launch(
                        **launch_kwargs
                    )
                    logger.info(
                        "Using Playwright's bundled Chromium (no Google"
                        " Chrome found). If video frames stay empty, the"
                        " stream may need H.264: install Chrome or set"
                        " CHROME_EXECUTABLE_PATH to a codec-capable browser"
                    )
            # Import here to avoid the main -> BrowserService import cycle.
            # Scope the HttpOnly cookie to the SDK origin. A browser-wide
            # Authorization header would leak the key to third-party requests
            # made by the page (map tiles, RTC services, and similar).
            import main as _main

            self._context = await self._browser.new_context(
                viewport=self._viewport,
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            sdk_page_url = self._sdk_page_url()
            sdk_origin = urlsplit(sdk_page_url)
            if sdk_origin.scheme not in ("http", "https") or not sdk_origin.netloc:
                raise RuntimeError("SDK_PAGE_URL must be an absolute http(s) URL")
            await self._context.add_cookies(
                [
                    {
                        "name": "rover_key",
                        "value": _main.ROVER_API_KEY,
                        "url": f"{sdk_origin.scheme}://{sdk_origin.netloc}",
                        "httpOnly": True,
                        "secure": sdk_origin.scheme == "https",
                        "sameSite": "Strict",
                    }
                ]
            )
            self._page = await self._context.new_page()
            await self._page.goto(sdk_page_url, wait_until="domcontentloaded")
            await self._page.click("#join")
            # Control and telemetry must remain available when a camera is
            # offline. Wait for RTM readiness, not for a video DOM element.
            await self._page.wait_for_function(
                "() => typeof window.sendMessage === 'function'"
                " && window.rtmReady === true",
                timeout=15000,
            )

            call = f"""() => {{
                window.initializeImageParams({{
                    imageFormat: "{FORMAT}",
                    imageQuality: {QUALITY}
                }});
            }}"""
            await self._page.evaluate(call)
            self._ready = True
            self.last_error = None
            logger.info("Headless browser connected to %s", SDK_PAGE_URL)
        except Exception as e:
            self.last_error = str(e).split("\n", 1)[0]
            logger.error("Error initializing browser: %s", e)
            await self._teardown()
            # A failed Playwright transport cannot recover by reusing the same
            # driver instance. Recreate it on the next warm-up attempt.
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
            raise

    async def _teardown(self):
        self._ready = False
        self._rear_camera = None
        for target in (self._page, self._context, self._browser):
            if target:
                try:
                    await target.close()
                except Exception:
                    pass
        self._page = None
        self._context = None
        self._browser = None

    async def _invalidate(self, failed_page):
        """Tear down only if the failed page is still the active generation."""
        async with self._get_lock():
            if self._page is failed_page:
                await self._teardown()

    async def _run(self, fn, *, retry_on_disconnect: bool = True):
        page = await self.ensure_page()
        try:
            return await fn(page)
        except PlaywrightError as e:
            disconnected = page.is_closed() or not (
                self._browser and self._browser.is_connected()
            )
            if not disconnected:
                # JavaScript/application errors are not browser crashes. Do not
                # disrupt feed/control calls that are sharing a healthy page.
                raise
            logger.warning("Browser disconnected (%s); relaunching", e)
            await self._invalidate(page)
            if not retry_on_disconnect:
                raise
            page = await self.ensure_page()
            return await fn(page)

    async def warmup(self, max_attempts: int = 5):
        delay = 2
        for attempt in range(1, max_attempts + 1):
            try:
                await self.ensure_page()
                return True
            except Exception as e:
                logger.warning(
                    "Browser warm-up attempt %s/%s failed: %s",
                    attempt,
                    max_attempts,
                    e,
                )
                if attempt < max_attempts:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30)
        logger.warning(
            "Browser warm-up gave up after %s attempts;"
            " it will initialize lazily on the next request",
            max_attempts,
        )
        return False

    async def capture_screenshots(self, elements: list) -> dict[str, bytes]:
        element_map = {"front": "#player-1000", "rear": "#player-1001", "map": "#map"}

        async def capture(page):
            screenshots = {}
            for name in elements:
                if name not in element_map:
                    logger.warning("Invalid element name: %s", name)
                    continue
                locator = page.locator(element_map[name])
                if await locator.count() == 0:
                    logger.warning("Element %s not found", element_map[name])
                    continue
                start_time = time.time()
                image = await locator.screenshot(type="png", timeout=5000)
                elapsed_ms = (time.time() - start_time) * 1000
                logger.info("Screenshot for %s took %.2f ms", name, elapsed_ms)
                screenshots[name] = image
            return screenshots

        return await self._run(capture)

    async def take_screenshot(self, video_output_folder: str, elements: list):
        """Backward-compatible wrapper; new API consumers should use bytes."""
        screenshots = await self.capture_screenshots(elements)
        paths = {}
        for name, image in screenshots.items():
            output_path = os.path.join(video_output_folder, f"{name}.png")
            await asyncio.to_thread(self._write_file, output_path, image)
            paths[name] = output_path
        return paths

    @staticmethod
    def _write_file(path: str, content: bytes):
        with open(path, "wb") as output:
            output.write(content)

    async def data(self) -> dict:
        return await self._run(lambda page: page.evaluate("() => window.rtm_data"))

    async def has_rear_camera(self) -> bool:
        # Capability comes from reality, not BOT_TYPE: the rover either
        # publishes a rear video track (uid 1001) or it doesn't. Cached so
        # frame polling doesn't pay a page round trip on every request.
        cached = self._rear_camera
        if cached and (cached[0] or time.monotonic() < cached[1]):
            return cached[0]
        result = bool(
            await self._run(
                lambda page: page.evaluate(
                    "() => !!(typeof remoteUsers !== 'undefined'"
                    " && remoteUsers[1001] && remoteUsers[1001].videoTrack)"
                )
            )
        )
        self._rear_camera = (result, time.monotonic() + 5)
        return result

    async def front(self) -> str:
        return await self._run(
            lambda page: page.evaluate("() => getLastBase64Frame(1000) || null")
        )

    async def rear(self) -> str:
        return await self._run(
            lambda page: page.evaluate("() => getLastBase64Frame(1001) || null")
        )

    async def front_feed(self) -> dict:
        return await self._run(
            lambda page: page.evaluate(
                "([quality]) => getFramePacket(1000, 'jpeg', quality)",
                [FEED_QUALITY],
            )
        )

    async def rear_feed(self) -> dict:
        return await self._run(
            lambda page: page.evaluate(
                "([quality]) => getFramePacket(1001, 'jpeg', quality)",
                [FEED_QUALITY],
            )
        )

    async def configured_frame(self, view: str) -> dict:
        uid = 1000 if view == "front" else 1001
        return await self._run(
            lambda page: page.evaluate(
                "([uid, format, quality]) => getFramePacket(uid, format, quality)",
                [uid, FORMAT, QUALITY],
            )
        )

    async def send_message(self, message: dict):
        # Non-blocking dispatch: returns once the message is on the wire.
        # Delivery is observed asynchronously via rtm_health().
        return await self._run(
            lambda page: page.evaluate(
                "(message) => window.sendMessage(message)", message
            ),
            retry_on_disconnect=False,
        )

    async def send_message_confirmed(self, message: dict) -> bool:
        """Send and wait for the rover's receipt (hasPeerReceived)."""
        result = await self._run(
            lambda page: page.evaluate(
                "async (message) => await window.sendMessageAwait(message)",
                message,
            ),
            retry_on_disconnect=False,
        )
        return result is True

    async def rtm_health(self) -> Optional[dict]:
        if not self.is_ready:
            return None
        try:
            return await self._run(
                lambda page: page.evaluate(
                    "() => window.rtmHealth ? window.rtmHealth() : null"
                ),
                retry_on_disconnect=False,
            )
        except Exception:
            return None

    async def reset(self):
        """Force a relaunch on next use — rebuilds the page, the Agora
        connections, and the RTM session (for when RTM dies but the page
        itself is still healthy)."""
        async with self._get_lock():
            await self._teardown()

    async def speak(self, audio_url: str):
        return await self._run(
            lambda page: page.evaluate(
                "async (audioUrl) => await window.playAudioToRover(audioUrl)",
                audio_url,
            ),
            retry_on_disconnect=False,
        )

    async def close(self):
        async with self._get_lock():
            await self._teardown()
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None

    # Backward-compatible alias
    async def close_browser(self):
        await self.close()

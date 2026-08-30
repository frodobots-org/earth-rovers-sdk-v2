import base64
import contextlib
import html
import hmac
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
import asyncio

import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from typing import Literal, Optional

from browser_service import FEED_QUALITY, FORMAT, QUALITY, BrowserService
from rtm_client import RtmClient
from telemetry_hub import TelemetryHub
from tts_service import generate_speech
from video_feed import FrameBroadcaster, FrameCaptureError

load_dotenv()

# Configurar el logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("http_logger")


async def warmup_browser_when_ready():
    # Hold off while mission gating applies: launching the headless browser
    # renders /sdk, which requires auth, and auth must not run before the
    # user calls /start-mission.
    while os.getenv("MISSION_SLUG") and not auth_response_data:
        await asyncio.sleep(2)
    await browser_service.warmup()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Background task: the page loads /sdk from this same server, which only
    # accepts connections after startup yields — never await warm-up here.
    app.state.http_session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=15)
    )
    warmup_task = asyncio.create_task(warmup_browser_when_ready())
    yield
    warmup_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await warmup_task
    cancel_control_watchdog()
    await asyncio.gather(
        *(broadcaster.close() for broadcaster in feed_broadcasters.values())
    )
    await browser_service.close()
    await app.state.http_session.close()


app = FastAPI(lifespan=lifespan)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRODOBOTS_API_URL = os.getenv(
    "FRODOBOTS_API_URL", "https://frodobots-web-api.onrender.com/api/v1"
)

# How long /v2/* waits for a fresh frame before failing. Kept short on
# purpose: with the warm capture loop a healthy camera answers in tens of
# milliseconds, so this budget only matters as "wait for recovery" during a
# transient capture blip — better a fast 404/503 than a multi-second stall.
V2_FRAME_TIMEOUT_S = float(os.getenv("V2_FRAME_TIMEOUT_S", "2"))


# In-memory storage for the response
auth_response_data = {}
checkpoints_list_data = {}
auth_lock = None
auth_lock_loop = None
INGEST_TOKEN = secrets.token_urlsafe(32)

app.mount("/static", StaticFiles(directory="./static"), name="static")

browser_service = BrowserService()
telemetry_hub = TelemetryHub()

feed_broadcasters = {
    "front": FrameBroadcaster(browser_service.front_feed),
    "rear": FrameBroadcaster(browser_service.rear_feed),
}


async def external_request(method: str, url: str, **kwargs) -> tuple[int, dict]:
    """Use pooled async HTTP so rover hot paths never block the event loop."""

    async def perform(session: aiohttp.ClientSession):
        debug = os.getenv("DEBUG") == "true"
        if debug:
            logger.info("External %s %s", method.upper(), url)
        async with session.request(method, url, **kwargs) as response:
            try:
                body = await response.json(content_type=None)
            except (aiohttp.ContentTypeError, json.JSONDecodeError):
                body = {"error": await response.text()}

            if debug and response.status >= 400:
                logger.error(
                    "External %s %s failed: %s %s",
                    method.upper(),
                    url,
                    response.status,
                    body,
                )
            return response.status, body

    try:
        session = getattr(app.state, "http_session", None)
        if session and not session.closed:
            return await perform(session)
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        ) as temporary_session:
            return await perform(temporary_session)
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="External API timed out") from exc
    except aiohttp.ClientError as exc:
        raise HTTPException(status_code=502, detail="External API unavailable") from exc


async def get_camera_frame(
    view: str,
) -> tuple[Optional[str], Optional[float]]:
    """Return a shared fresh frame and its capture timestamp."""
    if FORMAT == "jpeg" and QUALITY == FEED_QUALITY:
        broadcaster = feed_broadcasters[view]
        try:
            frame = await broadcaster.get_frame(
                max_age=1 / 30, timeout=V2_FRAME_TIMEOUT_S, fps=30
            )
        except FrameCaptureError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if frame:
            return frame.base64_data, frame.captured_at
        if broadcaster.last_error:
            raise HTTPException(status_code=503, detail=broadcaster.last_error)
        return None, None

    # Preserve explicit png/webp v2 configurations. The default and fastest
    # path is JPEG and shares the feed broadcaster above.
    try:
        packet = await asyncio.wait_for(
            browser_service.configured_frame(view), timeout=V2_FRAME_TIMEOUT_S
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=503, detail=f"{view} camera capture timed out"
        ) from exc
    if packet and packet.get("error"):
        raise HTTPException(status_code=503, detail=packet["error"])
    if not packet or not packet.get("data_url"):
        return None, None
    return packet["data_url"].split(",", 1)[1], float(packet["timestamp"])


async def latest_rover_data() -> dict:
    age = telemetry_hub.age_seconds
    if telemetry_hub.latest is not None and age is not None and age < 5:
        return telemetry_hub.latest
    return await browser_service.data() or {}


@app.get("/feed")
async def feed(view: str = "front", fps: int = 15):
    await need_start_mission()
    if not auth_response_data:
        await auth()

    if view not in feed_broadcasters:
        raise HTTPException(
            status_code=400, detail=f"Invalid view: {view}. Use front or rear"
        )
    fps = max(1, min(fps, 30))
    if view == "rear" and not await browser_service.has_rear_camera():
        raise HTTPException(status_code=404, detail="Rear camera is not available")

    broadcaster = feed_broadcasters[view]
    queue = await broadcaster.subscribe(
        fps, cached_max_age=min(0.5, max(0.1, 2.0 / fps))
    )
    try:
        first_frame = await asyncio.wait_for(queue.get(), timeout=5)
    except asyncio.TimeoutError as exc:
        await broadcaster.unsubscribe(queue)
        detail = f"{view} camera is not ready"
        if broadcaster.last_error:
            detail += f": {broadcaster.last_error}"
        raise HTTPException(status_code=503, detail=detail) from exc
    except asyncio.CancelledError:
        await broadcaster.unsubscribe(queue)
        raise

    async def stream():
        min_interval = 1.0 / fps
        last_sent = 0.0
        try:
            frame = first_frame
            # A None frame is the broadcaster's end-of-stream sentinel
            # (mission ended / server shutting down): finish the response.
            while frame is not None:
                now = time.monotonic()
                if now - last_sent < min_interval * 0.9:
                    frame = await queue.get()
                    continue  # this client asked for fewer fps than we capture
                last_sent = now
                jpeg = frame.jpeg
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                    + jpeg
                    + b"\r\n"
                )
                frame = await queue.get()
        finally:
            await broadcaster.unsubscribe(queue)

    return StreamingResponse(
        stream(), media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.websocket("/ws/ingest")
async def ws_ingest(websocket: WebSocket):
    # Private channel for the headless /sdk page; local connections only.
    client_host = websocket.client.host if websocket.client else None
    supplied_token = websocket.query_params.get("token", "")
    if client_host not in ("127.0.0.1", "::1") or not hmac.compare_digest(
        supplied_token, INGEST_TOKEN
    ):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    connection = telemetry_hub.connect_ingest()
    try:
        while True:
            data = await websocket.receive_json()
            telemetry_hub.publish(data)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        telemetry_hub.disconnect_ingest(connection)


@app.websocket("/ws/data")
async def ws_data(websocket: WebSocket):
    await websocket.accept()
    queue = telemetry_hub.subscribe()
    try:
        await websocket.send_json(telemetry_hub.snapshot())
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=5)
            except asyncio.TimeoutError:
                message = {"type": "status", **telemetry_hub.status()}
            await websocket.send_json(message)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        telemetry_hub.unsubscribe(queue)


@app.get("/status")
async def get_status():
    video = {
        view: {
            "loop_running": broadcaster.loop_running,
            "latest_frame_age_s": broadcaster.latest_age_seconds,
            "captures_total": broadcaster.captures_total,
            "failures_total": broadcaster.failures_total,
            "last_error": broadcaster.last_error,
        }
        for view, broadcaster in feed_broadcasters.items()
    }
    return JSONResponse(
        content={
            "browser_ready": browser_service.is_ready,
            "browser_error": browser_service.last_error,
            "mission_started": bool(auth_response_data)
            or not os.getenv("MISSION_SLUG"),
            "rtm": await browser_service.rtm_health(),
            "video": video,
            **telemetry_hub.status(),
        }
    )


async def auth_common():
    global auth_lock, auth_lock_loop, auth_response_data
    if auth_response_data:
        return auth_response_data

    # Hypercorn's reloader can recreate the application event loop without
    # recreating every imported module object. asyncio locks are loop-bound on
    # Python 3.9, so never carry this coordinator across loop generations.
    running_loop = asyncio.get_running_loop()
    if auth_lock is None or auth_lock_loop is not running_loop:
        auth_lock = asyncio.Lock()
        auth_lock_loop = running_loop

    async with auth_lock:
        if auth_response_data:
            return auth_response_data
        env_tokens = get_env_tokens()
        if env_tokens:
            auth_response_data = env_tokens
            return auth_response_data

        auth_header = os.getenv("SDK_API_TOKEN")
        bot_slug = os.getenv("BOT_SLUG")
        mission_slug = os.getenv("MISSION_SLUG")

        if not auth_header:
            raise HTTPException(
                status_code=500, detail="Authorization header not configured"
            )
        if not bot_slug:
            raise HTTPException(status_code=500, detail="Bot name not configured")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {auth_header}",
        }
        if mission_slug:
            response_data = await start_ride(headers, bot_slug, mission_slug)
        else:
            response_data = await retrieve_tokens(headers, bot_slug)

        auth_response_data = {
            "CHANNEL_NAME": response_data.get("CHANNEL_NAME"),
            "RTC_TOKEN": response_data.get("RTC_TOKEN"),
            "RTM_TOKEN": response_data.get("RTM_TOKEN"),
            "USERID": response_data.get("USERID"),
            "APP_ID": response_data.get("APP_ID"),
            "BOT_UID": response_data.get("BOT_UID"),
            "SPECTATOR_USERID": response_data.get("SPECTATOR_USERID"),
            "SPECTATOR_RTC_TOKEN": response_data.get("SPECTATOR_RTC_TOKEN"),
            "BOT_TYPE": response_data.get("BOT_TYPE", "mini"),
        }
        return auth_response_data


def get_env_tokens():
    channel_name = os.getenv("CHANNEL_NAME")
    rtc_token = os.getenv("RTC_TOKEN")
    rtm_token = os.getenv("RTM_TOKEN")
    userid = os.getenv("USERID")
    app_id = os.getenv("APP_ID")
    bot_uid = os.getenv("BOT_UID")

    if all([channel_name, rtc_token, rtm_token, userid, app_id, bot_uid]):
        return {
            "CHANNEL_NAME": channel_name,
            "RTC_TOKEN": rtc_token,
            "RTM_TOKEN": rtm_token,
            "USERID": userid,
            "APP_ID": app_id,
            "BOT_UID": bot_uid,
            "SPECTATOR_USERID": os.getenv("SPECTATOR_USERID"),
            "SPECTATOR_RTC_TOKEN": os.getenv("SPECTATOR_RTC_TOKEN"),
            "BOT_TYPE": os.getenv("BOT_TYPE", "mini"),
        }
    return None


def _sdk_error_detail(status, fallback):
    """Map known backend failures to safe, user-facing messages. Keyed off the
    HTTP status (not the raw body), so backend internals are never exposed and
    the message stays stable if the backend rewords its errors.
      401 -> the SDK API key didn't resolve to a user
      403 -> the bot is in use / not permitted for SDK
      other -> the caller's generic fallback"""
    if status == 401:
        return "User not found"
    if status == 403:
        return "Bot unavailable for SDK"
    return fallback


async def start_ride(headers, bot_slug, mission_slug):
    start_ride_data = {"bot_slug": bot_slug, "mission_slug": mission_slug}
    status, response_data = await external_request(
        "POST",
        FRODOBOTS_API_URL + "/sdk/start_ride",
        headers=headers,
        json=start_ride_data,
    )
    if status != 200:
        raise HTTPException(
            status_code=status,
            detail=_sdk_error_detail(status, "Bot unavailable for SDK"),
        )
    return response_data


async def end_ride(headers, bot_slug, mission_slug):
    end_ride_data = {"bot_slug": bot_slug, "mission_slug": mission_slug}
    status, response_data = await external_request(
        "POST",
        FRODOBOTS_API_URL + "/sdk/end_ride",
        headers=headers,
        json=end_ride_data,
    )
    if status != 200:
        raise HTTPException(
            status_code=status,
            detail=_sdk_error_detail(status, "Failed to end mission"),
        )
    return response_data


async def retrieve_tokens(headers, bot_slug):
    data = {"bot_slug": bot_slug}
    status, response_data = await external_request(
        "POST", FRODOBOTS_API_URL + "/sdk/token", headers=headers, json=data
    )
    if status != 200:
        raise HTTPException(
            status_code=status,
            detail=_sdk_error_detail(status, "Bot unavailable for SDK"),
        )
    return response_data


async def need_start_mission():
    if not os.getenv("MISSION_SLUG"):
        return
    if auth_response_data:
        return
    raise HTTPException(
        status_code=400, detail="Call /start-mission endpoint to start a mission"
    )


@app.post("/checkpoints-list")
@app.get("/checkpoints-list")
async def checkpoints():
    await need_start_mission()
    await get_checkpoints_list()
    return JSONResponse(content=checkpoints_list_data)


async def get_checkpoints_list():
    global checkpoints_list_data
    auth_header = os.getenv("SDK_API_TOKEN")
    bot_slug = os.getenv("BOT_SLUG")
    mission_slug = os.getenv("MISSION_SLUG")

    if not mission_slug:
        return

    if not auth_header:
        raise HTTPException(
            status_code=500, detail="Authorization header not configured"
        )
    if not bot_slug:
        raise HTTPException(status_code=500, detail="Bot name not configured")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_header}",
    }

    data = {"bot_slug": bot_slug, "mission_slug": mission_slug}

    status, response_data = await external_request(
        "POST",
        FRODOBOTS_API_URL + "/sdk/checkpoints_list",
        headers=headers,
        json=data,
    )
    if status != 200:
        raise HTTPException(
            status_code=status,
            detail="Failed to retrieve checkpoints list",
        )
    checkpoints_list_data = response_data
    return checkpoints_list_data


async def auth():
    await auth_common()
    if not checkpoints_list_data:
        await get_checkpoints_list()
    return JSONResponse(
        content={
            "auth_response_data": auth_response_data,
            "checkpoints_list_data": checkpoints_list_data,
        }
    )


@app.post("/start-mission")
async def start_mission():
    required_env_vars = ["SDK_API_TOKEN", "BOT_SLUG", "MISSION_SLUG"]
    missing_vars = [var for var in required_env_vars if not os.getenv(var)]

    if missing_vars:
        raise HTTPException(
            status_code=400,
            detail=f"Missing required environment variables: {', '.join(missing_vars)}",
        )

    if not auth_response_data:
        await auth()
    if not checkpoints_list_data:
        await get_checkpoints_list()
    return JSONResponse(
        status_code=200,
        content={
            "message": "Mission started successfully",
            "checkpoints_list": checkpoints_list_data,
        },
    )


@app.post("/end-mission")
async def end_mission():
    required_env_vars = ["SDK_API_TOKEN", "BOT_SLUG", "MISSION_SLUG"]
    missing_vars = [var for var in required_env_vars if not os.getenv(var)]

    if missing_vars:
        raise HTTPException(
            status_code=400,
            detail=f"Missing required environment variables: {', '.join(missing_vars)}",
        )

    auth_header = os.getenv("SDK_API_TOKEN")
    bot_slug = os.getenv("BOT_SLUG")
    mission_slug = os.getenv("MISSION_SLUG")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_header}",
    }

    # Ending the remote ride destroys the command path. Do not proceed until
    # the rover has positively received zero motion.
    await _require_confirmed_stop("end the mission")

    try:
        await end_ride(headers, bot_slug, mission_slug)
        cancel_control_watchdog()
        # Clear the stored auth and checkpoints data
        global auth_response_data, checkpoints_list_data
        auth_response_data = {}
        checkpoints_list_data = {}
        await asyncio.gather(
            *(broadcaster.close() for broadcaster in feed_broadcasters.values())
        )
        await browser_service.close()
        return JSONResponse(content={"message": "Mission ended successfully"})
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to end mission: {str(e)}")


def render_template(filename: str, template_vars: dict) -> HTMLResponse:
    with open(filename, "r", encoding="utf-8") as file:
        html_content = file.read()

    for key, value in template_vars.items():
        html_content = html_content.replace(f"{{{{ {key} }}}}", str(value))

    return HTMLResponse(content=html_content, status_code=200)


async def render_index_html(is_spectator: bool):
    await need_start_mission()
    if not auth_response_data:
        await auth()

    token_type: Literal["SPECTATOR_", ""] = "SPECTATOR_" if is_spectator else ""

    template_vars = {
        "appid": html.escape(str(auth_response_data.get("APP_ID", "")), quote=True),
        "rtc_token": html.escape(
            str(auth_response_data.get(f"{token_type}RTC_TOKEN", "")), quote=True
        ),
        "rtm_token": html.escape(
            str("" if is_spectator else auth_response_data.get("RTM_TOKEN", "")),
            quote=True,
        ),
        "channel": html.escape(
            str(auth_response_data.get("CHANNEL_NAME", "")), quote=True
        ),
        "uid": html.escape(
            str(auth_response_data.get(f"{token_type}USERID", "")), quote=True
        ),
        "bot_uid": html.escape(str(auth_response_data.get("BOT_UID", "")), quote=True),
        "ingest_token": html.escape(INGEST_TOKEN, quote=True),
        "checkpoints_list": json.dumps(
            checkpoints_list_data.get("checkpoints_list", [])
        ).replace("</", "<\\/"),
        "map_zoom_level": int(os.getenv("MAP_ZOOM_LEVEL", "18")),
    }

    return render_template("index.html", template_vars)


@app.get("/")
async def get_index(request: Request):
    # The dashboard renders even when the mission hasn't started or auth
    # fails — it degrades to "waiting" states instead of a raw JSON error.
    boot_notice = ""
    if not auth_response_data:
        try:
            await need_start_mission()
            await auth()
        except HTTPException as e:
            boot_notice = e.detail if isinstance(e.detail, str) else "SDK not ready"
        except Exception:
            boot_notice = "SDK auth failed - check the credentials in .env"

    tokens = auth_response_data or {}
    dashboard_config = {
        "appid": tokens.get("APP_ID") or "",
        "rtcToken": tokens.get("SPECTATOR_RTC_TOKEN") or "",
        "channel": tokens.get("CHANNEL_NAME") or "",
        "uid": tokens.get("SPECTATOR_USERID") or "",
        "botUid": tokens.get("BOT_UID") or "",
        "checkpointsList": checkpoints_list_data.get("checkpoints_list", []),
        "mapZoomLevel": int(os.getenv("MAP_ZOOM_LEVEL", "18")),
        "botSlug": os.getenv("BOT_SLUG", ""),
        "missionSlug": os.getenv("MISSION_SLUG", ""),
        "missionStarted": bool(auth_response_data) or not os.getenv("MISSION_SLUG"),
        "bootNotice": str(boot_notice).replace("\n", " "),
    }
    template_vars = {
        "dashboard_config": json.dumps(dashboard_config).replace("</", "<\\/")
    }
    return render_template("dashboard.html", template_vars)


@app.get("/sdk")
async def sdk(request: Request):
    return await render_index_html(is_spectator=False)


@app.post("/control-legacy")
async def control_legacy(request: Request):
    await need_start_mission()
    if not auth_response_data:
        await auth()

    body = await request.json()
    command = body.get("command")
    if not command:
        raise HTTPException(status_code=400, detail="Command not provided")

    arm_control_watchdog(command)
    await _dispatch_legacy_control(command)

    return {"message": "Command sent successfully"}


# Dead-man watchdog: the rover keeps executing its last command until a new
# one arrives, so a broken command path after a motion command means a
# runaway bot. The watchdog arms when a motion command is ACCEPTED (before
# dispatch, covering ambiguous delivery) and, once confirmed deliveries are
# stale for CONTROL_WATCHDOG_S, delivers a CONFIRMED stop (peer receipt) —
# retrying and rebuilding the RTM session until the rover confirms it. Failed
# traffic cannot refresh this deadline. CONTROL_WATCHDOG_S=0 disables it.
CONTROL_WATCHDOG_S = float(os.getenv("CONTROL_WATCHDOG_S", "3"))
WATCHDOG_RETRY_DELAY_S = 1.0
WATCHDOG_RESET_EVERY = 3  # rebuild the browser/RTM session every N failures
SAFETY_STOP_CONFIRM_TIMEOUT_S = float(
    os.getenv("SAFETY_STOP_CONFIRM_TIMEOUT_S", "12")
)

_control_watchdog_task: Optional[asyncio.Task] = None
_confirmed_stop_task: Optional[asyncio.Task] = None
_confirmed_stop_generation = 0
_control_dispatch_lock: Optional[asyncio.Lock] = None
_control_dispatch_lock_loop = None


def _command_is_moving(command) -> bool:
    try:
        return bool(
            float(command.get("linear") or 0) or float(command.get("angular") or 0)
        )
    except (TypeError, ValueError, AttributeError):
        return True  # unparseable command: assume motion, err on the safe side


def cancel_control_watchdog():
    global _control_watchdog_task, _confirmed_stop_task
    if _control_watchdog_task and not _control_watchdog_task.done():
        _control_watchdog_task.cancel()
    if _confirmed_stop_task and not _confirmed_stop_task.done():
        _confirmed_stop_task.cancel()
    _control_watchdog_task = None
    _confirmed_stop_task = None


def _get_control_dispatch_lock() -> asyncio.Lock:
    """Return a lock bound to the current application event loop."""
    global _control_dispatch_lock, _control_dispatch_lock_loop
    running_loop = asyncio.get_running_loop()
    if (
        _control_dispatch_lock is None
        or _control_dispatch_lock_loop is not running_loop
    ):
        _control_dispatch_lock = asyncio.Lock()
        _control_dispatch_lock_loop = running_loop
    return _control_dispatch_lock


def _confirmed_stop_pending() -> bool:
    return bool(_confirmed_stop_task and not _confirmed_stop_task.done())


async def _dispatch_browser_control(command):
    """Order local dispatches and reject motion while a safety stop is pending."""
    stop_generation = _confirmed_stop_generation
    stop_pending_at_start = _confirmed_stop_pending()
    async with _get_control_dispatch_lock():
        stop_overtook_dispatch = stop_generation != _confirmed_stop_generation
        if _command_is_moving(command) and (
            stop_pending_at_start
            or _confirmed_stop_pending()
            or stop_overtook_dispatch
        ):
            raise RuntimeError("Motion rejected because a safety stop took priority")
        return await browser_service.send_message(command)


async def _dispatch_legacy_control(command):
    """Keep a slow legacy REST motion ahead of its trailing safety stop."""
    stop_generation = _confirmed_stop_generation
    stop_pending_at_start = _confirmed_stop_pending()
    async with _get_control_dispatch_lock():
        stop_overtook_dispatch = stop_generation != _confirmed_stop_generation
        if _command_is_moving(command) and (
            stop_pending_at_start
            or _confirmed_stop_pending()
            or stop_overtook_dispatch
        ):
            raise RuntimeError("Motion rejected because a safety stop took priority")
        return await asyncio.to_thread(
            RtmClient(auth_response_data).send_message, command
        )


def arm_control_watchdog(command):
    """Start monitoring a drive without letting failed traffic reset its timer.

    The monitor follows Agora's confirmed-delivery timestamp. Healthy streams
    therefore keep it alive, while synchronously or asynchronously failed
    requests cannot postpone the safety deadline.
    """
    if CONTROL_WATCHDOG_S <= 0 or not _command_is_moving(command):
        return
    global _control_watchdog_task
    if _control_watchdog_task and not _control_watchdog_task.done():
        return
    lamp = command.get("lamp") or 0 if isinstance(command, dict) else 0
    _control_watchdog_task = asyncio.create_task(
        _control_watchdog(lamp, time.time())
    )


async def _recent_delivery_delay(armed_at: float) -> Optional[float]:
    """Return how long a recently confirmed control delivery remains fresh."""
    health = await browser_service.rtm_health()
    if not health:
        return None
    try:
        delivered_at = float(health.get("last_delivered_at"))
    except (TypeError, ValueError):
        return None
    if delivered_at < armed_at:
        return None
    age = max(0.0, time.time() - delivered_at)
    remaining = CONTROL_WATCHDOG_S - age
    return remaining if remaining > 0 else None


def _ensure_confirmed_stop(lamp=0) -> asyncio.Task:
    """Return the one shared stop-delivery task for this safety event."""
    global _confirmed_stop_task, _confirmed_stop_generation
    if not _confirmed_stop_task or _confirmed_stop_task.done():
        _confirmed_stop_generation += 1
        _confirmed_stop_task = asyncio.create_task(_deliver_confirmed_stop(lamp))
    return _confirmed_stop_task


async def _deliver_confirmed_stop(lamp) -> bool:
    stop_command = {"linear": 0, "angular": 0, "lamp": lamp}
    attempt = 0
    delay = WATCHDOG_RETRY_DELAY_S
    while auth_response_data:
        attempt += 1
        try:
            async with _get_control_dispatch_lock():
                if await browser_service.send_message_confirmed(stop_command):
                    logger.warning(
                        "Safety stop confirmed by rover (attempt %s)", attempt
                    )
                    return True
                raise RuntimeError("rover did not confirm the stop")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                "Safety stop attempt %s failed: %s",
                attempt,
                str(e).split("\n", 1)[0],
            )
            if attempt % WATCHDOG_RESET_EVERY == 0:
                logger.warning("Rebuilding the browser/RTM session to recover")
                async with _get_control_dispatch_lock():
                    with contextlib.suppress(Exception):
                        await browser_service.reset()
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 5.0)
    logger.info("Safety stop abandoned because the mission session was cleared")
    return False


async def _require_confirmed_stop(reason: str, lamp=0):
    """Block destructive lifecycle transitions until the rover confirms zero."""
    task = _ensure_confirmed_stop(lamp)
    try:
        confirmed = await asyncio.wait_for(
            asyncio.shield(task), timeout=SAFETY_STOP_CONFIRM_TIMEOUT_S
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot {reason}: rover has not confirmed the safety stop",
        ) from exc
    if not confirmed:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot {reason}: mission ended before the safety stop was confirmed",
        )


async def _control_watchdog(lamp, armed_at: float):
    delay = CONTROL_WATCHDOG_S
    while True:
        await asyncio.sleep(delay)
        delay = await _recent_delivery_delay(armed_at)
        if delay is None:
            break
    logger.warning(
        "Dead-man watchdog: no confirmed control delivery for %.1fs -"
        " delivering safety stop",
        CONTROL_WATCHDOG_S,
    )
    if not auth_response_data:
        logger.info("Watchdog safety stop skipped: mission session cleared")
        return
    await asyncio.shield(_ensure_confirmed_stop(lamp))


@app.post("/control")
async def control(request: Request):
    await need_start_mission()
    if not auth_response_data:
        await auth()

    body = await request.json()
    command = body.get("command")
    if not command:
        raise HTTPException(status_code=400, detail="Command not provided")

    # Arm BEFORE dispatch: if the send times out ambiguously the rover may
    # still have received the motion command — the watchdog must cover it.
    arm_control_watchdog(command)
    try:
        await _dispatch_browser_control(command)
        return {"message": "Command sent successfully"}
    except Exception as e:
        logger.error("Error sending control command: %s", str(e))
        reason = browser_service.last_error or str(e).split("\n", 1)[0]
        detail = "Failed to send control command"
        if reason:
            detail += f": {reason}"
        raise HTTPException(status_code=500, detail=detail) from e


@app.post("/speak")
async def speak(request: Request):
    await need_start_mission()
    if not auth_response_data:
        await auth()

    body = await request.json()
    text = body.get("text")
    if not text:
        raise HTTPException(status_code=400, detail="Text not provided")

    audio_path = None
    try:
        output_path = os.path.join(
            "static", f"tts_output_{secrets.token_urlsafe(12)}"
        )
        audio_path = await generate_speech(text, output_path)
        audio_filename = os.path.basename(audio_path)
        audio_url = f"http://127.0.0.1:8000/static/{audio_filename}"
        await browser_service.speak(audio_url)
        return {"message": "Speech sent to rover"}
    except Exception as e:
        logger.error("Error in /speak: %s", str(e))
        raise HTTPException(status_code=500, detail=f"TTS failed: {str(e)}") from e
    finally:
        if audio_path:
            with contextlib.suppress(FileNotFoundError):
                os.remove(audio_path)


@app.get("/screenshot")
async def get_screenshot(view_types: str = "rear,map,front"):
    await need_start_mission()
    if not auth_response_data:
        await auth()

    valid_views = {"rear", "map", "front"}
    views_list = view_types.split(",")

    for view in views_list:
        if view not in valid_views:
            raise HTTPException(status_code=400, detail=f"Invalid view type: {view}")

    screenshots = await browser_service.capture_screenshots(views_list)
    missing = [view for view in views_list if view not in screenshots]
    if missing:
        raise HTTPException(
            status_code=404, detail=f"Views not available: {', '.join(missing)}"
        )

    # Documented behavior since v3: the images are also saved to screenshots/.
    os.makedirs("screenshots", exist_ok=True)
    for view, image in screenshots.items():
        await asyncio.to_thread(
            browser_service._write_file,
            os.path.join("screenshots", f"{view}.png"),
            image,
        )

    response_content = {
        f"{view}_frame": base64.b64encode(image).decode("ascii")
        for view, image in screenshots.items()
    }

    response_content["timestamp"] = time.time()

    return JSONResponse(content=response_content)


@app.get("/data")
async def get_data():
    await need_start_mission()
    # Fast path: fresh telemetry pushed by the /sdk page, no page.evaluate.
    age = telemetry_hub.age_seconds
    if telemetry_hub.latest is not None and age is not None and age < 2:
        return JSONResponse(content=telemetry_hub.latest)
    data = await latest_rover_data()
    return JSONResponse(content=data)


def _pending_checkpoint_sequences() -> list[int]:
    sequences = sorted(
        cp.get("sequence")
        for cp in checkpoints_list_data.get("checkpoints_list", [])
        if isinstance(cp.get("sequence"), int)
    )
    try:
        latest = int(checkpoints_list_data.get("latest_scanned_checkpoint", 0))
    except (TypeError, ValueError):
        latest = 0
    return [sequence for sequence in sequences if sequence > latest]


@app.post("/checkpoint-reached")
async def checkpoint_reached(request: Request):
    global auth_response_data, checkpoints_list_data
    await need_start_mission()

    bot_slug = os.getenv("BOT_SLUG")
    mission_slug = os.getenv("MISSION_SLUG")
    auth_header = os.getenv("SDK_API_TOKEN")

    if not all([bot_slug, mission_slug, auth_header]):
        raise HTTPException(
            status_code=500, detail="Required environment variables not configured"
        )

    data = await latest_rover_data()
    latitude = data.get("latitude")
    longitude = data.get("longitude")

    if latitude is None or longitude is None:
        raise HTTPException(status_code=400, detail="Missing latitude or longitude")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_header}",
    }

    payload = {
        "bot_slug": bot_slug,
        "mission_slug": mission_slug,
        "latitude": latitude,
        "longitude": longitude,
    }

    # The backend ends the ride as part of accepting the final checkpoint.
    # Predict that transition from the cached mission progress and require a
    # confirmed zero while RTM is still available. With missing progress data,
    # be conservative because we cannot prove this is a non-final checkpoint.
    pending_sequences = _pending_checkpoint_sequences()
    checkpoint_may_complete = len(pending_sequences) <= 1
    if checkpoint_may_complete:
        await _require_confirmed_stop("complete the final checkpoint")

    status, response_data = await external_request(
        "POST",
        FRODOBOTS_API_URL + "/sdk/checkpoint_reached",
        headers=headers,
        json=payload,
    )
    if status != 200:
        raise HTTPException(
            status_code=status,
            detail={
                "error": response_data.get("error", "Failed to send checkpoint data"),
                "proximate_distance_to_checkpoint": response_data.get(
                    "distance_to_checkpoint", "Unknown"
                ),
            },
        )
    next_sequence = response_data.get("next_checkpoint_sequence", "")
    sequences = [
        cp.get("sequence")
        for cp in checkpoints_list_data.get("checkpoints_list", [])
        if isinstance(cp.get("sequence"), int)
    ]
    try:
        past_last = bool(sequences) and int(next_sequence) > max(sequences)
    except (TypeError, ValueError):
        past_last = False
    mission_completed = bool(sequences) and (not next_sequence or past_last)

    if pending_sequences:
        # Keep the cached progress current so the next request can identify the
        # final checkpoint before the backend tears down its RTM session.
        checkpoints_list_data["latest_scanned_checkpoint"] = pending_sequences[0]

    if mission_completed:
        # The backend ends the ride after the last checkpoint, which kills
        # the feed and makes the bot unreachable. Drop the local session so
        # /status reports it and /start-mission re-authenticates cleanly.
        # _require_confirmed_stop ran before the backend call for the final
        # checkpoint. It is now safe to tear down all local safety tasks.
        cancel_control_watchdog()
        auth_response_data = {}
        checkpoints_list_data = {}
        await asyncio.gather(
            *(broadcaster.close() for broadcaster in feed_broadcasters.values())
        )
        await browser_service.close()

    return JSONResponse(
        status_code=200,
        content={
            "message": "Checkpoint reached successfully",
            "next_checkpoint_sequence": next_sequence,
            "mission_completed": mission_completed,
        },
    )


@app.get("/missions-history")
async def missions_history():
    auth_header = os.getenv("SDK_API_TOKEN")
    bot_slug = os.getenv("BOT_SLUG")

    if not auth_header:
        raise HTTPException(status_code=500, detail="Authorization not configured")
    if not bot_slug:
        raise HTTPException(status_code=500, detail="Bot name not configured")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_header}",
    }

    data = {"bot_slug": bot_slug}

    status, response_data = await external_request(
        "POST", FRODOBOTS_API_URL + "/sdk/rides_history", headers=headers, json=data
    )
    if status != 200:
        raise HTTPException(
            status_code=status,
            detail="Failed to retrieve missions history",
        )
    return JSONResponse(content=response_data)


@app.get("/missions")
async def missions():
    auth_header = os.getenv("SDK_API_TOKEN")
    bot_slug = os.getenv("BOT_SLUG")

    if not auth_header:
        raise HTTPException(
            status_code=500, detail="Authorization header not configured"
        )
    if not bot_slug:
        raise HTTPException(status_code=500, detail="Bot name not configured")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_header}",
    }

    payload = {"bot_slug": bot_slug}

    status, response_data = await external_request(
        "GET", FRODOBOTS_API_URL + "/sdk/missions", headers=headers, params=payload
    )
    if status != 200:
        raise HTTPException(
            status_code=status,
            detail="Failed to retrieve missions",
        )
    missions_list = [
        {
            "slug": mission.get("slug"),
            "distance_in_m": mission.get("distance_in_m"),
            "checkpoints_count": mission.get("checkpoints_count"),
        }
        for mission in response_data.get("missions", [])
    ]
    return JSONResponse(content={"missions": missions_list})


@app.get("/v2/screenshot")
async def get_screenshot_v2():
    await need_start_mission()
    if not auth_response_data:
        await auth()

    async def get_frame(frame_type):
        frame, captured_at = await get_camera_frame(frame_type)
        if frame is None:
            return {}
        return {
            f"{frame_type}_frame": frame,
            f"{frame_type}_timestamp": captured_at,
        }

    front_task = asyncio.create_task(get_frame("front"))
    tasks = [front_task]

    if await browser_service.has_rear_camera():
        rear_task = asyncio.create_task(get_frame("rear"))
        tasks.append(rear_task)

    results = await asyncio.gather(*tasks)

    response_data = {}
    for result in results:
        response_data.update(result)

    if not response_data:
        raise HTTPException(status_code=404, detail="Frames not available")

    timestamps = [
        value for key, value in response_data.items() if key.endswith("_timestamp")
    ]
    response_data["timestamp"] = max(timestamps)

    return JSONResponse(content=response_data)


@app.get("/v2/front")
async def get_front_frame():
    await need_start_mission()
    if not auth_response_data:
        await auth()
    front_frame, captured_at = await get_camera_frame("front")
    response_data = {}
    if front_frame:
        response_data["front_frame"] = front_frame
        response_data["timestamp"] = captured_at
        return JSONResponse(content=response_data)
    else:
        raise HTTPException(status_code=404, detail="Front frame not available")


@app.get("/v2/rear")
async def get_rear_frame():
    await need_start_mission()
    if not auth_response_data:
        await auth()

    if not await browser_service.has_rear_camera():
        raise HTTPException(status_code=404, detail="Rear camera is not available")
    rear_frame, captured_at = await get_camera_frame("rear")
    response_data = {}
    if rear_frame:
        response_data["rear_frame"] = rear_frame
        response_data["timestamp"] = captured_at
        return JSONResponse(content=response_data)
    else:
        raise HTTPException(status_code=404, detail="Rear frame not available")


@app.post("/interventions/start")
async def start_intervention(request: Request):
    await need_start_mission()

    auth_header = os.getenv("SDK_API_TOKEN")
    bot_slug = os.getenv("BOT_SLUG")

    if not auth_header:
        raise HTTPException(
            status_code=500, detail="Authorization header not configured"
        )
    if not bot_slug:
        raise HTTPException(status_code=500, detail="Bot name not configured")

    data = await latest_rover_data()
    latitude = data.get("latitude")
    longitude = data.get("longitude")

    if latitude is None or longitude is None:
        raise HTTPException(status_code=400, detail="Missing latitude or longitude")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_header}",
    }

    payload = {
        "bot_slug": bot_slug,
        "latitude": latitude,
        "longitude": longitude,
    }

    status, response_data = await external_request(
        "POST",
        FRODOBOTS_API_URL + "/sdk/interventions/start",
        headers=headers,
        json=payload,
    )
    if status != 200:
        raise HTTPException(
            status_code=status,
            detail=response_data.get("error", "Failed to start intervention"),
        )
    return JSONResponse(
        status_code=200,
        content={
            "message": "Intervention started successfully",
            "intervention_id": response_data.get("intervention_id"),
        },
    )


@app.post("/interventions/end")
async def end_intervention(request: Request):
    await need_start_mission()

    auth_header = os.getenv("SDK_API_TOKEN")
    bot_slug = os.getenv("BOT_SLUG")

    if not auth_header:
        raise HTTPException(
            status_code=500, detail="Authorization header not configured"
        )
    if not bot_slug:
        raise HTTPException(status_code=500, detail="Bot name not configured")

    data = await latest_rover_data()
    latitude = data.get("latitude")
    longitude = data.get("longitude")

    if latitude is None or longitude is None:
        raise HTTPException(status_code=400, detail="Missing latitude or longitude")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_header}",
    }

    payload = {
        "bot_slug": bot_slug,
        "latitude": latitude,
        "longitude": longitude,
    }

    status, response_data = await external_request(
        "POST",
        FRODOBOTS_API_URL + "/sdk/interventions/end",
        headers=headers,
        json=payload,
    )
    if status != 200:
        raise HTTPException(
            status_code=status,
            detail=response_data.get("error", "Failed to end intervention"),
        )
    return JSONResponse(
        status_code=200,
        content={"message": "Intervention ended successfully"},
    )


@app.get("/interventions/history")
async def interventions_history():
    auth_header = os.getenv("SDK_API_TOKEN")
    bot_slug = os.getenv("BOT_SLUG")

    if not auth_header:
        raise HTTPException(
            status_code=500, detail="Authorization header not configured"
        )
    if not bot_slug:
        raise HTTPException(status_code=500, detail="Bot name not configured")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_header}",
    }

    payload = {"bot_slug": bot_slug}

    status, response_data = await external_request(
        "GET",
        FRODOBOTS_API_URL + "/sdk/interventions/history",
        headers=headers,
        params=payload,
    )
    if status != 200:
        raise HTTPException(
            status_code=status,
            detail="Failed to retrieve interventions history",
        )
    return JSONResponse(content=response_data)

"""Camera-number to Agora UID resolution for the GMU arena ceiling cameras.

The arena cameras are venue infrastructure, not rover hardware: each one
publishes into a single Agora channel under its own UID, and the backend owns
the camera-number-to-UID map. This module holds only the resolution logic, and
deliberately imports neither FastAPI nor Playwright, so the part with real edge
cases stays unit-testable without a browser or a running server.
"""

from typing import Optional


class ArenaCameras:
    """The arena credentials as served by the backend, plus cam -> uid lookup.

    The map is never hardcoded here. The venue may renumber its publishers, and
    the backend can be re-pointed at new UIDs without a deploy, so a copy kept
    on this side would only go stale.
    """

    def __init__(self, credentials: Optional[dict] = None):
        self._credentials = credentials if isinstance(credentials, dict) else {}

    @property
    def app_id(self) -> str:
        return str(self._credentials.get("APP_ID") or "")

    @property
    def channel_name(self) -> str:
        return str(self._credentials.get("CHANNEL_NAME") or "")

    @property
    def rtc_token(self) -> str:
        return str(self._credentials.get("RTC_TOKEN") or "")

    @property
    def viewer_uid(self) -> str:
        return str(self._credentials.get("USERID") or "")

    @property
    def expires_at(self) -> Optional[int]:
        try:
            return int(self._credentials["EXPIRES_AT"])
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def cameras(self) -> dict:
        """cam number -> publisher uid, both coerced to int.

        JSON object keys arrive as strings, so {"1": 1001} has to become
        {1: 1001} before a caller can look up ?cam=1. Entries that are not
        numeric are dropped rather than raising: one malformed entry from the
        backend should not take out every other camera.
        """
        resolved = {}
        cameras = self._credentials.get("CAMERAS")
        if not isinstance(cameras, dict):
            return resolved
        for cam, uid in cameras.items():
            try:
                resolved[int(cam)] = int(uid)
            except (TypeError, ValueError):
                continue
        return resolved

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.channel_name and self.rtc_token and self.cameras)

    def resolve(self, cam_param: str) -> list:
        """Turn a ?cam= value into [(cam, uid), ...].

        Accepts 'all', a single number, or a comma-separated list. Raises
        ValueError with a caller-facing message for anything else, so the
        endpoint can answer 400 instead of silently returning no frames.
        """
        if not self.cameras:
            raise ValueError("Arena cameras are not configured")

        requested = (cam_param or "").strip().lower()
        if requested in ("", "all"):
            return sorted(self.cameras.items())

        resolved = []
        for part in requested.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                cam = int(part)
            except ValueError:
                raise ValueError(f"Invalid camera: {part}") from None
            if cam not in self.cameras:
                known = ", ".join(str(c) for c in sorted(self.cameras))
                raise ValueError(f"Unknown camera: {cam} (known cameras: {known})")
            resolved.append((cam, self.cameras[cam]))

        if not resolved:
            raise ValueError("No camera requested")
        return resolved

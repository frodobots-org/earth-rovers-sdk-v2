import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import main


REPO_ROOT = Path(__file__).resolve().parents[1]


class QueryAuthenticationScopeTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def request(method, path):
        return Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "query_string": f"key={main.ROVER_API_KEY}".encode(),
                "headers": [],
                "client": ("127.0.0.1", 12345),
                "server": ("127.0.0.1", 8000),
            }
        )

    async def test_query_key_is_limited_to_read_only_feed(self):
        await main.require_api_key(self.request("GET", "/feed"))

        for method, path in (("GET", "/status"), ("POST", "/control")):
            with self.subTest(method=method, path=path):
                with self.assertRaises(HTTPException) as context:
                    await main.require_api_key(self.request(method, path))
                self.assertEqual(context.exception.status_code, 401)


class ControlAuthenticationTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        main.auth_response_data = {"BOT_UID": "test-bot"}
        main.cancel_control_watchdog()

    def tearDown(self):
        main.cancel_control_watchdog()
        main.auth_response_data = {}
        self.client.close()

    @staticmethod
    def command():
        return {"command": {"linear": 0, "angular": 0}}

    def test_control_routes_reject_missing_and_invalid_keys(self):
        for path in ("/control", "/control-legacy"):
            with self.subTest(path=path, credential="missing"):
                response = self.client.post(path, json=self.command())
                self.assertEqual(response.status_code, 401)
            with self.subTest(path=path, credential="invalid"):
                response = self.client.post(
                    path,
                    headers={"Authorization": "Bearer invalid"},
                    json=self.command(),
                )
                self.assertEqual(response.status_code, 401)

    def test_control_routes_accept_header_credentials(self):
        auth = {"Authorization": f"Bearer {main.ROVER_API_KEY}"}
        with (
            patch.object(main, "need_start_mission", AsyncMock()),
            patch.object(main, "_dispatch_browser_control", AsyncMock()),
            patch.object(main, "_dispatch_legacy_control", AsyncMock()),
        ):
            self.assertEqual(
                self.client.post(
                    "/control", headers=auth, json=self.command()
                ).status_code,
                200,
            )
            self.assertEqual(
                self.client.post(
                    "/control-legacy",
                    headers={"X-API-Key": main.ROVER_API_KEY},
                    json=self.command(),
                ).status_code,
                200,
            )

    def test_query_key_cannot_authorize_malicious_cross_origin_post(self):
        dispatch = AsyncMock()
        with (
            patch.object(main, "need_start_mission", AsyncMock()),
            patch.object(main, "_dispatch_browser_control", dispatch),
        ):
            response = self.client.post(
                f"/control?key={main.ROVER_API_KEY}",
                headers={
                    "Origin": "https://evil.example",
                    "Content-Type": "text/plain",
                },
                content='{"command":{"linear":1,"angular":0}}',
            )

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("access-control-allow-origin", response.headers)
        dispatch.assert_not_awaited()

    def test_default_cors_rejects_untrusted_preflight(self):
        response = self.client.options(
            "/control",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_websocket_rejects_missing_key(self):
        with self.assertRaises(WebSocketDisconnect) as context:
            with self.client.websocket_connect("/ws/data"):
                pass
        self.assertEqual(context.exception.code, 4401)


class DashboardAuthenticationTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()

    def test_dashboard_query_key_is_rejected_and_login_does_not_cache(self):
        response = self.client.get(f"/?key={main.ROVER_API_KEY}")
        self.assertEqual(response.status_code, 401)
        self.assertIn("ROVER_API_KEY", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")

    def test_header_login_sets_hardened_cookie_and_opens_dashboard(self):
        response = self.client.post(
            "/session",
            headers={"Authorization": f"Bearer {main.ROVER_API_KEY}"},
        )
        self.assertEqual(response.status_code, 204)
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertIn("path=/", cookie)

        with patch.object(main, "auth_response_data", {"BOT_UID": "test-bot"}):
            dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotIn("?key=", dashboard.text)
        self.assertEqual(dashboard.headers["cache-control"], "no-store")

    def test_api_routes_do_not_accept_dashboard_cookie(self):
        self.client.cookies.set("rover_key", main.ROVER_API_KEY)
        response = self.client.post("/control", json={"command": {"linear": 0}})
        self.assertEqual(response.status_code, 401)


class SecurityConfigurationTest(unittest.TestCase):
    def run_import(self, **environment):
        child_environment = os.environ.copy()
        child_environment.update(environment)
        return subprocess.run(
            [sys.executable, "-c", "import main"],
            cwd=REPO_ROOT,
            env=child_environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_short_configured_key_is_rejected(self):
        result = self.run_import(ROVER_API_KEY="too-short")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("at least 32 characters", result.stderr)

    def test_wildcard_cors_origin_is_rejected(self):
        result = self.run_import(
            ROVER_API_KEY="a-secure-random-key-with-32-characters",
            ALLOWED_ORIGINS="*",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("explicit trusted origins", result.stderr)

    def test_docker_defaults_are_loopback_only(self):
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("${ROVER_BIND_HOST:-127.0.0.1}:8000", dockerfile)
        self.assertIn('"127.0.0.1:8000:8000"', compose)


class BasicExampleClientTest(unittest.TestCase):
    def test_shared_client_adds_bearer_header(self):
        basics_path = REPO_ROOT / "examples" / "basics"
        sys.path.insert(0, str(basics_path))
        try:
            import _client

            with patch.dict(os.environ, {"ROVER_API_KEY": main.ROVER_API_KEY}):
                session = _client.rover_session()
            try:
                self.assertEqual(
                    session.headers["Authorization"],
                    f"Bearer {main.ROVER_API_KEY}",
                )
            finally:
                session.close()
        finally:
            sys.path.remove(str(basics_path))


if __name__ == "__main__":
    unittest.main()

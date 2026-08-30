import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

import main


class SpeakEndpointTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main.auth_response_data = {"BOT_UID": "bot"}

    async def asyncTearDown(self):
        main.auth_response_data = {}

    @staticmethod
    def _request(text):
        request = AsyncMock()
        request.json.return_value = {"text": text}
        return request

    async def test_concurrent_requests_use_distinct_audio_files_and_clean_up(self):
        generated_paths = []

        async def generate(_text, output_path):
            generated_paths.append(output_path)
            await asyncio.sleep(0)
            return f"{output_path}.mp3"

        play = AsyncMock()
        with (
            patch.dict(os.environ, {"MISSION_SLUG": ""}),
            patch.object(main, "generate_speech", side_effect=generate),
            patch.object(main.browser_service, "speak", play),
            patch.object(
                main.secrets,
                "token_urlsafe",
                side_effect=["request-one", "request-two"],
            ),
            patch.object(main.os, "remove") as remove,
        ):
            responses = await asyncio.gather(
                main.speak(self._request("first")),
                main.speak(self._request("second")),
            )

        self.assertCountEqual(
            generated_paths,
            [
                os.path.join("static", "tts_output_request-one"),
                os.path.join("static", "tts_output_request-two"),
            ],
        )
        self.assertCountEqual(
            [arguments.args[0] for arguments in play.await_args_list],
            [
                "http://127.0.0.1:8000/static/tts_output_request-one.mp3",
                "http://127.0.0.1:8000/static/tts_output_request-two.mp3",
            ],
        )
        self.assertCountEqual(
            [arguments.args[0] for arguments in remove.call_args_list],
            [
                os.path.join("static", "tts_output_request-one.mp3"),
                os.path.join("static", "tts_output_request-two.mp3"),
            ],
        )
        self.assertEqual(
            responses,
            [
                {"message": "Speech sent to rover"},
                {"message": "Speech sent to rover"},
            ],
        )

    async def test_generated_audio_is_removed_when_playback_fails(self):
        audio_path = os.path.join("static", "tts_output_failed.mp3")
        with (
            patch.dict(os.environ, {"MISSION_SLUG": ""}),
            patch.object(
                main, "generate_speech", AsyncMock(return_value=audio_path)
            ),
            patch.object(
                main.browser_service,
                "speak",
                AsyncMock(side_effect=RuntimeError("playback failed")),
            ),
            patch.object(main.os, "remove") as remove,
        ):
            with self.assertRaises(main.HTTPException) as context:
                await main.speak(self._request("hello"))

        self.assertEqual(context.exception.status_code, 500)
        remove.assert_called_once_with(audio_path)


if __name__ == "__main__":
    unittest.main()

import unittest

from arena_cameras import ArenaCameras

BACKEND_RESPONSE = {
    "APP_ID": "venue_app_id",
    "CHANNEL_NAME": "offroad_cam_1",
    "RTC_TOKEN": "a-token",
    "USERID": 900034,
    # JSON object keys arrive as strings, exactly as the backend sends them.
    "CAMERAS": {"1": 1001, "2": 1002, "3": 1003},
    "EXPIRES_AT": 1789036750,
}


class ArenaCredentialsTest(unittest.TestCase):
    def test_reads_the_backend_payload(self):
        arena = ArenaCameras(BACKEND_RESPONSE)

        self.assertEqual(arena.app_id, "venue_app_id")
        self.assertEqual(arena.channel_name, "offroad_cam_1")
        self.assertEqual(arena.viewer_uid, "900034")
        self.assertEqual(arena.expires_at, 1789036750)
        self.assertTrue(arena.configured)

    def test_camera_keys_become_integers(self):
        # ?cam=1 arrives as an int, so a string-keyed map would never match.
        self.assertEqual(ArenaCameras(BACKEND_RESPONSE).cameras[1], 1001)

    def test_missing_credentials_are_not_configured(self):
        for payload in ({}, None, {"APP_ID": "x"}, {"APP_ID": "x", "CAMERAS": {}}):
            self.assertFalse(ArenaCameras(payload).configured)

    def test_a_malformed_camera_entry_does_not_drop_the_others(self):
        arena = ArenaCameras({**BACKEND_RESPONSE, "CAMERAS": {"1": 1001, "x": "y"}})

        self.assertEqual(arena.cameras, {1: 1001})

    def test_expires_at_is_none_when_absent_or_junk(self):
        self.assertIsNone(ArenaCameras({}).expires_at)
        self.assertIsNone(ArenaCameras({"EXPIRES_AT": "soon"}).expires_at)


class ArenaResolveTest(unittest.TestCase):
    def setUp(self):
        self.arena = ArenaCameras(BACKEND_RESPONSE)

    def test_all_returns_every_camera_in_order(self):
        self.assertEqual(self.arena.resolve("all"), [(1, 1001), (2, 1002), (3, 1003)])

    def test_blank_defaults_to_all(self):
        self.assertEqual(self.arena.resolve(""), self.arena.resolve("all"))

    def test_single_camera(self):
        self.assertEqual(self.arena.resolve("2"), [(2, 1002)])

    def test_comma_separated_list_with_spaces(self):
        self.assertEqual(self.arena.resolve(" 1 , 3 "), [(1, 1001), (3, 1003)])

    def test_case_insensitive_all(self):
        self.assertEqual(self.arena.resolve("ALL"), self.arena.resolve("all"))

    def test_unknown_camera_names_the_known_ones(self):
        with self.assertRaises(ValueError) as ctx:
            self.arena.resolve("9")

        self.assertIn("Unknown camera: 9", str(ctx.exception))
        self.assertIn("1, 2, 3", str(ctx.exception))

    def test_non_numeric_camera_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.arena.resolve("front")

        self.assertIn("Invalid camera: front", str(ctx.exception))

    def test_resolving_without_credentials_raises(self):
        with self.assertRaises(ValueError):
            ArenaCameras({}).resolve("1")


if __name__ == "__main__":
    unittest.main()

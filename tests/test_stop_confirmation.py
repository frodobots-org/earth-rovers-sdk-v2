import unittest

from stop_confirmation import StoppedWheelConfirmation


class StoppedWheelConfirmationTest(unittest.TestCase):
    def test_zero_wheels_must_advance_after_send_and_settle(self):
        stop = StoppedWheelConfirmation(100)
        self.assertFalse(stop.update({"rpms": [[0, 0, 0, 0, 100.1]]}, 100.2))
        self.assertTrue(stop.update({"rpms": [[0, 0, 0, 0, 100.4]]}, 100.5))

    def test_old_repeated_and_future_samples_cannot_confirm(self):
        for samples in [
            [[0, 0, 0, 0, 99], [0, 0, 0, 0, 100]],
            [[0, 0, 0, 0, 100.1]] * 10,
            [[0, 0, 0, 0, 100.1], [0, 0, 0, 0, 999]],
        ]:
            with self.subTest(samples=samples):
                self.assertFalse(StoppedWheelConfirmation(100).update({"rpms": samples}, 100.5))

    def test_rotation_is_not_stopped_even_when_scalar_speed_is_zero(self):
        self.assertFalse(StoppedWheelConfirmation(100).update(
            {"speed": 0, "rpms": [[10, -10, 10, -10, 100.1], [10, -10, 10, -10, 100.4]]}, 100.5
        ))

    def test_motion_resets_settling_window(self):
        stop = StoppedWheelConfirmation(100)
        self.assertFalse(stop.update({"rpms": [
            [0, 0, 0, 0, 100.1], [0, 1, 0, 0, 100.2], [0, 0, 0, 0, 100.4]
        ]}, 100.5))
        self.assertTrue(stop.update({"rpms": [[0, 0, 0, 0, 100.7]]}, 100.8))

    def test_invalid_or_missing_data_fails_closed(self):
        for data in [None, [], {"speed": 0}, {"rpms": []}, {"rpms": "bad"},
                     {"rpms": [[0, 0, 0, 100.1]]},
                     {"rpms": [[False, 0, 0, 0, 100.1]]},
                     {"rpms": [[0, 0, 0, 0, float("nan")]]}]:
            with self.subTest(data=data):
                self.assertFalse(StoppedWheelConfirmation(100).update(data, 100.5))

    def test_old_batch_and_gaps_do_not_confirm(self):
        stop = StoppedWheelConfirmation(100)
        self.assertFalse(stop.update({"rpms": [[0, 0, 0, 0, 100.1], [0, 0, 0, 0, 100.4]]}, 102))
        stop = StoppedWheelConfirmation(100)
        stop.update({"rpms": [[0, 0, 0, 0, 100.1]]}, 100.2)
        self.assertFalse(stop.update({"rpms": [[0, 0, 0, 0, 102.1]]}, 102.2))

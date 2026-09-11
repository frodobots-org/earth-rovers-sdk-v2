"""Conservative fallback for RTSA peers without per-command RTM receipts."""

import math


class StoppedWheelConfirmation:
    """Require advancing four-wheel zero-RPM samples after send acceptance.

    Scalar speed cannot rule out an in-place turn. Sample timestamps (Unix
    seconds) must be fresh, not merely wrapped in a newly received packet.
    A quarter-second of zero readings filters a single transient zero sample.
    This observes stationary wheels; it is not a firmware command echo.
    """

    def __init__(self, after: float):
        self.after = after
        self.last_sample = after
        self.zero_since = None

    def update(self, data, now: float) -> bool:
        samples = data.get("rpms") if isinstance(data, dict) else None
        if not isinstance(samples, list) or not samples:
            self.zero_since = None
            return False
        parsed = []
        for sample in samples:
            if not isinstance(sample, list) or len(sample) != 5 or any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) for value in sample
            ):
                self.zero_since = None
                return False
            parsed.append(sample)
        advanced = False
        for sample in sorted(parsed, key=lambda row: row[4]):
            timestamp = sample[4]
            if timestamp <= self.last_sample:
                continue
            # Do not accept future-dated, replayed, or old sensor batches.
            if timestamp > now or now - timestamp > 1.0:
                self.zero_since = None
                return False
            if timestamp - self.last_sample > 1.0:
                self.zero_since = None
            self.last_sample = timestamp
            advanced = True
            if any(rpm != 0 for rpm in sample[:4]):
                self.zero_since = None
            elif self.zero_since is None:
                self.zero_since = timestamp
        return bool(
            advanced and self.zero_since is not None
            and self.last_sample - self.zero_since >= 0.25
        )

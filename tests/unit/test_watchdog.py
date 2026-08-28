"""Process watchdog: restart the bot when the event loop stops making progress.

`heartbeat_job` touches `/tmp/adso_heartbeat` every 60s and Docker's HEALTHCHECK
reads its age, but nothing acts on the result: `restart: unless-stopped` only
fires when the process exits, and Docker outside Swarm ignores health status. A
hung bot therefore stays hung, marked `unhealthy`, forever.

The check cannot live on the event loop it watches — that is the whole failure
mode. `heartbeat_job` is an apscheduler job on that same loop, so a blocked loop
means the job never runs and never gets to notice anything. The watchdog is an
OS thread instead, which the blocked loop cannot block.

Two mistakes these tests pin down:

- **`os._exit`, not `sys.exit`.** `sys.exit()` in a secondary thread raises
  `SystemExit` in that thread alone and leaves the process running — the bot
  would stay hung with the watchdog silently gone.
- **A missing heartbeat file is not the same as a stale one at startup.** The
  thread starts before `heartbeat_job` has run for the first time; tripping
  there would put the container in a restart loop.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import patch

from adso import watchdog


class _Trip:
    """Records that the watchdog decided to kill the process."""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> None:
        self.count += 1


def _heartbeat(tmp_path: Path, age_seconds: float) -> Path:
    """Create a heartbeat file whose mtime is `age_seconds` in the past."""
    path = tmp_path / "adso_heartbeat"
    path.touch()
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))
    return path


class TestStallDetection:
    def test_a_fresh_heartbeat_does_not_trip(self, tmp_path: Path) -> None:
        """Counter-case: a bot that is merely slow must not be killed."""
        trip = _Trip()
        watchdog.check_heartbeat(
            heartbeat_path=_heartbeat(tmp_path, age_seconds=10),
            started_at=time.time() - 3600,
            threshold=300,
            marker_path=tmp_path / "marker",
            on_stall=trip,
        )

        assert trip.count == 0

    def test_a_stale_heartbeat_trips(self, tmp_path: Path) -> None:
        trip = _Trip()
        watchdog.check_heartbeat(
            heartbeat_path=_heartbeat(tmp_path, age_seconds=600),
            started_at=time.time() - 3600,
            threshold=300,
            marker_path=tmp_path / "marker",
            on_stall=trip,
        )

        assert trip.count == 1, (
            "the heartbeat is 10 minutes old against a 5 minute threshold: the "
            "loop is not running the job any more"
        )

    def test_a_heartbeat_that_never_appears_trips_after_the_threshold(
        self, tmp_path: Path
    ) -> None:
        """A bot that hung before writing its first heartbeat is still hung."""
        trip = _Trip()
        watchdog.check_heartbeat(
            heartbeat_path=tmp_path / "never-written",
            started_at=time.time() - 600,
            threshold=300,
            marker_path=tmp_path / "marker",
            on_stall=trip,
        )

        assert trip.count == 1

    def test_a_missing_heartbeat_does_not_trip_during_startup(
        self, tmp_path: Path
    ) -> None:
        """Counter-case: the thread starts before the first heartbeat is written.

        Tripping here would put the container in a restart loop.
        """
        trip = _Trip()
        watchdog.check_heartbeat(
            heartbeat_path=tmp_path / "never-written",
            started_at=time.time() - 5,
            threshold=300,
            marker_path=tmp_path / "marker",
            on_stall=trip,
        )

        assert trip.count == 0


class TestSurvivesABlockedLoop:
    async def test_the_watchdog_runs_while_the_event_loop_is_blocked(
        self, tmp_path: Path
    ) -> None:
        """The reason this is a thread and not a job: it must outlive the hang."""
        trip = _Trip()
        heartbeat = _heartbeat(tmp_path, age_seconds=60)

        watchdog.start_watchdog(
            heartbeat_path=heartbeat,
            marker_path=tmp_path / "marker",
            threshold=0.05,
            poll_interval=0.02,
            on_stall=trip,
        )

        # Block the event loop the way a hung call would: synchronously, with no
        # await in sight. An apscheduler job could not run here — that is the
        # failure mode the watchdog exists for.
        time.sleep(0.4)

        assert trip.count >= 1, (
            "the watchdog did not fire while the loop was blocked, which is the "
            "only scenario it exists for"
        )
        # And the loop really was blocked: nothing else got to run.
        await asyncio.sleep(0)


class TestHardExit:
    def test_the_default_stall_action_kills_the_process(self) -> None:
        """`sys.exit()` in a thread only ends that thread — the bot stays hung."""
        with patch("os._exit") as hard_exit:
            watchdog._hard_exit()

        hard_exit.assert_called_once_with(1)


class TestTripMarker:
    def test_a_trip_leaves_a_marker_for_the_next_boot(self, tmp_path: Path) -> None:
        """Without it, a hang followed by an automatic restart is invisible."""
        marker = tmp_path / "marker"
        watchdog.check_heartbeat(
            heartbeat_path=_heartbeat(tmp_path, age_seconds=600),
            started_at=time.time() - 3600,
            threshold=300,
            marker_path=marker,
            on_stall=_Trip(),
        )

        assert marker.exists()

    def test_consuming_the_marker_reports_it_once_and_clears_it(
        self, tmp_path: Path
    ) -> None:
        marker = tmp_path / "marker"
        marker.write_text("stalled")

        assert watchdog.consume_trip_marker(marker) is True
        assert not marker.exists(), "a stale marker would warn on every restart"
        assert watchdog.consume_trip_marker(marker) is False

    def test_a_clean_boot_reports_nothing(self, tmp_path: Path) -> None:
        """Counter-case: a normal restart must not claim the bot hung."""
        assert watchdog.consume_trip_marker(tmp_path / "absent") is False


class TestThresholdOrdering:
    def test_the_watchdog_is_slower_than_the_docker_healthcheck(self) -> None:
        """The healthcheck gives visibility; the watchdog is the last resort.

        Docker marks the container unhealthy after ~2 minutes of silence. The
        watchdog must fire later than that, so a bot that is merely slow shows up
        as `unhealthy` first and only a real hang gets killed.
        """
        assert watchdog.STALL_THRESHOLD_SECONDS > 120, (
            f"threshold is {watchdog.STALL_THRESHOLD_SECONDS}s: at or below the "
            "healthcheck window, the watchdog kills bots that are merely slow"
        )

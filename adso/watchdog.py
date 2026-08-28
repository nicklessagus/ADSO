"""Process watchdog: restart the bot when the event loop stops making progress.

`heartbeat_job` touches `/tmp/adso_heartbeat` every 60s and Docker's HEALTHCHECK
reads its age, but nothing acts on the verdict: `restart: unless-stopped` only
fires when the process exits, and Docker outside Swarm ignores health status.
A hung bot stays hung, marked `unhealthy`, indefinitely.

**Why this is a thread and not a job.** The obvious implementation — have
`heartbeat_job` notice it is running late and exit — cannot work: the job is an
apscheduler task on the very event loop it would be watching, so a blocked loop
means it never runs and never gets to notice anything. It would only catch the
transient stalls that recover on their own, and miss every permanent hang. An OS
thread keeps its slot on the scheduler no matter what the loop is doing.

The known limit: a hang that holds the GIL blocks this thread too, and no
in-process watchdog can cover that. In practice the CPU-heavy work (PDF
rasterization, whisper) already runs through `asyncio.to_thread` in libraries
that release the GIL. Covering that last sliver would take an external
supervisor with access to the Docker socket, which is root-equivalent on the
host — too high a price for this deployment.

Restarting drops whatever is in `user_data`, including a preview waiting for
confirmation. That content is lost either way once the bot hangs; the difference
is that a restart says so. Hence the generous threshold: the Docker healthcheck
flags a merely slow bot as `unhealthy` long before the watchdog kills a hung one.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

HEARTBEAT_PATH = Path("/tmp/adso_heartbeat")
MARKER_PATH = Path("/tmp/adso_watchdog_tripped")

# Deliberately slower than the Docker healthcheck window (~2 min): that one is
# for visibility, this one acts. A bot that is slow shows up as `unhealthy`
# first; only one that is genuinely stuck gets killed.
STALL_THRESHOLD_SECONDS = 300.0
POLL_INTERVAL_SECONDS = 60.0


def _hard_exit() -> None:
    """Kill the process so `restart: unless-stopped` brings it back.

    `os._exit` and not `sys.exit`: the latter raises `SystemExit` in the calling
    thread only, which would end the watchdog and leave the bot hung — the exact
    outcome this module exists to prevent.
    """
    os._exit(1)


def _seconds_since_heartbeat(
    heartbeat_path: Path, started_at: float, now: float
) -> float:
    """Age of the heartbeat, falling back to the watchdog's own start time.

    A missing file means the heartbeat job has not run yet. Measuring from
    startup makes a bot that hung before its first beat trip on the same
    threshold as any other, without tripping during a normal boot.

    Args:
        heartbeat_path: File the heartbeat job touches.
        started_at: Wall-clock time the watchdog started.
        now: Current wall-clock time.

    Returns:
        Seconds since the last sign of life.
    """
    try:
        reference = heartbeat_path.stat().st_mtime
    except OSError:
        reference = started_at
    return now - reference


def check_heartbeat(
    *,
    heartbeat_path: Path,
    started_at: float,
    threshold: float,
    marker_path: Path,
    on_stall: Callable[[], None],
    now: Optional[float] = None,
) -> bool:
    """Run one liveness check and act on it.

    Args:
        heartbeat_path: File the heartbeat job touches.
        started_at: Wall-clock time the watchdog started.
        threshold: Seconds of silence that count as a hang.
        marker_path: File written before exiting, so the next boot can report it.
        on_stall: Called when the heartbeat is stale. Normally kills the process.
        now: Current time; injectable for tests.

    Returns:
        True if the heartbeat was stale and `on_stall` was called.

    Behaviour on error: a failure to write the marker is logged and ignored —
    losing the notification must never stop the restart.
    """
    age = _seconds_since_heartbeat(heartbeat_path, started_at, now or time.time())
    if age <= threshold:
        return False

    logger.critical(
        "Watchdog: sin heartbeat hace %.0fs (umbral %.0fs) — el event loop no "
        "avanza, reiniciando el proceso",
        age, threshold,
    )
    try:
        marker_path.write_text(f"stalled for {age:.0f}s\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("Watchdog: no se pudo escribir el marcador: %s", exc)

    on_stall()
    return True


def consume_trip_marker(marker_path: Path = MARKER_PATH) -> bool:
    """Report whether the previous run was killed by the watchdog, and clear it.

    Args:
        marker_path: File written by `check_heartbeat` before exiting.

    Returns:
        True if the marker was there. It is deleted, so it reports once and a
        later clean restart does not warn again.
    """
    try:
        marker_path.unlink()
    except OSError:
        return False
    return True


def start_watchdog(
    *,
    heartbeat_path: Path = HEARTBEAT_PATH,
    marker_path: Path = MARKER_PATH,
    threshold: float = STALL_THRESHOLD_SECONDS,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    on_stall: Callable[[], None] = _hard_exit,
) -> threading.Thread:
    """Start the watchdog thread. Returns it, already running.

    Args:
        heartbeat_path: File the heartbeat job touches.
        marker_path: File written before exiting, for the next boot to report.
        threshold: Seconds of silence that count as a hang.
        poll_interval: Seconds between checks.
        on_stall: Called when the heartbeat is stale; defaults to killing the
            process.

    Returns:
        The running daemon thread. Daemon so it never holds up a clean shutdown.
    """
    started_at = time.time()

    def _loop() -> None:
        while True:
            time.sleep(poll_interval)
            try:
                tripped = check_heartbeat(
                    heartbeat_path=heartbeat_path,
                    started_at=started_at,
                    threshold=threshold,
                    marker_path=marker_path,
                    on_stall=on_stall,
                )
            except Exception as exc:  # noqa: BLE001 — el watchdog no puede morir
                logger.warning("Watchdog: chequeo fallido (se reintenta): %s", exc)
                continue
            if tripped:
                # En producción `on_stall` no retorna (`os._exit`). Si retornó,
                # el watchdog ya cumplió: seguir chequeando solo inundaría el log.
                return

    thread = threading.Thread(target=_loop, name="adso-watchdog", daemon=True)
    thread.start()
    logger.info(
        "Watchdog iniciado: umbral %.0fs, chequeo cada %.0fs",
        threshold, poll_interval,
    )
    return thread

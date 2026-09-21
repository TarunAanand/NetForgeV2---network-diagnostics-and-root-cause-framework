"""Background scheduler that drives the M5 monitoring loop.

A thin daemon-thread wrapper around :meth:`ControllerService.run_due_schedules`.
It is intentionally framework-free (matching the stdlib HTTP server) and swallows
per-tick exceptions so a transient diagnosis failure never kills the loop.
"""

from __future__ import annotations

import logging
import threading

from controller.service import ControllerService

logger = logging.getLogger("netforge.scheduler")


class MonitorScheduler:
    def __init__(self, service: ControllerService, poll_interval: float = 5.0):
        self.service = service
        self.poll_interval = poll_interval
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="netforge-monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def tick(self) -> None:
        """Run one due-schedule pass (used by the loop and directly in tests)."""
        try:
            self.service.run_due_schedules()
        except Exception:  # pragma: no cover - defensive; loop must stay alive
            logger.exception("monitoring tick failed")

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.poll_interval)

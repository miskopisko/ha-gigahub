"""Runs the hub's speed test, from the button or on a schedule."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback

from .api import GigaHubApi
from .coordinator import GigaHubFastCoordinator, GigaHubSlowCoordinator

_LOGGER = logging.getLogger(__name__)

# The hub's counters trail the traffic slightly; keep excluding a little past the end.
SETTLE_SECONDS = 5


class SpeedTestRunner:
    """One speed test at a time, with its traffic kept out of the usage totals."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: GigaHubApi,
        fast: GigaHubFastCoordinator,
        slow: GigaHubSlowCoordinator,
        block_traffic: bool,
    ) -> None:
        self._hass = hass
        self._api = api
        self._fast = fast
        self._slow = slow
        self._block_traffic = block_traffic
        self._listeners: list[Callable[[], None]] = []
        self.running = False

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            self._listeners.remove(listener)

        return remove

    @callback
    def _set_running(self, running: bool) -> None:
        self.running = running
        for listener in list(self._listeners):
            listener()

    async def async_run(self) -> bool:
        """Run a test. Returns False if one was already running. Raises on failure."""
        if self.running:
            return False
        self._set_running(True)
        try:
            # Take a counter reading right before the test so only its traffic is excluded.
            await self._fast.async_refresh()
            self._fast.excluding_speed_test = True
            await self._api.run_speed_test(block_traffic=self._block_traffic)
            await asyncio.sleep(SETTLE_SECONDS)
            await self._fast.async_refresh()
        finally:
            self._fast.excluding_speed_test = False
            self._set_running(False)
        await self._slow.async_request_refresh()
        return True

    async def async_run_scheduled(self, *_args) -> None:
        """Entry point for the timer: log failures instead of raising."""
        try:
            if not await self.async_run():
                _LOGGER.debug("Skipping scheduled speed test: one is already running")
        except Exception as err:  # a failed scheduled test must not kill the timer
            _LOGGER.warning("Scheduled speed test failed: %s", err)

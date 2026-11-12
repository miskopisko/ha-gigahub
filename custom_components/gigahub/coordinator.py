"""Polling coordinators for the Giga Hub."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GigaHubApi, GigaHubAuthError, GigaHubConnectionError
from .const import DOMAIN
from .parse import FastSnapshot, Host, SlowSnapshot, TrafficLedger, rate_mbps

if TYPE_CHECKING:
    from . import GigaHubConfigEntry

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1
LEDGER_SAVE_DELAY = 60  # seconds; the ledger changes every poll, so debounce writes


@dataclass(slots=True)
class FastData:
    snapshot: FastSnapshot
    rates: dict[str, float | None] = field(default_factory=dict)
    # Internet totals with the integration's own speed tests taken out.
    bytes_received: int | None = None
    bytes_sent: int | None = None


class GigaHubFastCoordinator(DataUpdateCoordinator[FastData]):
    """Traffic counters, fibre, radios and ports: cheap to read, polled often."""

    config_entry: GigaHubConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: GigaHubConfigEntry, api: GigaHubApi, interval: int
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} fast",
            update_interval=timedelta(seconds=interval),
        )
        self.api = api
        self._previous: tuple[FastData, float] | None = None
        # Set by the speed test runner while a test is in flight.
        self.excluding_speed_test = False
        self._ledger_store: Store[dict[str, int | None]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.traffic_ledger"
        )
        self._ledger: TrafficLedger | None = None

    async def _async_update_data(self) -> FastData:
        try:
            snapshot, taken_at = await self.api.fetch_fast()
        except GigaHubAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GigaHubConnectionError as err:
            raise UpdateFailed(str(err)) from err

        if self._ledger is None:
            self._ledger = TrafficLedger.from_dict(await self._ledger_store.async_load())
        received, sent = self._ledger.update(
            snapshot.wan.bytes_received if snapshot.wan else None,
            snapshot.wan.bytes_sent if snapshot.wan else None,
            self.excluding_speed_test,
        )
        ledger = self._ledger
        self._ledger_store.async_delay_save(ledger.as_dict, LEDGER_SAVE_DELAY)
        data = FastData(snapshot=snapshot, bytes_received=received, bytes_sent=sent)

        rates = data.rates
        if self._previous is not None:
            old_data, old_at = self._previous
            old = old_data.snapshot
            seconds = taken_at - old_at
            rates["wan_download"] = rate_mbps(received, old_data.bytes_received, seconds)
            rates["wan_upload"] = rate_mbps(sent, old_data.bytes_sent, seconds)
            for band, traffic in snapshot.wifi_traffic.items():
                old_traffic = old.wifi_traffic.get(band)
                if old_traffic is None:
                    continue
                # From the hub's side, "sent" is towards the Wi-Fi clients.
                rates[f"radio_{band}_download"] = rate_mbps(
                    traffic.bytes_sent, old_traffic.bytes_sent, seconds
                )
                rates[f"radio_{band}_upload"] = rate_mbps(
                    traffic.bytes_received, old_traffic.bytes_received, seconds
                )
            old_ports = {p.number: p for p in old.ports}
            for port in snapshot.ports:
                old_port = old_ports.get(port.number)
                if old_port is None:
                    continue
                rates[f"port_{port.number}_download"] = rate_mbps(
                    port.bytes_sent, old_port.bytes_sent, seconds
                )
                rates[f"port_{port.number}_upload"] = rate_mbps(
                    port.bytes_received, old_port.bytes_received, seconds
                )
        self._previous = (data, taken_at)
        return data


class GigaHubSlowCoordinator(DataUpdateCoordinator[SlowSnapshot]):
    """Device list, Wi-Fi clients and speed test history: heavier, polled less often."""

    config_entry: GigaHubConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: GigaHubConfigEntry, api: GigaHubApi, interval: int
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} slow",
            update_interval=timedelta(seconds=interval),
        )
        self.api = api
        self._store: Store[dict[str, list[str]]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.known_devices"
        )
        self._known: set[str] | None = None
        # Drained by the event entity. A queue, because the first refresh runs
        # before the entity exists and its findings must not be lost.
        self.pending_new_devices: list[Host] = []

    async def _async_update_data(self) -> SlowSnapshot:
        try:
            snapshot = await self.api.fetch_slow()
        except GigaHubAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GigaHubConnectionError as err:
            raise UpdateFailed(str(err)) from err

        await self._track_new_devices(snapshot)
        return snapshot

    async def _track_new_devices(self, snapshot: SlowSnapshot) -> None:
        """Remember every MAC ever seen; anything not in that set is new.

        The first run seeds the set from the hub's own device history, so installing
        the integration doesn't announce every device in the house.
        """
        if self._known is None:
            stored = await self._store.async_load()
            self._known = set(stored["macs"]) if stored else set()
            if not stored:
                self._known.update(h.mac for h in snapshot.hosts)
                await self._store.async_save({"macs": sorted(self._known)})
                return
        new = [h for h in snapshot.hosts if h.mac not in self._known]
        if new:
            self.pending_new_devices.extend(new)
            self._known.update(h.mac for h in new)
            await self._store.async_save({"macs": sorted(self._known)})

"""Polling coordinators for the Giga Hub."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import GigaHubApi, GigaHubAuthError, GigaHubConnectionError
from .const import CONF_DEVICE_NAMES, DOMAIN
from .parse import (
    DeviceTrafficTracker,
    FastSnapshot,
    Host,
    RollingRate,
    SlowSnapshot,
    TrafficLedger,
    assign_names,
)

if TYPE_CHECKING:
    from . import GigaHubConfigEntry

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1
LEDGER_SAVE_DELAY = 60  # seconds; the ledger changes every poll, so debounce writes
# Rolling windows for live speeds. The hub syncs fibre, port and Wi-Fi counters about
# every 10 s; the internet (PPP) counter lands in irregular batches 15-25 s late.
FIBRE_WINDOW = 20
INTERNET_WINDOW = 60
RECENT_NEW_DEVICES = 25


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
        self._rates: dict[str, RollingRate] = {}
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

        # From the hub's side, "sent" is towards the house (download).
        counters: dict[str, tuple[int | None, float]] = {
            "wan_download": (received, INTERNET_WINDOW),
            "wan_upload": (sent, INTERNET_WINDOW),
        }
        if snapshot.optical:
            counters["fibre_download"] = (snapshot.optical.bytes_received, FIBRE_WINDOW)
            counters["fibre_upload"] = (snapshot.optical.bytes_sent, FIBRE_WINDOW)
        for band, traffic in snapshot.wifi_traffic.items():
            counters[f"radio_{band}_download"] = (traffic.bytes_sent, FIBRE_WINDOW)
            counters[f"radio_{band}_upload"] = (traffic.bytes_received, FIBRE_WINDOW)
        for port in snapshot.ports:
            counters[f"port_{port.number}_download"] = (port.bytes_sent, FIBRE_WINDOW)
            counters[f"port_{port.number}_upload"] = (port.bytes_received, FIBRE_WINDOW)
        for key, (value, window) in counters.items():
            tracker = self._rates.setdefault(key, RollingRate(window))
            data.rates[key] = tracker.update(taken_at, value)
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
        self._store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.known_devices")
        self._known: set[str] | None = None
        self._vendor_lookup = None
        self._traffic_store: Store[dict] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.device_traffic"
        )
        self.traffic: DeviceTrafficTracker | None = None
        # Drained by the event entity. A queue, because the first refresh runs
        # before the entity exists and its findings must not be lost.
        self.pending_new_devices: list[Host] = []
        self.recent_new_devices: list[dict] = []

    async def _async_update_data(self) -> SlowSnapshot:
        try:
            snapshot = await self.api.fetch_slow()
        except GigaHubAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GigaHubConnectionError as err:
            raise UpdateFailed(str(err)) from err

        if self._vendor_lookup is None:
            self._vendor_lookup = await _load_vendor_lookup()
        assign_names(
            snapshot,
            self._vendor_lookup or None,
            self.config_entry.options.get(CONF_DEVICE_NAMES),
        )
        if self.traffic is None:
            self.traffic = DeviceTrafficTracker(await self._traffic_store.async_load())
        snapshot.device_traffic = self.traffic.update(snapshot.wifi_clients, time.monotonic())
        self._traffic_store.async_delay_save(self.traffic.as_dict, LEDGER_SAVE_DELAY)
        await self._track_new_devices(snapshot)
        return snapshot

    async def _track_new_devices(self, snapshot: SlowSnapshot) -> None:
        """Remember every MAC ever seen; anything not in that set is new.

        The first run seeds the set from the hub's own device history, so installing
        the integration doesn't announce every device in the house. The most recent
        arrivals are kept too, for the "Newest device" sensor and its list.
        """
        if self._known is None:
            stored = await self._store.async_load() or {}
            self._known = set(stored.get("macs") or [])
            self.recent_new_devices = list(stored.get("recent") or [])
            if not stored:
                self._known.update(h.mac for h in snapshot.hosts)
                await self._save_known()
                return
        new = [h for h in snapshot.hosts if h.mac not in self._known]
        if new:
            self.pending_new_devices.extend(new)
            self._known.update(h.mac for h in new)
            joined = dt_util.utcnow().isoformat(timespec="seconds")
            arrivals = [
                {
                    "time": joined,
                    "name": h.name,
                    "mac": h.mac.upper(),
                    "vendor": h.vendor,
                    "connection": (h.interface_type or "").replace("WiFi", "Wi-Fi") or None,
                    "ip": h.ip,
                }
                for h in new
            ]
            self.recent_new_devices = (arrivals + self.recent_new_devices)[:RECENT_NEW_DEVICES]
            await self._save_known()

    async def _save_known(self) -> None:
        await self._store.async_save({"macs": sorted(self._known or ()), "recent": self.recent_new_devices})


async def _load_vendor_lookup():
    """MAC prefix -> manufacturer, from the OUI database Home Assistant already ships.

    Returns False (not None) when it's unavailable, so we only try once.
    """
    try:
        import aiooui

        await aiooui.async_load()
    except Exception as err:  # names just fall back to MAC addresses
        _LOGGER.debug("MAC vendor database unavailable: %s", err)
        return False
    return aiooui.get_vendor

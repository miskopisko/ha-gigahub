"""The Giga Hub integration: connection, fibre and Wi-Fi stats from a Bell Giga Hub."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_interval

from .api import GigaHubApi, GigaHubAuthError, GigaHubConnectionError, HubIdentity
from .const import (
    CONF_BLOCK_TRAFFIC,
    CONF_FAST_INTERVAL,
    CONF_SLOW_INTERVAL,
    CONF_SPEED_TEST_INTERVAL,
    DEFAULT_FAST_INTERVAL,
    DEFAULT_SLOW_INTERVAL,
    DOMAIN,
)
from .coordinator import GigaHubFastCoordinator, GigaHubSlowCoordinator
from .speedtest import SpeedTestRunner

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.EVENT, Platform.SENSOR]


@dataclass(slots=True)
class GigaHubRuntime:
    api: GigaHubApi
    identity: HubIdentity
    fast: GigaHubFastCoordinator
    slow: GigaHubSlowCoordinator
    speed_test: SpeedTestRunner


type GigaHubConfigEntry = ConfigEntry[GigaHubRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: GigaHubConfigEntry) -> bool:
    api = GigaHubApi(entry.data[CONF_HOST], entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    try:
        identity = await api.connect()
    except GigaHubAuthError as err:
        await api.close()
        raise ConfigEntryAuthFailed(str(err)) from err
    except GigaHubConnectionError as err:
        await api.close()
        raise ConfigEntryNotReady(str(err)) from err

    fast = GigaHubFastCoordinator(
        hass, entry, api, int(entry.options.get(CONF_FAST_INTERVAL, DEFAULT_FAST_INTERVAL))
    )
    slow = GigaHubSlowCoordinator(
        hass, entry, api, int(entry.options.get(CONF_SLOW_INTERVAL, DEFAULT_SLOW_INTERVAL))
    )
    try:
        await fast.async_config_entry_first_refresh()
        await slow.async_config_entry_first_refresh()
    except Exception:
        await api.close()
        raise

    runner = SpeedTestRunner(
        hass, api, fast, slow, block_traffic=entry.options.get(CONF_BLOCK_TRAFFIC, False)
    )
    entry.runtime_data = GigaHubRuntime(api=api, identity=identity, fast=fast, slow=slow, speed_test=runner)
    hours = int(entry.options.get(CONF_SPEED_TEST_INTERVAL, 0))
    if hours > 0:

        @callback
        def _scheduled_speed_test(_now) -> None:
            entry.async_create_background_task(
                hass, runner.async_run_scheduled(), f"{DOMAIN} scheduled speed test"
            )

        # The first test runs one interval after startup, not on every restart.
        entry.async_on_unload(async_track_time_interval(hass, _scheduled_speed_test, timedelta(hours=hours)))
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: GigaHubConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GigaHubConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.api.close()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: GigaHubConfigEntry) -> None:
    """Forget the stored device list and traffic ledger when the integration is deleted."""
    from homeassistant.helpers.storage import Store

    for name in ("known_devices", "traffic_ledger"):
        await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.{name}").async_remove()

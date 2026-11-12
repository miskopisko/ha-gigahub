"""Diagnostics download for the Giga Hub integration (secrets and identifiers redacted)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import GigaHubConfigEntry

TO_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    "public_ip",
    "mac",
    "ip",
    "name",
    "names",
    "server",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GigaHubConfigEntry
) -> dict[str, Any]:
    runtime = entry.runtime_data
    fast = runtime.fast.data
    slow = runtime.slow.data
    return async_redact_data(
        {
            "entry": {"data": dict(entry.data), "options": dict(entry.options)},
            "identity": {
                "model": runtime.identity.model,
                "product_class": runtime.identity.product_class,
                "firmware": runtime.identity.firmware,
                "access_points": runtime.identity.access_points,
                "tv_access_points": sorted(runtime.identity.tv_access_points),
            },
            "fast": {"snapshot": asdict(fast.snapshot), "rates": fast.rates} if fast else None,
            "slow": {
                "host_count": len(slow.hosts),
                "active_hosts": len(slow.active_hosts),
                "wifi_clients": [asdict(c) for c in slow.wifi_clients],
                "speed_test": asdict(slow.speed_test) if slow.speed_test else None,
                "speed_test_state": slow.speed_test_state,
            }
            if slow
            else None,
        },
        TO_REDACT,
    )

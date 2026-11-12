"""Base entity for the Giga Hub integration."""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo, format_mac
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from . import GigaHubConfigEntry
from .const import DOMAIN, MANUFACTURER


def hub_device_info(entry: GigaHubConfigEntry) -> DeviceInfo:
    identity = entry.runtime_data.identity
    mac = format_mac(identity.mac)
    return DeviceInfo(
        identifiers={(DOMAIN, mac)},
        connections={(CONNECTION_NETWORK_MAC, mac)},
        name="Giga Hub",
        manufacturer=MANUFACTURER,
        model=f"Giga Hub {identity.model}" if identity.model else "Giga Hub",
        model_id=identity.product_class,
        sw_version=identity.firmware,
        configuration_url=f"http://{entry.data[CONF_HOST]}",
    )


class GigaHubEntity[CoordinatorT: DataUpdateCoordinator[Any]](CoordinatorEntity[CoordinatorT]):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CoordinatorT,
        entry: GigaHubConfigEntry,
        description: EntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{format_mac(entry.runtime_data.identity.mac)}_{description.key}"
        self._attr_device_info = hub_device_info(entry)

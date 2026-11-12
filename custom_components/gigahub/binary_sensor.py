"""Binary sensors for the Giga Hub integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GigaHubConfigEntry
from .coordinator import FastData, GigaHubFastCoordinator
from .entity import GigaHubEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class GigaHubBinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[FastData], bool | None]


DESCRIPTIONS = (
    GigaHubBinaryDescription(
        key="internet",
        translation_key="internet",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        is_on_fn=lambda d: (
            str(d.snapshot.wan.connection_status).upper() == "CONNECTED" if d.snapshot.wan else None
        ),
    ),
    GigaHubBinaryDescription(
        key="fibre_problem",
        translation_key="fibre_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda d: (
            d.snapshot.optical.status != "UP" or (d.snapshot.optical.alarm or "NONE").upper() != "NONE"
            if d.snapshot.optical
            else None
        ),
    ),
)


SPEED_TEST_RUNNING = BinarySensorEntityDescription(
    key="speed_test_running",
    translation_key="speed_test_running",
    device_class=BinarySensorDeviceClass.RUNNING,
)


class GigaHubSpeedTestRunning(GigaHubEntity[GigaHubFastCoordinator], BinarySensorEntity):
    """On while the hub's speed test runs (its traffic is left out of the usage totals)."""

    def __init__(self, coordinator, entry: GigaHubConfigEntry) -> None:
        super().__init__(coordinator, entry, SPEED_TEST_RUNNING)
        self._runner = entry.runtime_data.speed_test

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._runner.async_add_listener(self.async_write_ha_state))

    @property
    def is_on(self) -> bool:
        return self._runner.running


class GigaHubBinarySensor(GigaHubEntity[GigaHubFastCoordinator], BinarySensorEntity):
    entity_description: GigaHubBinaryDescription

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on_fn(self.coordinator.data)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GigaHubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    fast = entry.runtime_data.fast
    entities: list[BinarySensorEntity] = [GigaHubBinarySensor(fast, entry, d) for d in DESCRIPTIONS]
    entities.append(GigaHubSpeedTestRunning(fast, entry))
    async_add_entities(entities)

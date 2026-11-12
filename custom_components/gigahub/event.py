"""Event entity that fires when a device the hub has never seen joins the network."""

from __future__ import annotations

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GigaHubConfigEntry
from .const import EVENT_NEW_DEVICE
from .coordinator import GigaHubSlowCoordinator
from .entity import GigaHubEntity

PARALLEL_UPDATES = 0

NEW_DEVICE = EventEntityDescription(
    key="new_device",
    translation_key="new_device",
    event_types=[EVENT_NEW_DEVICE],
)


class GigaHubNewDeviceEvent(GigaHubEntity[GigaHubSlowCoordinator], EventEntity):
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._fire_pending()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._fire_pending()
        super()._handle_coordinator_update()

    @callback
    def _fire_pending(self) -> None:
        pending = self.coordinator.pending_new_devices
        while pending:
            host = pending.pop(0)
            self._trigger_event(
                EVENT_NEW_DEVICE,
                {
                    "name": host.name,
                    "mac": host.mac.upper(),
                    "ip": host.ip,
                    "connection": host.interface_type,
                    "band": host.band,
                },
            )
            self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GigaHubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([GigaHubNewDeviceEvent(entry.runtime_data.slow, entry, NEW_DEVICE)])

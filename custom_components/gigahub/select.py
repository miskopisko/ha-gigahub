"""Live speed source: which counter the dashboard's live graph shows."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import GigaHubConfigEntry
from .coordinator import GigaHubFastCoordinator
from .entity import GigaHubEntity

PARALLEL_UPDATES = 0

SOURCES = ["fibre", "internet"]

LIVE_SOURCE = SelectEntityDescription(
    key="live_speed_source",
    translation_key="live_speed_source",
    options=SOURCES,
    entity_category=EntityCategory.CONFIG,
)


class GigaHubLiveSourceSelect(GigaHubEntity[GigaHubFastCoordinator], SelectEntity, RestoreEntity):
    """A dashboard preference, remembered across restarts.

    Fibre: prompt, but includes Fibe TV and voice. Internet: internet only, but the hub
    reports it in late, lumpy batches. Both sensor pairs always exist with their own
    history; this only tells the dashboard which pair to graph.
    """

    _attr_current_option = "fibre"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) and last.state in SOURCES:
            self._attr_current_option = last.state

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GigaHubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([GigaHubLiveSourceSelect(entry.runtime_data.fast, entry, LIVE_SOURCE)])

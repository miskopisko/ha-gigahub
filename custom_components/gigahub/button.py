"""Buttons for the Giga Hub integration: run a speed test, reboot."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GigaHubConfigEntry
from .api import GigaHubAuthError, GigaHubConnectionError
from .const import DOMAIN
from .coordinator import GigaHubSlowCoordinator
from .entity import GigaHubEntity

PARALLEL_UPDATES = 1

SPEED_TEST = ButtonEntityDescription(key="run_speed_test", translation_key="run_speed_test")
REBOOT = ButtonEntityDescription(
    key="reboot",
    device_class=ButtonDeviceClass.RESTART,
    entity_category=EntityCategory.CONFIG,
)


class GigaHubSpeedTestButton(GigaHubEntity[GigaHubSlowCoordinator], ButtonEntity):
    """Runs the hub's own speed test, which measures the fibre line, not a LAN port."""

    def __init__(self, coordinator, entry: GigaHubConfigEntry) -> None:
        super().__init__(coordinator, entry, SPEED_TEST)
        self._runner = entry.runtime_data.speed_test

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._runner.async_add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        return super().available and not self._runner.running

    async def async_press(self) -> None:
        try:
            await self._runner.async_run()
        except (GigaHubAuthError, GigaHubConnectionError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="speed_test_failed",
                translation_placeholders={"error": str(err)},
            ) from err


class GigaHubRebootButton(GigaHubEntity[GigaHubSlowCoordinator], ButtonEntity):
    def __init__(self, coordinator, entry: GigaHubConfigEntry) -> None:
        super().__init__(coordinator, entry, REBOOT)
        self._entry = entry

    async def async_press(self) -> None:
        try:
            await self._entry.runtime_data.api.reboot()
        except (GigaHubAuthError, GigaHubConnectionError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="reboot_failed",
                translation_placeholders={"error": str(err)},
            ) from err


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GigaHubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    slow = entry.runtime_data.slow
    async_add_entities([GigaHubSpeedTestButton(slow, entry), GigaHubRebootButton(slow, entry)])

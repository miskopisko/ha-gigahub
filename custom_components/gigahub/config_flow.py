"""Config flow for the Giga Hub integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.device_registry import format_mac

from .api import GigaHubApi, GigaHubAuthError, GigaHubConnectionError, HubIdentity
from .const import (
    CONF_BLOCK_TRAFFIC,
    CONF_DEVICE_NAMES,
    CONF_FAST_INTERVAL,
    CONF_SLOW_INTERVAL,
    CONF_SPEED_TEST_INTERVAL,
    DEFAULT_FAST_INTERVAL,
    DEFAULT_HOST,
    DEFAULT_SLOW_INTERVAL,
    DEFAULT_USERNAME,
    DOMAIN,
    MIN_FAST_INTERVAL,
    MIN_SLOW_INTERVAL,
)

PASSWORD_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)


async def _validate(host: str, username: str, password: str) -> HubIdentity:
    api = GigaHubApi(host, username, password)
    try:
        return await api.connect()
    finally:
        await api.close()


class GigaHubConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                identity = await _validate(
                    user_input[CONF_HOST], user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
            except GigaHubAuthError:
                errors["base"] = "invalid_auth"
            except GigaHubConnectionError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(format_mac(identity.mac))
                self._abort_if_unique_id_configured(updates={CONF_HOST: user_input[CONF_HOST]})
                return self.async_create_entry(title="Giga Hub", data=user_input)

        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, DEFAULT_HOST)): str,
                vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, DEFAULT_USERNAME)): str,
                vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await _validate(entry.data[CONF_HOST], entry.data[CONF_USERNAME], user_input[CONF_PASSWORD])
            except GigaHubAuthError:
                errors["base"] = "invalid_auth"
            except GigaHubConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> GigaHubOptionsFlow:
        return GigaHubOptionsFlow()


class GigaHubOptionsFlow(OptionsFlow):
    """A menu: polling and speed-test settings, or naming devices. Changes are kept
    until "Save and close" so several devices can be named in one go."""

    def __init__(self) -> None:
        self._options: dict[str, Any] | None = None

    @property
    def _opts(self) -> dict[str, Any]:
        if self._options is None:
            self._options = dict(self.config_entry.options)
            self._options[CONF_DEVICE_NAMES] = dict(self._options.get(CONF_DEVICE_NAMES) or {})
        return self._options

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["settings", "device_names", "finish"])

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_create_entry(data=self._opts)

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._opts.update(user_input)
            return await self.async_step_init()

        options = self._opts
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_FAST_INTERVAL,
                    default=options.get(CONF_FAST_INTERVAL, DEFAULT_FAST_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_FAST_INTERVAL,
                        max=600,
                        step=5,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_SLOW_INTERVAL,
                    default=options.get(CONF_SLOW_INTERVAL, DEFAULT_SLOW_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_SLOW_INTERVAL,
                        max=3600,
                        step=30,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_SPEED_TEST_INTERVAL,
                    default=options.get(CONF_SPEED_TEST_INTERVAL, 0),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=168,
                        step=1,
                        unit_of_measurement="h",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_BLOCK_TRAFFIC, default=options.get(CONF_BLOCK_TRAFFIC, False)): bool,
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)

    async def async_step_device_names(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is None or runtime.slow.data is None:
            return self.async_abort(reason="not_loaded")
        names: dict[str, str] = self._opts[CONF_DEVICE_NAMES]

        if user_input is not None:
            mac = user_input["device"]
            name = (user_input.get("name") or "").strip()
            if name:
                names[mac] = name
            else:
                names.pop(mac, None)
            return await self.async_step_init()

        snapshot = runtime.slow.data
        hosts = {h.mac: h for h in snapshot.hosts}
        # Online devices first, then any offline ones you've already named.
        macs = [h.mac for h in sorted(snapshot.active_hosts, key=lambda h: h.name.lower())]
        macs += sorted(m for m in names if m not in macs)

        def label(mac: str) -> str:
            host = hosts.get(mac)
            name = names.get(mac) or (host.name if host else mac.upper())
            parts = [name]
            if mac.upper() not in name:
                parts.append(mac.upper())
            if host and host.vendor and host.vendor not in name:
                parts.append(host.vendor)
            if host and host.interface_type:
                parts.append(host.interface_type.replace("WiFi", "Wi-Fi"))
            if not (host and host.active):
                parts.append("offline")
            return " · ".join(parts)

        schema = vol.Schema(
            {
                vol.Required("device"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[selector.SelectOptionDict(value=m, label=label(m)) for m in macs],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional("name"): str,
            }
        )
        return self.async_show_form(step_id="device_names", data_schema=schema)

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
    CONF_FAST_INTERVAL,
    CONF_SLOW_INTERVAL,
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
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
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
                vol.Required(CONF_BLOCK_TRAFFIC, default=options.get(CONF_BLOCK_TRAFFIC, False)): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

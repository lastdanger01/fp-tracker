"""Config flow for the fp_tracker integration."""

from __future__ import annotations

import json
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import callback

from .const import (
    CONF_CLIENT_ID,
    CONF_DEVICE_TOKEN,
    CONF_LOCALCODE,
    CONF_REFRESH_TOKEN,
    CONF_SESSION_ID,
    CONF_TOKEN,
    CONF_USER_SOURCE,
    DEFAULT_LOCALCODE,
    DOMAIN,
)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the config flow schema."""
    return vol.Schema(
        {
            vol.Required(
                CONF_TOKEN, default=defaults.get(CONF_TOKEN, "")
            ): str,
            vol.Required(
                CONF_DEVICE_TOKEN, default=defaults.get(CONF_DEVICE_TOKEN, "")
            ): str,
            vol.Required(
                CONF_REFRESH_TOKEN, default=defaults.get(CONF_REFRESH_TOKEN, "")
            ): str,
            vol.Optional(
                CONF_CLIENT_ID, default=defaults.get(CONF_CLIENT_ID, "")
            ): str,
            vol.Optional(
                CONF_SESSION_ID, default=defaults.get(CONF_SESSION_ID, "")
            ): str,
            vol.Optional(
                CONF_USER_SOURCE, default=defaults.get(CONF_USER_SOURCE, "")
            ): str,
            vol.Optional(
                CONF_LOCALCODE, default=defaults.get(CONF_LOCALCODE, DEFAULT_LOCALCODE)
            ): vol.In(["tw", "hk", "sg"]),
        }
    )


class FpTrackerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the fp_tracker config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            token = user_input.get(CONF_TOKEN, "").strip()
            if not token:
                errors[CONF_TOKEN] = "token_required"

        if errors:
            return self.async_show_form(
                step_id="user", data_schema=_schema(user_input or {}), errors=errors
            )

        await self.async_set_unique_id(user_input[CONF_TOKEN][:64])
        self._abort_if_unique_id_configured()

        title = f"foodpanda ({user_input.get(CONF_LOCALCODE, DEFAULT_LOCALCODE)})"
        return self.async_create_entry(title=title, data=user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> Any:
        """Return the options flow handler."""
        return FpTrackerOptionsFlow(config_entry)


class FpTrackerOptionsFlow(ConfigFlow):
    """Handle the fp_tracker options."""

    def __init__(self, config_entry: Any) -> None:
        """Initialize the options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(current))

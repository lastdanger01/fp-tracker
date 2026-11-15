"""Config flow for the fp_tracker integration."""

from __future__ import annotations

import hashlib
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


async def _async_token_works(hass: Any, user_input: dict[str, Any]) -> bool:
    """Check the token against the foodpanda API.

    A broken token would otherwise only show up later as a coordinator
    that never updates.
    """
    from .api import FoodpandaApiError, FoodpandaClient

    client = FoodpandaClient(
        hass,
        localcode=user_input.get(CONF_LOCALCODE, DEFAULT_LOCALCODE),
        token=str(user_input.get(CONF_TOKEN, "") or "").strip(),
        device_token=user_input.get(CONF_DEVICE_TOKEN, ""),
        refresh_token=user_input.get(CONF_REFRESH_TOKEN, ""),
        client_id=user_input.get(CONF_CLIENT_ID, ""),
        session_id=user_input.get(CONF_SESSION_ID, ""),
        user_source=user_input.get(CONF_USER_SOURCE, ""),
    )

    try:
        await client.async_get_orders()
    except FoodpandaApiError:
        return False
    except Exception:  # noqa: BLE001
        return False
    return True


class FpTrackerConfigFlow(ConfigFlow, domain=DOMAIN):

    """Handle the fp_tracker config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            token = str(user_input.get(CONF_TOKEN, "") or "").strip()
            if not token:
                errors[CONF_TOKEN] = "token_required"
            else:
                # Verify the token against the API before saving it, so a
                # typo does not end up as a broken config entry.
                if not await _async_token_works(self.hass, user_input):
                    errors[CONF_TOKEN] = "token_invalid"

        if not errors and user_input is not None:
            # The unique id is derived from the token, but the token
            # itself must never end up in the flow id, so only a
            # hash of it is used.
            await self.async_set_unique_id(
                hashlib.sha256(str(user_input[CONF_TOKEN]).strip().encode()).hexdigest()
            )
            self._abort_if_unique_id_configured()

            title = f"foodpanda ({user_input.get(CONF_LOCALCODE, DEFAULT_LOCALCODE)})"
            return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(user_input or {}),
            errors=errors,
        )

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

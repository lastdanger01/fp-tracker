"""Config flow for the fp_tracker integration."""

from __future__ import annotations

import base64
import hashlib
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
    DEFAULT_USER_SOURCE,
    DOMAIN,
)

# The key id of a device token says "device" in it, so a token that was
# pasted into the wrong field can be told apart from a good one.
DEVICE_TOKEN_KID_MARKER = "device"


def _jwt_kid(token: str) -> str:
    """Return the key id of a JWT, or an empty string.

    The access token and the device token are both JWTs signed by
    foodpanda. Their key ids differ, which makes it possible to catch
    a device token that was pasted into the token field: it is a very
    easy mistake to make, because both look like a long string of
    dots.
    """
    parts = token.split(".")
    if len(parts) < 2:
        return ""
    segment = parts[0]
    segment += "=" * (-len(segment) % 4)
    try:
        header = json.loads(base64.urlsafe_b64decode(segment))
    except (ValueError, TypeError):
        return ""
    kid = header.get("kid")
    return kid if isinstance(kid, str) else ""


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
            vol.Required(
                CONF_USER_SOURCE,
                default=defaults.get(CONF_USER_SOURCE) or DEFAULT_USER_SOURCE,
            ): str,
            vol.Optional(
                CONF_LOCALCODE, default=defaults.get(CONF_LOCALCODE, DEFAULT_LOCALCODE)
            ): vol.In(["tw", "hk", "sg"]),
        }
    )


async def _async_token_works(hass: Any, user_input: dict[str, Any]) -> str | None:
    """Check the token against the foodpanda API.

    Returns None when the token works, otherwise the key of the error
    that should be shown to the user.

    The failure modes used to collapse into a single "the token is
    invalid or expired" message, which was misleading: a 401 really is
    a dead token, but a 403 means the request was rejected (a missing
    user source, or the bot protection in front of the API) and a
    connection error means neither of those.
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
    except FoodpandaApiError as err:
        if err.status == 401:
            return "token_invalid"
        if err.status == 403:
            return "token_forbidden"
        return "cannot_connect"
    except Exception:  # noqa: BLE001
        # A connection error, a DNS failure, a timeout: nothing to say
        # about the token itself.
        return "cannot_connect"
    return None


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
            elif DEVICE_TOKEN_KID_MARKER in _jwt_kid(token):
                # A device token was pasted into the token field. The
                # API answers 401 for it, which is indistinguishable
                # from an expired token, so it is caught here instead.
                errors[CONF_TOKEN] = "token_is_device_token"
            elif not str(user_input.get(CONF_USER_SOURCE, "") or "").strip():
                # Without the user source header the API answers 403.
                errors[CONF_USER_SOURCE] = "user_source_required"
            else:
                # Verify the token against the API before saving it, so a
                # typo does not end up as a broken config entry.
                error = await _async_token_works(self.hass, user_input)
                if error:
                    errors[CONF_TOKEN] = error

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

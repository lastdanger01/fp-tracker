"""Credential storage for the fp_tracker integration.

The foodpanda access token is short lived, so it has to be refreshed
regularly. The refreshed credentials are stored in a Home Assistant
Store instead of the config entry, because writing to the config entry
would trigger the update listener and tear down the running
coordinator.

The credentials in the config entry are the fallback: they are used
when the store holds nothing for this entry.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    CONF_CLIENT_ID,
    CONF_DEVICE_TOKEN,
    CONF_LOCALCODE,
    CONF_REFRESH_TOKEN,
    CONF_SESSION_ID,
    CONF_TOKEN,
    CONF_USER_SOURCE,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "fp_tracker/credentials"
STORAGE_VERSION = 1


def _store(hass: HomeAssistant) -> Store:
    """Return the credential store."""
    return Store(hass, STORAGE_VERSION, STORAGE_KEY)


async def async_load_credentials(
    hass: HomeAssistant, entry_id: str
) -> dict[str, Any] | None:
    """Return the stored credentials for one config entry, if any."""
    data = await _store(hass).async_load()
    if not isinstance(data, dict):
        return None
    entry = data.get(entry_id)
    if not isinstance(entry, dict):
        return None
    return entry


async def async_save_credentials(
    hass: HomeAssistant, entry_id: str, credentials: dict[str, Any]
) -> None:
    """Store the credentials of one config entry."""
    store = _store(hass)
    data = await store.async_load()
    if not isinstance(data, dict):
        data = {}
    data[entry_id] = {
        CONF_TOKEN: credentials.get(CONF_TOKEN, ""),
        CONF_DEVICE_TOKEN: credentials.get(CONF_DEVICE_TOKEN, ""),
        CONF_REFRESH_TOKEN: credentials.get(CONF_REFRESH_TOKEN, ""),
        CONF_CLIENT_ID: credentials.get(CONF_CLIENT_ID, ""),
        CONF_SESSION_ID: credentials.get(CONF_SESSION_ID, ""),
        CONF_USER_SOURCE: credentials.get(CONF_USER_SOURCE, ""),
        CONF_LOCALCODE: credentials.get(CONF_LOCALCODE, "tw"),
    }
    await store.async_save(data)
    _LOGGER.debug("已寫回 fp_tracker 憑證")

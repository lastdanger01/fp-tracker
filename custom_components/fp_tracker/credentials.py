"""Credential storage for the fp_tracker integration.

The foodpanda access token is short lived, so it has to be refreshed
regularly. The refreshed credentials are stored in a Home Assistant
Store instead of the config entry, because writing to the config entry
would trigger the update listener and tear down the running
coordinator.

The credentials in the config entry are the fallback: they are used
when the store holds nothing for this entry.

A refresh token can only be used once: the foodpanda server hands out
a new one with every refresh and drops the old one. Two integrations
that keep their own copy of it therefore invalidate each other, which
is exactly what happened here: the older `foodpanda` integration
refreshed the shared device token and the copy kept by this
integration became worthless.

To avoid that, this integration reads and writes the credential file
of the older integration (`foodpanda/tokens.json`), so the whole
system only ever holds one refresh token. Its own store stays as a
fallback for the case where the older integration is not installed.

The shared file is only used when it holds the *newer* credentials. A
leftover file from an older installation can hold a token that expired
long ago, and adopting it would throw away a perfectly good token that
the user just pasted into the config flow. The comparison is done on
the expiry of the access token, so a token that was refreshed later
always wins.
"""

from __future__ import annotations

import base64
import json
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
    CONF_TOKEN_TIMEOUT,
    CONF_USER_SOURCE,
    CONF_X_DEVICE,
    DEFAULT_X_DEVICE,
    SHARED_STORAGE_KEY,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "fp_tracker/credentials"
STORAGE_VERSION = 1


def _store(hass: HomeAssistant) -> Store:
    """Return the credential store."""
    return Store(hass, STORAGE_VERSION, STORAGE_KEY)


def _shared_store(hass: HomeAssistant) -> Store:
    """Return the credential store of the older foodpanda integration."""
    return Store(hass, 1, SHARED_STORAGE_KEY)


def _normalize(entry: dict[str, Any]) -> dict[str, Any]:
    """Return the credentials in the names this integration uses.

    The older integration calls some of the fields differently, so
    both spellings are accepted.
    """
    return {
        CONF_TOKEN: entry.get(CONF_TOKEN, "") or "",
        CONF_DEVICE_TOKEN: entry.get(CONF_DEVICE_TOKEN, "") or "",
        CONF_REFRESH_TOKEN: entry.get(CONF_REFRESH_TOKEN, "") or "",
        CONF_CLIENT_ID: entry.get(CONF_CLIENT_ID, "")
        or entry.get("clientid", "")
        or "",
        CONF_SESSION_ID: entry.get(CONF_SESSION_ID, "")
        or entry.get("sessionid", "")
        or "",
        CONF_USER_SOURCE: entry.get(CONF_USER_SOURCE, "")
        or entry.get("usersouce", "")
        or "",
        CONF_X_DEVICE: entry.get(CONF_X_DEVICE, "") or DEFAULT_X_DEVICE,
        CONF_LOCALCODE: entry.get(CONF_LOCALCODE, "tw") or "tw",
        CONF_TOKEN_TIMEOUT: entry.get(CONF_TOKEN_TIMEOUT, 0) or 0,
    }


def _jwt_expiry(token: str) -> int:
    """Return the expiry of an access token, or 0 when it cannot be read.

    The access token is a JWT whose payload carries the expiry as
    "expires" (in seconds since the epoch). The device token uses the
    usual "exp" instead, but only the access token is compared here.
    """
    if not token:
        return 0
    parts = token.split(".")
    if len(parts) < 2:
        return 0
    segment = parts[1]
    segment += "=" * (-len(segment) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(segment))
    except (ValueError, TypeError):
        return 0
    for key in ("expires", "exp"):
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def credentials_expiry(credentials: dict[str, Any]) -> int:
    """Return the expiry of the access token in a set of credentials."""
    expiry = _jwt_expiry(str(credentials.get(CONF_TOKEN, "") or ""))
    if expiry:
        return expiry
    try:
        return int(credentials.get(CONF_TOKEN_TIMEOUT, 0) or 0)
    except (TypeError, ValueError):
        return 0


def shared_credentials_are_newer(
    shared: dict[str, Any], current: dict[str, Any]
) -> bool:
    """Say whether the shared credentials should replace the current ones.

    The shared file is written by the older foodpanda integration, but
    it can also be a leftover from an installation that is long gone.
    A token that expired in the past must not replace a token the user
    just pasted into the config flow, so the newer expiry wins.
    """
    shared_token = str(shared.get(CONF_TOKEN, "") or "")
    if not shared_token:
        return False

    current_token = str(current.get(CONF_TOKEN, "") or "")
    if not current_token:
        return True

    if shared_token == current_token:
        return False

    shared_expiry = credentials_expiry(shared)
    current_expiry = credentials_expiry(current)

    # An expiry that cannot be read is treated as the oldest possible
    # one, so a readable expiry always wins.
    return shared_expiry > current_expiry


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


def stored_credentials_are_newer(
    stored: dict[str, Any], current: dict[str, Any]
) -> bool:
    """Say whether the stored credentials should replace the current ones.

    The store holds the credentials of the last refresh. A token the user
    just pasted into the options flow is newer than that, so the store
    must not throw it away. Otherwise the integration keeps running on
    the old, dead token and the new one never takes effect.
    """
    stored_token = str(stored.get(CONF_TOKEN, "") or "")
    if not stored_token:
        return False

    current_token = str(current.get(CONF_TOKEN, "") or "")
    if not current_token:
        return True

    if stored_token == current_token:
        return False

    return credentials_expiry(stored) > credentials_expiry(current)


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
        CONF_X_DEVICE: credentials.get(CONF_X_DEVICE, ""),
        CONF_LOCALCODE: credentials.get(CONF_LOCALCODE, "tw"),
        CONF_TOKEN_TIMEOUT: credentials.get(CONF_TOKEN_TIMEOUT, 0),
    }
    await store.async_save(data)
    _LOGGER.debug("已寫回 fp_tracker 憑證")


async def async_load_shared_credentials(
    hass: HomeAssistant, account: str | None = None
) -> dict[str, Any] | None:
    """Return the credentials of the shared foodpanda credential file.

    The file is keyed by the foodpanda account name. When the account
    is not known, the only account in the file is used, which is the
    normal case for a single foodpanda user.
    """
    data = await _shared_store(hass).async_load()
    if not isinstance(data, dict) or not data:
        return None

    entry: dict[str, Any] | None = None
    if account and isinstance(data.get(account), dict):
        entry = data[account]
    else:
        candidates = {
            key: value
            for key, value in data.items()
            if isinstance(value, dict) and value.get(CONF_TOKEN)
        }
        if len(candidates) == 1:
            entry = next(iter(candidates.values()))
        elif account is None and candidates:
            # More than one account: pick the one whose refresh token
            # is still usable, the others are stale copies.
            for value in candidates.values():
                if value.get(CONF_REFRESH_TOKEN):
                    entry = value
                    break

    if entry is None:
        return None
    return _normalize(entry)


async def async_save_shared_credentials(
    hass: HomeAssistant, credentials: dict[str, Any], account: str | None = None
) -> bool:
    """Write the credentials back into the shared credential file.

    The account key of the existing file is reused, so a refresh by
    this integration replaces the old copy instead of adding a second
    one.
    """
    store = _shared_store(hass)
    data = await store.async_load()
    if not isinstance(data, dict):
        data = {}

    key = account
    if key is None:
        candidates = [k for k, v in data.items() if isinstance(v, dict)]
        if len(candidates) == 1:
            key = candidates[0]
        else:
            existing = next(
                (v for v in data.values() if isinstance(v, dict) and v.get(CONF_TOKEN)),
                None,
            )
            if existing is None:
                return False
            key = next(k for k, v in data.items() if v is existing)
    if key is None:
        return False

    current = data.get(key)
    current = dict(current) if isinstance(current, dict) else {}
    current.update(
        {
            CONF_TOKEN: credentials.get(CONF_TOKEN, ""),
            CONF_DEVICE_TOKEN: credentials.get(CONF_DEVICE_TOKEN, ""),
            CONF_REFRESH_TOKEN: credentials.get(CONF_REFRESH_TOKEN, ""),
            CONF_TOKEN_TIMEOUT: credentials.get(CONF_TOKEN_TIMEOUT, 0),
            "clientid": credentials.get(CONF_CLIENT_ID, ""),
            "sessionid": credentials.get(CONF_SESSION_ID, ""),
            "usersouce": credentials.get(CONF_USER_SOURCE, ""),
            CONF_X_DEVICE: credentials.get(CONF_X_DEVICE, "") or DEFAULT_X_DEVICE,
            CONF_LOCALCODE: credentials.get(CONF_LOCALCODE, "tw"),
        }
    )
    data[key] = current
    await store.async_save(data)
    _LOGGER.debug("已寫回共用 foodpanda 憑證")
    return True

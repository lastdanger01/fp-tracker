"""Offline tests for the credential store override.

This covers the bug found on 2026-11-16: the credentials of the last
refresh were kept in their own store, and that store overwrote a token
that the user had just pasted into the options flow. The integration
then kept running on the old, dead token.

The rule is the same as for the shared credentials file: only the
credentials with the newer expiry replace the current ones.
"""

from __future__ import annotations

import importlib
import pathlib
import sys
import types

PKG_DIR = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "fp_tracker"

pkg = types.ModuleType("fpt")
pkg.__path__ = [str(PKG_DIR)]
pkg.__package__ = "fpt"
sys.modules["fpt"] = pkg

# credentials.py imports homeassistant, which is not installed here, so
# the store helpers are loaded with a stub instead.
ha = types.ModuleType("homeassistant")
ha.__path__ = []
helpers = types.ModuleType("homeassistant.helpers")
helpers.__path__ = []
storage = types.ModuleType("homeassistant.helpers.storage")
core = types.ModuleType("homeassistant.core")


class HomeAssistant:  # noqa: D101
    """A stand-in for the real HomeAssistant object."""


core.HomeAssistant = HomeAssistant


class _Store:
    """A store that keeps its data in memory."""

    def __init__(self, hass, version, key, *args, **kwargs):
        self.key = key
        self.hass = hass

    async def async_load(self):
        return self.hass.data.get(self.key)

    async def async_save(self, data):
        self.hass.data[self.key] = data


storage.Store = _Store
sys.modules["homeassistant"] = ha
sys.modules["homeassistant.helpers"] = helpers
sys.modules["homeassistant.helpers.storage"] = storage
sys.modules["homeassistant.core"] = core

try:
    credentials = importlib.import_module("fpt.credentials")
except ModuleNotFoundError as err:  # pragma: no cover
    print(f"skip: {err}")
    raise SystemExit(0)

are_newer = credentials.stored_credentials_are_newer
expiry = credentials.credentials_expiry

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    """Compare one value and record the result."""
    if got == want:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label}: got={got!r} want={want!r}")
        FAILURES.append(label)


def token_with_expiry(expires: int) -> str:
    """Build a fake JWT that carries an expiry."""
    import base64
    import json

    def seg(data: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
        return raw.rstrip("=")

    header = seg({"alg": "RS256", "typ": "JWT"})
    payload = seg({"expires": expires})
    return f"{header}.{payload}.signature"


print("=== store 憑證 vs 目前憑證 ===")

NEW = 2_000_000_000
OLD = 1_000_000_000

# The store holds an older token than the one the user just pasted:
# the pasted one has to win, otherwise the dead token stays in use.
check(
    "舊的 store 不覆蓋新貼的 token",
    are_newer(
        {"token": token_with_expiry(OLD)},
        {"token": token_with_expiry(NEW)},
    ),
    False,
)

# The store holds a newer token than the config entry: the store wins,
# because that is the token of the last successful refresh.
check(
    "新的 store 覆蓋舊的 config entry",
    are_newer(
        {"token": token_with_expiry(NEW)},
        {"token": token_with_expiry(OLD)},
    ),
    True,
)

# The same token on both sides: nothing to replace.
check(
    "同一個 token 不重複覆蓋",
    are_newer(
        {"token": token_with_expiry(NEW)},
        {"token": token_with_expiry(NEW)},
    ),
    False,
)

# No token in the store at all.
check(
    "store 沒有 token 時不覆蓋",
    are_newer({}, {"token": token_with_expiry(NEW)}),
    False,
)

# No token in the config entry: the store is the only source.
check(
    "config entry 沒有 token 時採用 store",
    are_newer({"token": token_with_expiry(NEW)}, {}),
    True,
)

print()
print("=== 到期時間 ===")

check(
    "JWT 的 expires 有被讀到",
    expiry({"token": token_with_expiry(NEW)}),
    NEW,
)

check(
    "沒有 token 時到期時間是 0",
    expiry({}),
    0,
)

if FAILURES:
    print(f"\n❌ {len(FAILURES)} 個失敗")
    raise SystemExit(1)

print("\n✅ 全部通過")

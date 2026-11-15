"""Offline tests for the config flow of the fp_tracker integration.

The config flow used to report every failure as "the token is invalid
or expired". That sent people looking in the wrong place: a 401 is a
dead token, a 403 means the request was rejected (usually a missing
user source) and a connection error means neither of those.

The flow also does not catch a device token that was pasted into the
token field. Both are JWTs that look alike, and the API answers 401
for both, so it has to be recognised locally.
"""

from __future__ import annotations

import base64
import importlib
import json
import pathlib
import sys
import types

PKG_DIR = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "fp_tracker"

pkg = types.ModuleType("fpt")
pkg.__path__ = [str(PKG_DIR)]
pkg.__package__ = "fpt"
sys.modules["fpt"] = pkg

const = importlib.import_module("fpt.const")

# --------------------------------------------------- Home Assistant stubs
ha_core = types.ModuleType("homeassistant.core")
ha_core.HomeAssistant = object
ha_core.callback = lambda func: func
ha_root = types.ModuleType("homeassistant")
ha_root.__path__ = []
ha_root.core = ha_core
ha_config_entries = types.ModuleType("homeassistant.config_entries")


class _ConfigFlow:  # noqa: D101 - stub
    VERSION = 1

    def __init_subclass__(cls, **kwargs) -> None:
        """Accept the domain keyword the real base class takes."""
        super().__init_subclass__()


class _ConfigFlowResult(dict):  # noqa: D101 - stub
    pass


ha_config_entries.ConfigFlow = _ConfigFlow
ha_config_entries.ConfigFlowResult = _ConfigFlowResult
sys.modules.setdefault("homeassistant", ha_root)
sys.modules.setdefault("homeassistant.core", ha_core)
sys.modules.setdefault("homeassistant.config_entries", ha_config_entries)

config_flow = importlib.import_module("fpt.config_flow")

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    """Compare one value and record the result."""
    if got == want:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label}: got={got!r} want={want!r}")
        FAILURES.append(label)


def section(title: str) -> None:
    """Print a section header."""
    print(f"\n=== {title} ===")


def make_jwt(kid: str, payload: dict | None = None) -> str:
    """Return a fake JWT with the given key id."""

    def seg(data: dict) -> str:
        raw = json.dumps(data).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    header = seg({"alg": "RS256", "kid": kid})
    body = seg(payload or {"id": "x", "expires": 1794748191})
    return f"{header}.{body}.sig"


ACCESS_TOKEN = make_jwt("keymaker-volo-fp-tw")
DEVICE_TOKEN = make_jwt("keymaker-volo-device-fp-tw")

# --------------------------------------------------- the key id of a JWT
section("辨認 JWT 的種類")
check("access token 的 kid", config_flow._jwt_kid(ACCESS_TOKEN), "keymaker-volo-fp-tw")
check("device token 的 kid", config_flow._jwt_kid(DEVICE_TOKEN), "keymaker-volo-device-fp-tw")
check("不是 JWT 時回空字串", config_flow._jwt_kid("not-a-jwt"), "")
check("空字串回空字串", config_flow._jwt_kid(""), "")
check("壞掉的 base64 不會拋例外", config_flow._jwt_kid("!!!.???"), "")

# --------------------------------------------------- the user source
section("user_source 有預設值")
check("DEFAULT_USER_SOURCE", const.DEFAULT_USER_SOURCE, "volo")

schema = config_flow._schema({})
defaults = {str(k): k.default() for k in schema.schema if hasattr(k, "default")}
check("user_source 是必填", "user_source" in defaults, True)
check("user_source 預設是 volo", defaults.get("user_source"), "volo")

# --------------------------------------------------- the error mapping
section("錯誤訊息依 HTTP status 區分")


class _FakeApiError(Exception):
    """Stand in for the API error of the api module."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def install_api_error(status: int | None) -> None:
    """Make the api module raise the given error."""
    api_mod = types.ModuleType("fpt.api")

    class FoodpandaApiError(_FakeApiError):  # noqa: D101 - stub
        pass

    class FoodpandaClient:  # noqa: D101 - stub
        def __init__(self, *args, **kwargs):
            pass

        async def async_get_orders(self):
            raise FoodpandaApiError("boom", status=status)

    api_mod.FoodpandaApiError = FoodpandaApiError
    api_mod.FoodpandaClient = FoodpandaClient
    sys.modules["fpt.api"] = api_mod


async def run_flow() -> str | None:
    """Run the token check and return the error key."""
    return await config_flow._async_token_works(None, {"token": ACCESS_TOKEN})


import asyncio  # noqa: E402

install_api_error(401)
check(
    "401 → token_invalid",
    asyncio.run(run_flow()),
    "token_invalid",
)

install_api_error(403)
check(
    "403 → token_forbidden",
    asyncio.run(run_flow()),
    "token_forbidden",
)

install_api_error(500)
check(
    "其他狀態 → cannot_connect",
    asyncio.run(run_flow()),
    "cannot_connect",
)


def install_plain_exception() -> None:
    """Make the api module raise something that is not an API error."""
    api_mod = types.ModuleType("fpt.api")

    class FoodpandaApiError(_FakeApiError):  # noqa: D101 - stub
        pass

    class FoodpandaClient:  # noqa: D101 - stub
        def __init__(self, *args, **kwargs):
            pass

        async def async_get_orders(self):
            raise OSError("network down")

    api_mod.FoodpandaApiError = FoodpandaApiError
    api_mod.FoodpandaClient = FoodpandaClient
    sys.modules["fpt.api"] = api_mod


install_plain_exception()
check(
    "連線錯誤 → cannot_connect",
    asyncio.run(run_flow()),
    "cannot_connect",
)

print()
if FAILURES:
    print(f"❌ {len(FAILURES)} 項失敗：{FAILURES}")
    sys.exit(1)
print("✅ 全部通過")

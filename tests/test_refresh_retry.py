"""Offline tests for the refresh retry of the fp_tracker integration.

The bot protection in front of the foodpanda login API answers HTTP 403
with a captcha challenge at random. The same request that answers 200
the first time answers 403 the next time.

The refresh used to try two user agents once and then give up, so a
single captcha left the integration without a working token. It now
retries, and only a status the API itself produced ends the retrying.
"""

from __future__ import annotations

import asyncio
import importlib
import pathlib
import sys
import types

PKG_DIR = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "fp_tracker"

pkg = types.ModuleType("fpt")
pkg.__path__ = [str(PKG_DIR)]
pkg.__package__ = "fpt"
sys.modules["fpt"] = pkg

const = importlib.import_module("fpt.const")
api = importlib.import_module("fpt.api")

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


# --------------------------------------------------- a fake response
class _FakeResponse:
    """Stand in for a requests response."""

    def __init__(self, status_code: int) -> None:
        """Store the status code."""
        self.status_code = status_code
        self.headers = {}
        self.raw = types.SimpleNamespace(headers={})


class _FakeHass:
    """Run the executor job straight away."""

    async def async_add_executor_job(self, func, *args):
        """Call the function and return its result."""
        return func(*args)


# --------------------------------------------------- the retry itself
section("captcha 之後要重試")


def make_client(statuses):
    """Return a client whose refresh answers the given statuses in order."""
    client = api.FoodpandaClient(_FakeHass())
    client.localcode = "tw"
    client.token = "OLD"
    client.device_token = "DT"
    client.refresh_token = "RT"
    client.x_device = const.DEFAULT_X_DEVICE

    calls = []

    def fake_post(url, data=None, headers=None, verify=None, timeout=None):
        """Answer with the next status of the list."""
        calls.append(headers.get("User-Agent"))
        status = statuses[min(len(calls) - 1, len(statuses) - 1)]
        return _FakeResponse(status)

    api.requests.post = fake_post
    return client, calls


def run(statuses):
    """Run the refresh with the given statuses and report the outcome."""
    client, calls = make_client(statuses)
    # The delay would make the test slow, so it is removed.
    api.REFRESH_RETRY_DELAY = 0
    ok = asyncio.run(client.async_refresh_token())
    return ok, calls


# A captcha first, then success.
ok, calls = run([403, 200])
check("第一次 captcha 第二次成功 -> True", ok, True)
check("兩個 user agent 都試過", len(calls), 2)
check("先用 mobile UA", "iPhone" in calls[0], True)
check("再用 desktop UA", "Windows" in calls[1], True)

# Two captchas, then success.
ok, calls = run([403, 403, 200])
check("兩次 captcha 第三次成功 -> True", ok, True)
check("嘗試了三次", len(calls), 3)

# The mobile UA is blocked, the desktop one gets through.
ok, calls = run([403, 403, 403, 200])
check("三次 captcha 第四次成功 -> True", ok, True)

# The API says the refresh token is unknown: that is not retried.
ok, calls = run([400])
check("400 -> False", ok, False)
check("400 只試一次", len(calls), 1)

# Every attempt is blocked by the captcha.
ok, calls = run([403])
check("一直 captcha -> False", ok, False)
check("最多試 REFRESH_RETRY_ATTEMPTS 輪", len(calls), const.REFRESH_RETRY_ATTEMPTS * 2)

# A status the API itself produced ends the retrying right away.
ok, calls = run([500])
check("500 -> False", ok, False)
check("500 只試一次", len(calls), 1)

section("重試的參數")
check("REFRESH_RETRY_ATTEMPTS 是正數", const.REFRESH_RETRY_ATTEMPTS > 0, True)
check("REFRESH_RETRY_DELAY 是正數", const.REFRESH_RETRY_DELAY > 0, True)

print()
if FAILURES:
    print(f"❌ {len(FAILURES)} 項失敗：{FAILURES}")
    sys.exit(1)
print("✅ 全部通過")

"""Thin async wrapper around sagemcom_api for the Giga Hub."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import Any

from aiohttp import ClientError
from sagemcom_api.client import SagemcomClient
from sagemcom_api.enums import EncryptionMethod
from sagemcom_api.exceptions import (
    AccessRestrictionException,
    AuthenticationException,
    BaseSagemcomException,
    LoginRetryErrorException,
    LoginTimeoutException,
    MaximumSessionCountException,
    UnauthorizedException,
)

from .const import SPEED_TEST_POLL_SECONDS, SPEED_TEST_TIMEOUT
from .parse import (
    FastSnapshot,
    SlowSnapshot,
    find_access_points,
    find_ip_interface_uid,
    parse_fast,
    parse_slow,
    parse_speed_test,
)


class GigaHubAuthError(Exception):
    """The hub rejected the username or password."""


class GigaHubConnectionError(Exception):
    """The hub could not be reached or refused the session."""


@dataclass(slots=True)
class HubIdentity:
    mac: str
    model: str | None
    product_class: str | None
    firmware: str | None
    ip_data_uid: int | None
    access_points: dict[int, str]


def _wrap(err: Exception) -> Exception:
    if isinstance(err, (AuthenticationException, UnauthorizedException)):
        return GigaHubAuthError(str(err))
    return GigaHubConnectionError(f"{type(err).__name__}: {err}")


# The library re-raises socket failures as the built-in ConnectionError, so catch OSError too.
CONNECTION_ERRORS = (
    ClientError,
    OSError,
    TimeoutError,
    asyncio.TimeoutError,
    BaseSagemcomException,
    LoginRetryErrorException,
    LoginTimeoutException,
    MaximumSessionCountException,
    AccessRestrictionException,
)


class GigaHubApi:
    """One logged-in session to the hub.

    The hub numbers requests within a session, so calls are serialised with a lock
    instead of letting the two polling loops interleave.
    """

    def __init__(self, host: str, username: str, password: str) -> None:
        self._client = SagemcomClient(host, username, password, EncryptionMethod.SHA512)
        self._lock = asyncio.Lock()
        self._logged_in = False
        self.identity: HubIdentity | None = None

    async def close(self) -> None:
        async with self._lock:
            if self._logged_in:
                with contextlib.suppress(Exception):  # best effort on shutdown
                    await self._client.logout()
                self._logged_in = False
            await self._client.close()

    async def _call(self, coro_factory):
        async with self._lock:
            try:
                return await coro_factory()
            except CONNECTION_ERRORS as err:
                raise _wrap(err) from err

    async def connect(self) -> HubIdentity:
        """Log in and read what the integration needs to know once."""
        await self._call(self._client.login)
        self._logged_in = True
        raw = await self._call(
            lambda: self._client.get_values_by_xpaths(
                {
                    "mac": "Device/DeviceInfo/MACAddress",
                    "model": "Device/DeviceInfo/ModelName",
                    "product_class": "Device/DeviceInfo/ProductClass",
                    "firmware": "Device/DeviceInfo/SoftwareVersion",
                    "ip_interfaces": "Device/IP/Interfaces",
                    "ssids": "Device/WiFi/SSIDs",
                },
                ignore_unknown_paths=True,
            )
        )
        mac = raw.get("mac")
        if not isinstance(mac, str) or not mac:
            raise GigaHubConnectionError("The hub did not report a MAC address")
        self.identity = HubIdentity(
            mac=mac.upper(),
            model=raw.get("model") if isinstance(raw.get("model"), str) else None,
            product_class=raw.get("product_class") if isinstance(raw.get("product_class"), str) else None,
            firmware=raw.get("firmware") if isinstance(raw.get("firmware"), str) else None,
            ip_data_uid=find_ip_interface_uid(raw.get("ip_interfaces")),
            access_points=find_access_points(raw.get("ssids")),
        )
        return self.identity

    def _fast_xpaths(self) -> dict[str, str]:
        xpaths = {
            "wan": "Device/PPP/Interfaces/Interface[@uid='1']",
            "optical": "Device/Optical/Interfaces/Interface[@uid='1']",
            "radios": "Device/WiFi/Radios",
            "ssids": "Device/WiFi/SSIDs",
            "ports": "Device/Ethernet/Interfaces",
            "memory": "Device/DeviceInfo/MemoryStatus",
            "uptime": "Device/DeviceInfo/UpTime",
        }
        if self.identity and self.identity.ip_data_uid is not None:
            xpaths["public_ip"] = (
                f"Device/IP/Interfaces/Interface[@uid='{self.identity.ip_data_uid}']/IPv4Addresses"
            )
        return xpaths

    async def fetch_fast(self) -> tuple[FastSnapshot, float]:
        """Return the snapshot and the monotonic time it was taken, for rate maths."""
        raw = await self._call(
            lambda: self._client.get_values_by_xpaths(self._fast_xpaths(), ignore_unknown_paths=True)
        )
        return parse_fast(raw), time.monotonic()

    async def fetch_slow(self) -> SlowSnapshot:
        access_points = self.identity.access_points if self.identity else {}
        xpaths = {
            "hosts": "Device/Hosts/Hosts",
            "speed_test": "Device/IP/Diagnostics/SpeedTest",
            "firmware": "Device/DeviceInfo/SoftwareVersion",
            "reboot_count": "Device/DeviceInfo/RebootCount",
        }
        for uid in access_points:
            xpaths[f"ap_{uid}"] = f"Device/WiFi/AccessPoints/AccessPoint[@uid='{uid}']/AssociatedDevices"
        raw = await self._call(lambda: self._client.get_values_by_xpaths(xpaths, ignore_unknown_paths=True))
        return parse_slow(raw, access_points)

    async def raw(self, xpaths: dict[str, str]) -> dict[str, Any]:
        """Read arbitrary paths (used by diagnostics)."""
        return await self._call(lambda: self._client.get_values_by_xpaths(xpaths, ignore_unknown_paths=True))

    async def run_speed_test(self, block_traffic: bool) -> None:
        """Start the hub's built-in speed test and wait until a new result lands."""
        before, _ = parse_speed_test((await self.raw({"st": "Device/IP/Diagnostics/SpeedTest"})).get("st"))
        previous = before.timestamp if before else 0
        await self._call(lambda: self._client.run_speed_test(block_traffic=block_traffic))
        deadline = time.monotonic() + SPEED_TEST_TIMEOUT.total_seconds()
        while time.monotonic() < deadline:
            await asyncio.sleep(SPEED_TEST_POLL_SECONDS)
            result, state = parse_speed_test(
                (await self.raw({"st": "Device/IP/Diagnostics/SpeedTest"})).get("st")
            )
            if result and result.timestamp > previous:
                return
            if state and "ERROR" in str(state).upper():
                raise GigaHubConnectionError(f"Speed test failed: {state}")
        raise GigaHubConnectionError("Speed test did not finish within 3 minutes")

    async def reboot(self) -> None:
        await self._call(self._client.reboot)

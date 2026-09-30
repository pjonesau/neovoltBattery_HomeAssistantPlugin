"""Stateless transport for battery + grid feed-in settings.

Endpoints, payload construction, retries, and 6069 re-login live here.
Caching, pending-diff bookkeeping, and validation live in SettingsManager.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

from ..models import (
    VARIANT_CHARGE_CONFIG,
    VARIANT_CYCLE_STRATEGY,
    CycleStrategy,
    GridFeedInSettings,
)

if TYPE_CHECKING:
    from .neovolt_client import NeovoltClient

_LOGGER = logging.getLogger(__name__)

# Retry parameters — applied uniformly to GET/PUT/POST
DEFAULT_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0

# getCycleStrategy answers this on systems that only have the legacy API
CODE_SYSTEM_NOT_FOUND = 6030


async def _with_relogin(api_client: "NeovoltClient", op):
    """Run op (a no-arg async callable returning a response dict),
    re-login once and retry if the server returns session-expiry code 6069."""
    response = await op()
    if response and response.get("code") == 6069:
        _LOGGER.warning("Session expired (code 6069), re-logging in")
        if await api_client.async_login():
            response = await op()
    return response


class BatterySettingsAPI:
    """Stateless transport for the battery charge/discharge schedule.

    Talks to one of two server APIs, chosen per system the same way the
    portal does (see ``models.VARIANT_*``). The detected variant is
    remembered on the NeovoltClient so detection runs once per process.
    """

    VARIANT_ENDPOINT = "api/iterate/sysSet/hasNewVersionCharge?id="
    ENDPOINTS = {
        VARIANT_CYCLE_STRATEGY: (
            "api/iterate/sysSet/getCycleStrategy?id=",
            "api/iterate/sysSet/setCycleStrategy",
        ),
        VARIANT_CHARGE_CONFIG: (
            "api/iterate/sysSet/getChargeConfigInfo?id=",
            "api/iterate/sysSet/updateChargeConfigInfo",
        ),
    }

    def __init__(self, api_client: "NeovoltClient") -> None:
        self._client = api_client

    def _host_id(self) -> str:
        """Return the configured host systemId, or empty for single-inverter installs.

        The Byte-Watt API tolerates ``id=`` (empty) for accounts with one
        inverter — confirmed against HAR captures. For multi-inverter
        accounts the behaviour is undefined; we warn ONCE per process so
        operators see the signal in the log without spamming it on every
        poll. The repair-issue flow surfaces the same prompt in the UI.
        """
        host_id = getattr(self._client, "host_system_id", "") or ""
        if not host_id and not getattr(self._client, "_warned_empty_host_id", False):
            _LOGGER.warning(
                "Battery settings requests are using an empty host_system_id. "
                "This is safe for single-inverter accounts but ambiguous for "
                "multi-inverter accounts — open Settings → Devices & Services "
                "→ Byte-Watt → Reconfigure to pick the Host inverter explicitly."
            )
            self._client._warned_empty_host_id = True
        return host_id

    def _set_variant(self, variant: str, reason: str) -> str:
        if getattr(self._client, "settings_api_variant", None) != variant:
            _LOGGER.info("Battery settings API: using %s (%s)", variant, reason)
        self._client.settings_api_variant = variant
        return variant

    async def _variant(self) -> str:
        """Return the settings API variant for the host, detecting it once.

        If the portal's own flag can't be read, fall back to cycle-strategy
        without caching, so the next call tries detection again. A 6030
        from getCycleStrategy still switches to charge-config (see fetch).
        """
        known = getattr(self._client, "settings_api_variant", None)
        if known:
            return known
        response = await _with_relogin(
            self._client,
            lambda: self._client._async_get(f"{self.VARIANT_ENDPOINT}{self._host_id()}"),
        )
        if response and response.get("code") == 200 and isinstance(response.get("data"), bool):
            variant = VARIANT_CYCLE_STRATEGY if response["data"] else VARIANT_CHARGE_CONFIG
            return self._set_variant(variant, f"hasNewVersionCharge={response['data']}")
        _LOGGER.debug("hasNewVersionCharge unavailable (%s); assuming cycle strategy", response)
        return VARIANT_CYCLE_STRATEGY

    async def _get(self, variant: str):
        endpoint = f"{self.ENDPOINTS[variant][0]}{self._host_id()}"
        return await _with_relogin(self._client, lambda: self._client._async_get(endpoint))

    async def fetch_current_settings(
        self,
        max_retries: int = DEFAULT_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ) -> Optional[CycleStrategy]:
        variant = await self._variant()
        for attempt in range(max_retries):
            response = await self._get(variant)
            if (
                variant == VARIANT_CYCLE_STRATEGY
                and response
                and response.get("code") == CODE_SYSTEM_NOT_FOUND
            ):
                # Legacy-only system: getCycleStrategy doesn't know it
                variant = self._set_variant(VARIANT_CHARGE_CONFIG, "getCycleStrategy returned 6030")
                response = await self._get(variant)
            if response and response.get("code") == 200 and response.get("data"):
                if variant == VARIANT_CHARGE_CONFIG:
                    settings = CycleStrategy.from_charge_config(response["data"])
                else:
                    settings = CycleStrategy.from_api_response(response["data"])
                settings.host_system_id = self._host_id()
                _LOGGER.debug(
                    "Fetched battery settings via %s (id=%s): batUseCap=%.0f%%, "
                    "%d charge slot(s), %d discharge slot(s)",
                    variant,
                    self._host_id(),
                    settings.bat_use_cap,
                    len(settings.charge_slots),
                    len(settings.discharge_slots),
                )
                return settings
            _LOGGER.debug(
                "Battery settings fetch (%s) attempt %d/%d returned: %s",
                variant, attempt + 1, max_retries, response,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
        return None

    async def put(
        self,
        settings: CycleStrategy,
        max_retries: int = DEFAULT_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ) -> bool:
        # Write back to the API the settings were read from
        variant = settings.api_variant
        put_endpoint = self.ENDPOINTS[variant][1]
        if variant == VARIANT_CHARGE_CONFIG:
            payload = settings.to_charge_config_dict()
        else:
            payload = settings.to_dict()
            payload["id"] = self._host_id()
        for attempt in range(max_retries):
            response = await _with_relogin(
                self._client, lambda: self._client._async_put(put_endpoint, payload)
            )
            # updateChargeConfigInfo success is confirmed by code alone
            # (live-tested in upstream PR #32); setCycleStrategy also sends msg
            if response and response.get("code") == 200 and (
                variant == VARIANT_CHARGE_CONFIG or response.get("msg") == "Success"
            ):
                return True
            # Code 9007 is a transient server-side network exception; retry with backoff
            if response and response.get("code") == 9007:
                _LOGGER.warning(
                    "%s transient error 9007 (attempt %d/%d), retrying",
                    put_endpoint, attempt + 1, max_retries,
                )
            else:
                _LOGGER.debug(
                    "%s attempt %d/%d returned: %s",
                    put_endpoint, attempt + 1, max_retries, response,
                )
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
        return False


class GridFeedInSettingsAPI:
    """Stateless transport for getFeedStrategyList / v2/saveFeedStrategy.

    The portal saves through the v2 endpoint only; its payload carries the
    cutoff SOC per slot (see ``GridFeedInSettings.to_dict``).
    """

    GET_ENDPOINT = "api/iterate/sysSet/getFeedStrategyList?id="
    POST_ENDPOINT = "api/iterate/sysSet/v2/saveFeedStrategy"

    def __init__(self, api_client: "NeovoltClient") -> None:
        self._client = api_client

    def _host_id(self) -> str:
        return getattr(self._client, "host_system_id", "") or ""

    async def fetch_current_settings(
        self,
        max_retries: int = DEFAULT_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ) -> Optional[GridFeedInSettings]:
        host_id = self._host_id()
        if not host_id:
            _LOGGER.debug(
                "Skipping grid feed-in fetch — no host_system_id configured. "
                "Reconfigure the integration to select the Host inverter."
            )
            return None
        endpoint = f"{self.GET_ENDPOINT}{host_id}"
        for attempt in range(max_retries):
            response = await _with_relogin(self._client, lambda: self._client._async_get(endpoint))
            if response and response.get("code") == 200 and "data" in response:
                settings = GridFeedInSettings.from_api_response(response["data"], host_id)
                _LOGGER.debug(
                    "Fetched grid feed-in (id=%s): enabled=%s, %d slot(s)",
                    host_id, bool(settings.battery_en), len(settings.slots),
                )
                return settings
            _LOGGER.debug(
                "Grid feed-in fetch attempt %d/%d returned: %s",
                attempt + 1, max_retries, response,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
        return None

    async def post(
        self,
        settings: GridFeedInSettings,
        max_retries: int = DEFAULT_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ) -> bool:
        payload = settings.to_dict()
        payload["id"] = self._host_id()
        # The portal stamps every slot with the host's serial, including new ones
        host_sn = getattr(self._client, "host_sys_sn", "") or ""
        for dto in payload["feedStrategyDTOList"]:
            if host_sn and not dto.get("sysSn"):
                dto["sysSn"] = host_sn
        for attempt in range(max_retries):
            response = await _with_relogin(
                self._client, lambda: self._client._async_post(self.POST_ENDPOINT, payload)
            )
            if response and response.get("code") == 200:
                return True
            if response and response.get("code") == 9007:
                _LOGGER.warning(
                    "v2/saveFeedStrategy transient error 9007 (attempt %d/%d), retrying",
                    attempt + 1, max_retries,
                )
            else:
                _LOGGER.debug(
                    "v2/saveFeedStrategy attempt %d/%d returned: %s",
                    attempt + 1, max_retries, response,
                )
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
        return False

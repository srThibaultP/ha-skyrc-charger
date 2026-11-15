"""Polling of one SkyRC charger, and the commands that share its connection."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import time
from typing import Any

from bleak.exc import BleakError

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CHANNELS_BY_MODEL,
    CONF_ADDRESS,
    CONF_MODEL,
    DEFAULT_PASSCODE,
    DOMAIN,
    MODEL_MC5000,
    OPTION_PASSCODE,
)
from .mc5000_client import Mc5000Client
from .models import ChargerClient, ChargerState

_LOGGER = logging.getLogger(__name__)

type SkyrcConfigEntry = ConfigEntry[SkyrcCoordinator]

CLIENTS: dict[str, Callable[[Any], ChargerClient]] = {
    MODEL_MC5000: Mc5000Client,
}

# The models whose own settings (beeps, backlight, ...) can be written.
MODELS_WITH_SETTINGS = {MODEL_MC5000}

POLL_INTERVAL = timedelta(seconds=10)
MAX_BACKOFF = timedelta(minutes=5)
# The charger drops its link now and then; a poll or two without an answer
# keeps showing the last values rather than flipping every entity to
# unavailable.
TOLERATED_FAILURES = 3
# With polling turned off (to leave the charger to the SkyRC app), a link
# opened for a command is closed again after this long.
IDLE_DISCONNECT = 60
CURVE_REFRESH_SECONDS = 30

# Slot statuses of a program in progress.
RUNNING_STATUSES = ("charging", "discharging", "resting")

BLE_ERRORS = (BleakError, TimeoutError, OSError)


@dataclass(slots=True)
class CurveData:
    """The voltage curve of one slot, as last read from the charger."""

    samples_mv: list[int]
    interval_seconds: int | None
    checksum_ok: bool | None
    fetched_at: datetime


class SkyrcCoordinator(DataUpdateCoordinator[ChargerState]):
    """Owns the BLE link to one charger."""

    config_entry: SkyrcConfigEntry

    def __init__(self, hass: HomeAssistant, entry: SkyrcConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=POLL_INTERVAL,
        )
        self.address: str = str(entry.data[CONF_ADDRESS]).upper()
        self.model: str = entry.data.get(CONF_MODEL, MODEL_MC5000)
        self.channel_count: int = CHANNELS_BY_MODEL[self.model]

        #: What the user says is in each slot (0-based), set by the selects.
        self.expected_chemistry: dict[int, str] = {}
        self.curves: dict[int, CurveData] = {}
        self.auto_fetch_curves = False

        self._client: ChargerClient | None = None
        self._lock = asyncio.Lock()
        self._failures = 0
        self._last_curve_fetch = 0.0
        self._unsub_idle: CALLBACK_TYPE | None = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    @property
    def passcode(self) -> str:
        return str(self.config_entry.options.get(OPTION_PASSCODE, DEFAULT_PASSCODE))

    @property
    def supports_settings(self) -> bool:
        return self.model in MODELS_WITH_SETTINGS

    def _unreachable(self) -> UpdateFailed:
        """Explain why no connectable route to the charger exists."""
        heard = bluetooth.async_last_service_info(self.hass, self.address, connectable=False)
        if heard is None:
            return UpdateFailed(f"No Bluetooth adapter or proxy can see {self.address}")
        return UpdateFailed(
            f"{self.address} is only heard by the passive Bluetooth scanner {heard.source} "
            f"(RSSI {heard.rssi}), which cannot connect; bring an active Bluetooth proxy "
            "or adapter within range"
        )

    async def _async_connected_client(self) -> ChargerClient:
        """Return a client with an open, unlocked link. Hold the lock."""
        device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
        if device is None:
            raise self._unreachable()

        if self._client is None:
            self._client = CLIENTS[self.model](device)
            self._client.set_passcode(self.passcode)
        else:
            # A stale BLEDevice is the usual cause of reconnect loops.
            self._client.set_ble_device(device)

        if not self._client.is_connected:
            await self._client.connect()

        if getattr(self._client, "unlocked", None) is False:
            raise ConfigEntryAuthFailed("The charger refused the passcode")

        return self._client

    async def _async_drop_client(self) -> None:
        """Close the link; the charger takes one client at a time."""
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except BLE_ERRORS as err:
                _LOGGER.debug("Disconnecting from %s failed: %r", self.address, err)

    async def async_disconnect(self) -> None:
        async with self._lock:
            await self._async_drop_client()

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        self._cancel_idle()
        await self.async_disconnect()

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> ChargerState:
        async with self._lock:
            try:
                client = await self._async_connected_client()
                state = await client.async_update()
                if self.auto_fetch_curves:
                    await self._async_refresh_curves(client, state)
            except ConfigEntryAuthFailed:
                await self._async_drop_client()
                raise
            except (*BLE_ERRORS, UpdateFailed) as err:
                await self._async_drop_client()
                return self._tolerate(err)

        self._failures = 0
        self.update_interval = POLL_INTERVAL
        return state

    def _tolerate(self, err: Exception) -> ChargerState:
        """Keep the last values through a short outage, then give up."""
        self._failures += 1
        if self._failures <= TOLERATED_FAILURES and self.data is not None:
            _LOGGER.debug("Poll of %s failed (%d): %r", self.address, self._failures, err)
            return self.data

        excess = min(self._failures - TOLERATED_FAILURES, 16)
        self.update_interval = min(POLL_INTERVAL * 2**excess, MAX_BACKOFF)
        if isinstance(err, UpdateFailed):
            raise err
        raise UpdateFailed(f"Cannot talk to {self.address}: {err!r}") from err

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def _async_command[T](self, command: Callable[[ChargerClient], Awaitable[T]]) -> T:
        """Run one command on the shared link."""
        async with self._lock:
            try:
                client = await self._async_connected_client()
                result = await command(client)
            except ConfigEntryAuthFailed as err:
                await self._async_drop_client()
                raise HomeAssistantError(
                    "The charger refused the passcode; change it in the integration's options"
                ) from err
            except UpdateFailed as err:
                raise HomeAssistantError(str(err)) from err
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err
            except BLE_ERRORS as err:
                await self._async_drop_client()
                raise HomeAssistantError(f"Cannot talk to {self.address}: {err!r}") from err

        self._arm_idle_disconnect()
        return result

    def running_slots(self, slots: Any) -> list[int]:
        """Which of these slots (numbered from 1) have a program running.

        The charger acknowledges a new program for a running slot but keeps
        the old one going, so a start has to be refused up front.
        """
        channels = self.data.channels if self.data else []
        return [
            slot
            for slot in slots
            if 0 < slot <= len(channels) and channels[slot - 1].status in RUNNING_STATUSES
        ]

    def _refuse_running(self, slots: Any) -> None:
        running = self.running_slots(slots)
        if running:
            names = ", ".join(str(slot) for slot in running)
            raise HomeAssistantError(
                f"Slot {names} already runs a program; stop it first, the charger would "
                "ignore a new program"
            )

    def _program_for(self, slot: int, overrides: dict[str, Any]) -> dict[str, Any]:
        program = {key: value for key, value in overrides.items() if value is not None}
        if "chemistry" not in program:
            expected = self.expected_chemistry.get(slot - 1, "any")
            if expected != "any":
                program["chemistry"] = expected
        return program

    async def async_start_slot(self, slot: int, **overrides: Any) -> None:
        await self.async_request_refresh()
        self._refuse_running([slot])
        program = self._program_for(slot, overrides)
        _LOGGER.info("Starting slot %s of %s with %s", slot, self.address, program)
        await self._async_command(lambda client: client.start_channel(slot - 1, **program))
        await self.async_request_refresh()

    async def async_start_all(self, **overrides: Any) -> None:
        await self.async_request_refresh()
        self._refuse_running(range(1, self.channel_count + 1))
        per_channel = {
            slot - 1: self._program_for(slot, overrides)
            for slot in range(1, self.channel_count + 1)
        }
        _LOGGER.info("Starting every slot of %s with %s", self.address, per_channel)
        await self._async_command(lambda client: client.start_all(per_channel=per_channel))
        await self.async_request_refresh()

    async def async_stop_slot(self, slot: int) -> None:
        await self._async_command(lambda client: client.stop_channel(slot - 1))
        await self.async_request_refresh()

    async def async_stop_all(self) -> None:
        await self._async_command(lambda client: client.stop_all())
        await self.async_request_refresh()

    async def async_set_settings(self, **changes: Any) -> None:
        await self._async_command(lambda client: client.async_set_settings(**changes))
        self.async_update_listeners()

    # ------------------------------------------------------------------
    # Voltage curves
    # ------------------------------------------------------------------

    async def async_fetch_curve(self, slot: int) -> CurveData:
        curve = await self._async_command(lambda client: client.async_get_voltage_curve(slot - 1))
        return self._store_curve(slot - 1, curve)

    def _store_curve(self, index: int, curve: Any) -> CurveData:
        data = CurveData(
            samples_mv=list(curve.samples_mv),
            interval_seconds=curve.interval_seconds,
            checksum_ok=curve.checksum_ok,
            fetched_at=datetime.now().astimezone(),
        )
        self.curves[index] = data
        self.async_update_listeners()
        return data

    async def _async_refresh_curves(self, client: ChargerClient, state: ChargerState) -> None:
        """Read the curve of every working slot, at most every 30 seconds."""
        now = time.monotonic()
        if now - self._last_curve_fetch < CURVE_REFRESH_SECONDS:
            return
        working = [
            channel.index for channel in state.channels if channel.status in RUNNING_STATUSES
        ]
        if not working:
            return
        self._last_curve_fetch = now
        for index in working:
            try:
                self._store_curve(index, await client.async_get_voltage_curve(index))
            except BLE_ERRORS as err:
                _LOGGER.debug("Reading the curve of slot %s failed: %r", index + 1, err)

    # ------------------------------------------------------------------
    # Leaving the charger to the SkyRC app
    # ------------------------------------------------------------------

    @callback
    def _cancel_idle(self) -> None:
        if self._unsub_idle is not None:
            self._unsub_idle()
            self._unsub_idle = None

    @callback
    def _arm_idle_disconnect(self) -> None:
        """With polling off, do not hold the charger after a command."""
        self._cancel_idle()
        if not self.config_entry.pref_disable_polling:
            return

        async def disconnect(_now: datetime) -> None:
            self._unsub_idle = None
            await self.async_disconnect()

        self._unsub_idle = async_call_later(self.hass, IDLE_DISCONNECT, disconnect)

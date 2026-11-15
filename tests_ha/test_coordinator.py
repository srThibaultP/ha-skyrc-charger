"""Outages, backoff, and leaving the charger to the SkyRC app."""

from __future__ import annotations

from datetime import timedelta

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.skyrc_charger.coordinator import (
    IDLE_DISCONNECT,
    MAX_BACKOFF,
    POLL_INTERVAL,
    TOLERATED_FAILURES,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import FakeClient, make_entry


async def _tick(hass: HomeAssistant, seconds: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


async def test_a_short_outage_keeps_the_last_values(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    client.fail = BleakError("link dropped")

    for _ in range(TOLERATED_FAILURES):
        await _tick(hass, POLL_INTERVAL.total_seconds() + 1)
        assert hass.states.get("sensor.skyrc_mc5000_slot_1_status").state == "empty"

    await _tick(hass, MAX_BACKOFF.total_seconds() + 1)
    assert hass.states.get("sensor.skyrc_mc5000_slot_1_status").state == "unavailable"


async def test_a_long_outage_backs_off_then_recovers(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    coordinator = entry.runtime_data
    client.fail = BleakError("gone")
    for _ in range(TOLERATED_FAILURES + 2):
        await _tick(hass, MAX_BACKOFF.total_seconds() + 1)

    assert coordinator.update_interval > POLL_INTERVAL
    assert coordinator.update_interval <= MAX_BACKOFF

    client.fail = None
    await _tick(hass, MAX_BACKOFF.total_seconds() + 1)

    assert coordinator.update_interval == POLL_INTERVAL
    assert hass.states.get("sensor.skyrc_mc5000_slot_1_status").state == "empty"


async def test_a_dropped_link_is_reopened(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    client.connected = False

    await _tick(hass, POLL_INTERVAL.total_seconds() + 1)

    assert client.connected
    assert len(client.called("connect")) == 2


async def test_commands_wait_for_the_poll_in_progress(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    """Commands and polls share one link; they must never interleave."""
    import asyncio

    order: list[str] = []
    original_update = client.async_update

    async def slow_update():
        order.append("poll start")
        await asyncio.sleep(0.05)
        order.append("poll end")
        return await original_update()

    async def stop_channel(channel: int) -> None:
        order.append("stop")

    client.async_update = slow_update  # type: ignore[method-assign]
    client.stop_channel = stop_channel  # type: ignore[method-assign]

    poll = hass.async_create_task(entry.runtime_data.async_refresh())
    await asyncio.sleep(0)
    await entry.runtime_data.async_stop_slot(1)
    await poll

    assert order[:3] == ["poll start", "poll end", "stop"]


async def test_with_polling_off_the_link_is_closed_after_a_command(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    config_entry = make_entry(pref_disable_polling=True)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        "button", "press", {"entity_id": "button.skyrc_mc5000_slot_1_stop"}, blocking=True
    )
    assert client.connected

    await _tick(hass, IDLE_DISCONNECT + 1)

    assert not client.connected


async def test_turning_polling_off_lets_go_of_the_charger(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    assert client.connected

    hass.config_entries.async_update_entry(entry, pref_disable_polling=True)
    await hass.async_block_till_done()

    assert not client.connected

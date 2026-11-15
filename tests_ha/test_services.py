"""The services, and the buttons that share their logic."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skyrc_charger.const import DOMAIN
from custom_components.skyrc_charger.models import ChannelData
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import FakeClient


async def _call(hass: HomeAssistant, service: str, **data: object) -> None:
    await hass.services.async_call(DOMAIN, service, data, blocking=True)


async def test_start_slot_writes_the_program_with_the_declared_chemistry(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.skyrc_mc5000_slot_2_expected_chemistry", "option": "nimh"},
        blocking=True,
    )

    await _call(hass, "start_slot", slot=2, current_ma=500, capacity_mah=2400)

    assert client.called("start_channel") == [
        (1, {"chemistry": "nimh", "current": 500, "capacity": 2400})
    ]


async def test_a_chemistry_in_the_call_wins_over_the_select(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.skyrc_mc5000_slot_1_expected_chemistry", "option": "nimh"},
        blocking=True,
    )

    await _call(hass, "start_slot", slot=1, chemistry="liion", mode="charge")

    assert client.called("start_channel") == [(0, {"chemistry": "liion", "mode": "charge"})]


async def test_a_slot_that_already_runs_is_refused(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    client.channels[0] = ChannelData(index=0, status="charging", voltage=1.3, current=0.5)

    with pytest.raises(HomeAssistantError, match="stop it first"):
        await _call(hass, "start_slot", slot=1, chemistry="nimh")

    assert client.called("start_channel") == []


async def test_start_all_refuses_when_any_slot_runs(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    client.channels[2] = ChannelData(index=2, status="resting")

    with pytest.raises(HomeAssistantError, match="Slot 3"):
        await _call(hass, "start_all", chemistry="nimh")

    assert client.called("start_all") == []


async def test_start_all_gives_each_slot_its_own_chemistry(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.skyrc_mc5000_slot_3_expected_chemistry", "option": "lifepo4"},
        blocking=True,
    )

    await _call(hass, "start_all", mode="charge")

    (per_channel,) = client.called("start_all")
    assert per_channel[2] == {"mode": "charge", "chemistry": "lifepo4"}
    assert per_channel[0] == {"mode": "charge"}


async def test_stopping(hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient) -> None:
    await _call(hass, "stop_slot", slot=3)
    await _call(hass, "stop_all")

    assert client.called("stop_channel") == [2]
    assert client.called("stop_all") == [None]


async def test_a_slot_the_charger_does_not_have(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    with pytest.raises(Exception, match="slot"):
        await _call(hass, "stop_slot", slot=5)


async def test_a_value_the_charger_would_not_accept_is_explained(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    async def refuse(channel: int, **kwargs: object) -> None:
        raise ValueError("target_mv must be between 4050 and 4250 mV for this chemistry")

    client.start_channel = refuse  # type: ignore[method-assign]

    with pytest.raises(HomeAssistantError, match="target_mv"):
        await _call(hass, "start_slot", slot=1, chemistry="liion")


async def test_fetching_a_curve(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    await _call(hass, "fetch_voltage_curve", slot=1)

    state = hass.states.get("sensor.skyrc_mc5000_slot_1_voltage_curve_points")
    assert state.state == "3"  # the zero is not a sample
    assert state.attributes["interval_seconds"] == 8
    assert state.attributes["samples_v"] == [1.24, 1.241, 1.25, 0.0]


async def test_no_charger_set_up(hass: HomeAssistant) -> None:
    from custom_components.skyrc_charger import async_setup

    assert await async_setup(hass, {})

    with pytest.raises(HomeAssistantError, match="No SkyRC charger"):
        await _call(hass, "refresh")


async def test_an_unknown_entry_id(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    with pytest.raises(HomeAssistantError, match="Unknown"):
        await _call(hass, "refresh", entry_id="nope")


async def test_the_buttons_do_what_the_services_do(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.skyrc_mc5000_slot_1_expected_chemistry", "option": "nimh"},
        blocking=True,
    )

    await hass.services.async_call(
        "button", "press", {"entity_id": "button.skyrc_mc5000_slot_1_start"}, blocking=True
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.skyrc_mc5000_slot_1_stop"}, blocking=True
    )

    assert client.called("start_channel") == [(0, {"chemistry": "nimh"})]
    assert client.called("stop_channel") == [0]

"""Diagnostics, and the Bluetooth matcher of the manifest."""

from __future__ import annotations

import json
from pathlib import Path

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant.components.bluetooth.match import ble_device_matches
from homeassistant.core import HomeAssistant

from .conftest import ADDRESS
from .test_config_flow import _service_info

MANIFEST = Path(__file__).parents[1] / "custom_components" / "skyrc_charger" / "manifest.json"


async def test_diagnostics_hide_the_address_and_the_passcode(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, entry: MockConfigEntry
) -> None:
    result = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert result["entry"]["data"]["address"] == "**REDACTED**"
    assert result["entry"]["options"]["passcode"] == "**REDACTED**"
    assert result["state"]["device"]["sw_version"] == "1.83"
    assert len(result["state"]["channels"]) == 4
    assert ADDRESS not in json.dumps(result)


def test_the_manifest_matches_the_charger_by_its_name() -> None:
    (matcher,) = json.loads(MANIFEST.read_text())["bluetooth"]
    domain_matcher = {"domain": "skyrc_charger", **matcher}

    assert ble_device_matches(domain_matcher, _service_info())
    assert ble_device_matches(domain_matcher, _service_info(name="#Charger-0001"))
    assert not ble_device_matches(domain_matcher, _service_info(name="SomethingElse"))

"""Import the driver without Bluetooth libraries installed.

The driver (`mc5000_client`) is pure Python apart from `bleak` and
`bleak_retry_connector`. Stub those two with the handful of names it touches,
then load its modules straight from disk. The integration itself, which needs
Home Assistant, is tested under `tests_ha` with the real thing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).parents[1]
PACKAGE = "custom_components.skyrc_charger"
PACKAGE_PATH = ROOT / "custom_components" / "skyrc_charger"


class FakeBleakClient:
    """Records what the drivers ask of a BLE connection."""

    def __init__(self, device=None, **kwargs) -> None:
        self.device = device
        self.is_connected = True
        self.notify_handler = None
        self.writes: list[bytes] = []

    async def start_notify(self, _char, handler) -> None:
        self.notify_handler = handler

    async def write_gatt_char(self, _char, data, response=False) -> None:
        self.writes.append(bytes(data))

    async def disconnect(self) -> None:
        self.is_connected = False


class BleakError(Exception):
    pass


#: Every establish_connection() call made through the stub.
establish_connection_calls: list[dict] = []


def _install_stubs() -> None:
    if PACKAGE in sys.modules:
        return

    bleak = types.ModuleType("bleak")
    bleak.BleakClient = FakeBleakClient
    bleak.BleakScanner = types.SimpleNamespace(discover=None)
    bleak_exc = types.ModuleType("bleak.exc")
    bleak_exc.BleakError = BleakError
    bleak.exc = bleak_exc

    async def establish_connection(client_class, device, name, **kwargs):
        establish_connection_calls.append(
            {"client_class": client_class, "device": device, "name": name, **kwargs}
        )
        return client_class(device)

    retry = types.ModuleType("bleak_retry_connector")
    retry.establish_connection = establish_connection

    modules = {
        "bleak": bleak,
        "bleak.exc": bleak_exc,
        "bleak_retry_connector": retry,
    }
    sys.modules.update(modules)

    if "custom_components" not in sys.modules:
        namespace = types.ModuleType("custom_components")
        namespace.__path__ = [str(ROOT / "custom_components")]
        sys.modules["custom_components"] = namespace

    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(PACKAGE_PATH)]
    sys.modules[PACKAGE] = package


def load(module_name: str):
    """Load one module of the integration, stubbing its imports first."""
    _install_stubs()

    full_name = f"{PACKAGE}.{module_name}"
    if full_name in sys.modules:
        return sys.modules[full_name]

    spec = importlib.util.spec_from_file_location(full_name, PACKAGE_PATH / f"{module_name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module

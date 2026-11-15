"""MC5000 driver - native Python implementation of the BLE protocol.

The wire format follows the official SkyRC app (2.94), which drives the
MC5000 with the same command set as the NC3000:

    0x0F | length | command | data... | checksum
    checksum = sum(command..data) mod 256, length = len(data) + 2

Commands used here:

    0x57  device info (model code, firmware/hardware version)
    0x74  verify the passcode shown on the charger's screen ("1234" from
          the factory); answers 1 when it matches
    0x65  firmware update check (sent with "no update" by the app)
    0x91  slot status, one slot per request (slot bitmask)
    0x94  write a slot program; the charger starts it right away
    0x96  read the system settings (beeps, backlight, sleep timer)
    0x98  read a slot's logged voltage curve, sent back in several pages
    0xFE  stop the slots in the given bitmask

An earlier community write-up treated 0x93 as start/stop. In the app 0x93
only reads back a slot's settings, and nothing ever sends it to start or
stop, so this driver no longer does either.
"""

from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection

from .const import (
    BLE_CHAR_UUID,
    BLE_SERVICE_UUID,
    DEFAULT_PASSCODE,
    MC5000_BLE_NAME_PATTERNS,
)
from .models import ChannelData, ChargerClient, ChargerState, DeviceData, VoltageCurve

_LOGGER = logging.getLogger(__name__)

BLE_NAME_PATTERNS = MC5000_BLE_NAME_PATTERNS

SERVICE_UUID = BLE_SERVICE_UUID
CHAR_UUID = BLE_CHAR_UUID

START_BYTE = 0x0F

CMD_GREETING = 0x06
CMD_FIRMWARE_CHECK = 0x65
CMD_INFO = 0x57
CMD_VERIFY_PASSWORD = 0x74
CMD_STATUS = 0x91
CMD_QUERY_SLOT_SETTINGS = 0x93
CMD_CONFIG = 0x94
CMD_QUERY_SYSTEM = 0x96
CMD_VOLTAGE_CURVE = 0x98
CMD_STOP = 0xFE

# Model code the app matches the 0x57 answer against ("100214" in ASCII).
MC5000_MODEL_CODE = b"100214"

NOTIFY_TIMEOUT = 5.0
INFO_TIMEOUT = 2.0

# Chemistry byte, identical in the 0x94 program and the 0x91 status.
CHEMISTRY_TO_BYTE = {
    "liion": 0x00,
    "liion_hv": 0x01,
    "lifepo4": 0x02,
    "nimh": 0x03,
    "nicd": 0x04,
    "eneloop": 0x05,
    "nizn": 0x06,
    "ram": 0x07,
    "lto": 0x08,
    "naion": 0x09,
}
BYTE_TO_CHEMISTRY = {v: k for k, v in CHEMISTRY_TO_BYTE.items()}

# The mode byte means something different for each chemistry family: 0x02
# is "discharge" for a lithium cell but "break-in" for a NiMH one. Sending
# the lithium numbering to a nickel cell starts the wrong program.
LITHIUM_MODES = {"charge": 0, "storage": 1, "discharge": 2, "cycle": 3}
NICKEL_MODES = {"charge": 0, "refresh": 1, "break_in": 2, "discharge": 3, "cycle": 4}
ZINC_MODES = {"charge": 0, "discharge": 1, "cycle": 2}

MODES_BY_CHEMISTRY = {
    "liion": LITHIUM_MODES,
    "liion_hv": LITHIUM_MODES,
    "lifepo4": LITHIUM_MODES,
    "lto": LITHIUM_MODES,
    "naion": LITHIUM_MODES,
    "nimh": NICKEL_MODES,
    "nicd": NICKEL_MODES,
    "eneloop": NICKEL_MODES,
    "nizn": ZINC_MODES,
    "ram": ZINC_MODES,
}

# target / cutoff voltage (mV) per chemistry, used to build a sane default
# 0x94 config when the caller doesn't override them.
DEFAULT_TARGET_MV = {
    "liion": 4200, "liion_hv": 4350, "lifepo4": 3650, "nimh": 1650,
    "nicd": 1650, "eneloop": 1650, "nizn": 1900, "ram": 1650,
    "lto": 2850, "naion": 4000,
}
DEFAULT_CUTOFF_MV = {
    "liion": 3200, "liion_hv": 3400, "lifepo4": 2900, "nimh": 900,
    "nicd": 900, "eneloop": 900, "nizn": 1100, "ram": 900,
    "lto": 1800, "naion": 2000,
}

# default "secondary" field (data[33-34]): storage voltage (mV) for
# lithium-class chemistries, else 110% of capacity (mAh) for nickel/alkaline
DEFAULT_SECONDARY_MV = {
    "liion": 3800, "liion_hv": 3900, "lifepo4": 3300, "lto": 2400, "naion": 3500,
}

# Byte 18 of the 0x91 answer, as the app labels it.
STATUS_BYTE_NAME = {
    0x00: "idle",
    0x01: "standby",
    0x02: "charging",
    0x03: "discharging",
    0x04: "resting",
    0x05: "done",
    0x06: "done",
}

# Byte 20 of the 0x91 answer; 0 means no error.
ERROR_BYTE_NAME = {
    0x01: "input_voltage_low",
    0x02: "input_voltage_high",
    0x03: "battery_disconnected",
    0x04: "capacity_limit",
    0x05: "time_limit",
    0x06: "internal_temperature_high",
    0x07: "check_failed",
    0x08: "resistance_too_high",
    0x09: "battery_disconnected",
    0x0A: "wrong_battery_type",
    0x0B: "overload",
    0x0C: "reversed_polarity",
    0x0D: "battery_full",
}

# 0x96 answer: volumes are off/low/medium/high, backlight is 1-3.
VOLUME_NAME = {0: "off", 1: "low", 2: "medium", 3: "high"}
END_NOTIFICATION_NAME = {0: "once", 1: "repeat"}
BACKLIGHT_NAME = {1: "low", 2: "medium", 3: "high"}

CHANNEL_BITMASK = [0x01, 0x02, 0x04, 0x08]  # index -> bitmask, 4 slots


def mode_byte(chemistry: str, mode: str) -> int:
    """Return the 0x94 mode byte for this chemistry, or raise ValueError."""
    modes = MODES_BY_CHEMISTRY.get(chemistry)
    if modes is None:
        raise ValueError(f"Unknown MC5000 chemistry {chemistry!r}")
    if mode not in modes:
        raise ValueError(
            f"Mode {mode!r} is not available for {chemistry}; "
            f"choose one of {', '.join(modes)}"
        )
    return modes[mode]


def passcode_bytes(passcode: str) -> bytes:
    """One byte per digit, as the 0x74 command wants them."""
    if len(passcode) != 4 or not passcode.isascii() or not passcode.isdigit():
        raise ValueError("The MC5000 passcode is four digits")
    return bytes(int(digit) for digit in passcode)


def mode_name(chemistry: str | None, value: int) -> str | None:
    modes = MODES_BY_CHEMISTRY.get(chemistry or "")
    if modes is None:
        return None
    for name, byte in modes.items():
        if byte == value:
            return name
    return f"unknown_{value}"


def _checksum(payload: bytes) -> int:
    """payload = command byte + data bytes (no start/length/checksum)."""
    return sum(payload) & 0xFF


def _build_packet(command: int, data: bytes = b"") -> bytes:
    body = bytes([command]) + data
    length = len(body) + 1  # +1 for checksum byte itself
    checksum = _checksum(body)
    return bytes([START_BYTE, length]) + body + bytes([checksum])


def _u16(value: int) -> bytes:
    return int(value).to_bytes(2, "big")


class Mc5000Client(ChargerClient):
    channel_count = 4
    supports_voltage_curves = True

    def __init__(self, ble_device) -> None:
        self._device = ble_device
        self._client: BleakClient | None = None
        self._notify_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._rx_buffer = bytearray()
        self._device_info = DeviceData(name="SkyRC MC5000", model="MC5000")
        self._connect_lock = asyncio.Lock()
        self._connected = False
        self._passcode = DEFAULT_PASSCODE
        #: None until the charger answered, then whether it took the passcode.
        self.unlocked: bool | None = None
        # last known config per channel, used so "start" can be called
        # without forcing the user to re-specify chemistry every time
        self._last_config: dict[int, dict] = {}

    @property
    def name(self) -> str:
        return getattr(self._device, "name", None) or self._device_info.name

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None and self._client.is_connected

    def set_ble_device(self, ble_device) -> None:
        """Adopt a freshly discovered BLEDevice for the next connect."""
        self._device = ble_device

    def set_passcode(self, passcode: str) -> None:
        passcode_bytes(passcode)  # validate early
        self._passcode = passcode

    async def _unlock(self) -> None:
        resp = await self._request(
            CMD_VERIFY_PASSWORD, b"\x00" + passcode_bytes(self._passcode)
        )
        if resp is None or len(resp) < 5:
            self.unlocked = None
            return

        unlocked = resp[4] == 1
        if not unlocked and self.unlocked is not False:
            # Once per outage of the right passcode, not once per reconnect.
            _LOGGER.warning(
                "MC5000: the charger refused the passcode. It still reports its "
                "status, but may ignore a start or a stop. Enter the passcode "
                "shown on the charger's screen in the integration's options"
            )
        self.unlocked = unlocked

    def _notify_handler(self, _characteristic, data: bytearray) -> None:
        """Reassemble frames before queueing them.

        Voltage curve pages are up to 208 bytes. Over a link that kept the
        default MTU they arrive in pieces, so a notification is not always
        a whole frame: cut the stream on the length byte instead.
        """
        buffer = self._rx_buffer
        buffer.extend(data)
        while buffer:
            if buffer[0] != START_BYTE:
                start = buffer.find(bytes([START_BYTE]))
                if start < 0:
                    buffer.clear()
                    return
                del buffer[:start]
            if len(buffer) < 2 or len(buffer) < buffer[1] + 2:
                return
            size = buffer[1] + 2
            self._notify_queue.put_nowait(bytes(buffer[:size]))
            del buffer[:size]

    def _disconnected_callback(self, _client: BleakClient) -> None:
        """Mark the link as down so the next poll reconnects.

        Logged at debug on purpose: the MC5000 drops the link on its own all
        the time and every drop is recovered on the following poll.
        """
        _LOGGER.debug("MC5000: disconnected from %s", getattr(self._device, "address", "?"))
        self._connected = False

    async def connect(self) -> None:
        """Open the BLE link and run the charger's mandatory init sequence.

        Goes through bleak_retry_connector.establish_connection() rather than
        BleakClient.connect(): it retries the transient BlueZ/proxy errors
        that a bare connect() surfaces as a hard failure on every single
        poll, reuses Home Assistant's cached GATT services, and cooperates
        with the connection slot allocator so we don't fight other
        integrations for the adapter.
        """
        async with self._connect_lock:
            if self.is_connected:
                return

            # A fresh queue per connection: notifications left over from a
            # dropped link would otherwise be read as answers to the first
            # commands of the new one.
            self._notify_queue = asyncio.Queue()
            self._rx_buffer = bytearray()

            client = await establish_connection(
                client_class=BleakClient,
                device=self._device,
                name=self.name,
                disconnected_callback=self._disconnected_callback,
                use_services_cache=True,
                ble_device_callback=lambda: self._device,
            )

            self._client = client
            try:
                await client.start_notify(CHAR_UUID, self._notify_handler)
                self._connected = True

                # Drain the unsolicited greeting (0x06) if/when it arrives.
                try:
                    await asyncio.wait_for(self._notify_queue.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass

                # Same opening as the app: identify the charger, unlock it
                # with its passcode, decline the firmware update.
                # 0xFE is deliberately absent: it is the stop command.
                device_info = await self._query_device_info()
                await self._unlock()
                await self._send(CMD_FIRMWARE_CHECK, b"\x00\x00")

                if device_info:
                    self._device_info = device_info
                await self._query_system_settings()
            except Exception:
                # Never leave a half-initialised client behind; the charger
                # only accepts one connection and would refuse the retry.
                self._connected = False
                self._client = None
                try:
                    await client.disconnect()
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("MC5000: cleanup disconnect failed: %r", err)
                raise

    async def disconnect(self) -> None:
        self._connected = False
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("MC5000 disconnect error: %r", err)

    async def _send(
        self,
        command: int,
        data: bytes = b"",
        expect_response: bool = True,
        timeout: float = NOTIFY_TIMEOUT,
    ) -> bytes | None:
        client = self._client
        if client is None:
            raise BleakError("MC5000 not connected")

        packet = _build_packet(command, data)
        # drain stale notifications
        while not self._notify_queue.empty():
            self._notify_queue.get_nowait()

        await client.write_gatt_char(CHAR_UUID, packet, response=False)

        if not expect_response:
            return None

        try:
            return await asyncio.wait_for(self._notify_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            # Happens on every poll while the charger is asleep; the
            # coordinator reports the resulting stale data once, not 360
            # times an hour.
            _LOGGER.debug("MC5000: no response to command 0x%02x", command)
            return None

    async def _request(
        self, command: int, data: bytes = b"", timeout: float = NOTIFY_TIMEOUT
    ) -> bytes | None:
        """Send a command and return the answer to *that* command.

        The charger volunteers a 0x06 greeting shortly after connecting and
        it can land after the first command went out; taking whatever
        notification comes next would shift every answer by one.
        """
        resp = await self._send(command, data, timeout=timeout)
        for _ in range(3):
            if resp is None or (len(resp) > 2 and resp[2] == command):
                return resp
            _LOGGER.debug("MC5000: skipping unrelated notification %s", resp.hex())
            try:
                resp = await asyncio.wait_for(self._notify_queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                return None
        return None

    @staticmethod
    def _parse_device_info(resp: bytes, address: str = "") -> DeviceData | None:
        """Decode the 0x57 answer the way the app does.

        The answer is 20 bytes, or 21 when the link negotiated a larger MTU
        (everything then sits one byte further). Six bytes of model code,
        then a block whose bytes 11-12 are the firmware version.
        """
        if resp is None or len(resp) not in (20, 21):
            return None

        shift = len(resp) - 20
        model_code = resp[3 + shift : 9 + shift]
        block = resp[3 + shift : 19 + shift].hex()
        firmware = f"{resp[14 + shift]}.{resp[15 + shift]:02d}"

        if model_code != MC5000_MODEL_CODE:
            _LOGGER.debug("MC5000: unexpected model code %s", model_code.hex())

        return DeviceData(
            name="SkyRC MC5000",
            address=address,
            manufacturer="SkyRC",
            model="MC5000",
            sw_version=firmware,
            hw_version=block[27:28] or None,
        )

    async def _query_device_info(self) -> DeviceData | None:
        addr = getattr(self._device, "address", "") or ""
        # macOS hands out a UUID instead of a MAC; the charger answers
        # either way, so fall back to zeros rather than failing to parse.
        try:
            mac = bytes.fromhex(addr.replace(":", ""))
        except ValueError:
            mac = b""
        if len(mac) != 6:
            mac = bytes(6)

        # The charger drops whatever it is sent first after a connection,
        # so ask again when the first request goes unanswered.
        request = bytes([0x00]) + mac[::-1] + bytes(10)
        resp = None
        for _ in range(3):
            resp = await self._request(CMD_INFO, request, timeout=INFO_TIMEOUT)
            if resp is not None:
                break
        try:
            return self._parse_device_info(resp, addr)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("MC5000: could not parse device info: %r", err)
            return None

    @staticmethod
    def _apply_system_settings(device: DeviceData, resp: bytes | None) -> None:
        """Decode the 0x96 answer into the device-level settings."""
        if resp is None or len(resp) < 10:
            return

        device.beep_volume = VOLUME_NAME.get(resp[4], f"unknown_{resp[4]}")
        device.notification_volume = VOLUME_NAME.get(resp[5], f"unknown_{resp[5]}")
        device.end_notification = END_NOTIFICATION_NAME.get(resp[6], f"unknown_{resp[6]}")
        # The app shows 0 as the lowest step too.
        device.backlight = BACKLIGHT_NAME.get(resp[7] or 1, f"unknown_{resp[7]}")
        device.warning_tone = resp[8] != 0
        # Older firmware answers without the sleep timer; the last byte is
        # the checksum, never a setting.
        device.sleep_timer_minutes = resp[9] if len(resp) >= 11 else None

    async def _query_system_settings(self) -> None:
        resp = await self._request(CMD_QUERY_SYSTEM, b"\x04")
        self._apply_system_settings(self._device_info, resp)

    @staticmethod
    def _parse_channel(index: int, packet: bytes) -> ChannelData:
        """Decode one 0x91 answer (the full packet, start byte included).

        Offsets, as the app reads them:
          3 slot bitmask   4-5 current (mA)    6-7 voltage (mV)
          8-9 temperature  10-11 capacity (mAh)
          12-15 elapsed (s)  16-17 resistance (mOhm)
          18 status  19 mode  20 error  21 chemistry
        Firmware that answers with fewer than 22 bytes uses a 16-bit
        elapsed time and has no chemistry byte.
        """
        if len(packet) < 18:
            return ChannelData(index=index, status="unavailable")

        current_ma = int.from_bytes(packet[4:6], "big")
        voltage_mv = int.from_bytes(packet[6:8], "big")
        temperature = int.from_bytes(packet[8:10], "big")
        capacity = int.from_bytes(packet[10:12], "big")

        chem_byte = None
        error_byte = 0
        if len(packet) >= 22:
            elapsed = int.from_bytes(packet[12:16], "big")
            resistance = int.from_bytes(packet[16:18], "big")
            status_byte = packet[18]
            mode_value = packet[19]
            error_byte = packet[20]
            chem_byte = packet[21]
        else:
            elapsed = int.from_bytes(packet[12:14], "big")
            resistance = int.from_bytes(packet[14:16], "big")
            status_byte = packet[16]
            mode_value = packet[17]
            if len(packet) == 20:
                error_byte = packet[18]

        chemistry = (
            BYTE_TO_CHEMISTRY.get(chem_byte, f"unknown_0x{chem_byte:02x}")
            if chem_byte is not None
            else None
        )
        error = (
            ERROR_BYTE_NAME.get(error_byte, f"unknown_{error_byte}") if error_byte else None
        )

        # Below 5 mV the app considers the slot empty.
        has_cell = voltage_mv >= 5
        running = status_byte >= 2

        if error is not None:
            status = "error"
        elif not running and not has_cell:
            status = "empty"
        else:
            status = STATUS_BYTE_NAME.get(status_byte, f"unknown_{status_byte:02x}")

        if not has_cell and not running:
            # Nothing in the slot: the chemistry and mode bytes still hold
            # the previous program, which says nothing about this state.
            return ChannelData(
                index=index, status=status, voltage=0.0, current=0.0, error=error
            )

        return ChannelData(
            index=index,
            status=status,
            # While idle these two still describe the previous program.
            chemistry=chemistry if running else None,
            mode=mode_name(chemistry, mode_value) if running else None,
            voltage=round(voltage_mv / 1000, 3),
            current=round(current_ma / 1000, 3) if running else 0.0,
            capacity=capacity if running else None,
            temperature=temperature if running and temperature else None,
            resistance=resistance if running else None,
            time=elapsed if running else None,
            error=error,
        )

    async def async_update(self) -> ChargerState:
        if not self.is_connected:
            raise BleakError("MC5000 not connected")

        channels: list[ChannelData] = []
        for index, bitmask in enumerate(CHANNEL_BITMASK):
            resp = await self._request(CMD_STATUS, bytes([bitmask]))
            if resp is None or len(resp) < 4:
                channels.append(ChannelData(index=index, status="unavailable"))
                continue
            channels.append(self._parse_channel(index, resp))

        return ChargerState(device=self._device_info, channels=channels)

    @staticmethod
    def _parse_curve_page(page: bytes) -> tuple[int, int, int, list[int], bool]:
        """Split one 0x98 page into (total, page, interval, samples, checksum_ok).

        Layout: 0x0F len 0x98 slot total page interval, then 16-bit
        little-endian millivolt samples, then the checksum.
        """
        total, number, interval = page[4], page[5], page[6]
        samples = [
            int.from_bytes(page[offset : offset + 2], "little")
            for offset in range(7, len(page) - 2, 2)
        ]
        checksum_ok = (sum(page[2:-1]) & 0xFF) == page[-1]
        return total, number, interval, samples, checksum_ok

    async def async_get_voltage_curve(self, channel: int) -> VoltageCurve:
        """Read back the slot's logged voltage curve, every page of it.

        The charger answers one request with all the pages in a row, one
        sample every `interval` seconds since the program started.
        """
        bitmask = CHANNEL_BITMASK[channel]
        page = await self._request(CMD_VOLTAGE_CURVE, bytes([bitmask]))
        samples: list[int] = []
        interval = None
        checksum_ok = True

        while page is not None and len(page) >= 8 and page[2] == CMD_VOLTAGE_CURVE:
            if page[3] != bitmask:
                break
            total, number, interval, page_samples, page_ok = self._parse_curve_page(page)
            samples.extend(page_samples)
            checksum_ok = checksum_ok and page_ok
            if number >= total:
                break
            try:
                page = await asyncio.wait_for(self._notify_queue.get(), timeout=NOTIFY_TIMEOUT)
            except asyncio.TimeoutError:
                checksum_ok = False
                break

        return VoltageCurve(
            samples_mv=samples,
            interval_seconds=interval,
            checksum_ok=checksum_ok if samples else None,
        )

    def _build_config_payload(self, channel: int, **kwargs) -> bytes:
        # No default: guessing lithium for a NiMH cell would charge it to
        # 4.2 V. The caller has to say what is in the slot.
        chemistry = kwargs.get("chemistry")
        if not chemistry:
            raise ValueError(
                f"Slot {channel + 1}: the MC5000 needs a chemistry to start; pass one "
                "or set the slot's expected chemistry"
            )
        mode = kwargs.get("mode") or "charge"
        charge_current = int(kwargs.get("current", 1000))
        discharge_current = int(kwargs.get("discharge_current", charge_current))
        capacity = int(kwargs.get("capacity", 3000))
        target_mv = int(kwargs.get("target_mv", DEFAULT_TARGET_MV.get(chemistry, 4200)))
        cutoff_mv = int(kwargs.get("cutoff_mv", DEFAULT_CUTOFF_MV.get(chemistry, 3200)))
        charge_cutoff_ma = int(kwargs.get("charge_cutoff_ma", 100))
        discharge_cutoff_ma = int(kwargs.get("discharge_cutoff_ma", 100))
        delta_peak_mv = int(kwargs.get("delta_peak_mv", 6))
        trickle_x10 = int(kwargs.get("trickle_ma", 0)) // 10
        keep_mv = int(kwargs.get("keep_mv", 0))
        cutoff_timer_min = int(kwargs.get("cutoff_timer_min", 0))
        temperature_cutoff_c = int(kwargs.get("temperature_cutoff_c", 71))
        chem_byte = CHEMISTRY_TO_BYTE.get(chemistry, 0x00)
        if "secondary" in kwargs:
            secondary = int(kwargs["secondary"])
        elif chemistry in DEFAULT_SECONDARY_MV:
            secondary = DEFAULT_SECONDARY_MV[chemistry]
        else:
            secondary = int(capacity * 1.1)
        charge_rest_min = int(kwargs.get("charge_rest_min", 10))
        discharge_rest_min = int(kwargs.get("discharge_rest_min", 10))

        data = bytearray(40)
        data[0] = CHANNEL_BITMASK[channel] if channel is not None else 0x00
        data[1] = mode_byte(chemistry, mode)
        data[2:4] = _u16(charge_current)
        data[4:6] = _u16(discharge_current)
        data[6:8] = _u16(capacity)
        data[8:10] = _u16(target_mv)
        data[10:12] = _u16(cutoff_mv)
        data[12:14] = _u16(charge_cutoff_ma)
        data[14:16] = _u16(discharge_cutoff_ma)
        data[16:18] = _u16(charge_rest_min)
        data[18:20] = _u16(discharge_rest_min)
        data[20] = int(kwargs.get("cycle_count", 1))
        data[21] = int(kwargs.get("cycle_direction", 0))
        data[22] = delta_peak_mv
        data[23] = trickle_x10
        data[24:26] = _u16(keep_mv)
        data[26] = temperature_cutoff_c  # the app sends 71 by default
        data[27:29] = _u16(cutoff_timer_min)  # safety timer, minutes
        # data[29:31] turbo charge capacity and data[31] turbo mode stay off
        data[32] = chem_byte
        data[33:35] = _u16(secondary)
        data[35] = int(kwargs.get("eneloop_type", 0))
        # data[36:40] padding stays zero
        return bytes(data)

    async def _send_program(self, channel: int, payload: bytes) -> None:
        resp = await self._request(CMD_CONFIG, payload)
        # The charger answers 0x94 with 1 when it took the program.
        if resp is not None and len(resp) > 4 and resp[4] != 1:
            reason = " (the passcode was refused)" if self.unlocked is False else ""
            raise BleakError(f"MC5000 refused the program for slot {channel + 1}{reason}")

    async def start_channel(self, channel: int, **kwargs) -> None:
        """Write a complete program to the slot, which starts it.

        The MC5000 has no "use the last program" command: the app always
        pushes a full 0x94 program, and that write is the start. Any field
        the caller doesn't supply gets a default.
        """
        config = {**self._last_config.get(channel, {}), **kwargs}
        payload = self._build_config_payload(channel, **config)
        self._last_config[channel] = config
        await self._send_program(channel, payload)

    async def start_all(self, per_channel: dict[int, dict] | None = None, **kwargs) -> None:
        per_channel = per_channel or {}
        # Build every program before writing any, so one invalid slot
        # doesn't leave the others half started.
        payloads = []
        for channel in range(self.channel_count):
            config = {
                **self._last_config.get(channel, {}),
                **kwargs,
                **per_channel.get(channel, {}),
            }
            payloads.append((channel, config, self._build_config_payload(channel, **config)))

        for channel, config, payload in payloads:
            self._last_config[channel] = config
            await self._send_program(channel, payload)

    async def stop_channel(self, channel: int) -> None:
        await self._request(CMD_STOP, bytes([CHANNEL_BITMASK[channel]]))

    async def stop_all(self) -> None:
        # One slot per command, like the app; a combined mask is untested.
        for channel in range(self.channel_count):
            await self.stop_channel(channel)

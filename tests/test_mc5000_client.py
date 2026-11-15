"""MC5000 driver: connection handling and wire format."""

from __future__ import annotations

import asyncio
import unittest

import _harness
from _harness import FakeBleakClient, load

mc5000 = load("mc5000_client")
Mc5000Client = mc5000.Mc5000Client


class FakeDevice:
    name = "TelinkSE"
    address = "AA:BB:CC:DD:EE:FF"


def _client() -> Mc5000Client:
    return Mc5000Client(FakeDevice())


async def _connect(client: Mc5000Client) -> FakeBleakClient:
    """Connect, answering each command the init sequence sends."""

    async def answer():
        # The init sequence and the handshake each wait on a notification;
        # feed one back for every packet written.
        for _ in range(200):
            await asyncio.sleep(0)
            if client._client is not None and client._client.notify_handler is not None:
                client._notify_queue.put_nowait(b"\x0f\x02\x06\x08")

    task = asyncio.create_task(answer())
    try:
        await client.connect()
    finally:
        task.cancel()

    return client._client


class ConnectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_connect_goes_through_bleak_retry_connector(self) -> None:
        """A bare BleakClient.connect() fails hard on every transient error.

        establish_connection() retries them, reuses the cached GATT services
        and cooperates with Home Assistant's connection slot allocator, which
        is what stops a flaky link turning into a warning per poll.
        """
        _harness.establish_connection_calls.clear()
        client = _client()

        await _connect(client)

        self.assertEqual(len(_harness.establish_connection_calls), 1)
        call = _harness.establish_connection_calls[0]
        self.assertIs(call["device"], client._device)
        self.assertIsNotNone(call["disconnected_callback"])
        self.assertTrue(call["use_services_cache"])
        self.assertTrue(client.is_connected)

    async def test_disconnect_callback_marks_the_link_down(self) -> None:
        _harness.establish_connection_calls.clear()
        client = _client()
        await _connect(client)

        _harness.establish_connection_calls[0]["disconnected_callback"](client._client)

        self.assertFalse(client.is_connected)

    async def test_a_failed_init_leaves_no_half_open_client(self) -> None:
        """The charger accepts one connection; a leaked one blocks the retry."""
        _harness.establish_connection_calls.clear()
        client = _client()

        boom = RuntimeError("notify refused")

        async def failing_start_notify(_self, _char, _handler):
            raise boom

        original_init = FakeBleakClient.start_notify
        FakeBleakClient.start_notify = failing_start_notify
        try:
            with self.assertRaises(RuntimeError):
                await client.connect()
        finally:
            FakeBleakClient.start_notify = original_init

        self.assertIsNone(client._client)
        self.assertFalse(client.is_connected)

    async def test_set_ble_device_is_used_by_the_next_connect(self) -> None:
        _harness.establish_connection_calls.clear()
        client = _client()

        fresh = FakeDevice()
        client.set_ble_device(fresh)
        await _connect(client)

        self.assertIs(_harness.establish_connection_calls[0]["device"], fresh)


def _status_packet(**fields) -> bytes:
    """Build a 23 byte 0x91 answer the way firmware 1.83 sends it."""
    packet = bytearray(23)
    packet[0:3] = b"\x0f\x15\x91"
    packet[3] = fields.get("slot", 0x01)
    packet[4:6] = fields.get("current", 0).to_bytes(2, "big")
    packet[6:8] = fields.get("voltage", 0).to_bytes(2, "big")
    packet[8:10] = fields.get("temperature", 0).to_bytes(2, "big")
    packet[10:12] = fields.get("capacity", 0).to_bytes(2, "big")
    packet[12:16] = fields.get("elapsed", 0).to_bytes(4, "big")
    packet[16:18] = fields.get("resistance", 0).to_bytes(2, "big")
    packet[18] = fields.get("status", 0)
    packet[19] = fields.get("mode", 0)
    packet[20] = fields.get("error", 0)
    packet[21] = fields.get("chemistry", 0)
    packet[22] = sum(packet[2:22]) & 0xFF
    return bytes(packet)


class PacketTest(unittest.TestCase):
    def test_packet_framing_and_checksum(self) -> None:
        packet = mc5000._build_packet(0x91, b"\x08")

        self.assertEqual(packet, bytes.fromhex("0f03910899"))

    def test_empty_slot_as_captured_from_the_charger(self) -> None:
        # Real answer from an MC5000 on firmware 1.83 with nothing inserted;
        # byte 21 still holds the chemistry of the last program (NiMH).
        packet = bytes.fromhex("0f15910100000000000000000000000000000000000395")

        channel = Mc5000Client._parse_channel(0, packet)

        self.assertEqual(channel.status, "empty")
        self.assertEqual(channel.voltage, 0.0)
        self.assertIsNone(channel.chemistry)
        self.assertIsNone(channel.error)

    def test_charging_slot_reads_every_field_at_the_app_offsets(self) -> None:
        packet = _status_packet(
            current=1500,
            voltage=1432,
            temperature=31,
            capacity=820,
            elapsed=70000,
            resistance=45,
            status=2,
            mode=0,
            chemistry=3,
        )

        channel = Mc5000Client._parse_channel(0, packet)

        self.assertEqual(channel.status, "charging")
        self.assertEqual(channel.current, 1.5)
        self.assertEqual(channel.voltage, 1.432)
        self.assertEqual(channel.temperature, 31)
        self.assertEqual(channel.capacity, 820)
        self.assertEqual(channel.time, 70000)  # needs all four bytes
        self.assertEqual(channel.resistance, 45)
        self.assertEqual(channel.chemistry, "nimh")
        self.assertEqual(channel.mode, "charge")

    def test_mode_byte_is_read_per_chemistry_family(self) -> None:
        lithium = Mc5000Client._parse_channel(
            0, _status_packet(voltage=3700, status=3, mode=2, chemistry=0)
        )
        nickel = Mc5000Client._parse_channel(
            0, _status_packet(voltage=1300, status=2, mode=2, chemistry=3)
        )

        self.assertEqual(lithium.mode, "discharge")
        self.assertEqual(nickel.mode, "break_in")

    def test_idle_cell_reports_its_voltage_only(self) -> None:
        channel = Mc5000Client._parse_channel(
            0, _status_packet(voltage=1290, status=1, chemistry=0)
        )

        self.assertEqual(channel.status, "standby")
        self.assertEqual(channel.voltage, 1.29)
        self.assertIsNone(channel.chemistry)
        self.assertEqual(channel.current, 0.0)

    def test_error_byte_is_named(self) -> None:
        channel = Mc5000Client._parse_channel(
            0, _status_packet(voltage=1300, status=1, error=12, chemistry=3)
        )

        self.assertEqual(channel.status, "error")
        self.assertEqual(channel.error, "reversed_polarity")

    def test_short_answer_from_older_firmware(self) -> None:
        packet = bytearray(20)
        packet[6:8] = (4100).to_bytes(2, "big")
        packet[12:14] = (600).to_bytes(2, "big")
        packet[16] = 2

        channel = Mc5000Client._parse_channel(2, bytes(packet))

        self.assertEqual(channel.voltage, 4.1)
        self.assertEqual(channel.time, 600)
        self.assertEqual(channel.status, "charging")
        self.assertIsNone(channel.chemistry)

    def test_device_info_as_captured_from_the_charger(self) -> None:
        resp = bytes.fromhex("0f1357003130303231340400000000015301007f57")

        info = Mc5000Client._parse_device_info(resp, "DC:23:52:88:36:E2")

        self.assertEqual(info.sw_version, "1.83")
        self.assertEqual(info.hw_version, "1")
        self.assertEqual(info.name, "SkyRC MC5000")

    def test_system_settings_as_captured_from_the_charger(self) -> None:
        device = load("models").DeviceData()

        Mc5000Client._apply_system_settings(device, bytes.fromhex("0f0996010001000101009a"))

        self.assertEqual(device.beep_volume, "off")
        self.assertEqual(device.notification_volume, "low")
        self.assertEqual(device.end_notification, "once")
        self.assertEqual(device.backlight, "low")
        self.assertTrue(device.warning_tone)
        self.assertEqual(device.sleep_timer_minutes, 0)


class ProgramTest(unittest.TestCase):
    def test_mode_byte_depends_on_the_chemistry(self) -> None:
        client = _client()

        lithium = client._build_config_payload(0, chemistry="liion", mode="discharge")
        nickel = client._build_config_payload(0, chemistry="nimh", mode="discharge")
        zinc = client._build_config_payload(0, chemistry="nizn", mode="discharge")

        self.assertEqual((lithium[1], nickel[1], zinc[1]), (2, 3, 1))

    def test_a_mode_the_chemistry_lacks_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            _client()._build_config_payload(0, chemistry="nimh", mode="storage")

    def test_no_chemistry_no_program(self) -> None:
        """Defaulting to Li-ion would charge a NiMH cell to 4.2 V."""
        with self.assertRaises(ValueError):
            _client()._build_config_payload(0, mode="charge")

    def test_program_layout_matches_the_app(self) -> None:
        payload = _client()._build_config_payload(
            2, chemistry="eneloop", mode="charge", current=800, capacity=2000
        )

        self.assertEqual(len(payload), 40)
        self.assertEqual(payload[0], 0x04)  # slot 3 as a bitmask
        self.assertEqual(int.from_bytes(payload[2:4], "big"), 800)
        self.assertEqual(int.from_bytes(payload[6:8], "big"), 2000)
        self.assertEqual(payload[26], 60)  # temperature cut-off, app default
        self.assertEqual(payload[31], 0)  # turbo off
        self.assertEqual(payload[32], 0x05)


class ProgramDefaultsTest(unittest.TestCase):
    """Defaults and limits taken from the SkyRC app's own tables."""

    def _payload(self, chemistry: str, mode: str = "charge", **kwargs) -> bytes:
        return _client()._build_config_payload(0, chemistry=chemistry, mode=mode, **kwargs)

    def test_voltages_default_to_the_apps_per_chemistry_values(self) -> None:
        for chemistry, target, cutoff, storage in (
            ("liion", 4200, 3200, 3800),
            ("liion_hv", 4350, 3400, 3900),
            ("lifepo4", 3650, 2900, 3300),
            ("nimh", 1650, 900, 3300),
            ("nizn", 1900, 1100, 3300),
            ("lto", 2850, 1800, 2400),
            ("naion", 4000, 2000, 3500),
        ):
            payload = self._payload(chemistry)
            self.assertEqual(int.from_bytes(payload[8:10], "big"), target, chemistry)
            self.assertEqual(int.from_bytes(payload[10:12], "big"), cutoff, chemistry)
            self.assertEqual(int.from_bytes(payload[33:35], "big"), storage, chemistry)

    def test_currents_and_capacity_default_per_chemistry(self) -> None:
        def currents(payload: bytes) -> tuple[int, int, int]:
            return tuple(int.from_bytes(payload[i : i + 2], "big") for i in (6, 2, 4))

        self.assertEqual(currents(self._payload("liion")), (3000, 3000, 2000))
        self.assertEqual(currents(self._payload("nimh")), (3000, 1000, 1000))
        self.assertEqual(currents(self._payload("naion")), (5000, 2000, 2000))
        self.assertEqual(currents(self._payload("nimh", "break_in")), (3000, 300, 600))

    def test_eneloop_defaults_follow_the_cell_type(self) -> None:
        payload = self._payload("eneloop", eneloop_type=1)

        self.assertEqual(int.from_bytes(payload[6:8], "big"), 800)
        self.assertEqual(int.from_bytes(payload[2:4], "big"), 400)
        self.assertEqual(payload[35], 1)

    def test_a_voltage_outside_the_apps_range_is_refused(self) -> None:
        for chemistry, name, value in (
            ("liion", "target_mv", 4300),
            ("lifepo4", "target_mv", 4200),
            ("nimh", "target_mv", 1900),
            ("liion", "cutoff_mv", 2000),
            ("liion", "storage_mv", 4000),
        ):
            with self.assertRaises(ValueError, msg=f"{chemistry} {name}"):
                self._payload(chemistry, **{name: value})

    def test_the_edges_of_the_range_are_accepted(self) -> None:
        self._payload("liion", target_mv=4050)
        self._payload("liion", target_mv=4250)
        self._payload("lifepo4", target_mv=3700)

    def test_an_unknown_eneloop_type_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self._payload("eneloop", eneloop_type=9)


class StartStopTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self, client, coro_factory, answer: bytes):
        async def feed():
            for _ in range(400):
                await asyncio.sleep(0)
                client._notify_queue.put_nowait(answer)

        task = asyncio.create_task(feed())
        try:
            await coro_factory()
        finally:
            task.cancel()
        return [bytes(write) for write in client._client.writes]

    async def _connected(self) -> Mc5000Client:
        client = _client()
        await _connect(client)
        client._client.writes.clear()
        return client

    async def test_connect_never_sends_the_stop_command(self) -> None:
        client = _client()
        await _connect(client)

        commands = [write[2] for write in client._client.writes]
        self.assertNotIn(mc5000.CMD_STOP, commands)
        self.assertIn(mc5000.CMD_INFO, commands)
        self.assertIn(mc5000.CMD_VERIFY_PASSWORD, commands)

    async def _unlock(self, client, answer: bytes) -> list[bytes]:
        return await self._run(client, client._unlock, answer)

    async def test_the_default_passcode_is_sent_digit_by_digit(self) -> None:
        client = await self._connected()

        writes = await self._unlock(client, bytes.fromhex("0f0474010176"))

        # 0x74 + 0x00 + 1 + 2 + 3 + 4 = 0x7E
        self.assertEqual(writes, [bytes.fromhex("0f077400010203047e")])
        self.assertTrue(client.unlocked)

    async def test_a_refused_passcode_is_reported_once(self) -> None:
        # Real answer from a charger whose passcode is not the default.
        client = await self._connected()
        client.unlocked = None

        with self.assertLogs(mc5000.__name__, level="WARNING") as logs:
            await self._unlock(client, bytes.fromhex("0f0474010075"))
        self.assertFalse(client.unlocked)
        self.assertEqual(len(logs.records), 1)

        with self.assertNoLogs(mc5000.__name__, level="WARNING"):
            await self._unlock(client, bytes.fromhex("0f0474010075"))

    async def test_a_custom_passcode_is_used(self) -> None:
        client = await self._connected()
        client.set_passcode("9081")

        writes = await self._unlock(client, bytes.fromhex("0f0474010176"))

        self.assertEqual(writes[0][3:8], bytes([0, 9, 0, 8, 1]))

    def test_passcode_must_be_four_digits(self) -> None:
        for bad in ("123", "12345", "12a4", ""):
            with self.assertRaises(ValueError):
                _client().set_passcode(bad)

    async def test_start_is_the_program_write_alone(self) -> None:
        client = await self._connected()

        writes = await self._run(
            client,
            lambda: client.start_channel(1, chemistry="nimh", current=500),
            b"\x0f\x04\x94\x02\x01\x97",
        )

        self.assertEqual([w[2] for w in writes], [mc5000.CMD_CONFIG])
        self.assertEqual(writes[0][3], 0x02)

    async def test_a_refused_program_raises(self) -> None:
        client = await self._connected()

        with self.assertRaises(mc5000.BleakError):
            await self._run(
                client,
                lambda: client.start_channel(0, chemistry="nimh"),
                b"\x0f\x04\x94\x01\x00\x95",
            )

    async def test_start_all_writes_nothing_when_one_slot_is_invalid(self) -> None:
        client = await self._connected()

        with self.assertRaises(ValueError):
            await self._run(
                client,
                lambda: client.start_all(
                    per_channel={0: {"chemistry": "nimh"}, 1: {"chemistry": "nimh"}}
                ),
                b"\x0f\x04\x94\x01\x01\x96",
            )

        self.assertEqual(client._client.writes, [])

    async def test_stop_targets_a_single_slot(self) -> None:
        client = await self._connected()

        writes = await self._run(
            client, lambda: client.stop_channel(2), b"\x0f\x04\xfe\x04\x01\x03"
        )

        self.assertEqual(writes, [bytes.fromhex("0f03fe0402")])

    async def test_stop_all_stops_each_slot(self) -> None:
        client = await self._connected()

        writes = await self._run(client, client.stop_all, b"\x0f\x04\xfe\x01\x01\x00")

        self.assertEqual([w[2] for w in writes], [mc5000.CMD_STOP] * 4)
        self.assertEqual([w[3] for w in writes], [1, 2, 4, 8])

    async def test_a_late_greeting_is_not_taken_for_the_answer(self) -> None:
        client = await self._connected()
        status = _status_packet(slot=1, voltage=1300, status=2, current=400, chemistry=3)

        async def feed():
            for _ in range(400):
                await asyncio.sleep(0)
                if client._client.writes and client._notify_queue.empty():
                    client._notify_queue.put_nowait(b"\x0f\x04\x06\x01\x01\x08")
                    client._notify_queue.put_nowait(status)
                    return

        task = asyncio.create_task(feed())
        try:
            resp = await client._request(mc5000.CMD_STATUS, b"\x01")
        finally:
            task.cancel()

        self.assertEqual(resp, status)


# A real three-page answer to 0x98 for slot 1, 38 minutes into a NiMH charge.
CURVE_PAGES = [
    bytes.fromhex(
        "0fce9801030108d804d804d804d904db04db04de04e004e004e204e204e304e404e404e604e704e704e904e904eb04ec04ec04ee04ee04ee04f004f004f204f304f304f504f504f504f504f504f704f804f804fa04fa04fa04fb04fb04fc04fc04fd04ff04ff04ff04ff04ff040105010501050105010503050405040505050505050506050605080508050805090509050a050a050a050b050b050b050c050c050d050d050d050d050d050f05100510051005100510051205120512051205120513051305130515051505150515050c"
    ),
    bytes.fromhex(
        "0fce9801030208150517051705170517051705180518051805190519051905190519051a051a051a051c051c051c051c051c051c051c051d051e051e051e051e051e051f051f0520052105210521052105210521052105220523052305230523052305230523052505250525052505250525052505250525052505260527052705280528052805280528052905290529052a052a052a052a052a052a052a052a052b052b052c052d052d052d052d052d052d052d052e052e052e052f052f052f052f052f052f052f052f0530053005a0"
    ),
    bytes.fromhex(
        "0fb69801030308310531053105310531053105310531053205320532053305340534053405340534053405340534053405350536053605360536053605360536053605360536053705370538053805380539053905390539053905390539053905390539053a053a053a053b053b053b053b053b053b053b053b053b053b053c053d053d053e053e053e053e053e053e053e053e053e053e053e053f053f05400540054005400540054005400540054005400540054005ff"
    ),
]


class VoltageCurveTest(unittest.IsolatedAsyncioTestCase):
    def test_a_captured_page_is_split_into_samples(self) -> None:
        total, number, interval, samples, ok = Mc5000Client._parse_curve_page(CURVE_PAGES[2])

        self.assertEqual((total, number, interval), (3, 3, 8))
        self.assertEqual(len(samples), 88)
        self.assertEqual(samples[0], 1329)
        self.assertEqual(samples[-1], 1344)
        self.assertTrue(ok)

    async def test_every_page_is_collected(self) -> None:
        client = _client()
        await _connect(client)
        client._client.writes.clear()

        async def feed():
            while not client._client.writes:
                await asyncio.sleep(0)
            for page in CURVE_PAGES:
                client._notify_queue.put_nowait(page)

        task = asyncio.create_task(feed())
        try:
            curve = await client.async_get_voltage_curve(0)
        finally:
            task.cancel()

        self.assertEqual(client._client.writes, [bytes.fromhex("0f03980199")])
        self.assertEqual(len(curve.samples_mv), 288)
        self.assertEqual(curve.samples_mv[0], 1240)
        self.assertEqual(curve.samples_mv[-1], 1344)
        self.assertEqual(curve.interval_seconds, 8)
        self.assertTrue(curve.checksum_ok)

    def test_frames_split_by_a_small_mtu_are_reassembled(self) -> None:
        client = _client()
        stream = bytes.fromhex("0f0406010108") + CURVE_PAGES[2]

        for start in range(0, len(stream), 20):
            client._notify_handler(None, bytearray(stream[start : start + 20]))

        self.assertEqual(client._notify_queue.get_nowait(), bytes.fromhex("0f0406010108"))
        self.assertEqual(client._notify_queue.get_nowait(), CURVE_PAGES[2])
        self.assertTrue(client._notify_queue.empty())


def _settings_frame(values: list[int]) -> bytes:
    body = bytes([0x96, 0x10, *values])
    return bytes([0x0F, len(body) + 1]) + body + bytes([sum(body) & 0xFF])


class SettingsTest(unittest.IsolatedAsyncioTestCase):
    """Writing the charger's settings, with a charger that drops a command."""

    async def asyncSetUp(self) -> None:
        mc5000.SETTINGS_SETTLE_SECONDS = 0
        mc5000.SETTINGS_ANSWER_SECONDS = 0.05
        self.client = _client()
        await _connect(self.client)
        self.client._client.writes.clear()
        # beep, notification, end notification, backlight, warning, sleep
        self.state = [0, 1, 0, 1, 1, 0]
        self.drop_next_write = False
        self.fake = self.client._client

        async def write(_char, data, response=False):
            self.fake.writes.append(bytes(data))
            command = data[2]
            if command == mc5000.CMD_QUERY_SYSTEM:
                self.client._notify_queue.put_nowait(_settings_frame(self.state))
            elif command == mc5000.CMD_WRITE_SYSTEM:
                if self.drop_next_write:
                    self.drop_next_write = False
                    return
                self.state = list(data[4:10])
                self.client._notify_queue.put_nowait(bytes.fromhex("0f04951001a6"))

        self.fake.write_gatt_char = write

    async def test_only_the_asked_setting_changes(self) -> None:
        await self.client.async_set_settings(backlight="high")

        writes = [w for w in self.fake.writes if w[2] == mc5000.CMD_WRITE_SYSTEM]
        self.assertEqual(len(writes), 1)
        self.assertEqual(self.state, [0, 1, 0, 3, 1, 0])
        self.assertEqual(len(writes[0]), 21)  # 17 data bytes + header + checksum
        self.assertEqual(self.client._device_info.backlight, "high")

    async def test_every_setting_has_its_own_byte(self) -> None:
        await self.client.async_set_settings(
            beep_volume="high",
            notification_volume="off",
            end_notification="repeat",
            warning_tone=False,
            sleep_timer_minutes=15,
        )

        self.assertEqual(self.state, [3, 0, 1, 1, 0, 15])

    async def test_a_dropped_write_is_retried_and_checked(self) -> None:
        self.drop_next_write = True

        await self.client.async_set_settings(backlight="medium")

        writes = [w for w in self.fake.writes if w[2] == mc5000.CMD_WRITE_SYSTEM]
        self.assertEqual(len(writes), 2)
        self.assertEqual(self.state[3], 2)

    async def test_a_setting_that_never_sticks_raises(self) -> None:
        async def deaf(_char, data, response=False):
            self.fake.writes.append(bytes(data))
            if data[2] == mc5000.CMD_QUERY_SYSTEM:
                self.client._notify_queue.put_nowait(_settings_frame(self.state))
            else:
                # Acknowledges the write without applying it.
                self.client._notify_queue.put_nowait(bytes.fromhex("0f04951001a6"))

        self.fake.write_gatt_char = deaf

        with self.assertRaises(mc5000.BleakError):
            await self.client.async_set_settings(backlight="high")

    async def test_unknown_values_are_refused_before_any_write(self) -> None:
        for changes in (
            {"backlight": "blinding"},
            {"sleep_timer_minutes": 7},
            {"volume": "high"},
        ):
            with self.assertRaises(ValueError):
                await self.client.async_set_settings(**changes)

        self.assertEqual(self.fake.writes, [])


nc3000 = load("nc3000_client")
Nc3000Client = nc3000.Nc3000Client


def _nc_packet(**fields) -> bytes:
    packet = bytearray(23)
    packet[0:3] = b"\x0f\x15\x91"
    packet[3] = fields.get("slot", 0x01)
    packet[4:6] = fields.get("current", 0).to_bytes(2, "big")
    packet[6:8] = fields.get("voltage", 0).to_bytes(2, "big")
    packet[8:10] = fields.get("temperature", 0).to_bytes(2, "big")
    packet[10:12] = fields.get("capacity", 0).to_bytes(2, "big")
    packet[12:16] = fields.get("elapsed", 0).to_bytes(4, "big")
    packet[16:18] = fields.get("resistance", 0).to_bytes(2, "big")
    packet[18] = fields.get("status", 0)
    packet[19] = fields.get("mode", 0)
    packet[20] = fields.get("error", 0)
    packet[22] = sum(packet[2:22]) & 0xFF
    return bytes(packet)


class Nc3000Test(unittest.IsolatedAsyncioTestCase):
    def test_it_knows_itself_by_its_model_code(self) -> None:
        resp = bytes.fromhex("0f1357003130303230390400000000015301007f57")

        info = Nc3000Client._parse_device_info(resp, "AA:BB:CC:DD:EE:FF")

        self.assertIsNotNone(info)
        self.assertEqual(info.model, "NC3000")
        self.assertEqual(info.model_code, "100209")

    def test_a_charging_slot(self) -> None:
        channel = Nc3000Client._parse_channel(
            0, _nc_packet(current=1000, voltage=1380, capacity=300, elapsed=1200, status=2, mode=0)
        )

        self.assertEqual(channel.status, "charging")
        self.assertEqual(channel.mode, "charge")
        self.assertEqual(channel.current, 1.0)
        self.assertEqual(channel.time, 1200)
        self.assertIsNone(channel.chemistry)

    def test_the_statuses_are_the_mc5000s(self) -> None:
        names = {0: "idle", 1: "standby", 2: "charging", 3: "discharging", 4: "resting", 5: "done"}
        for byte, name in names.items():
            channel = Nc3000Client._parse_channel(0, _nc_packet(voltage=1300, status=byte))
            self.assertEqual(channel.status, name, byte)

    def test_the_modes_have_the_nc3000_numbering(self) -> None:
        modes = {0: "charge", 1: "discharge", 2: "break_in", 3: "cycle", 4: "max_boost", 5: "turbo"}
        for byte, name in modes.items():
            channel = Nc3000Client._parse_channel(0, _nc_packet(voltage=1300, status=2, mode=byte))
            self.assertEqual(channel.mode, name, byte)

    def test_an_empty_slot_and_an_error_code(self) -> None:
        self.assertEqual(Nc3000Client._parse_channel(0, _nc_packet()).status, "empty")
        failed = Nc3000Client._parse_channel(0, _nc_packet(voltage=1300, status=1, error=12))
        self.assertEqual((failed.status, failed.error), ("error", "reversed_polarity"))

    async def test_a_start_is_refused_without_writing_anything(self) -> None:
        client = Nc3000Client(FakeDevice())
        await _connect(client)
        client._client.writes.clear()

        with self.assertRaises(ValueError):
            await client.start_channel(0, chemistry="nimh")
        with self.assertRaises(ValueError):
            await client.start_all()

        self.assertEqual(client._client.writes, [])


type228 = load("type228_client")
Type228Client = type228.Type228Client


def _slot_block(**fields) -> bytes:
    block = bytearray(17)
    block[0:2] = fields.get("current", 0).to_bytes(2, "big")
    block[2:4] = fields.get("voltage", 0).to_bytes(2, "big")
    block[4:6] = fields.get("temperature", 0).to_bytes(2, "big")
    block[6:8] = fields.get("capacity", 0).to_bytes(2, "big")
    block[8:12] = fields.get("elapsed", 0).to_bytes(4, "big")
    block[12:14] = fields.get("resistance", 0).to_bytes(2, "big")
    block[14] = fields.get("status", 0)
    block[15] = fields.get("mode", 0)
    block[16] = fields.get("error", 0)
    return bytes(block)


def _frame_228(blocks: list[bytes], device_mode: int = 0) -> bytes:
    body = bytes([0x9A, 0x00, device_mode]) + b"".join(blocks)
    frame = bytes([0x0F, len(body) + 1]) + body
    return frame + bytes([sum(body) & 0xFF])


class Type228Test(unittest.IsolatedAsyncioTestCase):
    def test_it_has_eight_slots_named_by_number(self) -> None:
        client = Type228Client(FakeDevice())

        self.assertEqual(client.channel_count, 8)
        self.assertEqual([client._port_byte(i) for i in range(8)], [1, 2, 3, 4, 5, 6, 7, 8])

    def test_a_charging_slot(self) -> None:
        channel = Type228Client._parse_slot(
            2,
            _slot_block(current=1000, voltage=1380, capacity=300, elapsed=70000, status=2, mode=0),
        )

        self.assertEqual((channel.index, channel.status, channel.mode), (2, "charging", "charge"))
        self.assertEqual(channel.current, 1.0)
        self.assertEqual(channel.time, 70000)

    def test_the_modes_have_the_type_228_numbering(self) -> None:
        modes = {
            0: "charge",
            1: "discharge",
            2: "break_in",
            3: "cycle",
            4: "max_boost",
            6: "three_stage_charge",
        }
        for byte, name in modes.items():
            channel = Type228Client._parse_slot(0, _slot_block(voltage=1300, status=2, mode=byte))
            self.assertEqual(channel.mode, name, byte)

    def test_an_empty_slot_and_an_error_code(self) -> None:
        self.assertEqual(Type228Client._parse_slot(0, _slot_block()).status, "empty")
        failed = Type228Client._parse_slot(0, _slot_block(voltage=1300, status=1, error=12))
        self.assertEqual((failed.status, failed.error), ("error", "reversed_polarity"))

    async def test_one_request_reads_the_eight_slots(self) -> None:
        client = Type228Client(FakeDevice())
        await _connect(client)
        client._client.writes.clear()
        blocks = [_slot_block(voltage=1200 + i, status=2, current=100 * i) for i in range(8)]
        frame = _frame_228(blocks)
        self.assertEqual(len(frame), 142)

        async def feed():
            while not client._client.writes:
                await asyncio.sleep(0)
            client._notify_queue.put_nowait(frame)

        task = asyncio.create_task(feed())
        try:
            state = await client.async_update()
        finally:
            task.cancel()

        self.assertEqual(client._client.writes, [bytes.fromhex("0f039a009a")])
        self.assertEqual(len(state.channels), 8)
        self.assertEqual(
            [c.voltage for c in state.channels], [round(1.2 + i / 1000, 3) for i in range(8)]
        )
        self.assertEqual(state.channels[7].current, 0.7)

    def test_a_charger_that_does_not_measure_resistance_says_so(self) -> None:
        block = _slot_block(voltage=1300, status=2, resistance=55)

        self.assertEqual(Type228Client._parse_slot(0, block, device_mode=0).resistance, 55)
        self.assertIsNone(Type228Client._parse_slot(0, block, device_mode=1).resistance)

    async def test_a_short_answer_leaves_the_slots_unavailable(self) -> None:
        client = Type228Client(FakeDevice())
        await _connect(client)

        async def feed():
            while not client._client.writes:
                await asyncio.sleep(0)
            client._notify_queue.put_nowait(bytes.fromhex("0f039a009a"))

        client._client.writes.clear()
        task = asyncio.create_task(feed())
        try:
            state = await client.async_update()
        finally:
            task.cancel()

        self.assertEqual(len(state.channels), 8)
        self.assertTrue(all(c.status == "unavailable" for c in state.channels))

    async def test_stopping_names_the_slot_by_its_number(self) -> None:
        client = Type228Client(FakeDevice())
        await _connect(client)
        client._client.writes.clear()

        async def feed():
            for _ in range(200):
                await asyncio.sleep(0)
                client._notify_queue.put_nowait(b"\x0f\x04\xfe\x05\x01\x04")

        task = asyncio.create_task(feed())
        try:
            await client.stop_channel(4)
        finally:
            task.cancel()

        self.assertEqual(client._client.writes, [bytes.fromhex("0f03fe0503")])

    async def test_a_start_is_refused_without_writing_anything(self) -> None:
        client = Type228Client(FakeDevice())
        await _connect(client)
        client._client.writes.clear()

        with self.assertRaises(ValueError):
            await client.start_channel(0, chemistry="nimh")
        self.assertEqual(client._client.writes, [])


nc2200 = load("nc2200_client")
Nc2200Client = nc2200.Nc2200Client


def _nc2200_packet(**fields) -> bytes:
    packet = bytearray(20)
    packet[0:3] = b"\x0f\x12\x91"
    packet[3] = fields.get("slot", 0)
    packet[4:6] = fields.get("current", 0).to_bytes(2, "big")
    packet[6:8] = fields.get("voltage", 0).to_bytes(2, "big")
    packet[8:10] = fields.get("temperature", 0).to_bytes(2, "big")
    packet[10:12] = fields.get("capacity", 0).to_bytes(2, "big")
    packet[12:14] = fields.get("elapsed", 0).to_bytes(2, "big")
    packet[14:16] = fields.get("resistance", 0).to_bytes(2, "big")
    packet[16] = fields.get("status", 0)
    packet[17] = fields.get("mode", 0)
    packet[18] = fields.get("error", 0)
    packet[19] = sum(packet[2:19]) & 0xFF
    return bytes(packet)


class Nc2200Test(unittest.IsolatedAsyncioTestCase):
    def test_a_charging_slot(self) -> None:
        channel = Nc2200Client._parse_channel(
            1,
            _nc2200_packet(
                slot=1, current=800, voltage=1350, capacity=120, elapsed=900, status=7, mode=1
            ),
        )

        self.assertEqual((channel.status, channel.mode), ("charging", "charge"))
        self.assertEqual((channel.current, channel.voltage, channel.time), (0.8, 1.35, 900))

    def test_the_statuses_and_modes_have_the_nc2200_numbering(self) -> None:
        statuses = {
            0: "idle",
            3: "standby",
            7: "charging",
            8: "discharging",
            9: "cooling",
            10: "done",
        }
        for byte, name in statuses.items():
            channel = Nc2200Client._parse_channel(0, _nc2200_packet(voltage=1300, status=byte))
            self.assertEqual(channel.status, name, byte)
        modes = {1: "charge", 2: "discharge", 3: "refresh", 4: "break_in", 5: "turbo"}
        for byte, name in modes.items():
            channel = Nc2200Client._parse_channel(
                0, _nc2200_packet(voltage=1300, status=7, mode=byte)
            )
            self.assertEqual(channel.mode, name, byte)

    def test_an_empty_slot_and_an_error_flag(self) -> None:
        self.assertEqual(Nc2200Client._parse_channel(0, _nc2200_packet()).status, "empty")
        flagged = Nc2200Client._parse_channel(0, _nc2200_packet(voltage=1300, status=2, error=1))
        self.assertEqual((flagged.status, flagged.error), ("error", "error"))

    async def test_a_slot_is_asked_for_by_its_index(self) -> None:
        client = Nc2200Client(FakeDevice())
        await _connect(client)
        client._client.writes.clear()

        async def feed():
            for _ in range(2000):
                await asyncio.sleep(0)
                if len(client._client.writes) > len(fed):
                    fed.append(1)
                    client._notify_queue.put_nowait(_nc2200_packet(slot=len(fed) - 1, voltage=1300))

        fed: list[int] = []
        task = asyncio.create_task(feed())
        try:
            state = await client.async_update()
        finally:
            task.cancel()

        asked = [write[3] for write in client._client.writes]
        self.assertEqual(asked, [0, 1, 2, 3])
        self.assertEqual(len(state.channels), 4)

    async def test_a_start_is_refused_without_writing_anything(self) -> None:
        client = Nc2200Client(FakeDevice())
        await _connect(client)
        client._client.writes.clear()

        with self.assertRaises(ValueError):
            await client.start_channel(0, chemistry="nimh")
        self.assertEqual(client._client.writes, [])


legacy = load("legacy_client")
LegacyClient = legacy.LegacyClient
const = load("const")


def _legacy_status(state: int, **fields) -> bytes:
    packet = bytearray(36)
    packet[0:3] = b"\x0f\x22\x55"
    packet[3] = fields.get("mask", 1)
    packet[4] = state
    packet[5:7] = fields.get("capacity", 0).to_bytes(2, "big")
    packet[7:9] = fields.get("elapsed", 0).to_bytes(2, "big")
    packet[9:11] = fields.get("voltage", 0).to_bytes(2, "big")
    packet[11:13] = fields.get("current", 0).to_bytes(2, "big")
    packet[13] = fields.get("ext_temp", 0)
    packet[14] = fields.get("int_temp", 0)
    packet[15:17] = fields.get("resistance", 0).to_bytes(2, "big")
    packet[35] = sum(packet[2:35]) & 0xFF
    return bytes(packet)


class LegacyTest(unittest.IsolatedAsyncioTestCase):
    def test_the_model_table_comes_with_its_channels(self) -> None:
        models = {model.key: model for model in const.LEGACY_MODELS}

        self.assertEqual(models["q200"].channels, 4)
        self.assertEqual(models["d100"].channels, 2)
        self.assertEqual(models["b6evo"].channels, 1)
        self.assertFalse(models["b6evo"].password)
        self.assertEqual(len({model.code for model in models.values()}), len(models))

    def test_each_model_has_its_own_driver(self) -> None:
        q200 = legacy.make_legacy_client("q200")
        b6 = legacy.make_legacy_client("b6nano")

        self.assertEqual(
            (q200.MODEL_NAME, q200.channel_count, q200.MODEL_CODE), ("Q200", 4, b"100104")
        )
        self.assertEqual((b6.MODEL_NAME, b6.channel_count), ("B6nano", 1))

    def test_a_working_channel(self) -> None:
        channel = LegacyClient._parse_channel(
            1,
            _legacy_status(
                1, capacity=250, elapsed=600, voltage=4150, current=1500, int_temp=31, resistance=40
            ),
        )

        self.assertEqual((channel.status, channel.voltage, channel.current), ("active", 4.15, 1.5))
        self.assertEqual((channel.capacity, channel.time, channel.resistance), (250, 600, 40))
        self.assertEqual(channel.temperature, 31.0)

    def test_no_reading_is_zero(self) -> None:
        channel = LegacyClient._parse_channel(0, _legacy_status(3, voltage=0xFFFF, current=0xFFFF))

        self.assertEqual((channel.voltage, channel.current), (0.0, 0.0))

    def test_idle_error_and_unknown_states(self) -> None:
        for state in (0, 2, 6):
            self.assertEqual(LegacyClient._parse_channel(0, _legacy_status(state)).status, "idle")
        failed = LegacyClient._parse_channel(0, bytes([0x0F, 0x08, 0x55, 1, 4, 9, 3, 0, 0]))
        self.assertEqual((failed.status, failed.error), ("error", "system_9_charge_3"))
        self.assertEqual(LegacyClient._parse_channel(0, _legacy_status(8)).status, "unknown_8")

    def test_the_password_is_four_digits(self) -> None:
        legacy._password_bytes("5793")
        for bad in ("123", "12345", "12a4", ""):
            with self.assertRaises(ValueError):
                legacy._password_bytes(bad)

    async def test_each_channel_is_asked_with_the_password_and_a_start_is_refused(self) -> None:
        client = legacy.make_legacy_client("d100")(FakeDevice())
        await _connect(client)
        client._client.writes.clear()
        reply = bytearray(15)
        reply[0:3] = b"\x0f\x0d\x5f"
        reply[12] = 1

        async def feed():
            for _ in range(4000):
                await asyncio.sleep(0)
                if client._client.writes and client._notify_queue.empty():
                    client._notify_queue.put_nowait(bytes(reply))

        task = asyncio.create_task(feed())
        try:
            await client._async_handshake()
        finally:
            task.cancel()

        asked = [bytes(write[2:8]) for write in client._client.writes if write[2] == 0x5F]
        self.assertEqual(asked[:2], [bytes([0x5F, 1, 5, 7, 9, 3]), bytes([0x5F, 2, 5, 7, 9, 3])])
        self.assertTrue(client.unlocked)
        with self.assertRaises(ValueError):
            await client.start_channel(0)


if __name__ == "__main__":
    unittest.main()

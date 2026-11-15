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
            current=1500, voltage=1432, temperature=31, capacity=820,
            elapsed=70000, resistance=45, status=2, mode=0, chemistry=3,
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
        self.assertEqual(payload[26], 71)  # temperature cut-off, app default
        self.assertEqual(payload[31], 0)  # turbo off
        self.assertEqual(payload[32], 0x05)


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


if __name__ == "__main__":
    unittest.main()

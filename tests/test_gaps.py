"""Edge cases of the drivers: missing answers, short frames, refusals."""

from __future__ import annotations

import asyncio
import unittest
import unittest.mock

import _harness
from _harness import FakeBleakClient, load

mc5000 = load("mc5000_client")
mc3000 = load("mc3000_client")
nc2600 = load("nc2600_client")
nc3000 = load("nc3000_client")
nc2200 = load("nc2200_client")
type228 = load("type228_client")
psu650 = load("psu650_client")
legacy = load("legacy_client")
const = load("const")
BleakError = _harness.BleakError


class Device:
    name = "Charger"
    address = "AA:BB:CC:DD:EE:FF"


class NoMacDevice:
    name = "Charger"
    address = "not-a-mac"


def wire(client, replies=None, default=None):
    """Make a client think it is connected; answer by command from `replies`."""
    client._client = FakeBleakClient()
    client._connected = True
    asked: list[tuple[int, bytes]] = []
    replies = replies or {}

    async def request(command, data=b"", timeout=0):
        asked.append((command, data))
        reply = replies.get(command, default)
        return reply(data) if callable(reply) else reply

    client._request = request
    return asked


async def no_sleep(_seconds):
    return None


def frame(command: int, *values: int, size: int = 20, cmd_at: int = 1) -> bytes:
    out = bytearray(size)
    out[0] = 0x0F
    out[cmd_at] = command
    for offset, value in enumerate(values):
        out[cmd_at + 1 + offset] = value
    return bytes(out)


class Mc5000EdgeTest(unittest.IsolatedAsyncioTestCase):
    def test_unknown_chemistries_and_values(self) -> None:
        with self.assertRaises(ValueError):
            mc5000.mode_byte("plutonium", "charge")
        with self.assertRaises(ValueError):
            mc5000.mode_byte("liion", "break_in")
        self.assertEqual(mc5000.mode_name("liion", 99), "unknown_99")

    def test_the_stream_is_resynchronised_on_the_start_byte(self) -> None:
        client = mc5000.Mc5000Client(Device())
        client._notify_handler(None, bytearray(b"\x01\x02\x0f\x03\x06\x01\x02\x03"))
        self.assertEqual(client._notify_queue.get_nowait(), b"\x0f\x03\x06\x01\x02")
        client._notify_handler(None, bytearray(b"\x01\x02"))  # nothing to start from
        self.assertEqual(len(client._rx_buffer), 0)

    async def test_connect_while_connected_does_nothing(self) -> None:
        client = mc5000.Mc5000Client(Device())
        wire(client)
        _harness.establish_connection_calls.clear()
        await client.connect()
        self.assertEqual(_harness.establish_connection_calls, [])

    async def test_a_probe_reads_the_model_and_a_failed_handshake_cleans_up(self) -> None:
        client = mc5000.Mc5000Client(Device())

        async def info():
            return mc5000.DeviceData(model_code="100214")

        client._query_device_info = info
        with unittest.mock.patch("asyncio.wait_for", new=_timeout):
            await client.connect(probe=True)
        self.assertEqual(client.model_code, "100214")
        self.assertEqual(client.model_code, "100214")
        await client.disconnect()
        await client.disconnect()

        failing = mc5000.Mc5000Client(Device())

        async def boom():
            raise BleakError("no")

        failing._async_handshake = boom
        with unittest.mock.patch("asyncio.wait_for", new=_timeout), self.assertRaises(BleakError):
            await failing.connect()
        self.assertFalse(failing.is_connected)

    async def test_sending_without_a_link_fails(self) -> None:
        with self.assertRaises(BleakError):
            await mc5000.Mc5000Client(Device())._send(0x57)

    async def test_a_request_skips_unrelated_notifications(self) -> None:
        client = mc5000.Mc5000Client(Device())
        client._client = FakeBleakClient()
        client._connected = True
        client._notify_queue.put_nowait(b"\x0f\x02\x06\x08")
        sent = await client._send(0x57, expect_response=False)
        self.assertIsNone(sent)

        async def feed():
            await asyncio.sleep(0)
            client._notify_queue.put_nowait(b"\x0f\x03\x06\x01\x07")
            await asyncio.sleep(0)
            client._notify_queue.put_nowait(b"\x0f\x04\x57\x01\x02\x03")

        task = asyncio.create_task(feed())
        answer = await client._request(0x57)
        await task
        self.assertEqual(answer[2], 0x57)

        async def nothing(_aw, timeout):
            _aw.close()
            raise TimeoutError

        client._notify_queue.put_nowait(b"\x0f\x03\x06\x01\x07")
        with unittest.mock.patch("asyncio.wait_for", new=nothing):
            self.assertIsNone(await client._request(0x99, timeout=0))

    def test_device_info_of_a_foreign_model_is_still_read(self) -> None:
        resp = bytearray(20)
        resp[3:9] = b"999999"
        info = mc5000.Mc5000Client._parse_device_info(bytes(resp), "AA")
        self.assertEqual(info.model_code, "999999")
        self.assertIsNone(mc5000.Mc5000Client._parse_device_info(b"\x00", "AA"))

    async def test_the_info_request_survives_a_bad_address_and_silence(self) -> None:
        client = mc5000.Mc5000Client(NoMacDevice())
        asked = wire(client, default=None)
        self.assertIsNone(await client._query_device_info())
        self.assertEqual(len(asked), 3)

        client = mc5000.Mc5000Client(Device())
        wire(client, {0x57: b"\x0f\x01"})  # too short to parse
        self.assertIsNone(await client._query_device_info())

    async def test_short_status_and_empty_answers(self) -> None:
        client = mc5000.Mc5000Client(Device())
        self.assertEqual(client._parse_channel(0, b"\x0f").status, "unavailable")
        wire(client, default=None)
        state = await client.async_update()
        self.assertTrue(all(c.status == "unavailable" for c in state.channels))
        client._connected = False
        with self.assertRaises(BleakError):
            await client.async_update()

    async def test_settings_that_never_come_are_an_error(self) -> None:
        client = mc5000.Mc5000Client(Device())
        wire(client, default=None)
        with unittest.mock.patch("asyncio.sleep", new=no_sleep), self.assertRaises(BleakError):
            await client.async_set_settings(beep_volume="low")

    async def test_the_curve_stops_on_a_foreign_slot_or_a_missing_page(self) -> None:
        client = mc5000.Mc5000Client(Device())
        page = bytearray(12)
        page[:3] = b"\x0f\x0a\x98"
        page[3], page[4], page[5], page[6] = 2, 2, 1, 30  # slot 2, total 2, page 1
        wire(client, {0x98: bytes(page)})
        curve = await client.async_get_voltage_curve(0)  # asked for slot 1
        self.assertEqual(curve.samples_mv, [])

        page[3] = 1
        wire(client, {0x98: bytes(page)})

        async def nothing(_aw, timeout):
            _aw.close()
            raise TimeoutError

        with unittest.mock.patch("asyncio.wait_for", new=nothing):
            curve = await client.async_get_voltage_curve(0)
        self.assertFalse(curve.checksum_ok)

    async def test_start_all_writes_every_program_after_building_them(self) -> None:
        client = mc5000.Mc5000Client(Device())
        asked = wire(client, {0x94: b"\x0f\x03\x94\x00\x01"})
        await client.start_all(chemistry="liion")
        self.assertEqual([c for c, _ in asked].count(0x94), 4)
        with self.assertRaises(ValueError):
            await client.start_all(per_channel={2: {"chemistry": "plutonium"}})

        refused = mc5000.Mc5000Client(Device())
        wire(refused, {0x94: b"\x0f\x03\x94\x00\x00"})
        with self.assertRaises(BleakError):
            await refused.start_channel(0, chemistry="liion")


async def _timeout(aw, timeout):
    aw.close()
    raise TimeoutError


class Mc3000EdgeTest(unittest.IsolatedAsyncioTestCase):
    def test_the_stream_is_resynchronised(self) -> None:
        client = mc3000.Mc3000Client(Device())
        client._notify_handler(None, bytearray(b"\x01\x02") + bytearray(frame(0x55)))
        self.assertEqual(len(client._notify_queue.get_nowait()), 20)
        client._notify_handler(None, bytearray(b"\x01\x02"))
        self.assertEqual(len(client._rx_buffer), 0)

    async def test_the_handshake_and_the_info_request(self) -> None:
        client = mc3000.Mc3000Client(NoMacDevice())
        info = frame(0x57)
        asked = wire(client, {0x57: info, 0x61: frame(0x61, 1, 1, 2, 1, 3, 0x2B, 0x67)})
        await client._async_handshake()
        self.assertEqual(client._device_info.sw_version, "0.00")
        self.assertTrue(client._fahrenheit)
        self.assertTrue(client._device_info.beep)
        self.assertEqual(client._device_info.display_timeout, "1_min")
        self.assertEqual(client._device_info.cooling_fan, "20")
        self.assertEqual(client._device_info.input_voltage, 11.1)
        self.assertEqual(asked[0][1][1:], bytes(6))  # no usable address: zeros

        silent = mc3000.Mc3000Client(Device())
        wire(silent, default=None)
        self.assertIsNone(await silent._query_device_info())
        self.assertFalse(silent.identified)
        await silent._read_basic()  # no answer: nothing changes

    def test_a_short_status_and_an_unknown_state(self) -> None:
        client = mc3000.Mc3000Client(Device())
        self.assertEqual(client._parse_slot(0, b"\x0f").status, "unavailable")
        packet = bytearray(frame(0x55, 0, 9, 9, 0, 9))  # type 9, mode 9
        packet[6] = 77
        slot = client._parse_slot(0, bytes(packet))
        self.assertEqual(slot.status, "unknown_77")
        self.assertEqual(slot.chemistry, "naion")

    async def test_updates_and_curves_without_answers(self) -> None:
        client = mc3000.Mc3000Client(Device())
        wire(client, default=None)
        state = await client.async_update()
        self.assertTrue(all(c.status == "unavailable" for c in state.channels))
        self.assertEqual((await client.async_get_voltage_curve(0)).samples_mv, [])
        client._connected = False
        with self.assertRaises(BleakError):
            await client.async_update()
        client._client = None
        with self.assertRaises(BleakError):
            await client._write_frame(bytes(20))
        client.set_passcode("anything")

    def test_program_options(self) -> None:
        build = mc3000.Mc3000Client(Device())._build_program
        self.assertEqual(
            build(0, chemistry="eneloop", eneloop_type=1)[5:7], (800).to_bytes(2, "big")
        )
        with self.assertRaises(ValueError):
            build(0, chemistry="eneloop", eneloop_type=99)
        with self.assertRaises(ValueError):
            build(0, chemistry="nimh", mode="storage")
        with self.assertRaises(ValueError):
            build(0, chemistry="nimh", cycle_direction=4)
        big = build(0, chemistry="nimh", mode="break_in", capacity=20000)
        self.assertEqual(int.from_bytes(big[9:11], "big"), 2000)
        huge = build(0, chemistry="nimh", mode="break_in", capacity=40000)
        self.assertEqual(int.from_bytes(huge[7:9], "big"), 3000)
        build(0, chemistry="naion", keep_mv=0, temperature_cutoff_c=0, charge_cutoff_ma=10)
        with self.assertRaises(ValueError):
            build(0, chemistry="liion", keep_mv=1)
        with self.assertRaises(ValueError):
            build(0, chemistry="liion", temperature_cutoff_c=5)

    async def test_start_all_and_the_last_program_is_reused(self) -> None:
        client = mc3000.Mc3000Client(Device())
        sent: list[tuple[int, bytes]] = []

        async def program(channel, payload):
            sent.append((channel, payload))

        client._send_program = program
        await client.start_all(
            per_channel={
                0: {"chemistry": "nimh"},
                1: {"chemistry": "liion"},
                2: {"chemistry": "nimh"},
                3: {"chemistry": "nimh"},
            }
        )
        self.assertEqual([c for c, _ in sent], [0, 1, 2, 3])
        await client.start_channel(1)  # same chemistry as last time
        self.assertEqual(sent[-1][1][3], 0)

    async def test_settings_edge_cases(self) -> None:
        client = mc3000.Mc3000Client(Device())
        with self.assertRaises(BleakError):
            await client.async_set_settings(beep=True)  # not read yet
        info = client._device_info
        info.beep, info.display_timeout, info.screensaver = True, "auto", False
        info.cooling_fan, info.input_voltage = "auto", 11.0
        with self.assertRaises(ValueError):
            await client.async_set_settings(display_timeout="never")
        with self.assertRaises(ValueError):
            await client.async_set_settings(cooling_fan="turbo")
        with self.assertRaises(ValueError):
            await client.async_set_settings(input_voltage=15.0)
        wire(client, default=None)
        with unittest.mock.patch("asyncio.sleep", new=no_sleep), self.assertRaises(BleakError):
            await client.async_set_settings(beep=False)


class Nc2600EdgeTest(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_short_frames_and_errors(self) -> None:
        client = nc2600.Nc2600Client(Device())
        wire(client, {0x57: frame(0x57)})
        await client._async_handshake()
        self.assertTrue(client.identified)
        self.assertEqual(client._parse_slot(0, b"\x0f").status, "unavailable")
        with self.assertRaises(NotImplementedError):
            await client.async_get_voltage_curve(0)
        with self.assertRaises(ValueError):
            await client.stop_all()
        with self.assertRaises(ValueError):
            client._build_program(0, mode="sleep")

    async def test_a_program_is_sent_and_a_silent_charger_is_an_error(self) -> None:
        client = nc2600.Nc2600Client(Device())
        asked = wire(client, {0x11: frame(0x11)})
        await client.start_channel(1, mode="charge", current=500)
        self.assertEqual(asked[0][0], 0x11)
        silent = nc2600.Nc2600Client(Device())
        wire(silent, default=None)
        with self.assertRaises(BleakError):
            await silent.start_channel(0)

    def test_an_unknown_mode_byte(self) -> None:
        client = nc2600.Nc2600Client(Device())
        packet = bytearray(frame(0x55, 0, 31, 1, 0, 10, 5, 0x1F))
        slot = client._parse_slot(0, bytes(packet))
        self.assertEqual(slot.mode, "unknown_31")


class OtherEdgeTest(unittest.IsolatedAsyncioTestCase):
    def test_nc3000_packets_of_every_length(self) -> None:
        parse = nc3000.Nc3000Client._parse_channel
        self.assertEqual(parse(0, b"\x0f").status, "unavailable")
        for size in (18, 20, 22):
            packet = bytearray(size)
            packet[6:8] = (1300).to_bytes(2, "big")
            slot = parse(0, bytes(packet))
            self.assertEqual(slot.voltage, 1.3)

    async def test_nc2200_and_type228_without_answers(self) -> None:
        for module, cls in ((nc2200, "Nc2200Client"), (type228, "Type228Client")):
            client = getattr(module, cls)(Device())
            wire(client, default=None)
            state = await client.async_update()
            self.assertTrue(all(c.status == "unavailable" for c in state.channels))
            client._connected = False
            with self.assertRaises(BleakError):
                await client.async_update()
            with self.assertRaises(ValueError):
                await client.start_channel(0)

    async def test_psu650_updates(self) -> None:
        client = psu650.Psu650Client(Device())
        asked = wire(client, {0x57: frame(0x57), 0x74: frame(0x74, 0, 0, 1, cmd_at=2)})
        await client._async_handshake()
        self.assertGreaterEqual(len(asked), 2)

        wire(client, default=None)
        state = await client.async_update()
        self.assertTrue(all(c.status == "unavailable" for c in state.channels))

        realtime = bytearray(60)
        realtime[:3] = b"\x0f\x3a\xb0"
        realtime[4:6] = (655).to_bytes(2, "big")
        realtime[6] = 3  # error code, as the realtime layout has it
        wire(client, {0xB0: bytes(realtime), 0xB1: None})
        state = await client.async_update()
        self.assertEqual(len(state.channels), 4)
        psu650.Psu650Client._apply_pd_detail(state.channels, None)
        with self.assertRaises(ValueError):
            await client.start_channel(0)
        client._connected = False
        with self.assertRaises(BleakError):
            await client.async_update()

    async def test_legacy_handshake_and_updates(self) -> None:
        cls = legacy.make_legacy_client("q200")
        client = cls(Device())
        with self.assertRaises(ValueError):
            client.set_passcode("12")
        client.set_passcode("5793")
        refused = bytearray(20)
        refused[12] = 0
        wire(client, {0x57: frame(0x57), 0x5F: bytes(refused)})
        await client._async_handshake()
        self.assertFalse(client.unlocked)
        wire(client, {0x57: None, 0x5F: b"\x0f"})
        await client._async_handshake()
        self.assertTrue(client.unlocked)

        wire(client, {0x55: None})
        state = await client.async_update()
        self.assertTrue(all(c.status == "unavailable" for c in state.channels))
        wire(client, {0x55: frame(0x55, 0, 0, 7, 0)})
        state = await client.async_update()
        self.assertEqual(state.channels[0].status, "dc_output")
        self.assertEqual(client._parse_channel(0, b"\x0f").status, "unavailable")
        client._connected = False
        with self.assertRaises(BleakError):
            await client.async_update()
        with self.assertRaises(ValueError):
            await client.start_channel(0)
        with self.assertRaises(ValueError):
            await client.start_all()


if __name__ == "__main__":
    unittest.main()

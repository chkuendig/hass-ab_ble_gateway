"""Tests for the gateway message decoder and advertisement parser.

util.py has no Home Assistant dependency, so it is loaded straight from its
file to keep these tests runnable with only msgpack and pytest installed.
"""
import importlib.util
from pathlib import Path

import msgpack

UTIL_PATH = Path(__file__).parents[1] / "custom_components" / "ab_ble_gateway" / "util.py"
_spec = importlib.util.spec_from_file_location("ab_ble_gateway_util", UTIL_PATH)
util = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(util)

MAC = bytes.fromhex("5C857EB009F2")


def _device(adv_structs: list[bytes], rssi: int = -70) -> bytes:
    """Build a gateway device entry: type, MAC, RSSI, advertisement data.

    https://wiki.aprbrother.com/en/User_Guide_For_AB_BLE_Gateway_V4.html#data-format
    """
    payload = b"".join(bytes([len(s) + 0]) + s for s in adv_structs)
    return bytes([0]) + MAC + bytes([rssi & 0xFF]) + payload


def _message(devices: list[bytes]) -> bytes:
    return msgpack.packb(
        {"v": "1.5.13", "mid": 1, "time": 1234, "ip": "192.168.0.2",
         "mac": "C45BBE8E518C", "devices": devices},
        use_bin_type=True,
    )


FLAGS = b"\x01\x06"
SERVICE_DATA = b"\x16\x95\xfe" + bytes.fromhex("7120980000f209b07e855c0d")
MANUF_APPLE = b"\xff\x4c\x00" + bytes.fromhex("0215")
MANUF_OTHER = b"\xff\x59\x00" + bytes.fromhex("aabb")


def _parse(device: bytes):
    return util.parse_raw_data(util.parse_ap_ble_devices_data(device))


def test_parse_service_data():
    adv = _parse(_device([FLAGS, SERVICE_DATA, b"\x09Flower care"]))
    assert adv["address"] == "5C:85:7E:B0:09:F2"
    assert adv["rssi"] == -70
    assert adv["local_name"] == "Flower care"
    assert adv["service_data"] == {
        "0000fe95-0000-1000-8000-00805f9b34fb": bytes.fromhex("7120980000f209b07e855c0d")
    }
    assert adv["manufacturer_data"] == {}


def test_parse_multiple_manufacturer_data_blocks():
    """Used to `raise "..."`, i.e. TypeError: exceptions must derive from BaseException."""
    adv = _parse(_device([FLAGS, MANUF_APPLE, MANUF_OTHER]))
    assert adv["manufacturer_data"] == {
        0x004C: bytes.fromhex("0215"),
        0x0059: bytes.fromhex("aabb"),
    }


def test_parse_overlong_ad_structure_is_skipped():
    device = bytearray(_device([FLAGS, SERVICE_DATA]))
    device[8] = 0x40  # AD structure length runs past the payload
    adv = _parse(bytes(device[:9]))
    assert adv["service_data"] == {} and adv["manufacturer_data"] == {}


def test_parse_too_short_entry_raises_index_error():
    """The scanner catches IndexError per entry and skips it."""
    try:
        _parse(MAC)
    except IndexError:
        return
    raise AssertionError("expected IndexError")


def test_iter_complete_message():
    devices = [_device([FLAGS, SERVICE_DATA]), _device([FLAGS, MANUF_APPLE])]
    assert list(util.iter_gateway_devices(_message(devices))) == devices


def test_iter_truncated_message_keeps_complete_entries():
    """Used to drop the whole batch with 'Unpack failed: incomplete input'."""
    devices = [_device([FLAGS, SERVICE_DATA]) for _ in range(5)]
    payload = _message(devices)
    for cut in range(len(payload)):
        got = list(util.iter_gateway_devices(payload[:cut]))
        assert got == devices[: len(got)]
    # Cutting inside the last entry keeps the first four.
    assert len(list(util.iter_gateway_devices(payload[:-1]))) == 4


def test_iter_garbage_and_missing_devices():
    assert list(util.iter_gateway_devices(b"")) == []
    assert list(util.iter_gateway_devices(b"\xc1\xc1\xc1")) == []
    assert list(util.iter_gateway_devices(msgpack.packb([1, 2, 3]))) == []
    assert list(util.iter_gateway_devices(msgpack.packb({"v": 1}))) == []

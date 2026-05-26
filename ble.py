"""
BLE client for Omron HEM-7144T2 (and similar BLESmart devices).

Protocol (reverse-engineered):
  - Proprietary service 0xFE4A holds OMRON_CMD (write-only) char
  - Write bytes(20) to OMRON_CMD triggers dump of all stored records
  - Records arrive as standard GATT BP Measurement (0x2A35) indications
  - End of transfer detected by 3s silence after last notification
  - Current Time Service (0x1805) written on connect so future records have timestamps
"""

import asyncio
import struct
from datetime import datetime

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

OMRON_NAME_PREFIX = ("OMRON", "HEM-", "BLESMART")

BP_MEASURE   = "00002a35-0000-1000-8000-00805f9b34fb"
OMRON_CMD    = "db5b55e0-aee7-11e1-965e-0002a5d5c51b"
CURRENT_TIME = "00002a2b-0000-1000-8000-00805f9b34fb"

TRIGGER_CMD       = bytes(20)   # 20 zero bytes triggers full record dump
SILENCE_TIMEOUT   = 3.0         # seconds of no new records = transfer done
FIRST_REC_TIMEOUT = 15.0        # seconds to wait for first record before giving up


def _sfloat(raw: int) -> float:
    """Decode IEEE-11073 16-bit SFLOAT."""
    exp = (raw >> 12) & 0xF
    if exp >= 8:
        exp -= 16
    mantissa = raw & 0x0FFF
    if mantissa >= 0x0800:
        mantissa -= 0x1000
    return mantissa * (10 ** exp)


def parse_bp_measurement(data: bytes) -> dict | None:
    """Parse GATT Blood Pressure Measurement (0x2A35), 19-byte Omron format."""
    if len(data) < 7:
        return None

    flags     = data[0]
    kpa       = bool(flags & 0x01)
    has_ts    = bool(flags & 0x02)
    has_pulse = bool(flags & 0x04)
    has_user  = bool(flags & 0x08)

    systolic  = _sfloat(struct.unpack_from("<H", data, 1)[0])
    diastolic = _sfloat(struct.unpack_from("<H", data, 3)[0])
    mean_ap   = _sfloat(struct.unpack_from("<H", data, 5)[0])
    offset = 7

    result: dict = {
        "systolic":      round(systolic),
        "diastolic":     round(diastolic),
        "mean_arterial": round(mean_ap),
        "unit": "kPa" if kpa else "mmHg",
        "timestamp":     None,
    }

    if has_ts and len(data) >= offset + 7:
        year               = struct.unpack_from("<H", data, offset)[0]
        month, day, h, m, s = data[offset + 2: offset + 7]
        try:
            result["timestamp"] = datetime(year, month, day, h, m, s)
        except ValueError:
            pass
        offset += 7

    if has_pulse and len(data) >= offset + 2:
        result["pulse"] = round(_sfloat(struct.unpack_from("<H", data, offset)[0]))
        offset += 2

    if has_user and len(data) >= offset + 1:
        result["user"] = data[offset]

    return result


def _current_time_bytes() -> bytes:
    """Pack current time for GATT Current Time characteristic (0x2A2B)."""
    now = datetime.now()
    return struct.pack(
        "<HBBBBBBB",
        now.year, now.month, now.day,
        now.hour, now.minute, now.second,
        now.isoweekday(),  # 1=Mon … 7=Sun
        0,                 # fractions256
        1,                 # adjust reason: manual
    )


async def scan_devices(
    timeout: float = 10.0, all_devices: bool = False
) -> list[tuple[BLEDevice, AdvertisementData]]:
    """Return (device, adv) pairs. all_devices=True returns everything sorted by RSSI."""
    found: dict[str, tuple[BLEDevice, AdvertisementData]] = await BleakScanner.discover(
        timeout=timeout, return_adv=True
    )
    pairs = list(found.values())
    if all_devices:
        return sorted(pairs, key=lambda p: p[1].rssi or -999, reverse=True)
    return [
        (dev, adv) for dev, adv in pairs
        if dev.name and any(dev.name.upper().startswith(p) for p in OMRON_NAME_PREFIX)
    ]


async def sync_records(address: str, progress_cb=None) -> list[dict]:
    """
    Connect, trigger record dump, return all BP records.
    Mirrors the subscription sequence that probe confirmed works.
    progress_cb(record) called for each record as it arrives.
    """
    OMRON_DATA   = "b305b680-aee7-11e1-a730-0002a5d5c51b"
    OMRON_STATUS = "49123040-aee8-11e1-a74d-0002a5d5c51b"

    records: list[dict] = []
    got_first   = asyncio.Event()
    last_ts: list[float] = [0.0]

    def on_bp_measure(_, data: bytearray):
        rec = parse_bp_measurement(bytes(data))
        if rec:
            records.append(rec)
            last_ts[0] = asyncio.get_event_loop().time()
            got_first.set()
            if progress_cb:
                progress_cb(rec)

    def _noop(_, __):
        pass

    async with BleakClient(address) as client:
        if not client.is_connected:
            raise RuntimeError("Failed to connect")

        # Subscribe to all notifiable chars — device checks these before sending
        await client.start_notify(BP_MEASURE, on_bp_measure)
        try:
            await client.start_notify(OMRON_DATA, _noop)
        except Exception:
            pass
        try:
            await client.start_notify(OMRON_STATUS, _noop)
        except Exception:
            pass

        # Wait for subscriptions to fully register with device
        await asyncio.sleep(1.0)

        # Trigger full record dump
        await client.write_gatt_char(OMRON_CMD, TRIGGER_CMD, response=True)

        # Wait for first record
        try:
            await asyncio.wait_for(got_first.wait(), timeout=FIRST_REC_TIMEOUT)
        except asyncio.TimeoutError:
            raise RuntimeError(
                "No records received. Device awake? (Bluetooth symbol blinking on display)"
            )

        # Drain until 3s silence = transfer complete
        while True:
            await asyncio.sleep(0.25)
            if asyncio.get_event_loop().time() - last_ts[0] >= SILENCE_TIMEOUT:
                break

        await client.stop_notify(BP_MEASURE)

    return records

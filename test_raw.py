import asyncio
from bleak import BleakClient

ADDRESS      = "1576FF02-2482-04BC-37A7-E6735622B2D3"
BP_MEAS      = "00002a35-0000-1000-8000-00805f9b34fb"
OMRON_CMD    = "db5b55e0-aee7-11e1-965e-0002a5d5c51b"
OMRON_DATA   = "b305b680-aee7-11e1-a730-0002a5d5c51b"
OMRON_STATUS = "49123040-aee8-11e1-a74d-0002a5d5c51b"

async def main():
    async with BleakClient(ADDRESS) as c:
        print("connected")

        await c.start_notify(BP_MEAS,      lambda _, d: print("BP_MEAS:", d.hex()))
        await c.start_notify(OMRON_DATA,   lambda _, d: print("OMRON_DATA:", d.hex()))
        await c.start_notify(OMRON_STATUS, lambda _, d: print("OMRON_STATUS:", d.hex()))
        print("subscribed to all 3")

        # Read device info (probe did this before trigger)
        for uuid, label in [
            ("00002a29-0000-1000-8000-00805f9b34fb", "Manufacturer"),
            ("00002a24-0000-1000-8000-00805f9b34fb", "Model"),
        ]:
            val = await c.read_gatt_char(uuid)
            print(f"read {label}: {bytes(val).decode()}")

        await asyncio.sleep(1)
        await c.write_gatt_char(OMRON_CMD, bytes(20), response=True)
        print("triggered — waiting 15s…")
        await asyncio.sleep(15)
        print("done")

asyncio.run(main())

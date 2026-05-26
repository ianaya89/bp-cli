# omron-bp

CLI to sync and display blood pressure records from Omron BLESmart BLE monitors on macOS.

Reverse-engineered from the HEM-7144T2 (wrist monitor). Works with any Omron device that advertises as `BLESmart_*` and exposes the `0xFE4A` proprietary service.

## Requirements

- macOS (CoreBluetooth)
- Python 3.11+
- Terminal with Bluetooth permission

## Install

```bash
pip install -r requirements.txt
ln -sf "$PWD/bp.py" ~/.local/bin/bp
```

Grant Bluetooth access: **System Settings → Privacy & Security → Bluetooth → add Terminal.app**

## Usage

```bash
# 1. Put device in advertising mode: press Bluetooth button once (symbol blinks)
bp scan                          # find device UUID
bp sync <UUID>                   # pull all new records
bp list                          # show records + avg/min/max footer
bp stats                         # averages, min, max per metric
```

### All commands

| Command | Description |
|---------|-------------|
| `bp scan` | Scan for nearby Omron BLE devices |
| `bp sync <uuid>` | Sync new records from device |
| `bp list` | List stored records with stats footer |
| `bp stats` | Averages, min, max from all records |
| `bp discover <uuid>` | Dump all GATT services/characteristics |
| `bp probe <uuid>` | Raw debug: subscribe + dump bytes |

### Options

```bash
bp scan --timeout 15             # longer scan window
bp scan --all                    # show all BLE devices (not just Omron)
bp list --user 2                 # filter by user slot
bp list --limit 20
bp sync <uuid> --user 1
bp stats --user 2
```

## Data

Records stored at `~/.omron-bp/records.db` (SQLite).

BP classification (color-coded in output):

| Color | Systolic | Diastolic |
|-------|----------|-----------|
| Green | < 130 | < 80 |
| Yellow | 130–139 | 80–89 |
| Red | ≥ 140 | ≥ 90 |

## How sync works

The device only sends records that haven't been transferred yet — it tracks the transfer state internally. After a successful sync, those records won't be sent again until new measurements are taken.

Protocol: write `bytes(20)` to the Omron proprietary CMD characteristic → records arrive as standard GATT Blood Pressure Measurement (0x2A35) indications → 3s silence = transfer complete.

## Device compatibility

Confirmed: Omron HEM-7144T2

Likely works: HEM-6232T, HEM-7155T, HEM-7361T, and other recent Omron BLE wrist/arm monitors that advertise as `BLESmart_*`.

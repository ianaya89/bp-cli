# omron-bp

CLI and local web UI to sync and display blood pressure records from Omron BLESmart BLE monitors. Works on **macOS** (CoreBluetooth) and **Linux** (BlueZ).

Reverse-engineered from the HEM-7144T2 (wrist monitor). Works with any Omron device that advertises as `BLESmart_*` and exposes the `0xFE4A` proprietary service.

## Requirements

- Python 3.10+
- **macOS**: grant Bluetooth permission — System Settings → Privacy & Security → Bluetooth → add Terminal (or your terminal app)
- **Linux**: BlueZ installed (`bluez` package), bluetooth service running (`systemctl status bluetooth`). Device address is a MAC (`AA:BB:CC:DD:EE:FF`). On most desktop installs the current user can already access BlueZ over D-Bus; if `obp scan` fails with a permissions error, check your D-Bus policy.

## Install

### pipx (recommended — macOS and Linux)

```bash
pipx install git+https://github.com/ianaya89/omron-bp.git          # latest from main
pipx install git+https://github.com/ianaya89/omron-bp.git@v0.1.0   # a pinned release
```

Or from a local clone:

```bash
pipx install -e .
```

pipx isolates dependencies and gives you a global `obp` command. If your `pipx` is backed by a broken Python build, pass a working interpreter explicitly:

```bash
pipx install -e . --python $(which python3)
```

### pip / development

```bash
pip install -e .   # inside a virtualenv
```

## Usage

```bash
# 1. Wake the device: press its Bluetooth button once (symbol blinks)
obp scan                          # find device address / UUID
obp sync <name|uuid|mac>          # pull new records (retries 3x)
obp list                          # show records grouped into 5-min sessions
obp stats                         # averages, min, max
```

### All commands

| Command | Description |
|---------|-------------|
| `obp scan` | Scan for nearby Omron BLE devices |
| `obp sync <name\|uuid\|mac>` | Pull new records from device (3 retries) |
| `obp list` | Records grouped into 5-min sessions; lowest reading shown per session |
| `obp stats` | Averages, min, max |
| `obp chart` | Terminal line plot of systolic/diastolic over time |
| `obp pdf` | Export printable PDF report (table + chart) |
| `obp add <sys> <dia>` | Manually add a record |
| `obp edit <id>` | Edit a record by ID |
| `obp rm <id>` | Delete a record by ID |
| `obp tags add/ls/rm` | Manage the tag vocabulary |
| `obp web` | Local dark web UI (Tailwind, fully offline): browse, add/edit/delete, filter, session grouping |
| `obp devices add/ls/rm` | Manage saved device aliases |
| `obp discover <uuid>` | Dump all GATT services/characteristics |
| `obp probe <uuid>` | Raw debug: subscribe + dump bytes |

### Options and examples

```bash
# scan
obp scan --timeout 15             # longer scan window
obp scan --all                    # show all BLE devices, not just Omron

# sync
obp sync omron                    # use saved alias
obp sync <uuid> --user 1          # specific user slot
obp sync <mac> --debug            # verbose BLE output

# list
obp list --user 2
obp list --limit 20
obp list --days 30                # last 30 days (default; --days 0 = all time)
obp list --since 2026-05-01 --until 2026-05-31
obp list --tag morning            # filter by tag
obp list --expand                 # reveal every reading inside each session
obp list --no-group               # flat list, no session grouping
obp list --window 10              # change session window to 10 minutes

# tags
obp tags add morning              # define a tag in the vocabulary
obp tags ls
obp tags rm morning               # also strips the tag from all records

# add / edit / remove
obp add 120 80 --pulse 65 --timestamp '2024-01-15 08:30'
obp add 118 76 --tag morning      # tag must exist in vocabulary first
obp add 118 76 --tag morning --tag evening   # multiple tags
obp add -i                        # interactive prompt
obp edit 42 --systolic 118 -i
obp edit 12 --add-tag morning --rm-tag old
obp rm 42 --yes

# stats / chart / pdf
obp stats --user 2 --tag morning
obp chart --pulse
obp pdf                           # → YYYYMMDD_bp_report.pdf in cwd
obp pdf --output ~/Desktop/obp.pdf --user 1 --limit 100 --pulse --tag morning

# devices
obp devices add omron <uuid>
obp devices ls
obp devices rm omron

# web UI
obp web                           # http://127.0.0.1:8000
obp web --host 0.0.0.0 --port 9000
```

## Data

All data lives under `~/.omron-bp/`:

| File | Contents |
|------|----------|
| `records.db` | SQLite database of readings |
| `devices.json` | Saved device aliases |
| `tags.json` | Tag vocabulary |

BP classification (color-coded in output):

| Color | Systolic | Diastolic |
|-------|----------|-----------|
| Green | < 130 | < 80 |
| Yellow | 130–139 | 80–89 |
| Red | ≥ 140 | ≥ 90 |

## How sync works

The device only sends records that haven't been transferred yet — it tracks the transfer state internally. After a successful sync, those records won't be sent again until new measurements are taken. Wake the monitor by pressing its Bluetooth button (the BLE symbol blinks), then run `obp sync` immediately.

Protocol: write `bytes(20)` to the Omron proprietary CMD characteristic → records arrive as standard GATT Blood Pressure Measurement (0x2A35) indications → 3s silence = transfer complete.

## Device compatibility

Confirmed: Omron HEM-7144T2

Likely works: HEM-6232T, HEM-7155T, HEM-7361T, and other recent Omron BLE wrist/arm monitors that advertise as `BLESmart_*`.

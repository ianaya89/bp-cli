# omron-bp

[![CI](https://github.com/ianaya89/omron-bp/actions/workflows/ci.yml/badge.svg)](https://github.com/ianaya89/omron-bp/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/ianaya89/omron-bp?sort=semver)](https://github.com/ianaya89/omron-bp/releases)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A Python CLI **and** local web UI to **sync**, **browse**, **tag**, **chart** and **report** blood pressure readings from Omron BLESmart BLE monitors. Cross-platform: **macOS** (CoreBluetooth) and **Linux** (BlueZ), via the `bleak` library. CLI built with `typer` + `rich`.

Reverse-engineered from the Omron HEM-7144T2 (wrist monitor). Works with any Omron device that advertises as `BLESmart_*` and exposes the `0xFE4A` proprietary service.

**Features:** BLE sync from Omron monitors · session grouping (lowest reading per sitting) · tag vocabulary · terminal chart · PDF report · local web UI (offline, dark) · manual add/edit/delete · saved device aliases · macOS + Linux.

## Sessions and tags

The Omron monitor stores several readings per sitting. `obp list` groups readings taken within **5 minutes** into one *session* and shows the **lowest reading** as the representative (the clinically recommended value), marked with a `▸N` badge where N is the number of readings in the group.

- `--expand` reveals every reading inside a session.
- `--no-group` switches to a flat list with no grouping.
- `--window N` changes the grouping window from the default 5 minutes.

**Tags** are a controlled vocabulary you manage with `obp tags add/ls/rm`. There is no rename — remove the tag and re-add it with the new name (which also strips it from all records). Once a tag exists in the vocabulary you can attach it to records and filter by it across `obp list`, `obp stats`, and `obp pdf`.

```
~/.omron-bp/
├── records.db     # all readings
└── tags.json      # controlled tag vocabulary
```

## Installation

| Platform | Recommended |
| --- | --- |
| macOS | pipx |
| Linux | pipx |
| Any (with Python) | from source |

### pipx (recommended — macOS and Linux)

```sh
pipx install git+https://github.com/ianaya89/omron-bp.git          # latest from main
pipx install git+https://github.com/ianaya89/omron-bp.git@v0.1.0   # pinned release
```

pipx isolates dependencies and gives you a global `obp` command.

> If your `pipx` is backed by a broken Python build, pass a working interpreter explicitly: `pipx install git+https://github.com/ianaya89/omron-bp.git --python $(which python3)`

### From source / development

```sh
git clone https://github.com/ianaya89/omron-bp && cd omron-bp && pip install -e .
```

Run inside a virtualenv.

### Bluetooth setup

> **macOS** — grant Bluetooth permission: System Settings → Privacy & Security → Bluetooth → add Terminal (or your terminal app).

> **Linux** — `bluez` package installed, bluetooth service running (`systemctl status bluetooth`). Device address is a MAC (`AA:BB:CC:DD:EE:FF`). On most desktop installs the current user can already access BlueZ over D-Bus; if `obp scan` hits a permissions error, check your D-Bus policy.

## Usage

```sh
# Quick start: wake the device (press its Bluetooth button once — symbol blinks)
obp scan                          # find device address / UUID
obp sync <name|uuid|mac>          # pull new records (retries 3x)
obp list                          # show records grouped into 5-min sessions
obp stats                         # averages, min, max
```

### Examples

```sh
# scan
obp scan --timeout 15             # longer scan window
obp scan --all                    # show all BLE devices, not just Omron

# sync
obp sync omron                    # use saved alias
obp sync <uuid> --user 1          # specific user slot
obp sync <mac> --debug            # verbose BLE output

# list — grouping and filtering
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

> `obp list` reopens with the same grouping settings you last used. The `▸N` badge on a session row tells you how many readings are merged — use `--expand` to see them all.

## Commands

| Command | Description |
| --- | --- |
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

## Data

All data lives under `~/.omron-bp/`:

| File | Contents |
| --- | --- |
| `records.db` | SQLite database of readings |
| `devices.json` | Saved device aliases |
| `tags.json` | Tag vocabulary |

BP classification (color-coded in terminal output):

| Color | Systolic | Diastolic |
| --- | --- | --- |
| Green | < 130 | < 80 |
| Yellow | 130–139 | 80–89 |
| Red | ≥ 140 | ≥ 90 |

## How it works

- **Transfer state** — the device only sends records that haven't been transferred yet; it tracks this state internally. After a successful sync, those records won't be sent again until new measurements are taken.
- **Waking the device** — press the Bluetooth button once (the BLE symbol blinks), then run `obp sync` immediately.
- **Protocol** — `obp sync` writes `bytes(20)` to the Omron proprietary CMD characteristic → readings arrive as standard GATT Blood Pressure Measurement (0x2A35) indications → 3 seconds of silence = transfer complete.
- **Connection handling** — sync pre-scans to resolve the device address, then retries the connection up to 3 times with a timeout before giving up.

## Layout

```
omron-bp/
├── omron_bp/
│   ├── bp.py        # Typer CLI: all commands
│   ├── ble.py       # BLE scan/connect/sync (bleak), protocol parsing
│   ├── db.py        # SQLite storage, queries, session grouping
│   ├── devices.py   # saved device aliases
│   ├── tags.py      # tag vocabulary
│   ├── web.py       # local web UI (stdlib http.server) + JSON API
│   └── static/      # vendored tailwind.js + chart.js (offline)
├── pyproject.toml
└── .github/workflows/  # ci.yml, release.yml
```

## Development

```sh
pip install -e .    # inside a virtualenv
obp …               # run commands directly against your local clone
python -m build     # build a wheel + sdist
```

CI (test matrix on macOS + Linux, Python 3.10–3.13, plus a wheel build) runs on every push and PR.

### Releasing

Bump `version` in `pyproject.toml` (e.g. `0.1.0` → `0.2.0`), commit, then tag and push:

```sh
git tag v0.2.0 && git push origin main --tags
```

The **Release** workflow verifies the tag matches the `pyproject.toml` version, builds the wheel + sdist, and publishes a GitHub Release with the artifacts and auto-generated notes. PyPI publishing via Trusted Publishing is wired but commented out in `.github/workflows/release.yml` — enable it once a PyPI project exists.

## Device compatibility

Confirmed: **Omron HEM-7144T2**

Likely works: HEM-6232T, HEM-7155T, HEM-7361T, and other recent Omron BLE wrist/arm monitors that advertise as `BLESmart_*`.

## License

MIT

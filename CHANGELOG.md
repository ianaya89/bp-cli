# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-06-05

First packaged release.

### Added
- **Packaging**: installable Python package (`omron_bp/`) with `pyproject.toml`
  and a console entry point — the command is `obp`. Cross-platform: macOS
  (CoreBluetooth) and Linux (BlueZ) via `bleak`. Static web assets bundled.
- **BLE sync** (`obp scan` / `obp sync`): pre-scans to resolve the device,
  connect timeout + 3 retries, clearer error messages.
- **Tags**: CLI-managed vocabulary (`obp tags add/ls/rm`); tag records with
  `obp add --tag` and `obp edit --add-tag/--rm-tag`; filter `list`/`stats`/
  `chart`/`pdf` with `--tag`. Removing a tag strips it from all records.
- **Session grouping**: `obp list` groups readings taken within 5 minutes into
  one session and shows the lowest reading; `--expand`, `--no-group`, `--window N`.
- **Date ranges** for `obp list`: `--days` (default 30), `--since`, `--until`.
- **Web UI** (`obp web`): dark, compact, fully offline (vendored Tailwind +
  Chart.js); browse, add/edit/delete records, edit tags, filter by tag and date,
  toggle session grouping.
- **Reports**: terminal chart (`obp chart`) and printable PDF (`obp pdf`, with a
  Tags column).
- **Records**: manual `obp add` / `obp edit` / `obp rm`; device aliases
  (`obp devices`).
- **CI/CD**: GitHub Actions — test matrix (macOS + Linux, Python 3.10–3.13) and
  a wheel build that verifies bundled assets; release workflow builds and
  publishes a GitHub Release on `v*` tags.
- `LICENSE` (MIT) and a CHANGELOG.

[Unreleased]: https://github.com/ianaya89/omron-bp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ianaya89/omron-bp/releases/tag/v0.1.0

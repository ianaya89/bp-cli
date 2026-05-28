import json
from pathlib import Path

_DEVICES_PATH = Path.home() / ".omron-bp" / "devices.json"


def _load() -> dict[str, str]:
    if _DEVICES_PATH.exists():
        return json.loads(_DEVICES_PATH.read_text())
    return {}


def _save(data: dict[str, str]) -> None:
    _DEVICES_PATH.parent.mkdir(exist_ok=True)
    _DEVICES_PATH.write_text(json.dumps(data, indent=2))


def resolve(name_or_address: str) -> str:
    """Return address for name, or pass through if not a known alias."""
    devices = _load()
    return devices.get(name_or_address, name_or_address)


def add(name: str, address: str) -> None:
    devices = _load()
    devices[name] = address
    _save(devices)


def remove(name: str) -> bool:
    devices = _load()
    if name not in devices:
        return False
    del devices[name]
    _save(devices)
    return True


def list_all() -> dict[str, str]:
    return _load()

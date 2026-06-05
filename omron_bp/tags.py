import json
from pathlib import Path

_TAGS_PATH = Path.home() / ".omron-bp" / "tags.json"


def _load() -> list[str]:
    if _TAGS_PATH.exists():
        return json.loads(_TAGS_PATH.read_text())
    return []


def _save(tags: list[str]) -> None:
    _TAGS_PATH.parent.mkdir(exist_ok=True)
    _TAGS_PATH.write_text(json.dumps(tags, indent=2))


def list_all() -> list[str]:
    return _load()


def exists(name: str) -> bool:
    return name in _load()


def add(name: str) -> bool:
    name = name.strip()
    tags = _load()
    if not name or name in tags:
        return False
    tags.append(name)
    tags.sort()
    _save(tags)
    return True


def remove(name: str) -> bool:
    tags = _load()
    if name not in tags:
        return False
    tags.remove(name)
    _save(tags)
    return True

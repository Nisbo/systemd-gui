from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ITEM_TYPES = {"category", "command"}


@dataclass
class QuickShellEntry:
    item: dict[str, Any]
    path: str
    depth: int


def default_quick_shell() -> dict[str, Any]:
    return {
        "items": [
            {
                "type": "category",
                "name": "System commands",
                "enabled": True,
                "items": [
                    {
                        "type": "command",
                        "name": "List files",
                        "command": "ls -al",
                        "enabled": True,
                        "confirm": True,
                    }
                ],
            }
        ]
    }


def read_quick_shell(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = default_quick_shell()
    return normalize_tree(data)


def write_quick_shell(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalize_tree(data), indent=2, sort_keys=False) + "\n", encoding="utf-8")


def normalize_tree(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        data = {}
    items = data.get("items")
    if not isinstance(items, list):
        items = []
    return {"items": [normalize_item(item) for item in items if isinstance(item, dict)]}


def normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    item_type = str(item.get("type") or "command").strip().lower()
    if item_type not in ITEM_TYPES:
        item_type = "command"
    normalized: dict[str, Any] = {
        "type": item_type,
        "name": str(item.get("name") or "").strip(),
        "enabled": bool(item.get("enabled", True)),
    }
    if item_type == "category":
        children = item.get("items")
        normalized["items"] = [normalize_item(child) for child in children if isinstance(child, dict)] if isinstance(children, list) else []
    else:
        normalized["command"] = str(item.get("command") or "").strip()
        normalized["confirm"] = bool(item.get("confirm", True))
    return normalized


def entry_label(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "").strip()
    if name:
        return name
    command = str(item.get("command") or "").strip()
    return command or "Unnamed entry"


def flatten_entries(items: list[dict[str, Any]], prefix: str = "", depth: int = 0) -> list[QuickShellEntry]:
    entries: list[QuickShellEntry] = []
    for index, item in enumerate(items):
        path = f"{prefix}.{index}" if prefix else str(index)
        entries.append(QuickShellEntry(item=item, path=path, depth=depth))
        if item.get("type") == "category":
            entries.extend(flatten_entries(list(item.get("items") or []), path, depth + 1))
    return entries


def parse_path(value: str) -> list[int]:
    if not value:
        return []
    parts = value.split(".")
    if any(not part.isdigit() for part in parts):
        raise ValueError("Invalid quick shell path.")
    return [int(part) for part in parts]


def children_for_path(data: dict[str, Any], item_path: str) -> list[dict[str, Any]]:
    parts = parse_path(item_path)
    if not parts:
        return list(data.get("items") or [])
    item = item_for_path(data, item_path)
    if item.get("type") != "category":
        raise ValueError("Only categories can contain entries.")
    return list(item.get("items") or [])


def item_for_path(data: dict[str, Any], item_path: str) -> dict[str, Any]:
    parts = parse_path(item_path)
    if not parts:
        raise ValueError("Root is not a quick shell item.")
    items = data.get("items") or []
    current: dict[str, Any] | None = None
    for index in parts:
        if index < 0 or index >= len(items):
            raise ValueError("Quick shell entry was not found.")
        current = items[index]
        items = current.get("items") or []
    if current is None:
        raise ValueError("Quick shell entry was not found.")
    return current


def parent_children_for_path(data: dict[str, Any], item_path: str) -> tuple[list[dict[str, Any]], int]:
    parts = parse_path(item_path)
    if not parts:
        raise ValueError("Root cannot be changed.")
    index = parts[-1]
    if len(parts) == 1:
        return data.setdefault("items", []), index
    parent = item_for_path(data, ".".join(str(part) for part in parts[:-1]))
    if parent.get("type") != "category":
        raise ValueError("Parent is not a category.")
    return parent.setdefault("items", []), index


def add_item(data: dict[str, Any], parent_path: str, item: dict[str, Any]) -> None:
    if parent_path:
        parent = item_for_path(data, parent_path)
        if parent.get("type") != "category":
            raise ValueError("Only categories can contain entries.")
        parent.setdefault("items", []).append(normalize_item(item))
    else:
        data.setdefault("items", []).append(normalize_item(item))


def update_item(data: dict[str, Any], item_path: str, item: dict[str, Any]) -> None:
    items, index = parent_children_for_path(data, item_path)
    if index < 0 or index >= len(items):
        raise ValueError("Quick shell entry was not found.")
    old_item = items[index]
    next_item = normalize_item(item)
    if old_item.get("type") == "category" and next_item.get("type") == "category":
        next_item["items"] = old_item.get("items") or []
    items[index] = next_item


def delete_item(data: dict[str, Any], item_path: str) -> None:
    items, index = parent_children_for_path(data, item_path)
    if index < 0 or index >= len(items):
        raise ValueError("Quick shell entry was not found.")
    del items[index]


def move_item(data: dict[str, Any], item_path: str, direction: str) -> None:
    items, index = parent_children_for_path(data, item_path)
    if index < 0 or index >= len(items):
        raise ValueError("Quick shell entry was not found.")
    next_index = index - 1 if direction == "up" else index + 1
    if next_index < 0 or next_index >= len(items):
        return
    items[index], items[next_index] = items[next_index], items[index]

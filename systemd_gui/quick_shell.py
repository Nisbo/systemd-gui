from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ITEM_TYPES = {"category", "command"}


@dataclass
class QuickShellEntry:
    item: dict[str, Any]
    path: str
    depth: int


@dataclass
class QuickShellHelperStatus:
    path: Path
    installed: bool
    executable: bool
    ready: bool
    message: str


@dataclass
class ShellIntegrationStatus:
    shell_id: str
    label: str
    target: Path
    detected: bool
    installed: bool
    supported: bool
    description: str
    message: str


SHELL_INTEGRATIONS = {
    "bash": {
        "label": "bash / sh",
        "names": {"bash", "sh", "dash"},
        "target": Path("/etc/profile.d/systemd-gui-qs.sh"),
        "description": "Loaded by many POSIX-style login shells, including bash on Debian.",
    },
    "zsh": {
        "label": "zsh",
        "names": {"zsh"},
        "target": Path("/etc/zsh/zshrc"),
        "description": "Loaded by interactive zsh sessions on Debian systems with zsh installed.",
    },
}
INTEGRATION_BEGIN_TEMPLATE = "# >>> systemd-gui quick shell:{shell_id} >>>"
INTEGRATION_END_TEMPLATE = "# <<< systemd-gui quick shell:{shell_id} <<<"


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
                        "show_menu_after": False,
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


def quick_shell_helper_status(path: Path, app_root: Path | None = None, data_dir: Path | None = None) -> QuickShellHelperStatus:
    installed = path.exists()
    executable = installed and os.access(path, os.X_OK)
    content = ""
    if installed:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            content = ""
    expected_root = f'export SYSTEMD_GUI_ROOT="{app_root}"' if app_root else ""
    expected_data = f'export SYSTEMD_GUI_DATA_DIR="{data_dir}"' if data_dir else ""
    matches_paths = bool(
        installed
        and (not expected_root or expected_root in content)
        and (not expected_data or expected_data in content)
    )
    ready = bool(executable and matches_paths)
    if not installed:
        message = "Helper is not installed yet."
    elif not executable:
        message = "Helper exists but is not executable."
    elif not matches_paths:
        message = "Helper is installed but should be updated for the current app path."
    else:
        message = "Helper is installed."
    return QuickShellHelperStatus(path=path, installed=installed, executable=executable, ready=ready, message=message)


def install_quick_shell_helper(path: Path, app_root: Path, data_dir: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(
        [
            "#!/usr/bin/env sh",
            f'export SYSTEMD_GUI_ROOT="{app_root}"',
            f'export SYSTEMD_GUI_DATA_DIR="{data_dir}"',
            f'exec /usr/bin/python3 "{app_root / "scripts" / "quick_shell.py"}" "$@"',
            "",
        ]
    )
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def shell_integration_statuses(helper_path: Path) -> list[ShellIntegrationStatus]:
    shell_names = _detected_shell_names()
    statuses: list[ShellIntegrationStatus] = []
    for shell_id, config in SHELL_INTEGRATIONS.items():
        target = Path(config["target"])
        detected = bool(shell_names.intersection(config["names"]))
        installed = _integration_block_installed(target, shell_id, helper_path)
        if installed:
            message = "Integration is installed."
        elif detected:
            message = "Shell detected. Integration can be installed."
        else:
            message = "Shell was not detected on this system."
        statuses.append(
            ShellIntegrationStatus(
                shell_id=shell_id,
                label=str(config["label"]),
                target=target,
                detected=detected,
                installed=installed,
                supported=True,
                description=str(config["description"]),
                message=message,
            )
        )
    return statuses


def install_shell_integration(shell_id: str, helper_path: Path) -> Path:
    config = _integration_config(shell_id)
    target = Path(config["target"])
    block = _integration_block(shell_id, helper_path)
    existing = _remove_integration_block(_read_text(target), shell_id).rstrip()
    next_content = f"{existing}\n\n{block}\n" if existing else f"{block}\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(next_content, encoding="utf-8")
    return target


def remove_shell_integration(shell_id: str) -> Path:
    config = _integration_config(shell_id)
    target = Path(config["target"])
    next_content = _remove_integration_block(_read_text(target), shell_id)
    if not next_content.strip() and target.name == "systemd-gui-qs.sh":
        try:
            target.unlink()
        except FileNotFoundError:
            pass
    elif target.exists():
        target.write_text(next_content, encoding="utf-8")
    return target


def _integration_config(shell_id: str) -> dict[str, object]:
    if shell_id not in SHELL_INTEGRATIONS:
        raise ValueError("Unsupported shell integration.")
    return SHELL_INTEGRATIONS[shell_id]


def _detected_shell_names() -> set[str]:
    names: set[str] = set()
    for path in [Path("/etc/shells")]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            value = line.strip()
            if value and not value.startswith("#"):
                names.add(Path(value).name)
    try:
        passwd_lines = Path("/etc/passwd").read_text(encoding="utf-8").splitlines()
    except OSError:
        passwd_lines = []
    for line in passwd_lines:
        parts = line.split(":")
        if len(parts) >= 7 and parts[-1]:
            names.add(Path(parts[-1]).name)
    return names


def _integration_block(shell_id: str, helper_path: Path) -> str:
    begin = INTEGRATION_BEGIN_TEMPLATE.format(shell_id=shell_id)
    end = INTEGRATION_END_TEMPLATE.format(shell_id=shell_id)
    quoted_helper = _shell_quote(str(helper_path))
    return "\n".join(
        [
            begin,
            "qs() {",
            '  __systemd_gui_qs_action_file="$(mktemp "${TMPDIR:-/tmp}/systemd-gui-qs.XXXXXX")" || return 1',
            f"  {quoted_helper} --shell-action-file \"$__systemd_gui_qs_action_file\"",
            "  __systemd_gui_qs_status=$?",
            '  if [ -s "$__systemd_gui_qs_action_file" ]; then',
            '    . "$__systemd_gui_qs_action_file"',
            "    __systemd_gui_qs_status=$?",
            "  fi",
            '  rm -f "$__systemd_gui_qs_action_file"',
            "  unset __systemd_gui_qs_action_file",
            "  return $__systemd_gui_qs_status",
            "}",
            end,
        ]
    )


def _integration_block_installed(path: Path, shell_id: str, helper_path: Path) -> bool:
    content = _read_text(path)
    begin = INTEGRATION_BEGIN_TEMPLATE.format(shell_id=shell_id)
    return begin in content and str(helper_path) in content


def _remove_integration_block(content: str, shell_id: str) -> str:
    begin = INTEGRATION_BEGIN_TEMPLATE.format(shell_id=shell_id)
    end = INTEGRATION_END_TEMPLATE.format(shell_id=shell_id)
    lines = content.splitlines()
    output: list[str] = []
    skipping = False
    for line in lines:
        if line.strip() == begin:
            skipping = True
            continue
        if skipping and line.strip() == end:
            skipping = False
            continue
        if not skipping:
            output.append(line)
    return "\n".join(output).rstrip() + ("\n" if output else "")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


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
        normalized["show_menu_after"] = bool(item.get("show_menu_after", False))
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

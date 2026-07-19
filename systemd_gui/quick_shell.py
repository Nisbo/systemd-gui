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
    shell_path: str


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
    refresh_command: str
    message: str


@dataclass
class BashHistoryTimestampStatus:
    target: Path
    installed: bool
    message: str
    refresh_command: str


SHELL_INTEGRATIONS = {
    "bash": {
        "label": "bash / sh",
        "names": {"bash", "sh", "dash"},
        "target": Path("/etc/profile.d/systemd-gui-qs.sh"),
        "description": "Loaded by many POSIX-style login shells, including bash on Debian.",
        "refresh_command": "source /etc/profile.d/systemd-gui-qs.sh",
    },
    "zsh": {
        "label": "zsh",
        "names": {"zsh"},
        "target": Path("/etc/zsh/zshrc"),
        "description": "Loaded by interactive zsh sessions on Debian systems with zsh installed.",
        "refresh_command": "source /etc/zsh/zshrc",
    },
}
INTEGRATION_BEGIN_TEMPLATE = "# >>> systemd-gui quick shell:{shell_id} >>>"
INTEGRATION_END_TEMPLATE = "# <<< systemd-gui quick shell:{shell_id} <<<"
BASH_HISTORY_TIMESTAMP_TARGET = Path("/etc/profile.d/systemd-gui-history-time.sh")
BASH_HISTORY_TIMESTAMP_CONTENT = "\n".join(
    [
        "# Managed by systemd-gui.",
        '# Enables timestamps for future bash history entries used by "qs" Shell history.',
        'export HISTTIMEFORMAT="%F %T "',
        "",
    ]
)


def default_quick_shell() -> dict[str, Any]:
    return {
        "settings": default_quick_shell_settings(),
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


def default_quick_shell_settings() -> dict[str, Any]:
    return {
        "history_limit": 80,
        "history_show_timestamps": True,
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
    expected_content = _quick_shell_helper_content(app_root, data_dir) if app_root and data_dir else ""
    matches_content = bool(installed and (not expected_content or content == expected_content))
    ready = bool(executable and matches_content)
    if not installed:
        message = "Helper is not installed yet."
    elif not executable:
        message = "Helper exists but is not executable."
    elif not matches_content:
        message = "Helper is installed but should be updated."
    else:
        message = "Helper is installed."
    return QuickShellHelperStatus(path=path, installed=installed, executable=executable, ready=ready, message=message)


def install_quick_shell_helper(path: Path, app_root: Path, data_dir: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_quick_shell_helper_content(app_root, data_dir), encoding="utf-8")
    path.chmod(0o755)


def _quick_shell_helper_content(app_root: Path, data_dir: Path) -> str:
    return "\n".join(
        [
            "#!/usr/bin/env sh",
            f'export SYSTEMD_GUI_ROOT="{app_root}"',
            f'export SYSTEMD_GUI_DATA_DIR="{data_dir}"',
            f'exec /usr/bin/python3 "{app_root / "scripts" / "quick_shell.py"}" "$@"',
            "",
        ]
    )


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
                refresh_command=str(config["refresh_command"]),
                message=message,
            )
        )
    return statuses


def bash_history_timestamp_status() -> BashHistoryTimestampStatus:
    installed = _read_text(BASH_HISTORY_TIMESTAMP_TARGET) == BASH_HISTORY_TIMESTAMP_CONTENT
    if installed:
        message = "Bash history timestamps are enabled for new shells."
    elif BASH_HISTORY_TIMESTAMP_TARGET.exists():
        message = "Timestamp file exists but should be updated."
    else:
        message = "Bash history timestamps are not enabled by Systemd Gui."
    return BashHistoryTimestampStatus(
        target=BASH_HISTORY_TIMESTAMP_TARGET,
        installed=installed,
        message=message,
        refresh_command=f"source {BASH_HISTORY_TIMESTAMP_TARGET}",
    )


def install_bash_history_timestamps() -> Path:
    BASH_HISTORY_TIMESTAMP_TARGET.parent.mkdir(parents=True, exist_ok=True)
    BASH_HISTORY_TIMESTAMP_TARGET.write_text(BASH_HISTORY_TIMESTAMP_CONTENT, encoding="utf-8")
    return BASH_HISTORY_TIMESTAMP_TARGET


def remove_bash_history_timestamps() -> Path:
    if _read_text(BASH_HISTORY_TIMESTAMP_TARGET) == BASH_HISTORY_TIMESTAMP_CONTENT:
        try:
            BASH_HISTORY_TIMESTAMP_TARGET.unlink()
        except FileNotFoundError:
            pass
    return BASH_HISTORY_TIMESTAMP_TARGET


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
            '  if [ -n "${BASH_VERSION:-}" ]; then',
            "    history -a 2>/dev/null || true",
            '  elif [ -n "${ZSH_VERSION:-}" ]; then',
            "    fc -AI 2>/dev/null || true",
            "  fi",
            f"  {quoted_helper} --shell-action-file \"$__systemd_gui_qs_action_file\" \"$@\"",
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
    return _integration_block(shell_id, helper_path) in content


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
    return {
        "settings": normalize_settings(data.get("settings") if isinstance(data, dict) else {}),
        "items": [normalize_item(item) for item in items if isinstance(item, dict)],
    }


def normalize_settings(settings: Any) -> dict[str, Any]:
    defaults = default_quick_shell_settings()
    if not isinstance(settings, dict):
        settings = {}
    try:
        history_limit = int(settings.get("history_limit", defaults["history_limit"]))
    except (TypeError, ValueError):
        history_limit = int(defaults["history_limit"])
    history_limit = max(10, min(history_limit, 500))
    return {
        "history_limit": history_limit,
        "history_show_timestamps": bool(settings.get("history_show_timestamps", defaults["history_show_timestamps"])),
    }


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


def flatten_entries(
    items: list[dict[str, Any]],
    prefix: str = "",
    depth: int = 0,
    shell_prefix: str = "",
    ancestors_enabled: bool = True,
) -> list[QuickShellEntry]:
    entries: list[QuickShellEntry] = []
    enabled_number = 0
    for index, item in enumerate(items):
        path = f"{prefix}.{index}" if prefix else str(index)
        item_enabled = bool(item.get("enabled", True))
        shell_path = ""
        if ancestors_enabled and item_enabled:
            enabled_number += 1
            shell_path = f"{shell_prefix}-{enabled_number}" if shell_prefix else str(enabled_number)
        entries.append(QuickShellEntry(item=item, path=path, depth=depth, shell_path=shell_path))
        if item.get("type") == "category":
            entries.extend(
                flatten_entries(
                    list(item.get("items") or []),
                    path,
                    depth + 1,
                    shell_path,
                    ancestors_enabled and item_enabled,
                )
            )
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


def move_item_to_position(data: dict[str, Any], item_path: str, position: int) -> None:
    items, index = parent_children_for_path(data, item_path)
    if index < 0 or index >= len(items):
        raise ValueError("Quick shell entry was not found.")
    next_index = max(0, min(position - 1, len(items) - 1))
    if next_index == index:
        return
    item = items.pop(index)
    items.insert(next_index, item)

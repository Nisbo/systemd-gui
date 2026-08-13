#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import importlib.util
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "green": "\033[32m",
    "cyan": "\033[36m",
    "blue": "\033[34m",
    "yellow": "\033[33m",
    "red": "\033[31m",
}
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")


def _use_color() -> bool:
    color_setting = os.environ.get("SYSTEMD_GUI_QS_COLOR", "").lower()
    if color_setting in {"1", "true", "yes", "on"}:
        return True
    if color_setting in {"0", "false", "no", "off"} or os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _style(value: str, *names: str) -> str:
    if not _use_color():
        return value
    prefix = "".join(COLORS[name] for name in names if name in COLORS)
    return f"{prefix}{value}{COLORS['reset']}" if prefix else value


def _heading(value: str, color: str = "green") -> str:
    return _style(value, "bold", color)


def _muted(value: str) -> str:
    return _style(value, "dim")


def _error(value: str) -> str:
    return _style(value, "red")


def _app_root() -> Path:
    return Path(os.environ.get("SYSTEMD_GUI_ROOT") or Path(__file__).resolve().parents[1])


def _data_dir() -> Path:
    return Path(os.environ.get("SYSTEMD_GUI_DATA_DIR") or (_app_root() / "data"))


def _state_path() -> Path:
    return _data_dir() / "quick-shell-state.json"


def _runs_path() -> Path:
    return _data_dir() / "quick-shell-runs.json"


def _nodes_path() -> Path:
    return _data_dir() / "nodes.json"


def _load_helpers():
    root = _app_root()
    module_path = root / "systemd_gui" / "quick_shell.py"
    module_name = "systemd_gui_quick_shell_helpers"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load Quick Shell helpers from {module_path}.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.entry_label, module.read_quick_shell


def _enabled_items(items):
    return [item for item in items if item.get("enabled", True)]


def _menu_title(stack):
    if not stack:
        return "Quick Shell"
    return "Quick Shell / " + " / ".join(stack)


def _remote_context_label() -> str:
    return os.environ.get("SYSTEMD_GUI_REMOTE_NODE", "").strip()


def _remote_keep_menu_enabled() -> bool:
    return os.environ.get("SYSTEMD_GUI_REMOTE_KEEP_MENU", "").strip().lower() in {"1", "true", "yes", "on"}


def _should_show_menu_after(item) -> bool:
    return _remote_keep_menu_enabled() or bool(item.get("show_menu_after", False))


def _print_menu_header(title: str) -> None:
    print(_heading(title, "green"))
    print(_style("=" * len(title), "green"))
    remote_label = _remote_context_label()
    if remote_label:
        print(f"{_style('REMOTE:', 'bold', 'red')} {remote_label}")


def _quit_label() -> str:
    return "Disconnect" if _remote_context_label() else "Quit"


def _prompt_choice(max_number: int, can_go_back: bool, can_open_remote: bool = False) -> str:
    hints = ["number", "pN", "cN", "S"]
    if can_open_remote:
        hints.append("N")
    if can_go_back:
        hints.append("b")
    hints.append("q")
    return input(f"Choose ({'/'.join(hints)}): ").strip().lower()


def _parse_prefixed_choice(choice: str) -> tuple[str, int] | None:
    prefixes = {"p": "print", "print": "print", "c": "copy", "copy": "copy"}
    for prefix, action in sorted(prefixes.items(), key=lambda item: len(item[0]), reverse=True):
        if choice.startswith(prefix):
            value = choice[len(prefix):].strip()
            if value.isdigit():
                return action, int(value)
    return None


def _parse_direct_path(args: list[str]) -> list[int]:
    numbers: list[int] = []
    for arg in args:
        parts = arg.split("-")
        if any(not part.isdigit() for part in parts):
            raise ValueError(f'Invalid selection "{arg}". Use numbers like "qs 1 5" or "qs 1-5".')
        for part in parts:
            number = int(part)
            if number < 1:
                raise ValueError("Menu numbers start at 1.")
            numbers.append(number)
    return numbers


def _print_debug(args: list[str], shell_action_file: Path | None) -> int:
    print(_heading("Quick Shell debug", "blue"))
    print(f"script: {Path(__file__).resolve()}")
    print(f"app root: {_app_root()}")
    print(f"data dir: {_data_dir()}")
    print(f"data file: {_data_dir() / 'quick-shell.json'}")
    print(f"state file: {_state_path()}")
    print(f"shell action file: {shell_action_file or '-'}")
    print(f"arguments: {args or '-'}")
    try:
        direct_path = _parse_direct_path(args)
    except ValueError as exc:
        print(f"direct path: {_error(f'invalid ({exc})')}")
    else:
        print(f"direct path: {direct_path or '-'}")
    return 0


def _menu_name(stack: list[str]) -> str:
    return " / ".join(stack) if stack else "root menu"


def _select_direct_path(items: list[dict], numbers: list[int], entry_label):
    current_items = items
    stack: list[str] = []
    for depth, number in enumerate(numbers, start=1):
        enabled = _enabled_items(current_items)
        if not enabled:
            raise ValueError(f"The {_menu_name(stack)} has no active entries.")
        if number > len(enabled):
            raise ValueError(f"Menu number {number} is not available in {_menu_name(stack)}. Available range: 1-{len(enabled)}.")
        item = enabled[number - 1]
        label = entry_label(item)
        if depth < len(numbers):
            if item.get("type") != "category":
                raise ValueError(f'"{label}" is a command, not a category. It cannot contain another number.')
            stack.append(label)
            current_items = list(item.get("items") or [])
            continue
        return item, stack
    raise ValueError("No menu number was selected.")


def _build_category_stacks(items: list[dict], numbers: list[int], entry_label):
    current_items = items
    menu_stack: list[list[dict]] = [items]
    label_stack: list[str] = []
    path_stack: list[list[int]] = [[]]
    current_path: list[int] = []

    for number in numbers:
        enabled = _enabled_items(current_items)
        if not enabled:
            raise ValueError(f"The {_menu_name(label_stack)} has no active entries.")
        if number > len(enabled):
            raise ValueError(f"Menu number {number} is not available in {_menu_name(label_stack)}. Available range: 1-{len(enabled)}.")
        item = enabled[number - 1]
        label = entry_label(item)
        if item.get("type") != "category":
            raise ValueError(f'"{label}" is a command, not a category.')
        current_path = [*current_path, number]
        current_items = list(item.get("items") or [])
        label_stack.append(label)
        menu_stack.append(current_items)
        path_stack.append(current_path)

    return menu_stack, label_stack, path_stack


def _read_resume_path() -> list[int]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    value = data.get("resume_path")
    if not isinstance(value, list) or not all(isinstance(item, int) and item > 0 for item in value):
        return []
    return value


def _write_resume_path(numbers: list[int]) -> None:
    try:
        _state_path().parent.mkdir(parents=True, exist_ok=True)
        _state_path().write_text(json.dumps({"resume_path": numbers}, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def _parse_cd_target(command: str) -> Path | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    if not parts or parts[0] != "cd" or len(parts) > 2:
        return None
    if len(parts) == 1:
        return Path.home()
    if parts[1] == "-":
        oldpwd = os.environ.get("OLDPWD")
        return Path(oldpwd) if oldpwd else None
    return Path(parts[1]).expanduser()


def _write_shell_action(path: Path, action: str) -> None:
    path.write_text(action + "\n", encoding="utf-8")


def _read_remote_nodes() -> list[dict[str, object]]:
    try:
        data = json.loads(_nodes_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    nodes = data.get("nodes") if isinstance(data, dict) else []
    if not isinstance(nodes, list):
        return []
    return [node for node in nodes if isinstance(node, dict) and _remote_node_host(node)]


def _remote_node_host(node: dict[str, object]) -> str:
    return str(node.get("ssh_host") or node.get("host") or "").strip()


def _remote_node_port(node: dict[str, object]) -> str:
    value = str(node.get("ssh_port") or "").strip()
    return value if value.isdigit() else "22"


def _remote_node_user(node: dict[str, object]) -> str:
    return str(node.get("ssh_user") or "").strip()


def _remote_node_label(node: dict[str, object]) -> str:
    name = str(node.get("name") or "").strip() or _remote_node_host(node)
    user = _remote_node_user(node)
    host = _remote_node_host(node)
    port = _remote_node_port(node)
    target = f"{user + '@' if user else ''}{host}:{port}"
    return f"{name} ({target})"


def _remote_keep_menu_mode(node: dict[str, object]) -> str:
    mode = str(node.get("remote_keep_menu") or "yes").strip().lower()
    return mode if mode in {"yes", "no", "ask"} else "yes"


def _ask_remote_keep_menu(node: dict[str, object]) -> bool:
    mode = _remote_keep_menu_mode(node)
    if mode == "yes":
        return True
    if mode == "no":
        return False
    answer = input("Keep remote qs open after commands? [Y/n] ").strip().lower()
    return answer not in {"n", "no"}


def _ssh_base_command(node: dict[str, object], user: str, keep_menu: bool) -> list[str]:
    host = _remote_node_host(node)
    port = _remote_node_port(node)
    key_path = str(node.get("ssh_key_path") or "").strip()
    command = ["ssh", "-t", "-o", "StrictHostKeyChecking=accept-new", "-p", port]
    if key_path:
        command.extend(["-i", key_path])
    remote_label = _remote_node_label({**node, "ssh_user": user})
    keep_value = "1" if keep_menu else "0"
    remote_command = (
        "if [ -x /usr/local/bin/qs ]; then __systemd_gui_qs=/usr/local/bin/qs; "
        "elif command -v qs >/dev/null 2>&1; then __systemd_gui_qs=$(command -v qs); "
        "else printf '%s\\n' 'Remote qs helper not found. Install or update the Quick Shell helper on the target node first.' >&2; exit 127; fi; "
        f"SYSTEMD_GUI_REMOTE_NODE={shlex.quote(remote_label)} SYSTEMD_GUI_REMOTE_KEEP_MENU={keep_value} \"$__systemd_gui_qs\""
    )
    command.extend([f"{user}@{host}", remote_command])
    return command


def _ssh_preflight_command(ssh: str, node: dict[str, object], user: str) -> list[str]:
    host = _remote_node_host(node)
    port = _remote_node_port(node)
    key_path = str(node.get("ssh_key_path") or "").strip()
    command = [
        ssh,
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-p",
        port,
    ]
    if key_path:
        command.extend(["-i", key_path])
    command.extend([f"{user}@{host}", "true"])
    return command


def _ssh_host_key_changed(stderr: str) -> bool:
    text = stderr.lower()
    return (
        "remote host identification has changed" in text
        or ("host key verification failed" in text and "offending" in text)
    )


def _known_hosts_target(host: str, port: str) -> str:
    if port != "22":
        return f"[{host}]:{port}"
    return host


def _forget_known_host(host: str, port: str) -> bool:
    ssh_keygen = shutil.which("ssh-keygen")
    if not ssh_keygen:
        print(_error("ssh-keygen is not installed, so qs cannot update known_hosts automatically."))
        print(_muted(f"Remove the old entry manually, then reconnect: ssh-keygen -R {_known_hosts_target(host, port)}"))
        return False
    target = _known_hosts_target(host, port)
    result = subprocess.run([ssh_keygen, "-R", target], check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        print(_error(f"Could not remove the old SSH host key for {target}."))
        if detail:
            print(_muted(detail))
        return False
    return True


def _confirm_changed_host_key(node: dict[str, object], user: str) -> bool:
    host = _remote_node_host(node)
    port = _remote_node_port(node)
    label = _remote_node_label({**node, "ssh_user": user})
    print()
    print(_error(f"SSH host key changed for {label}"))
    print(_muted("This can be harmless after reinstalling the server, but it can also mean you are connecting to a different machine."))
    print(_muted(f"Only trust the new key if you expected this change for {host}:{port}."))
    print()
    print(f"{_style('1', 'yellow')} Trust new key and reconnect")
    print(f"{_style('2', 'yellow')} Cancel")
    answer = input("Choose (1/2): ").strip().lower()
    if answer not in {"1", "trust", "y", "yes"}:
        print(_muted("Remote connection cancelled."))
        return False
    return _forget_known_host(host, port)


def _check_remote_host_key(ssh: str, node: dict[str, object], user: str) -> bool:
    try:
        result = subprocess.run(
            _ssh_preflight_command(ssh, node, user),
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    stderr = result.stderr or ""
    if _ssh_host_key_changed(stderr):
        return _confirm_changed_host_key(node, user)
    return True


def _run_remote_node(node: dict[str, object]) -> int:
    host = _remote_node_host(node)
    if not host:
        print(_error("This saved node has no SSH host. Open Nodes in the GUI and edit the node."))
        return 1
    user = _remote_node_user(node)
    if not user:
        user = input(f"SSH user for {host}: ").strip()
    if not user:
        print(_error("SSH user is required."))
        return 1

    ssh = shutil.which("ssh")
    if not ssh:
        print(_error("ssh is not installed on this system."))
        return 1
    if not _check_remote_host_key(ssh, node, user):
        return 3
    keep_menu = _ask_remote_keep_menu(node)
    command = _ssh_base_command(node, user, keep_menu)
    command[0] = ssh
    password = str(node.get("ssh_password") or "")
    env = os.environ.copy()
    if password and not node.get("ssh_key_path"):
        sshpass = shutil.which("sshpass")
        if sshpass:
            env["SSHPASS"] = password
            command = [sshpass, "-e", *command]
        else:
            print(_error("Stored password found, but sshpass is not installed."))
            print(_muted("Install sshpass from Quick Shell > Shell setup, or install it manually with: apt install sshpass"))
            return 2

    print()
    print(_heading(f"Connecting to {_remote_node_label(node)}", "blue"))
    if keep_menu:
        print(_muted("The remote server will keep qs open after commands. Choose q to disconnect."))
    else:
        print(_muted("The remote server will close qs after a command. SSH then returns here."))
    result = subprocess.run(command, env=env)
    if result.returncode != 0:
        if password and result.returncode == 6:
            print(_error("sshpass could not verify or accept the SSH host key."))
            print(_muted("Try a normal SSH login once from this server, or check whether the target host key changed."))
        print(_error(f"Remote SSH session ended with exit code {result.returncode}."))
    return result.returncode


def _show_remote_nodes_menu() -> int | None:
    while True:
        nodes = _read_remote_nodes()
        print()
        title = "Quick Shell / Remote nodes"
        print(_heading(title, "blue"))
        print(_style("=" * len(title), "blue"))
        if not nodes:
            print(_muted("No saved nodes with an SSH host are configured yet. Add or edit nodes in the GUI first."))
        for index, node in enumerate(nodes, start=1):
            print(f"{_style(str(index), 'bold')} {_remote_node_label(node)}")
        print(f"{_style('b', 'yellow')} Back")
        print(f"{_style('q', 'yellow')} Quit")
        print(_muted("Tip: SSH keys are used automatically. Without a saved user, qs asks for one."))
        choice = input("Choose (number/b/q): ").strip().lower()
        if choice == "q":
            return 0
        if choice == "b":
            return None
        if not choice.isdigit():
            print(_error("Please enter a number, b or q."))
            continue
        selected_index = int(choice) - 1
        if selected_index < 0 or selected_index >= len(nodes):
            print(_error("That number is not in the remote node list."))
            continue
        return _run_remote_node(nodes[selected_index])


def _run_remote_nodes_direct(args: list[str]) -> int:
    try:
        direct_path = _parse_direct_path(args)
    except ValueError as exc:
        print(_error(str(exc)), file=sys.stderr)
        return 2
    if not direct_path:
        result_code = _show_remote_nodes_menu()
        return result_code or 0
    if len(direct_path) > 1:
        print(_error('Remote nodes only use one number, for example: qs n 1'), file=sys.stderr)
        return 2
    nodes = _read_remote_nodes()
    if not nodes:
        print(_error("No saved nodes with an SSH host are configured yet."), file=sys.stderr)
        return 1
    selected_index = direct_path[0] - 1
    if selected_index < 0 or selected_index >= len(nodes):
        print(_error("That number is not in the remote node list."), file=sys.stderr)
        return 1
    return _run_remote_node(nodes[selected_index])


def _command_for_item(item) -> str:
    if item.get("type") == "sequence":
        return str(item.get("commands") or "").strip()
    return str(item.get("command") or "").strip()


def _placeholder_label(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def _resolve_placeholders(value: str, values: dict[str, str] | None = None) -> str:
    if values is None:
        values = {}

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            answer = input(f"{_placeholder_label(name)}: ")
            values[name] = answer
        return shlex.quote(values[name])

    return PLACEHOLDER_RE.sub(replace, value)


def _sequence_lines(item) -> list[str]:
    return [step["command"] for step in _sequence_steps(item)]


def _shell_line_continues(line: str) -> bool:
    stripped = line.rstrip()
    if not stripped.endswith("\\"):
        return False
    backslashes = 0
    for char in reversed(stripped):
        if char != "\\":
            break
        backslashes += 1
    return backslashes % 2 == 1


def _sequence_steps(item) -> list[dict[str, object]]:
    steps: list[dict[str, object]] = []
    pending_comments: list[str] = []
    pending_label = ""
    pending_command_lines: list[str] = []

    def append_step() -> None:
        nonlocal pending_comments, pending_label, pending_command_lines
        command = "\n".join(pending_command_lines).strip()
        if command:
            steps.append({"command": command, "comments": pending_comments, "label": pending_label})
        pending_comments = []
        pending_label = ""
        pending_command_lines = []

    for line in str(item.get("commands") or "").splitlines():
        raw = line.rstrip()
        value = raw.strip()
        if not pending_command_lines and not value:
            continue
        if not pending_command_lines and value.startswith("#"):
            comment = value[1:].strip()
            if comment:
                pending_comments.append(comment)
            continue
        if not pending_command_lines and value.startswith("@"):
            label = value[1:].strip()
            if label:
                pending_label = label
            continue
        pending_command_lines.append(raw)
        if not _shell_line_continues(raw):
            append_step()
    if pending_command_lines:
        append_step()
    return steps


def _sequence_comment_lines(step: dict[str, object]) -> list[str]:
    lines: list[str] = []
    comments = step.get("comments")
    if isinstance(comments, list):
        lines = [str(comment).strip() for comment in comments if str(comment).strip()]
    return lines


def _item_name(item) -> str:
    name = str(item.get("name") or "").strip()
    if name:
        return name
    if item.get("type") == "sequence":
        lines = _sequence_lines(item)
        return lines[0] if lines else "Unnamed sequence"
    return _command_for_item(item) or "Unnamed entry"


def _print_command(item, styled: bool = True) -> int:
    if item.get("type") == "category":
        print(_error("Categories do not have a command to print. Select a command inside the category."))
        return 1
    command = _resolve_placeholders(_command_for_item(item))
    if not command:
        print(_error("This entry has no command."))
        return 1
    if styled:
        print()
        print(_heading("Print", "blue"))
        print(_style("=====", "blue"))
        print(command)
        print()
    else:
        print(command)
    return 0


def _copy_to_clipboard(value: str) -> bool:
    clipboard_tools = [
        ("pbcopy", []),
        ("wl-copy", []),
        ("xclip", ["-selection", "clipboard"]),
        ("xsel", ["--clipboard", "--input"]),
    ]
    for command, args in clipboard_tools:
        path = shutil.which(command)
        if not path:
            continue
        try:
            subprocess.run([path, *args], input=value, text=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            continue
        return True
    return False


def _copy_command(item) -> int:
    if item.get("type") == "category":
        print(_error("Categories do not have a command to copy. Select a command inside the category."))
        return 1
    command = _resolve_placeholders(_command_for_item(item))
    if not command:
        print(_error("This entry has no command."))
        return 1
    if _copy_to_clipboard(command):
        print()
        print(_heading("Copy", "green"))
        print(_style("====", "green"))
        print(_style("Command copied to clipboard.", "green"))
        print()
        return 0
    print()
    print(_heading("Copy", "yellow"))
    print(_style("====", "yellow"))
    print(_style("Clipboard tool not available. Use print instead.", "yellow"))
    print(command)
    print()
    return 2


def _history_candidates() -> list[Path]:
    home = Path.home()
    candidates = [
        Path(os.environ["HISTFILE"]).expanduser(),
    ] if os.environ.get("HISTFILE") else []
    candidates.extend([home / ".bash_history", home / ".zsh_history"])
    if os.geteuid() == 0:
        candidates.extend([Path("/root/.bash_history"), Path("/root/.zsh_history")])

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def _history_command_from_line(line: str, source: Path) -> str:
    value = line.strip()
    if source.name == ".zsh_history" and value.startswith(": ") and ";" in value:
        value = value.split(";", 1)[1].strip()
    return value


def _history_shell(source: Path) -> str | None:
    if source.name == ".bash_history":
        return shutil.which("bash") or "/bin/bash"
    if source.name == ".zsh_history":
        return shutil.which("zsh") or "/bin/zsh"
    return None


def _history_display_limit(settings: dict | None = None) -> int:
    if settings:
        try:
            configured = int(settings.get("history_limit", 80))
        except (TypeError, ValueError):
            configured = 80
        return max(10, min(configured, 500))
    value = os.environ.get("SYSTEMD_GUI_QS_HISTORY_LIMIT", "").strip()
    if value.isdigit() and int(value) > 0:
        return int(value)
    return 80


def _history_show_timestamps(settings: dict | None = None) -> bool:
    if settings and "history_show_timestamps" in settings:
        return bool(settings.get("history_show_timestamps"))
    return True


def _history_source_mode(settings: dict | None = None) -> str:
    value = str((settings or {}).get("history_source") or "combined")
    return value if value in {"shell", "quick-shell", "combined"} else "combined"


def _parse_timestamp(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    return None


def _parse_iso_timestamp(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value).timestamp())
    except ValueError:
        return None


def _shell_history_item(source: Path, command: str) -> dict:
    return {
        "type": "command",
        "name": command,
        "command": command,
        "shell": _history_shell(source),
        "enabled": True,
        "confirm": True,
        "show_menu_after": False,
    }


def _read_shell_history() -> list[tuple[str, str, int | None, dict]]:
    entries: list[tuple[str, str, int | None, dict]] = []
    for source in _history_candidates():
        try:
            lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        pending_timestamp: int | None = None
        for line in lines:
            if source.name == ".bash_history" and line.startswith("#") and line[1:].isdigit():
                pending_timestamp = _parse_timestamp(line[1:])
                continue
            timestamp = pending_timestamp
            if source.name == ".zsh_history" and line.startswith(": ") and ";" in line:
                header, _command = line.split(";", 1)
                parts = header.split(":")
                if len(parts) >= 2:
                    timestamp = _parse_timestamp(parts[1].strip())
            command = _history_command_from_line(line, source)
            if command:
                entries.append((source.name, command, timestamp, _shell_history_item(source, command)))
            pending_timestamp = None
    return list(reversed(entries))


def _read_quick_shell_history() -> list[tuple[str, str, int | None, dict]]:
    try:
        data = json.loads(_runs_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    entries: list[tuple[str, str, int | None, dict]] = []
    for record in reversed(data):
        if not isinstance(record, dict):
            continue
        record_type = str(record.get("type") or "command")
        timestamp = _parse_iso_timestamp(str(record.get("started_at") or ""))
        if record_type == "sequence":
            lines = record.get("lines") if isinstance(record.get("lines"), list) else []
            commands = "\n".join(str(line.get("command") or "") for line in lines if isinstance(line, dict) and str(line.get("command") or "").strip())
            if not commands.strip():
                continue
            name = str(record.get("name") or "Sequence").strip()
            item = {
                "type": "sequence",
                "name": name,
                "commands": commands,
                "shell": str(record.get("shell") or ""),
                "enabled": True,
                "confirm": True,
                "confirm_each": bool(record.get("confirm_each", False)),
                "print_comments": bool(record.get("print_comments", True)),
                "stop_on_error": bool(record.get("stop_on_error", True)),
                "show_menu_after": False,
            }
            entries.append(("quick-shell", f"{name} ({len(lines)} sequence lines)", timestamp, item))
            continue
        command = str(record.get("command") or "").strip()
        if not command:
            continue
        item = {
            "type": "command",
            "name": str(record.get("name") or command).strip(),
            "command": command,
            "shell": str(record.get("shell") or ""),
            "enabled": True,
            "confirm": True,
            "show_menu_after": False,
        }
        entries.append(("quick-shell", command, timestamp, item))
    return entries


def _read_history_entries(settings: dict | None = None) -> list[tuple[str, str, int | None, dict]]:
    mode = _history_source_mode(settings)
    entries: list[tuple[str, str, int | None, dict]] = []
    if mode in {"shell", "combined"}:
        entries.extend(_read_shell_history())
    if mode in {"quick-shell", "combined"}:
        entries.extend(_read_quick_shell_history())
    return sorted(entries, key=lambda entry: entry[2] or 0, reverse=True)


def _compact_history(entries: list[tuple[str, str, int | None, dict]]) -> list[tuple[str, str, int | None, dict, int]]:
    compacted: list[tuple[str, str, int | None, dict, int]] = []
    for source, command, timestamp, item in entries:
        if compacted and compacted[-1][1] == command:
            previous_source, previous_command, previous_timestamp, previous_item, previous_count = compacted[-1]
            compacted[-1] = (previous_source, previous_command, previous_timestamp, previous_item, previous_count + 1)
            continue
        compacted.append((source, command, timestamp, item, 1))
    return compacted


def _raw_history(entries: list[tuple[str, str, int | None, dict]]) -> list[tuple[str, str, int | None, dict, int]]:
    return [(source, command, timestamp, item, 1) for source, command, timestamp, item in entries]


def _format_history_time(timestamp: int | None) -> str:
    if timestamp is None:
        return "-"
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return "-"


def _read_sequence_statuses(path: Path) -> dict[int, str]:
    statuses: dict[int, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return statuses
    for line in lines:
        parts = line.split("\t", 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        statuses[int(parts[0])] = parts[1]
    return statuses


def _append_run_record(record: dict) -> None:
    path = _runs_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = []
    if not isinstance(data, list):
        data = []
    data.append(record)
    data = data[-200:]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def _record_command_run(item: dict, command: str, shell: str, started_at: str, ended_at: str, exit_code: int) -> None:
    _append_run_record(
        {
            "type": "command",
            "name": _item_name(item),
            "command": command,
            "started_at": started_at,
            "ended_at": ended_at,
            "exit_code": exit_code,
            "shell": shell,
        }
    )


def _show_history_menu(settings: dict | None = None, shell_action_file: Path | None = None) -> int | None:
    raw_entries = _read_history_entries(settings)
    show_unfiltered = False
    filter_query = ""
    page = 0
    source_mode = _history_source_mode(settings)
    while True:
        entries = _raw_history(raw_entries) if show_unfiltered else _compact_history(raw_entries)
        if filter_query:
            needle = filter_query.lower()
            entries = [entry for entry in entries if needle in entry[1].lower()]
        page_size = _history_display_limit(settings)
        show_timestamps = _history_show_timestamps(settings)
        total_pages = max(1, (len(entries) + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))
        page_start = page * page_size
        page_entries = entries[page_start:page_start + page_size]
        print()
        title = "Quick Shell / Command history"
        print(_heading(title, "blue"))
        print(_style("=" * len(title), "blue"))
        if filter_query:
            print(_muted(f'Filter: "{filter_query}"'))
        if not entries and filter_query:
            print(_muted("No history entries match this filter."))
        elif not entries:
            if source_mode == "quick-shell":
                print(_muted("No Quick Shell commands have been recorded yet."))
            elif source_mode == "shell":
                print(_muted("No readable shell history file was found for this user."))
                print(_muted("Some shells write history only after logout or after running history -a."))
            else:
                print(_muted("No readable shell or Quick Shell history was found for this user."))
        elif show_unfiltered:
            timestamp_note = " Time is shown when available." if show_timestamps else ""
            print(_muted(f"Newest {source_mode} history entries for the current server user, including repeated commands.{timestamp_note}"))
        else:
            timestamp_note = " Time is shown when available." if show_timestamps else ""
            print(_muted(f"Newest {source_mode} history entries for the current server user. Consecutive duplicates are collapsed.{timestamp_note}"))
        if entries:
            print(_muted(f"Page {page + 1}/{total_pages}. Showing {page_start + 1}-{page_start + len(page_entries)} of {len(entries)}."))
        for offset, (source, command, timestamp, _item, count) in enumerate(page_entries):
            number_value = page_start + offset + 1
            number = _style(str(number_value), "bold")
            repeat = f" {_style(f'x{count}', 'yellow')}" if count > 1 else ""
            history_time = f"{_muted(_format_history_time(timestamp))} " if show_timestamps else ""
            print(f"{number} {_muted(source)} {history_time}{command}{repeat}")
        if page > 0:
            print(f"{_style('p', 'yellow')} Previous page")
        if page + 1 < total_pages:
            print(f"{_style('n', 'yellow')} Next page")
        if raw_entries:
            print(f"{_style('f', 'yellow')} Search or clear filter")
            toggle_label = "Hide repeated commands" if show_unfiltered else "Show unfiltered history"
            print(f"{_style('u', 'yellow')} {toggle_label}")
        print(f"{_style('b', 'yellow')} Back")
        print(f"{_style('q', 'yellow')} {_quit_label()}")
        print(_muted("Tip: f nginx searches history. f without text clears the filter. p2 prints item 2; c2 copies it."))

        raw_choice = input("Choose (number/pN/cN/f/n/p/u/b/q): ").strip()
        choice = raw_choice.lower()
        if choice == "q":
            return 0
        if choice == "b":
            return None
        if choice == "n" and page + 1 < total_pages:
            page += 1
            continue
        if choice == "p" and page > 0:
            page -= 1
            continue
        if choice == "u" and raw_entries:
            show_unfiltered = not show_unfiltered
            page = 0
            continue
        if choice == "f":
            filter_query = ""
            page = 0
            continue
        if choice.startswith("f "):
            filter_query = raw_choice[2:].strip()
            page = 0
            continue
        prefixed_choice = _parse_prefixed_choice(choice)
        if prefixed_choice:
            action, number = prefixed_choice
            selected_index = number - 1
            if selected_index < 0 or selected_index >= len(entries):
                print(_error("That number is not in the history list."))
                continue
            _source, _command, _timestamp, item, _count = entries[selected_index]
            result_code = _print_command(item) if action == "print" else _copy_command(item)
            if not _should_show_menu_after(item):
                return result_code
            continue
        if not choice.isdigit():
            print(_error("Please enter a number, pN, cN, f search, n, p, u, b or q."))
            continue
        selected_index = int(choice) - 1
        if selected_index < 0 or selected_index >= len(entries):
            print(_error("That number is not in the history list."))
            continue
        _source, _command, _timestamp, item, _count = entries[selected_index]
        return _run_command(item, shell_action_file)


def _command_shell(item) -> str | None:
    configured = str(item.get("shell") or "").strip()
    candidates = [configured, os.environ.get("SHELL") or ""]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    for fallback in ["/bin/bash", "/usr/bin/bash", "/bin/zsh", "/usr/bin/zsh"]:
        path = Path(fallback)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def _run_command(item, shell_action_file: Path | None = None) -> int:
    if item.get("type") == "sequence":
        return _run_sequence(item)
    command = _resolve_placeholders(_command_for_item(item))
    if not command:
        print(_error("This entry has no command."))
        return 1
    if item.get("confirm", True):
        answer = input(f'Run "{command}"? [Y/n] ').strip().lower()
        if answer in {"n", "no"}:
            print(_muted("Skipped."))
            return 0
    cd_target = _parse_cd_target(command)
    if cd_target is not None:
        if shell_action_file is None:
            print(_error("This cd command needs Shell Integration. Install it from the Quick Shell page and open a new shell."))
            return 2
        _write_shell_action(shell_action_file, f"cd {shlex.quote(str(cd_target))}")
        now = datetime.now().isoformat(timespec="seconds")
        _record_command_run(item, command, "shell-integration", now, now, 0)
        return 0
    print()
    started_at = datetime.now().isoformat(timespec="seconds")
    shell = _command_shell(item)
    result = subprocess.run(command, shell=True, executable=shell)
    ended_at = datetime.now().isoformat(timespec="seconds")
    _record_command_run(item, command, shell or "/bin/sh", started_at, ended_at, result.returncode)
    if result.returncode != 0:
        print()
        print(_error(f"Command finished with exit code {result.returncode}."))
    return result.returncode


def _run_sequence(item) -> int:
    steps = _sequence_steps(item)
    placeholder_values: dict[str, str] = {}
    resolved_steps: list[dict[str, object]] = []
    for step in steps:
        resolved_steps.append({
            **step,
            "original_command": str(step["command"]),
            "command": _resolve_placeholders(str(step["command"]), placeholder_values),
        })
    steps = resolved_steps
    lines = [str(step["command"]) for step in steps]
    name = _item_name(item)
    if not steps:
        print(_error("This sequence has no command lines."))
        return 1
    confirm_each = bool(item.get("confirm_each", False))
    print_comments = bool(item.get("print_comments", True))
    if item.get("confirm", True) and not confirm_each:
        answer = input(f'Run sequence "{name}" with {len(lines)} command line(s)? [Y/n] ').strip().lower()
        if answer in {"n", "no"}:
            print(_muted("Skipped."))
            return 0

    shell = _command_shell(item)
    status_file = tempfile.NamedTemporaryFile(prefix="systemd-gui-qs-status-", delete=False)
    status_path = Path(status_file.name)
    status_file.close()
    script_file = tempfile.NamedTemporaryFile("w", prefix="systemd-gui-qs-sequence-", suffix=".sh", delete=False, encoding="utf-8")
    script_path = Path(script_file.name)
    started_at = datetime.now().isoformat(timespec="seconds")
    try:
        script_file.write("#!/usr/bin/env sh\n")
        script_file.write(f'__qs_status_file={shlex.quote(str(status_path))}\n')
        if item.get("confirm", True) and confirm_each:
            confirm_prompt = f'Run sequence "{name}" with {len(lines)} command line(s)? [Y/n] '
            script_file.write(f"printf '%s' {shlex.quote(confirm_prompt)}\n")
            script_file.write("IFS= read -r __qs_answer\n")
            script_file.write('case "$__qs_answer" in\n')
            script_file.write("  n|N|no|NO) printf '%s\\n' 'Skipped.'; exit 0 ;;\n")
            script_file.write("esac\n")
        script_file.write("printf '\\n'\n")
        script_file.write(f"printf '%s\\n' {shlex.quote(f'Running sequence: {name}')}\n")
        script_file.write(f"printf '%s\\n' {shlex.quote('Runs in a separate shell; your current shell returns unchanged afterward.')}\n")
        for index, step in enumerate(steps, start=1):
            command = str(step["command"])
            if print_comments:
                comments = _sequence_comment_lines(step)
                if comments:
                    script_file.write("printf '\\n'\n")
                    for comment in comments:
                        script_file.write(f"printf '%s\\n' {shlex.quote(f'# {comment}')}\n")
            display_command = str(step.get("label") or command)
            label = f"[{index}/{len(steps)}] {display_command}"
            script_file.write("printf '\\n'\n")
            script_file.write(f"printf '%s\\n' {shlex.quote(label)}\n")
            script_file.write("__qs_skip=0\n")
            if confirm_each:
                script_file.write("printf 'Run this command? [Enter/Y=yes, N=skip, Q=abort] '\n")
                script_file.write("IFS= read -r __qs_answer\n")
                script_file.write('case "$__qs_answer" in\n')
                script_file.write("  n|N|no|NO|s|S|skip|SKIP) printf '%s\\n' 'Skipped.'; printf '%s\\tskipped\\n' " + shlex.quote(str(index)) + ' >> "$__qs_status_file"; __qs_skip=1 ;;\n')
                script_file.write("  e|E|q|Q|exit|quit) printf '%s\\n' 'Sequence aborted.'; printf '%s\\taborted\\n' " + shlex.quote(str(index)) + ' >> "$__qs_status_file"; exit 130 ;;\n')
                script_file.write("esac\n")
            script_file.write('if [ "$__qs_skip" -eq 0 ]; then\n')
            script_file.write(f"{command}\n")
            script_file.write("__qs_status=$?\n")
            script_file.write("printf '%s\\t%s\\n' " + shlex.quote(str(index)) + ' "$__qs_status" >> "$__qs_status_file"\n')
            script_file.write('if [ "$__qs_status" -ne 0 ]; then\n')
            script_file.write(f"  printf '%s\\n' {shlex.quote('Line failed with exit code')}\" $__qs_status.\"\n")
            if item.get("stop_on_error", True):
                script_file.write("  exit \"$__qs_status\"\n")
            script_file.write("fi\n")
            script_file.write("fi\n")
        script_file.close()
        script_path.chmod(0o700)
        result = subprocess.run([shell or "/bin/sh", str(script_path)])
        statuses = _read_sequence_statuses(status_path)
    finally:
        try:
            script_file.close()
        except OSError:
            pass
        try:
            script_path.unlink()
        except OSError:
            pass
        try:
            status_path.unlink()
        except OSError:
            pass

    ended_at = datetime.now().isoformat(timespec="seconds")
    _append_run_record(
        {
            "type": "sequence",
            "name": name,
            "started_at": started_at,
            "ended_at": ended_at,
            "exit_code": result.returncode,
            "shell": shell or "/bin/sh",
            "confirm_each": confirm_each,
            "print_comments": print_comments,
            "stop_on_error": bool(item.get("stop_on_error", True)),
            "lines": [
                {
                    "number": index,
                    "command": str(step["command"]),
                    "comments": _sequence_comment_lines(step),
                    "status": statuses.get(index, "not-run"),
                }
                for index, step in enumerate(steps, start=1)
            ],
        }
    )
    if result.returncode != 0:
        print()
        print(_error(f"Sequence finished with exit code {result.returncode}."))
    return result.returncode


def main() -> int:
    shell_action_file = None
    output_mode = "run"
    resume_last = False
    args = sys.argv[1:]
    if args[:1] == ["--shell-action-file"]:
        if len(args) < 2:
            print("Usage: qs [--shell-action-file PATH] [--print|--p|--copy|--c] [NUMBER ...]", file=sys.stderr)
            return 2
        shell_action_file = Path(args[1])
        args = args[2:]
    if args[:1] == ["--debug"]:
        return _print_debug(args[1:], shell_action_file)
    if args[:1] in (["--resume"], ["--r"], ["-r"], ["-rr"]):
        resume_last = True
        args = args[1:]
    if args[:1] in (["--print"], ["--p"], ["--copy"], ["--c"]):
        output_mode = "print" if args[0] in {"--print", "--p"} else "copy"
        args = args[1:]
    if args[:1] and args[0].lower() in {"n", "nodes", "remote", "remotes"}:
        if output_mode != "run":
            print(_error("Remote nodes cannot be printed or copied."), file=sys.stderr)
            return 2
        return _run_remote_nodes_direct(args[1:])

    try:
        direct_path = _parse_direct_path(args)
    except ValueError as exc:
        print(_error(str(exc)), file=sys.stderr)
        return 2
    if output_mode != "run" and not direct_path:
        print(_error("Print/copy needs a menu path, for example: qs --print 1-2"), file=sys.stderr)
        return 2

    entry_label, read_quick_shell = _load_helpers()
    data_path = _data_dir() / "quick-shell.json"
    data = read_quick_shell(data_path)
    items = data.get("items") or []
    initial_stack: list[str] = []
    initial_path: list[int] = []
    initial_menu_stack: list[list[dict]] | None = None
    initial_path_stack: list[list[int]] | None = None

    if direct_path:
        try:
            item, stack = _select_direct_path(items, direct_path, entry_label)
        except ValueError as exc:
            print(_error(str(exc)), file=sys.stderr)
            return 1
        if output_mode == "print":
            return _print_command(item, styled=False)
        if output_mode == "copy":
            return _copy_command(item)
        if item.get("type") == "category":
            try:
                initial_menu_stack, initial_stack, initial_path_stack = _build_category_stacks(items, direct_path, entry_label)
            except ValueError as exc:
                print(_error(str(exc)), file=sys.stderr)
                return 1
            initial_path = direct_path
        else:
            return _run_command(item, shell_action_file)
    elif resume_last:
        resume_path = _read_resume_path()
        if resume_path:
            try:
                item, stack = _select_direct_path(items, resume_path, entry_label)
            except ValueError:
                _write_resume_path([])
            else:
                if item.get("type") == "category":
                    try:
                        initial_menu_stack, initial_stack, initial_path_stack = _build_category_stacks(items, resume_path, entry_label)
                    except ValueError:
                        _write_resume_path([])
                    else:
                        initial_path = resume_path

    stack = initial_stack
    menu_stack: list[list[dict]] = initial_menu_stack or [items]
    path_stack: list[list[int]] = initial_path_stack or [initial_path]

    while True:
        _write_resume_path(path_stack[-1])
        current_items = _enabled_items(menu_stack[-1])
        remote_nodes = _read_remote_nodes() if len(menu_stack) == 1 else []
        print()
        title = _menu_title(stack)
        _print_menu_header(title)
        if not current_items:
            print(_muted("No active entries in this menu."))
        print(f"{_style('S', 'yellow')} Command history")
        if remote_nodes:
            print(f"{_style('N', 'yellow')} Remote nodes")
        for index, item in enumerate(current_items, start=1):
            label = entry_label(item)
            number = _style(str(index), "bold")
            if item.get("type") == "category":
                print(f"{number} {_style(label + '/', 'cyan')}")
            else:
                print(f"{number} {label}")
        if len(menu_stack) > 1:
            print(f"{_style('b', 'yellow')} Back")
        print(f"{_style('q', 'yellow')} {_quit_label()}")
        print(_muted("Tip: p2 means print item 2. c2 means copy item 2 when a clipboard tool is available."))

        choice = _prompt_choice(len(current_items), len(menu_stack) > 1, bool(remote_nodes))
        if choice == "q":
            return 0
        if choice == "s":
            result_code = _show_history_menu(data.get("settings") or {}, shell_action_file)
            if result_code is not None:
                return result_code
            continue
        if choice == "n" and remote_nodes:
            result_code = _show_remote_nodes_menu()
            if result_code is not None:
                return result_code
            continue
        if choice == "b" and len(menu_stack) > 1:
            menu_stack.pop()
            stack.pop()
            path_stack.pop()
            continue
        prefixed_choice = _parse_prefixed_choice(choice)
        if prefixed_choice:
            action, number = prefixed_choice
            selected_index = number - 1
            if selected_index < 0 or selected_index >= len(current_items):
                print(_error("That number is not in the menu."))
                continue
            item = current_items[selected_index]
            result_code = _print_command(item) if action == "print" else _copy_command(item)
            if not _should_show_menu_after(item):
                return result_code
            continue
        if not choice.isdigit():
            print(_error(f"Please enter a number, pN, cN, S, b or q ({_quit_label().lower()})."))
            continue
        selected_index = int(choice) - 1
        if selected_index < 0 or selected_index >= len(current_items):
            print(_error("That number is not in the menu."))
            continue
        item = current_items[selected_index]
        label = entry_label(item)
        if item.get("type") == "category":
            stack.append(label)
            menu_stack.append(list(item.get("items") or []))
            path_stack.append([*path_stack[-1], selected_index + 1])
            continue
        result_code = _run_command(item, shell_action_file)
        if not _should_show_menu_after(item):
            return result_code


if __name__ == "__main__":
    raise SystemExit(main())

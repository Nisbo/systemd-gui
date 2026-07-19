#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import importlib.util
import shlex
import shutil
import subprocess
import sys
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


def _prompt_choice(max_number: int, can_go_back: bool) -> str:
    hints = ["number", "pN", "cN", "S"]
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


def _command_for_item(item) -> str:
    return str(item.get("command") or "").strip()


def _print_command(item, styled: bool = True) -> int:
    if item.get("type") == "category":
        print(_error("Categories do not have a command to print. Select a command inside the category."))
        return 1
    command = _command_for_item(item)
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
    command = _command_for_item(item)
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


def _parse_timestamp(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    return None


def _read_shell_history() -> list[tuple[Path, str, int | None]]:
    entries: list[tuple[Path, str, int | None]] = []
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
                entries.append((source, command, timestamp))
            pending_timestamp = None
    return list(reversed(entries))


def _compact_history(entries: list[tuple[Path, str, int | None]]) -> list[tuple[Path, str, int | None, int]]:
    compacted: list[tuple[Path, str, int | None, int]] = []
    for source, command, timestamp in entries:
        if compacted and compacted[-1][1] == command:
            previous_source, previous_command, previous_timestamp, previous_count = compacted[-1]
            compacted[-1] = (previous_source, previous_command, previous_timestamp, previous_count + 1)
            continue
        compacted.append((source, command, timestamp, 1))
    return compacted


def _raw_history(entries: list[tuple[Path, str, int | None]]) -> list[tuple[Path, str, int | None, int]]:
    return [(source, command, timestamp, 1) for source, command, timestamp in entries]


def _format_history_time(timestamp: int | None) -> str:
    if timestamp is None:
        return "-"
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return "-"


def _history_item(source: Path, command: str) -> dict:
    return {
        "type": "command",
        "name": command,
        "command": command,
        "shell": _history_shell(source),
        "enabled": True,
        "confirm": True,
        "show_menu_after": False,
    }


def _show_history_menu(settings: dict | None = None, shell_action_file: Path | None = None) -> int | None:
    raw_entries = _read_shell_history()
    show_unfiltered = False
    filter_query = ""
    page = 0
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
        title = "Quick Shell / Shell history"
        print(_heading(title, "blue"))
        print(_style("=" * len(title), "blue"))
        if filter_query:
            print(_muted(f'Filter: "{filter_query}"'))
        if not entries and filter_query:
            print(_muted("No history entries match this filter."))
        elif not entries:
            print(_muted("No readable shell history file was found for this user."))
            print(_muted("Some shells write history only after logout or after running history -a."))
        elif show_unfiltered:
            timestamp_note = " Time is shown when the shell saved it." if show_timestamps else ""
            print(_muted(f"Newest readable entries for the current server user, including repeated commands.{timestamp_note}"))
        else:
            timestamp_note = " Time is shown when the shell saved it." if show_timestamps else ""
            print(_muted(f"Newest readable entries for the current server user. Consecutive duplicates are collapsed.{timestamp_note}"))
        if entries:
            print(_muted(f"Page {page + 1}/{total_pages}. Showing {page_start + 1}-{page_start + len(page_entries)} of {len(entries)}."))
        for offset, (source, command, timestamp, count) in enumerate(page_entries):
            number_value = page_start + offset + 1
            number = _style(str(number_value), "bold")
            repeat = f" {_style(f'x{count}', 'yellow')}" if count > 1 else ""
            history_time = f"{_muted(_format_history_time(timestamp))} " if show_timestamps else ""
            print(f"{number} {_muted(source.name)} {history_time}{command}{repeat}")
        if page > 0:
            print(f"{_style('p', 'yellow')} Previous page")
        if page + 1 < total_pages:
            print(f"{_style('n', 'yellow')} Next page")
        if raw_entries:
            print(f"{_style('f', 'yellow')} Search or clear filter")
            toggle_label = "Hide repeated commands" if show_unfiltered else "Show unfiltered history"
            print(f"{_style('u', 'yellow')} {toggle_label}")
        print(f"{_style('b', 'yellow')} Back")
        print(f"{_style('q', 'yellow')} Quit")
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
            source, command, _timestamp, _count = entries[selected_index]
            item = _history_item(source, command)
            result_code = _print_command(item) if action == "print" else _copy_command(item)
            if not item.get("show_menu_after", False):
                return result_code
            continue
        if not choice.isdigit():
            print(_error("Please enter a number, pN, cN, f search, n, p, u, b or q."))
            continue
        selected_index = int(choice) - 1
        if selected_index < 0 or selected_index >= len(entries):
            print(_error("That number is not in the history list."))
            continue
        source, command, _timestamp, _count = entries[selected_index]
        return _run_command(_history_item(source, command), shell_action_file)


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
    command = _command_for_item(item)
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
        return 0
    print()
    result = subprocess.run(command, shell=True, executable=_command_shell(item))
    if result.returncode != 0:
        print()
        print(_error(f"Command finished with exit code {result.returncode}."))
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
        print()
        title = _menu_title(stack)
        print(_heading(title, "green"))
        print(_style("=" * len(title), "green"))
        if not current_items:
            print(_muted("No active entries in this menu."))
        print(f"{_style('S', 'yellow')} Shell history")
        for index, item in enumerate(current_items, start=1):
            label = entry_label(item)
            number = _style(str(index), "bold")
            if item.get("type") == "category":
                print(f"{number} {_style(label + '/', 'cyan')}")
            else:
                print(f"{number} {label}")
        if len(menu_stack) > 1:
            print(f"{_style('b', 'yellow')} Back")
        print(f"{_style('q', 'yellow')} Quit")
        print(_muted("Tip: p2 means print item 2. c2 means copy item 2 when a clipboard tool is available."))

        choice = _prompt_choice(len(current_items), len(menu_stack) > 1)
        if choice == "q":
            return 0
        if choice == "s":
            result_code = _show_history_menu(data.get("settings") or {}, shell_action_file)
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
            if not item.get("show_menu_after", False):
                return result_code
            continue
        if not choice.isdigit():
            print(_error("Please enter a number, pN, cN, S, b or q."))
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
        if not item.get("show_menu_after", False):
            return result_code


if __name__ == "__main__":
    raise SystemExit(main())

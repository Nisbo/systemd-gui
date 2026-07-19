#!/usr/bin/env python3
from __future__ import annotations

import os
import importlib.util
import shlex
import subprocess
import sys
from pathlib import Path


def _app_root() -> Path:
    return Path(os.environ.get("SYSTEMD_GUI_ROOT") or Path(__file__).resolve().parents[1])


def _data_dir() -> Path:
    return Path(os.environ.get("SYSTEMD_GUI_DATA_DIR") or (_app_root() / "data"))


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
    hints = ["number"]
    if can_go_back:
        hints.append("b")
    hints.append("q")
    return input(f"Choose ({'/'.join(hints)}): ").strip().lower()


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
    print("Quick Shell debug")
    print(f"script: {Path(__file__).resolve()}")
    print(f"app root: {_app_root()}")
    print(f"data dir: {_data_dir()}")
    print(f"data file: {_data_dir() / 'quick-shell.json'}")
    print(f"shell action file: {shell_action_file or '-'}")
    print(f"arguments: {args or '-'}")
    try:
        direct_path = _parse_direct_path(args)
    except ValueError as exc:
        print(f"direct path: invalid ({exc})")
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


def _run_command(item, shell_action_file: Path | None = None) -> int:
    command = str(item.get("command") or "").strip()
    if not command:
        print("This entry has no command.")
        return 1
    if item.get("confirm", True):
        answer = input(f'Run "{command}"? [y/N] ').strip().lower()
        if answer not in {"y", "yes"}:
            print("Skipped.")
            return 0
    cd_target = _parse_cd_target(command)
    if cd_target is not None:
        if shell_action_file is None:
            print("This cd command needs Shell Integration. Install it from the Quick Shell page and open a new shell.")
            return 2
        _write_shell_action(shell_action_file, f"cd {shlex.quote(str(cd_target))}")
        return 0
    print()
    result = subprocess.run(command, shell=True)
    if result.returncode != 0:
        print()
        print(f"Command finished with exit code {result.returncode}.")
    return result.returncode


def main() -> int:
    shell_action_file = None
    args = sys.argv[1:]
    if args[:1] == ["--shell-action-file"]:
        if len(args) < 2:
            print("Usage: qs [--shell-action-file PATH] [NUMBER ...]", file=sys.stderr)
            return 2
        shell_action_file = Path(args[1])
        args = args[2:]
    if args[:1] == ["--debug"]:
        return _print_debug(args[1:], shell_action_file)

    try:
        direct_path = _parse_direct_path(args)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    entry_label, read_quick_shell = _load_helpers()
    data_path = _data_dir() / "quick-shell.json"
    data = read_quick_shell(data_path)
    items = data.get("items") or []
    initial_stack: list[str] = []

    if direct_path:
        try:
            item, stack = _select_direct_path(items, direct_path, entry_label)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        if item.get("type") == "category":
            stack.append(entry_label(item))
            items = list(item.get("items") or [])
            initial_stack = stack
        else:
            return _run_command(item, shell_action_file)

    stack = initial_stack
    menu_stack: list[list[dict]] = [items]

    while True:
        current_items = _enabled_items(menu_stack[-1])
        print()
        print(_menu_title(stack))
        print("=" * len(_menu_title(stack)))
        if not current_items:
            print("No active entries in this menu.")
        for index, item in enumerate(current_items, start=1):
            label = entry_label(item)
            suffix = "/" if item.get("type") == "category" else ""
            print(f"{index} {label}{suffix}")
        if len(menu_stack) > 1:
            print("b Back")
        print("q Quit")

        choice = _prompt_choice(len(current_items), len(menu_stack) > 1)
        if choice == "q":
            return 0
        if choice == "b" and len(menu_stack) > 1:
            menu_stack.pop()
            stack.pop()
            continue
        if not choice.isdigit():
            print("Please enter a number, b or q.")
            continue
        selected_index = int(choice) - 1
        if selected_index < 0 or selected_index >= len(current_items):
            print("That number is not in the menu.")
            continue
        item = current_items[selected_index]
        label = entry_label(item)
        if item.get("type") == "category":
            stack.append(label)
            menu_stack.append(list(item.get("items") or []))
            continue
        result_code = _run_command(item, shell_action_file)
        if not item.get("show_menu_after", False):
            return result_code


if __name__ == "__main__":
    raise SystemExit(main())

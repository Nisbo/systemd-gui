#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _app_root() -> Path:
    return Path(os.environ.get("SYSTEMD_GUI_ROOT") or Path(__file__).resolve().parents[1])


def _data_dir() -> Path:
    return Path(os.environ.get("SYSTEMD_GUI_DATA_DIR") or (_app_root() / "data"))


def _load_helpers():
    root = _app_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from systemd_gui.quick_shell import entry_label, read_quick_shell

    return entry_label, read_quick_shell


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


def _run_command(item, label: str) -> None:
    command = str(item.get("command") or "").strip()
    if not command:
        print("This entry has no command.")
        return
    if item.get("confirm", True):
        answer = input(f'Run "{command}"? [y/N] ').strip().lower()
        if answer not in {"y", "yes"}:
            print("Skipped.")
            return
    print()
    result = subprocess.run(command, shell=True)
    print()
    print(f"{label} finished with exit code {result.returncode}.")


def main() -> int:
    entry_label, read_quick_shell = _load_helpers()
    data_path = _data_dir() / "quick-shell.json"
    data = read_quick_shell(data_path)
    items = data.get("items") or []
    stack: list[str] = []
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
        _run_command(item, label)


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class UpdateResult:
    ok: bool
    message: str
    details: list[str]


def git_update_state(app_root: Path) -> dict[str, str | bool]:
    if not (app_root / ".git").exists():
        return {
            "available": False,
            "dirty": False,
            "branch": "",
            "commit": "",
            "remote": "",
            "message": "This installation is not a git checkout.",
        }
    if not shutil.which("git"):
        return {
            "available": False,
            "dirty": False,
            "branch": "",
            "commit": "",
            "remote": "",
            "message": "git is not available in this environment.",
        }

    status = _git(app_root, ["status", "--short"])
    branch = _git(app_root, ["branch", "--show-current"])
    commit = _git(app_root, ["rev-parse", "--short", "HEAD"])
    remote = _git(app_root, ["remote", "get-url", "origin"])
    dirty = bool(status.output.strip()) if status.ok else True
    message = "Local changes are present." if dirty else "Working tree is clean."
    if not remote.ok:
        message = "No git remote named origin is configured."

    return {
        "available": True,
        "dirty": dirty,
        "branch": branch.output.strip() if branch.ok else "",
        "commit": commit.output.strip() if commit.ok else "",
        "remote": remote.output.strip() if remote.ok else "",
        "message": message,
    }


def update_from_git(app_root: Path) -> UpdateResult:
    state = git_update_state(app_root)
    if not state["available"]:
        return UpdateResult(False, str(state["message"]), [])
    if state["dirty"]:
        return UpdateResult(False, "Git update refused because local changes are present.", [str(state["message"])])
    if not state["remote"]:
        return UpdateResult(False, "Git update refused because no origin remote is configured.", [])

    result = _git(app_root, ["pull", "--ff-only"], timeout=60)
    details = [result.output] if result.output else []
    if result.ok:
        return UpdateResult(True, "Git update completed. Restart Systemd Gui to load changed files.", details)
    return UpdateResult(False, "Git update failed.", details)


@dataclass
class _CommandResult:
    ok: bool
    output: str


def _git(app_root: Path, args: list[str], timeout: int = 12) -> _CommandResult:
    git = shutil.which("git")
    if not git:
        return _CommandResult(False, "git is not available in this environment.")
    try:
        result = subprocess.run(
            [git, *args],
            cwd=app_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _CommandResult(False, str(exc))
    output = (result.stdout + "\n" + result.stderr).strip()
    return _CommandResult(result.returncode == 0, output)

from __future__ import annotations

import html
import json
import shutil
import stat
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from .version import APP_VERSION, RELEASES_LIST_API_URL

PROJECT_DIRS = ("systemd_gui", "scripts")
PROJECT_FILES = ("run.py", "README.md", ".gitignore")
RUNTIME_DATA_ITEMS = ("favorites.json", "service-notes.json", "quick-shell.json", "unit-backups", "drop-in-backups", "env-backups")
APP_BACKUP_META_FILE = "backup-meta.txt"


@dataclass
class UpdateResult:
    ok: bool
    message: str
    details: list[str] = field(default_factory=list)
    backup_path: Path | None = None


@dataclass
class AppBackupEntry:
    backup_id: str
    path: Path
    created_at: str
    size: int
    reason: str
    comment: str


@dataclass
class ReleaseNote:
    version: str
    title: str
    url: str | None
    body: str
    body_html: str
    published_at: str | None


@dataclass
class UpdateStatus:
    current_version: str
    latest_version: str | None
    update_available: bool
    release_url: str | None
    zipball_url: str | None
    release_notes: list[ReleaseNote]
    error: str | None = None
    no_releases: bool = False


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
    if not state["remote"]:
        return UpdateResult(False, "Git update refused because no origin remote is configured.", [])

    branch = str(state["branch"] or "main")
    if branch == "HEAD":
        branch = "main"
    remote_ref = f"origin/{branch}"
    backup_path = create_app_backup(app_root, "Before Git update", f"Created automatically before moving Systemd Gui {APP_VERSION} to {remote_ref}.")
    details = [f"Backup created: {backup_path}"]

    fetch = _git(app_root, ["fetch", "--tags", "--prune", "origin"], timeout=60)
    details.append(_format_command_result("git fetch --tags --prune origin", fetch))
    if not fetch.ok:
        return UpdateResult(False, "Git fetch failed. No files were replaced.", details, backup_path)

    remote_check = _git(app_root, ["rev-parse", "--verify", remote_ref])
    details.append(_format_command_result(f"git rev-parse --verify {remote_ref}", remote_check))
    if not remote_check.ok:
        return UpdateResult(False, f"Remote branch {remote_ref} was not found after fetch.", details, backup_path)

    checkout = _git(app_root, ["checkout", "--force", "-B", branch, remote_ref])
    details.append(_format_command_result(f"git checkout --force -B {branch} {remote_ref}", checkout))
    if not checkout.ok:
        return UpdateResult(False, "Git checkout failed. Check the output below.", details, backup_path)

    return UpdateResult(True, "Git update completed. Restart Systemd Gui to run the new code.", details, backup_path)


def check_for_update(timeout: int = 5) -> UpdateStatus:
    request = urllib.request.Request(
        RELEASES_LIST_API_URL,
        headers={
            "Accept": "application/vnd.github.html+json",
            "User-Agent": "systemd-gui-update-check",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return _no_releases_status()
        return _error_status(str(exc))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return _error_status(str(exc))

    releases = payload if isinstance(payload, list) else []
    official_releases = [release for release in releases if not release.get("draft") and not release.get("prerelease")]
    if not official_releases:
        return _no_releases_status()

    latest_release = official_releases[0]
    latest = _release_version(latest_release)
    newer_releases = [
        release
        for release in official_releases
        if _release_version(release) and _version_key(_release_version(release)) > _version_key(APP_VERSION)
    ]
    return UpdateStatus(
        current_version=APP_VERSION,
        latest_version=latest or None,
        update_available=bool(latest and _version_key(latest) > _version_key(APP_VERSION)),
        release_url=latest_release.get("html_url"),
        zipball_url=latest_release.get("zipball_url"),
        release_notes=[_release_note(release) for release in newer_releases],
    )


def update_status_to_dict(status: UpdateStatus) -> dict[str, object]:
    return {
        **asdict(status),
        "release_notes": [asdict(note) for note in status.release_notes],
    }


def update_from_release(app_root: Path, zip_url: str, version: str, timeout: int = 30) -> UpdateResult:
    git_result = _update_git_checkout_to_release(app_root, version)
    if git_result is not None:
        return git_result

    request = urllib.request.Request(
        zip_url,
        headers={
            "Accept": "application/zip",
            "User-Agent": "systemd-gui-release-update",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            zip_data = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return UpdateResult(False, f"Release download failed: {exc}")

    result = update_from_zip(
        app_root,
        BytesIO(zip_data),
        backup_reason=f"Before installing release {version}",
        backup_comment=f"Created automatically before updating Systemd Gui from {APP_VERSION} to release {version}.",
    )
    if result.ok:
        result.message = f"Release {version} installed. Restart Systemd Gui to run the new code."
    result.details.insert(0, f"Downloaded official release ZIP: {version}")
    return result


def update_from_zip(app_root: Path, zip_stream: BinaryIO, backup_reason: str = "Before ZIP update", backup_comment: str | None = None) -> UpdateResult:
    with tempfile.TemporaryDirectory(prefix="systemd-gui-update-") as tmp_name:
        extract_dir = Path(tmp_name) / "extract"
        extract_dir.mkdir()
        try:
            _safe_extract_zip(zip_stream, extract_dir)
            source_root = _find_project_root(extract_dir)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            return UpdateResult(False, f"ZIP update failed validation: {exc}")

        backup_path = create_app_backup(app_root, backup_reason, backup_comment or f"Created automatically before installing an uploaded ZIP over Systemd Gui {APP_VERSION}.")
        details = [
            f"Backup created: {backup_path}",
            f"Source root detected: {source_root.name}",
        ]
        try:
            copied = _copy_project_files(source_root, app_root)
        except (OSError, ValueError) as exc:
            return UpdateResult(False, f"ZIP update failed while copying files: {exc}", details, backup_path)
        details.extend(f"Updated: {item}" for item in copied)

    return UpdateResult(True, "ZIP update installed. Restart Systemd Gui to run the new code.", details, backup_path)


def create_app_backup(app_root: Path, reason: str = "Manual app backup", comment: str = "") -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_root = _app_backup_root(app_root) / timestamp
    backup_root.mkdir(parents=True, exist_ok=False)

    for directory in PROJECT_DIRS:
        source = app_root / directory
        if source.exists():
            shutil.copytree(source, backup_root / directory, ignore=_ignore_runtime_files)

    for filename in PROJECT_FILES:
        source = app_root / filename
        if source.exists():
            shutil.copy2(source, backup_root / filename)

    _copy_runtime_data(app_root / "data", backup_root / "data")
    _write_backup_meta(backup_root, reason, comment)
    return backup_root


def list_app_backups(app_root: Path) -> list[AppBackupEntry]:
    backup_root = _app_backup_root(app_root)
    if not backup_root.exists():
        return []

    entries: list[AppBackupEntry] = []
    for path in backup_root.iterdir():
        if not path.is_dir():
            continue
        meta = _read_backup_meta(path)
        entries.append(
            AppBackupEntry(
                backup_id=path.name,
                path=path,
                created_at=_format_backup_id(path.name),
                size=_directory_size(path),
                reason=meta["reason"],
                comment=meta["comment"],
            )
        )
    return sorted(entries, key=lambda entry: entry.backup_id, reverse=True)


def restore_app_backup(app_root: Path, backup_id: str) -> UpdateResult:
    try:
        backup_path = _app_backup_path(app_root, backup_id)
    except ValueError as exc:
        return UpdateResult(False, str(exc))
    if not backup_path.exists() or not backup_path.is_dir():
        return UpdateResult(False, f"App update backup not found: {backup_id}")

    safety_backup = create_app_backup(app_root, "Before app backup restore", f"Created automatically before restoring app backup {backup_id}.")
    details = [f"Safety backup created: {safety_backup}", f"Restored from: {backup_path}"]
    try:
        copied = _copy_project_files(backup_path, app_root)
        copied.extend(_copy_runtime_data(backup_path / "data", app_root / "data"))
    except (OSError, ValueError) as exc:
        return UpdateResult(False, f"App backup restore failed: {exc}", details, safety_backup)
    details.extend(f"Restored: {item}" for item in copied)
    return UpdateResult(True, "App backup restored. Restart Systemd Gui to run the restored code.", details, safety_backup)


def delete_app_backup(app_root: Path, backup_id: str) -> None:
    backup_path = _app_backup_path(app_root, backup_id)
    if not backup_path.exists() or not backup_path.is_dir():
        raise FileNotFoundError(f"App update backup not found: {backup_id}")
    shutil.rmtree(backup_path)


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


def _update_git_checkout_to_release(app_root: Path, version: str) -> UpdateResult | None:
    git = shutil.which("git")
    if not git or not (app_root / ".git").exists():
        return None

    backup_path = create_app_backup(
        app_root,
        f"Before installing release {version}",
        f"Created automatically before moving Systemd Gui from {APP_VERSION} to release {version}.",
    )
    details = [f"Backup created: {backup_path}"]

    fetch = _git(app_root, ["fetch", "--tags", "--prune", "origin"], timeout=60)
    details.append(_format_command_result("git fetch --tags --prune origin", fetch))
    if not fetch.ok:
        return UpdateResult(False, "Git fetch failed. No files were replaced.", details, backup_path)

    tag_ref = f"refs/tags/{version}"
    tag_check = _git(app_root, ["rev-parse", "--verify", tag_ref])
    details.append(_format_command_result(f"git rev-parse --verify {tag_ref}", tag_check))
    if not tag_check.ok:
        return UpdateResult(False, f"Release tag {version} was not found after fetch.", details, backup_path)

    checkout = _git(app_root, ["checkout", "--force", "-B", "main", tag_ref])
    details.append(_format_command_result(f"git checkout --force -B main {tag_ref}", checkout))
    if not checkout.ok:
        return UpdateResult(False, "Git checkout failed. Check the output below.", details, backup_path)

    return UpdateResult(True, f"Release {version} installed through git. Restart Systemd Gui to run the new code.", details, backup_path)


def _safe_extract_zip(zip_stream: BinaryIO, destination: Path) -> None:
    with zipfile.ZipFile(zip_stream) as archive:
        for info in archive.infolist():
            target = (destination / info.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError(f"Unsafe ZIP path: {info.filename}")

            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(f"Symlinks are not allowed in update ZIP files: {info.filename}")

            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _find_project_root(extract_dir: Path) -> Path:
    candidates = [extract_dir]
    candidates.extend(path for path in extract_dir.iterdir() if path.is_dir())

    for candidate in candidates:
        if (candidate / "systemd_gui").is_dir() and (candidate / "run.py").is_file():
            return candidate

    raise ValueError("ZIP must contain run.py and the systemd_gui directory.")


def _copy_project_files(source_root: Path, app_root: Path) -> list[str]:
    copied: list[str] = []

    for directory in PROJECT_DIRS:
        source = source_root / directory
        if not source.exists():
            continue
        target = app_root / directory
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, ignore=_ignore_runtime_files)
        copied.append(directory)

    for filename in PROJECT_FILES:
        source = source_root / filename
        if not source.exists():
            continue
        shutil.copy2(source, app_root / filename)
        copied.append(filename)

    if "systemd_gui" not in copied or "run.py" not in copied:
        raise ValueError("Update did not contain the required app files.")
    return copied


def _copy_runtime_data(source_data: Path, target_data: Path) -> list[str]:
    copied: list[str] = []
    if not source_data.exists():
        return copied
    target_data.mkdir(parents=True, exist_ok=True)
    for item_name in RUNTIME_DATA_ITEMS:
        source = source_data / item_name
        if not source.exists():
            continue
        target = target_data / item_name
        if source.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target, ignore=_ignore_runtime_files)
        else:
            shutil.copy2(source, target)
        copied.append(f"data/{item_name}")
    return copied


def _ignore_runtime_files(directory: str, names: list[str]) -> set[str]:
    ignored = {"__pycache__", ".DS_Store"}
    if Path(directory).name == "systemd_gui":
        ignored.add("data")
    return ignored.intersection(names)


def _app_backup_root(app_root: Path) -> Path:
    return app_root / "data" / "app-updates" / "backups"


def _app_backup_path(app_root: Path, backup_id: str) -> Path:
    if not backup_id or "/" in backup_id or "\\" in backup_id or backup_id in {".", ".."}:
        raise ValueError("Invalid app backup id.")
    backup_root = _app_backup_root(app_root).resolve()
    backup_path = (backup_root / backup_id).resolve()
    if not backup_path.is_relative_to(backup_root):
        raise ValueError("Invalid app backup path.")
    return backup_path


def _directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def _format_backup_id(backup_id: str) -> str:
    try:
        stamp = backup_id.split("-", 2)
        if len(stamp) < 2:
            return backup_id
        base = "-".join(stamp[:2])
        parsed = datetime.strptime(base, "%Y%m%d-%H%M%S")
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return backup_id


def _write_backup_meta(path: Path, reason: str, comment: str) -> None:
    content = "\n".join(
        (
            f"reason={_clean_meta_value(reason)}",
            f"comment={_clean_meta_value(comment)}",
        )
    )
    (path / APP_BACKUP_META_FILE).write_text(content + "\n", encoding="utf-8")


def _read_backup_meta(path: Path) -> dict[str, str]:
    meta = {"reason": "Unknown", "comment": ""}
    meta_path = path / APP_BACKUP_META_FILE
    if not meta_path.exists():
        return meta
    for line in meta_path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, separator, value = line.partition("=")
        if separator and key in meta:
            meta[key] = value.strip()
    return meta


def _release_version(release: dict[str, object]) -> str:
    version = str(release.get("tag_name") or release.get("name") or "").strip()
    return version.removeprefix("v")


def _release_note(release: dict[str, object]) -> ReleaseNote:
    version = _release_version(release)
    title = str(release.get("name") or version)
    body = str(release.get("body") or "").strip()
    body_html = str(release.get("body_html") or "").strip()
    if not body_html and body:
        body_html = f"<pre>{html.escape(body)}</pre>"
    return ReleaseNote(
        version=version,
        title=title,
        url=release.get("html_url") if isinstance(release.get("html_url"), str) else None,
        body=body,
        body_html=body_html,
        published_at=release.get("published_at") if isinstance(release.get("published_at"), str) else None,
    )


def _version_key(value: str) -> tuple[int, ...]:
    import re

    parts = re.findall(r"\d+", value)
    return tuple(int(part) for part in parts) or (0,)


def _no_releases_status() -> UpdateStatus:
    return UpdateStatus(
        current_version=APP_VERSION,
        latest_version=None,
        update_available=False,
        release_url=None,
        zipball_url=None,
        release_notes=[],
        no_releases=True,
    )


def _error_status(error: str) -> UpdateStatus:
    return UpdateStatus(
        current_version=APP_VERSION,
        latest_version=None,
        update_available=False,
        release_url=None,
        zipball_url=None,
        release_notes=[],
        error=error,
    )


def _format_command_result(label: str, result: _CommandResult) -> str:
    output = result.output or "(no output)"
    return f"$ {label}\n{output}"


def _clean_meta_value(value: str) -> str:
    return " ".join(str(value).split())

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

PROTECTED_EXACT = {
    "dbus.service",
    "networking.service",
    "NetworkManager.service",
    "ssh.service",
    "sshd.service",
    "systemd-journald.service",
    "systemd-logind.service",
    "systemd-networkd.service",
    "systemd-resolved.service",
    "systemd-timesyncd.service",
    "systemd-udevd.service",
}
PROTECTED_PREFIXES = ("systemd-",)
VALID_SERVICE_RE = re.compile(r"^[A-Za-z0-9_.@:-]+\.service$")
SERVICE_CATALOG_PATH = Path(__file__).with_name("service_catalog.json")
SYSTEMD_ETC_ROOT = Path("/etc/systemd/system")
ACTIVE_STATE_HELP = {
    "active": "The unit is currently started. For services this can mean it is still running, or it successfully finished and remains active.",
    "inactive": "The unit is currently stopped. Nothing is running for this service right now.",
    "failed": "The unit tried to start or run but ended with an error. Open Logs to see why.",
    "activating": "The unit is currently starting or reloading.",
    "deactivating": "The unit is currently stopping.",
    "reloading": "The unit is active and systemd is reloading its configuration.",
    "unknown": "systemd did not report a clear active state for this service.",
    "not-found": "systemd did not find this service. The name may be wrong or the unit may not exist on this system.",
}
SUB_STATE_HELP = {
    "running": "The service process is currently running.",
    "exited": "The start command finished successfully. This is normal for one-shot services that do their work and exit.",
    "dead": "No service process is running.",
    "failed": "The service stopped because of an error.",
    "auto-restart": "systemd is waiting to restart the service automatically.",
    "start": "systemd is currently starting the service.",
    "start-pre": "A pre-start command is running.",
    "start-post": "A post-start command is running.",
    "stop": "systemd is currently stopping the service.",
    "stop-sigterm": "systemd sent SIGTERM and is waiting for the service to stop.",
    "stop-sigkill": "systemd sent SIGKILL because the service did not stop in time.",
    "stop-post": "A post-stop command is running.",
    "reload": "systemd is currently reloading the service.",
    "listening": "The service is waiting for incoming socket activity.",
    "mounted": "The unit is mounted. This is unusual in the service-only view.",
    "unknown": "systemd did not report a clear detailed state.",
    "not-found": "No detailed state is available because systemd did not find this service.",
}
UNIT_FILE_STATE_HELP = {
    "enabled": "This service is configured to start automatically at boot.",
    "enabled-runtime": "This service is enabled only until the next reboot.",
    "disabled": "This service is not configured to start automatically at boot.",
    "static": "This unit cannot be enabled directly. It is usually started by another unit, dependency, socket, path or timer.",
    "alias": "This name is an alias that points to another unit.",
    "masked": "This unit is blocked from being started until it is unmasked.",
    "generated": "This unit file was generated automatically by systemd or another tool.",
    "transient": "This unit was created at runtime and may not have a normal unit file on disk.",
    "bad": "systemd found a problem with this unit file.",
    "unknown": "systemd did not report an autostart state. This is common for template instances, generated units or units that only exist at runtime.",
    "not-found": "No autostart state is available because systemd did not find this service.",
}


@dataclass
class CommandResult:
    ok: bool
    output: str
    returncode: int


def systemctl_available() -> bool:
    return bool(shutil.which("systemctl"))


def journalctl_available() -> bool:
    return bool(shutil.which("journalctl"))


def valid_service_name(name: str) -> bool:
    return bool(VALID_SERVICE_RE.match(name or ""))


def is_protected_service(name: str) -> bool:
    if name in PROTECTED_EXACT:
        return True
    return any(name.startswith(prefix) for prefix in PROTECTED_PREFIXES)


def is_template_unit(name: str) -> bool:
    return "@" in name and name.endswith("@.service")


def run_systemctl(args: list[str], timeout: int = 12) -> CommandResult:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return CommandResult(False, "systemctl is not available in this environment.", 127)
    try:
        result = subprocess.run([systemctl, *args], check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(False, str(exc), 1)
    output = (result.stdout + "\n" + result.stderr).strip()
    return CommandResult(result.returncode == 0, output, result.returncode)


def run_journalctl(service: str, lines: int = 200) -> CommandResult:
    journalctl = shutil.which("journalctl")
    if not journalctl:
        return CommandResult(False, "journalctl is not available in this environment.", 127)
    try:
        result = subprocess.run(
            [journalctl, "-u", service, "-n", str(lines), "--no-pager", "--output=short-iso"],
            check=False,
            capture_output=True,
            text=True,
            timeout=12,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(False, str(exc), 1)
    output = (result.stdout + "\n" + result.stderr).strip()
    return CommandResult(result.returncode == 0, output, result.returncode)


def list_services(query: str = "", favorites: set[str] | None = None, state_filter: str = "", sub_filter: str = "", autostart_filter: str = "") -> list[dict[str, str | bool]]:
    favorites = favorites or set()
    units = _active_units()
    files = _unit_files()
    names = sorted(set(units) | set(files))
    if query:
        q = query.lower()
        names = [name for name in names if q in name.lower() or q in str(units.get(name, {}).get("description", "")).lower()]

    services = []
    for name in names:
        unit = units.get(name, {})
        file_state = files.get(name, {})
        active = unit.get("active", "inactive")
        sub = unit.get("sub", "-")
        if state_filter and active != state_filter:
            continue
        if sub_filter and sub != sub_filter:
            continue
        enabled = file_state.get("state", "unknown")
        if autostart_filter and enabled != autostart_filter:
            continue
        info = service_catalog_info(name, unit.get("description", ""))
        services.append({
            "name": name,
            "load": unit.get("load", "-"),
            "active": active,
            "sub": sub,
            "description": unit.get("description", ""),
            "enabled": enabled,
            "enabled_help": unit_file_state_help(enabled, name),
            "preset": file_state.get("preset", ""),
            "favorite": name in favorites,
            "protected": is_protected_service(name),
            "override": has_local_drop_ins(name),
            "override_help": drop_in_help(name),
            "template_unit": is_template_unit(name),
            "active_help": active_state_help(active),
            "sub_help": sub_state_help(sub),
            "info_title": info["title"],
            "info_summary": info["summary"],
            "info_links": info["links"],
        })
    services.sort(key=lambda item: (not item["favorite"], str(item["name"]).lower()))
    return services


def active_state_help(state: str) -> str:
    if not state:
        return "No ActiveState was reported for this service."
    return f"{state}: {ACTIVE_STATE_HELP.get(state, 'systemd reported this high-level ActiveState.')}"


def sub_state_help(state: str) -> str:
    if not state or state == "-":
        return "No detailed SubState is available for this service."
    return f"{state}: {SUB_STATE_HELP.get(state, 'systemd reported this detailed SubState.')}"


def unit_file_state_help(state: str, name: str = "") -> str:
    state = state or "unknown"
    help_text = UNIT_FILE_STATE_HELP.get(state, "systemd reported this unit-file state.")
    if "@" in name:
        help_text += " The @ means this is a template unit or an instance of a template unit."
    return f"{state}: {help_text}"


def drop_in_help(name: str) -> str:
    return (
        "Override: this service has local drop-in configuration under "
        f"/etc/systemd/system/{name}.d. Drop-ins adjust service settings without editing the original unit file."
    )


def service_info(name: str) -> dict[str, str | bool]:
    if not valid_service_name(name):
        raise ValueError("Only .service units are supported.")
    result = run_systemctl([
        "show",
        name,
        "--property=Id,Description,LoadState,ActiveState,SubState,UnitFileState,FragmentPath,DropInPaths,ExecStart,ExecReload,Restart,ActiveEnterTimestamp,StateChangeTimestamp",
        "--no-pager",
    ])
    values = _parse_properties(result.output if result.ok else "")
    catalog_info = service_catalog_info(name, values.get("Description", ""))
    load_state = values.get("LoadState", "")
    available = result.ok and bool(values.get("Id") or load_state) and load_state != "not-found"
    return {
        "name": name,
        "description": values.get("Description", ""),
        "load": values.get("LoadState", "not-found" if not available else "unknown"),
        "active": values.get("ActiveState", "not-found" if not available else "unknown"),
        "sub": values.get("SubState", "not-found" if not available else "unknown"),
        "enabled": values.get("UnitFileState", "not-found" if not available else "unknown"),
        "enabled_help": unit_file_state_help(values.get("UnitFileState", "not-found" if not available else "unknown"), name),
        "fragment_path": values.get("FragmentPath", ""),
        "drop_in_paths": values.get("DropInPaths", ""),
        "drop_in_path_list": [item for item in values.get("DropInPaths", "").split() if item],
        "local_drop_in_paths": [str(path) for path in local_drop_in_paths(name)],
        "override": bool(values.get("DropInPaths", "")) or has_local_drop_ins(name),
        "override_help": drop_in_help(name),
        "override_path": str(drop_in_override_path(name)),
        "override_exists": drop_in_override_path(name).is_file(),
        "exec_start": values.get("ExecStart", ""),
        "exec_reload": values.get("ExecReload", ""),
        "restart": values.get("Restart", ""),
        "active_enter_timestamp": values.get("ActiveEnterTimestamp", ""),
        "state_change_timestamp": values.get("StateChangeTimestamp", ""),
        "protected": is_protected_service(name),
        "template_unit": is_template_unit(name),
        "available": available,
        "message": result.output,
        "active_help": active_state_help(values.get("ActiveState", "not-found" if not available else "unknown")),
        "sub_help": sub_state_help(values.get("SubState", "not-found" if not available else "unknown")),
        "info_title": catalog_info["title"],
        "info_summary": catalog_info["summary"],
        "info_links": catalog_info["links"],
    }


def service_catalog_info(name: str, description: str = "") -> dict[str, object]:
    catalog = _service_catalog()
    entry = catalog.get(name)
    if not entry and "@" in name:
        template_name = f"{name.split('@', 1)[0]}@.service"
        entry = catalog.get(template_name)
    if not entry:
        readable = description or "systemd did not provide a description for this service."
        return {
            "title": name,
            "summary": f"No curated explanation is available yet. systemd describes this service as: {readable}",
            "links": [],
        }
    return {
        "title": str(entry.get("title") or name),
        "summary": str(entry.get("summary") or description or "No summary is available."),
        "links": [link for link in entry.get("links", []) if isinstance(link, dict)],
    }


@lru_cache(maxsize=1)
def _service_catalog() -> dict[str, dict[str, object]]:
    try:
        data = json.loads(SERVICE_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, dict)}


def unit_content(name: str) -> str:
    result = run_systemctl(["cat", name, "--no-pager"])
    return result.output


def create_unit_backup(name: str, backup_dir: Path) -> Path:
    if not valid_service_name(name):
        raise ValueError("Only .service units are supported.")
    result = run_systemctl(["cat", name, "--no-pager"])
    if not result.ok:
        raise ValueError(result.output or "Unit content could not be read.")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{name}.manual.{stamp}.bak"
    backup_path.write_text(result.output.rstrip() + "\n", encoding="utf-8")
    return backup_path


def editable_unit_path(name: str) -> Path:
    info = service_info(name)
    path = Path(str(info.get("fragment_path") or ""))
    if not path.exists() or not path.is_file():
        raise ValueError("Unit file was not found on disk.")
    etc_root = Path("/etc/systemd/system")
    try:
        path.resolve().relative_to(etc_root)
    except ValueError as exc:
        raise ValueError("Only unit files below /etc/systemd/system are editable. Vendor units should be overridden with drop-ins instead.") from exc
    return path


def read_editable_unit(name: str) -> tuple[Path, str]:
    path = editable_unit_path(name)
    return path, path.read_text(encoding="utf-8")


def write_editable_unit(name: str, content: str, backup_dir: Path) -> Path:
    path = editable_unit_path(name)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{path.name}.{stamp}.bak"
    backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return backup_path


def drop_in_dir(name: str) -> Path:
    if not valid_service_name(name):
        raise ValueError("Only .service units are supported.")
    return SYSTEMD_ETC_ROOT / f"{name}.d"


def drop_in_override_path(name: str) -> Path:
    return drop_in_dir(name) / "override.conf"


def local_drop_in_paths(name: str) -> list[Path]:
    try:
        directory = drop_in_dir(name)
    except ValueError:
        return []
    if not directory.exists() or not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.conf") if path.is_file())


def has_local_drop_ins(name: str) -> bool:
    return bool(local_drop_in_paths(name))


def read_drop_in_override(name: str) -> tuple[Path, str, bool]:
    path = drop_in_override_path(name)
    if not path.exists():
        return path, "", False
    if not path.is_file():
        raise ValueError("Override path exists but is not a file.")
    return path, path.read_text(encoding="utf-8"), True


def write_drop_in_override(name: str, content: str, backup_dir: Path) -> Path | None:
    content = content.rstrip()
    if not content:
        raise ValueError("Override content is empty. Use delete override if you want to remove the file.")
    path = drop_in_override_path(name)
    backup_path = None
    if path.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"{name}.override.{stamp}.bak"
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")
    return backup_path


def delete_drop_in_override(name: str, backup_dir: Path) -> Path:
    path, content, exists = read_drop_in_override(name)
    if not exists:
        raise ValueError("Override file does not exist.")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{name}.override-delete.{stamp}.bak"
    backup_path.write_text(content, encoding="utf-8")
    path.unlink()
    try:
        path.parent.rmdir()
    except OSError:
        pass
    return backup_path


def list_unit_backups(name: str, backup_dir: Path) -> list[dict[str, str | int]]:
    if not valid_service_name(name):
        raise ValueError("Only .service units are supported.")
    if not backup_dir.exists():
        return []

    backups = []
    for path in backup_dir.glob(f"{name}.*.bak"):
        if not path.is_file():
            continue
        stat = path.stat()
        backups.append({
            "name": path.name,
            "size": stat.st_size,
            "created": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    backups.sort(key=lambda item: str(item["name"]), reverse=True)
    return backups


def read_unit_backup(name: str, backup_name: str, backup_dir: Path) -> tuple[Path, str]:
    if not valid_service_name(name):
        raise ValueError("Only .service units are supported.")
    if Path(backup_name).name != backup_name or not backup_name.startswith(f"{name}.") or not backup_name.endswith(".bak"):
        raise ValueError("Backup name is invalid.")

    backup_root = backup_dir.resolve()
    backup_path = (backup_root / backup_name).resolve()
    try:
        backup_path.relative_to(backup_root)
    except ValueError as exc:
        raise ValueError("Backup path is outside the backup directory.") from exc
    if not backup_path.is_file():
        raise ValueError("Backup file was not found.")
    return backup_path, backup_path.read_text(encoding="utf-8")


def delete_unit_backup(name: str, backup_name: str, backup_dir: Path) -> Path:
    backup_path, _content = read_unit_backup(name, backup_name, backup_dir)
    backup_path.unlink()
    return backup_path


def restore_unit_backup(name: str, backup_name: str, backup_dir: Path, backup_current: bool = True) -> Path | None:
    _backup_path, backup_content = read_unit_backup(name, backup_name, backup_dir)
    unit_path = editable_unit_path(name)
    current_backup_path = None
    if backup_current:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        current_backup_path = backup_dir / f"{name}.pre-restore.{stamp}.bak"
        current_backup_path.write_text(unit_path.read_text(encoding="utf-8"), encoding="utf-8")
    unit_path.write_text(backup_content.rstrip() + "\n", encoding="utf-8")
    return current_backup_path


def read_favorites(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {item for item in data if isinstance(item, str) and valid_service_name(item)}


def write_favorites(path: Path, favorites: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(favorites), indent=2) + "\n", encoding="utf-8")


def _active_units() -> dict[str, dict[str, str]]:
    result = run_systemctl(["list-units", "--type=service", "--all", "--no-legend", "--no-pager"])
    units: dict[str, dict[str, str]] = {}
    if not result.ok:
        return units
    for line in result.output.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4 or not parts[0].endswith(".service"):
            continue
        units[parts[0]] = {
            "load": parts[1],
            "active": parts[2],
            "sub": parts[3],
            "description": parts[4] if len(parts) > 4 else "",
        }
    return units


def _unit_files() -> dict[str, dict[str, str]]:
    result = run_systemctl(["list-unit-files", "--type=service", "--no-legend", "--no-pager"])
    files: dict[str, dict[str, str]] = {}
    if not result.ok:
        return files
    for line in result.output.splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[0].endswith(".service"):
            continue
        files[parts[0]] = {"state": parts[1], "preset": parts[2] if len(parts) > 2 else ""}
    return files


def _parse_properties(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key] = value
    return values

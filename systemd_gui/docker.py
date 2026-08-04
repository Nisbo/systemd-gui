from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


VALID_CONTAINER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
DOCKER_ACTIONS = {"start", "stop", "restart"}
COMPOSE_FILE_NAMES = (
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
)


@dataclass
class DockerResult:
    ok: bool
    output: str
    returncode: int


def docker_available() -> bool:
    return bool(shutil.which("docker"))


def docker_status() -> dict[str, object]:
    docker = shutil.which("docker")
    if not docker:
        return {
            "available": False,
            "running": False,
            "client_version": "",
            "server_version": "",
            "message": "Docker is not installed or the docker command is not in PATH.",
        }

    client_version = _run([docker, "--version"], timeout=5)
    info = _run([docker, "info", "--format", "{{json .}}"], timeout=8)
    if not info.ok:
        return {
            "available": True,
            "running": False,
            "client_version": client_version.output.strip(),
            "server_version": "",
            "message": info.output.strip() or "Docker is installed, but the daemon is not reachable.",
        }

    server_version = ""
    try:
        payload = json.loads(info.output)
        server_version = str(payload.get("ServerVersion") or "")
    except json.JSONDecodeError:
        server_version = ""

    return {
        "available": True,
        "running": True,
        "client_version": client_version.output.strip(),
        "server_version": server_version,
        "message": "Docker is installed and the daemon is reachable.",
    }


def list_containers() -> tuple[dict[str, object], list[dict[str, object]]]:
    status = docker_status()
    if not status["available"] or not status["running"]:
        return status, []

    docker = shutil.which("docker")
    result = _run([docker or "docker", "ps", "-a", "--no-trunc", "--format", "{{json .}}"], timeout=12)
    if not result.ok:
        status = {**status, "running": False, "message": result.output}
        return status, []

    containers = []
    for line in result.output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        containers.append(_container_from_ps(item))
    _enrich_containers(containers)
    containers.sort(key=_container_sort_key)
    return status, containers


def container_detail(container_id: str) -> dict[str, object]:
    _validate_container_id(container_id)
    docker = shutil.which("docker")
    if not docker:
        raise DockerError("Docker is not installed or the docker command is not in PATH.")
    result = _run([docker, "inspect", container_id], timeout=12)
    if not result.ok:
        raise DockerError(result.output or f"Docker container {container_id} could not be inspected.")
    try:
        payload = json.loads(result.output)
    except json.JSONDecodeError as exc:
        raise DockerError(f"Docker inspect returned invalid JSON: {exc}") from exc
    if not payload:
        raise DockerError(f"Docker container {container_id} was not found.")
    raw = payload[0]
    container = _container_from_inspect(raw, _docker_mount_path_mappings(docker))
    _enrich_compose_file_contents(container)
    stats = _container_stats([str(container.get("id") or container_id)])
    for stats_id, stat in stats.items():
        if str(container.get("id") or "").startswith(stats_id):
            container["stats"] = stat
            break
    return container


def container_logs(container_id: str, lines: int = 200) -> DockerResult:
    _validate_container_id(container_id)
    docker = shutil.which("docker")
    if not docker:
        return DockerResult(False, "Docker is not installed or the docker command is not in PATH.", 127)
    tail = str(max(1, min(int(lines or 200), 5000)))
    return _run([docker, "logs", "--tail", tail, "--timestamps", container_id], timeout=20)


def run_docker_action(container_id: str, action: str) -> DockerResult:
    _validate_container_id(container_id)
    if action not in DOCKER_ACTIONS:
        return DockerResult(False, f"Unsupported Docker action: {action}", 2)
    docker = shutil.which("docker")
    if not docker:
        return DockerResult(False, "Docker is not installed or the docker command is not in PATH.", 127)
    return _run([docker, action, container_id], timeout=30)


def _enrich_containers(containers: list[dict[str, object]]) -> None:
    if not containers:
        return
    docker = shutil.which("docker")
    if not docker:
        return
    by_id = {str(container.get("id") or ""): container for container in containers}
    ids = [container_id for container_id in by_id if container_id]
    inspect_result = _run([docker, "inspect", *ids], timeout=15)
    if inspect_result.ok:
        try:
            payload = json.loads(inspect_result.output)
        except json.JSONDecodeError:
            payload = []
        mount_path_mappings = _mount_path_mappings(payload if isinstance(payload, list) else [])
        for raw in payload if isinstance(payload, list) else []:
            if not isinstance(raw, dict):
                continue
            container = by_id.get(str(raw.get("Id") or ""))
            if not container:
                continue
            labels = _container_labels(raw)
            state = raw.get("State") if isinstance(raw.get("State"), dict) else {}
            started_at = str(state.get("StartedAt") or "")
            finished_at = str(state.get("FinishedAt") or "")
            container.update({
                "started_at": started_at,
                "started_at_display": _format_docker_time(started_at),
                "finished_at": finished_at,
                "finished_at_display": _format_docker_time(finished_at),
                "running_for": _running_for(started_at) if state.get("Running") else "",
                "compose": _compose_info(labels, mount_path_mappings),
                "image_source": _image_repository_url(str(container.get("image") or ""), labels),
            })

    stats = _container_stats(ids)
    for container_id, stat in stats.items():
        target = by_id.get(container_id)
        if not target:
            for full_id, container in by_id.items():
                if full_id.startswith(container_id):
                    target = container
                    break
        if target:
            target["stats"] = stat


def _run(command: list[str], timeout: int = 12) -> DockerResult:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return DockerResult(False, f"Command timed out: {' '.join(command)}", 124)
    except OSError as exc:
        return DockerResult(False, str(exc), 127)
    output = (completed.stdout or "") + (completed.stderr or "")
    return DockerResult(completed.returncode == 0, output.strip(), completed.returncode)


def _validate_container_id(container_id: str) -> None:
    if not VALID_CONTAINER_RE.match(container_id or ""):
        raise DockerError("Invalid Docker container name or ID.")


def _container_from_ps(item: dict[str, object]) -> dict[str, object]:
    state = str(item.get("State") or "").lower()
    name = str(item.get("Names") or "")
    container_id = str(item.get("ID") or "")
    return {
        "id": container_id,
        "short_id": container_id[:12],
        "name": name,
        "image": str(item.get("Image") or ""),
        "status": str(item.get("Status") or ""),
        "state": state or "unknown",
        "ports": str(item.get("Ports") or ""),
        "created": str(item.get("CreatedAt") or item.get("Created") or ""),
        "running_for": str(item.get("RunningFor") or ""),
        "started_at": "",
        "started_at_display": "",
        "finished_at": "",
        "finished_at_display": "",
        "compose": {},
        "stats": {},
        "image_source": "",
    }


def _container_from_inspect(raw: dict[str, object], path_mappings: list[tuple[str, str]] | None = None) -> dict[str, object]:
    config = raw.get("Config") if isinstance(raw.get("Config"), dict) else {}
    state = raw.get("State") if isinstance(raw.get("State"), dict) else {}
    host_config = raw.get("HostConfig") if isinstance(raw.get("HostConfig"), dict) else {}
    network_settings = raw.get("NetworkSettings") if isinstance(raw.get("NetworkSettings"), dict) else {}
    restart_policy = host_config.get("RestartPolicy") if isinstance(host_config.get("RestartPolicy"), dict) else {}
    labels = dict(config.get("Labels") or {})
    name = str(raw.get("Name") or "").lstrip("/")
    container_id = str(raw.get("Id") or "")
    started_at = str(state.get("StartedAt") or "")
    return {
        "id": container_id,
        "short_id": container_id[:12],
        "name": name,
        "image": str(config.get("Image") or raw.get("Image") or ""),
        "status": str(state.get("Status") or "unknown"),
        "running": bool(state.get("Running")),
        "started_at": started_at,
        "started_at_display": _format_docker_time(started_at),
        "running_for": _running_for(started_at) if state.get("Running") else "",
        "finished_at": str(state.get("FinishedAt") or ""),
        "finished_at_display": _format_docker_time(str(state.get("FinishedAt") or "")),
        "exit_code": state.get("ExitCode"),
        "error": str(state.get("Error") or ""),
        "created": str(raw.get("Created") or ""),
        "hostname": str(config.get("Hostname") or ""),
        "entrypoint": _shell_join(config.get("Entrypoint")),
        "command": _shell_join(config.get("Cmd")),
        "env": list(config.get("Env") or []),
        "labels": labels,
        "compose": _compose_info(labels, path_mappings or []),
        "image_source": _image_repository_url(str(config.get("Image") or raw.get("Image") or ""), labels),
        "stats": {},
        "mounts": list(raw.get("Mounts") or []),
        "ports": network_settings.get("Ports") or {},
        "networks": network_settings.get("Networks") or {},
        "restart_policy": restart_policy.get("Name") or "no",
        "restart_max_retry": restart_policy.get("MaximumRetryCount") or 0,
        "network_mode": host_config.get("NetworkMode") or "",
        "binds": list(host_config.get("Binds") or []),
        "raw_json": json.dumps(raw, indent=2, sort_keys=True),
    }


def _shell_join(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(part) for part in value)
    return str(value)


def _container_labels(raw: dict[str, object]) -> dict[str, str]:
    config = raw.get("Config") if isinstance(raw.get("Config"), dict) else {}
    labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
    return {str(key): str(value) for key, value in labels.items()}


def _compose_info(labels: dict[str, str], path_mappings: list[tuple[str, str]] | None = None) -> dict[str, object]:
    project = labels.get("com.docker.compose.project", "")
    service = labels.get("com.docker.compose.service", "")
    working_dir = labels.get("com.docker.compose.project.working_dir", "")
    config_files = labels.get("com.docker.compose.project.config_files", "")
    if not any([project, service, working_dir, config_files]):
        return {}
    path_mappings = path_mappings or []
    config_file_list = _compose_config_file_paths(working_dir, config_files, path_mappings)
    config_files_inferred = False
    if not config_file_list and working_dir:
        config_file_list = _infer_compose_config_file_paths(working_dir, path_mappings)
        config_files_inferred = bool(config_file_list)
    primary_config_file = config_file_list[0] if config_file_list else ""
    return {
        "project": project,
        "service": service,
        "working_dir": working_dir,
        "config_files": config_files,
        "config_file_list": config_file_list,
        "primary_config_file": primary_config_file,
        "config_files_inferred": config_files_inferred,
        "group_key": f"{project}|{primary_config_file or config_files}",
        "group_label": f"{project} · {primary_config_file or config_files}" if project else primary_config_file or config_files,
    }


def _image_repository_url(image: str, labels: dict[str, str]) -> str:
    for key in ("org.opencontainers.image.source", "org.opencontainers.image.url"):
        source = labels.get(key, "").strip()
        if source.startswith(("https://", "http://")):
            return source.replace("http://", "https://", 1)

    reference = image.split("@", 1)[0].strip("/")
    last_slash = reference.rfind("/")
    last_colon = reference.rfind(":")
    if last_colon > last_slash:
        reference = reference[:last_colon]
    if not reference:
        return ""
    parts = reference.split("/")
    registry = parts[0].lower() if "." in parts[0] or ":" in parts[0] or parts[0] == "localhost" else ""

    if registry in {"", "docker.io", "index.docker.io"}:
        path = parts[1:] if registry else parts
        if len(path) == 1:
            return f"https://hub.docker.com/_/{path[0]}"
        if len(path) >= 2:
            return f"https://hub.docker.com/r/{path[0]}/{path[1]}"

    if registry == "ghcr.io" and len(parts) >= 3:
        owner = parts[1]
        package = "/".join(parts[2:])
        return f"https://github.com/orgs/{owner}/packages/container/package/{package.replace('/', '%2F')}"

    if registry == "quay.io" and len(parts) >= 3:
        return f"https://quay.io/repository/{parts[1]}/{parts[2]}"

    return ""


def _compose_config_file_paths(working_dir: str, config_files: str, path_mappings: list[tuple[str, str]] | None = None) -> list[str]:
    paths = []
    base = Path(working_dir) if working_dir else None
    for raw_path in re.split(r"[,;]", config_files or ""):
        raw_path = raw_path.strip()
        if not raw_path:
            continue
        path = Path(raw_path)
        if not path.is_absolute() and base:
            path = base / path
        paths.append(_host_path_for(str(path), path_mappings or []))
    return paths


def _infer_compose_config_file_paths(working_dir: str, path_mappings: list[tuple[str, str]] | None = None) -> list[str]:
    for base in _candidate_working_dirs(working_dir, path_mappings or []):
        standard_paths = [base / name for name in COMPOSE_FILE_NAMES]
        existing_standard = [str(path) for path in standard_paths if path.is_file()]
        if existing_standard:
            return existing_standard
        discovered = sorted(path for pattern in ("*.yml", "*.yaml") for path in base.glob(pattern) if path.is_file())
        if discovered:
            return [str(path) for path in discovered]
    return []


def _candidate_working_dirs(working_dir: str, path_mappings: list[tuple[str, str]]) -> list[Path]:
    candidates = []
    for value in (working_dir, _host_path_for(working_dir, path_mappings)):
        if not value:
            continue
        path = Path(value)
        if path not in candidates and path.is_dir():
            candidates.append(path)
    return candidates


def _host_path_for(path_value: str, path_mappings: list[tuple[str, str]]) -> str:
    path_value = str(path_value or "")
    if not path_value.startswith("/"):
        return path_value
    normalized = path_value.rstrip("/") or "/"
    if Path(normalized).exists():
        return normalized
    for container_path, host_path in path_mappings:
        if normalized == container_path or normalized.startswith(f"{container_path}/"):
            suffix = normalized[len(container_path):].lstrip("/")
            return str(Path(host_path) / suffix) if suffix else host_path
    return path_value


def _docker_mount_path_mappings(docker: str) -> list[tuple[str, str]]:
    ids_result = _run([docker, "ps", "-aq", "--no-trunc"], timeout=8)
    ids = [line.strip() for line in ids_result.output.splitlines() if line.strip()] if ids_result.ok else []
    if not ids:
        return []
    inspect_result = _run([docker, "inspect", *ids], timeout=15)
    if not inspect_result.ok:
        return []
    try:
        payload = json.loads(inspect_result.output)
    except json.JSONDecodeError:
        return []
    return _mount_path_mappings(payload if isinstance(payload, list) else [])


def _mount_path_mappings(raw_containers: list[object]) -> list[tuple[str, str]]:
    mappings = []
    for raw in raw_containers:
        if not isinstance(raw, dict):
            continue
        mounts = raw.get("Mounts") if isinstance(raw.get("Mounts"), list) else []
        for mount in mounts:
            if not isinstance(mount, dict):
                continue
            source = str(mount.get("Source") or "").rstrip("/")
            destination = str(mount.get("Destination") or "").rstrip("/")
            if source.startswith("/") and destination.startswith("/") and destination != "/":
                mappings.append((destination or "/", source or "/"))
    return sorted(set(mappings), key=lambda item: len(item[0]), reverse=True)


def _enrich_compose_file_contents(container: dict[str, object]) -> None:
    compose = container.get("compose") if isinstance(container.get("compose"), dict) else {}
    if not compose:
        return
    contents = []
    for path_value in compose.get("config_file_list") if isinstance(compose.get("config_file_list"), list) else []:
        path = Path(str(path_value))
        entry = {"path": str(path), "ok": False, "content": "", "message": ""}
        try:
            if not path.is_file():
                entry["message"] = "File was not found on this server."
            elif path.stat().st_size > 512 * 1024:
                entry["message"] = "File is larger than 512 KiB and was not loaded."
            else:
                entry["content"] = path.read_text(encoding="utf-8", errors="replace")
                entry["ok"] = True
        except OSError as exc:
            entry["message"] = str(exc)
        contents.append(entry)
    compose["file_contents"] = contents


def _container_sort_key(container: dict[str, object]) -> tuple[str, str, str, str]:
    compose = container.get("compose") if isinstance(container.get("compose"), dict) else {}
    group = str(compose.get("group_key") or "")
    service = str(compose.get("service") or "")
    name = str(container.get("name") or "").lower()
    return (group.lower() if group else "~", service.lower(), name, str(container.get("id") or ""))


def _container_stats(container_ids: list[str]) -> dict[str, dict[str, str]]:
    docker = shutil.which("docker")
    if not docker or not container_ids:
        return {}
    result = _run([docker, "stats", "--no-stream", "--format", "{{json .}}", *container_ids], timeout=12)
    if not result.ok:
        return {}
    stats: dict[str, dict[str, str]] = {}
    for line in result.output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        container_id = str(item.get("ID") or "")
        if not container_id:
            continue
        stats[container_id] = {
            "cpu": str(item.get("CPUPerc") or ""),
            "memory": str(item.get("MemUsage") or ""),
            "memory_percent": str(item.get("MemPerc") or ""),
            "network": str(item.get("NetIO") or ""),
            "block": str(item.get("BlockIO") or ""),
        }
    return stats


def _running_for(started_at: str) -> str:
    started = _parse_docker_time(started_at)
    if not started:
        return ""
    delta = datetime.now(timezone.utc) - started
    seconds = max(0, int(delta.total_seconds()))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, _seconds = divmod(seconds, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return "less than 1m"


def _format_docker_time(value: str) -> str:
    parsed = _parse_docker_time(value)
    if not parsed:
        return ""
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _parse_docker_time(value: str) -> datetime | None:
    if not value or value.startswith("0001-"):
        return None
    normalized = value
    normalized = re.sub(r"(\.\d{6})\d+", r"\1", normalized)
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class DockerError(ValueError):
    pass

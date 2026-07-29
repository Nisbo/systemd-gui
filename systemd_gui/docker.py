from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone


VALID_CONTAINER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
DOCKER_ACTIONS = {"start", "stop", "restart"}


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
    containers.sort(key=lambda item: (str(item.get("name") or "").lower(), str(item.get("id") or "")))
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
    container = _container_from_inspect(raw)
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
                "compose": _compose_info(labels),
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
    }


def _container_from_inspect(raw: dict[str, object]) -> dict[str, object]:
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
        "compose": _compose_info(labels),
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


def _compose_info(labels: dict[str, str]) -> dict[str, str]:
    project = labels.get("com.docker.compose.project", "")
    service = labels.get("com.docker.compose.service", "")
    working_dir = labels.get("com.docker.compose.project.working_dir", "")
    config_files = labels.get("com.docker.compose.project.config_files", "")
    if not any([project, service, working_dir, config_files]):
        return {}
    return {
        "project": project,
        "service": service,
        "working_dir": working_dir,
        "config_files": config_files,
    }


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

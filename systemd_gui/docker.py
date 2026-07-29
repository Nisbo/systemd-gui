from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass


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
    return _container_from_inspect(raw)


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
    }


def _container_from_inspect(raw: dict[str, object]) -> dict[str, object]:
    config = raw.get("Config") if isinstance(raw.get("Config"), dict) else {}
    state = raw.get("State") if isinstance(raw.get("State"), dict) else {}
    host_config = raw.get("HostConfig") if isinstance(raw.get("HostConfig"), dict) else {}
    network_settings = raw.get("NetworkSettings") if isinstance(raw.get("NetworkSettings"), dict) else {}
    restart_policy = host_config.get("RestartPolicy") if isinstance(host_config.get("RestartPolicy"), dict) else {}
    name = str(raw.get("Name") or "").lstrip("/")
    container_id = str(raw.get("Id") or "")
    return {
        "id": container_id,
        "short_id": container_id[:12],
        "name": name,
        "image": str(config.get("Image") or raw.get("Image") or ""),
        "status": str(state.get("Status") or "unknown"),
        "running": bool(state.get("Running")),
        "started_at": str(state.get("StartedAt") or ""),
        "finished_at": str(state.get("FinishedAt") or ""),
        "exit_code": state.get("ExitCode"),
        "error": str(state.get("Error") or ""),
        "created": str(raw.get("Created") or ""),
        "hostname": str(config.get("Hostname") or ""),
        "entrypoint": _shell_join(config.get("Entrypoint")),
        "command": _shell_join(config.get("Cmd")),
        "env": list(config.get("Env") or []),
        "labels": dict(config.get("Labels") or {}),
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


class DockerError(ValueError):
    pass

from __future__ import annotations

import json
import secrets
import shutil
import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import urlparse

from .version import APP_NAME, APP_VERSION

SERVICE_TYPE = "_systemd-gui._tcp"
AVAHI_SERVICE_FILE = Path("/etc/avahi/services/systemd-gui.service")


@dataclass(frozen=True)
class DiscoveryResult:
    available: bool
    message: str
    nodes: list[dict[str, str]]


@dataclass(frozen=True)
class NodeCommandResult:
    ok: bool
    message: str
    output: str = ""


def default_nodes_data() -> dict[str, object]:
    return {
        "settings": {
            "node_id": secrets.token_hex(12),
            "node_name": socket.gethostname() or "Systemd Gui",
            "announce_enabled": True,
        },
        "nodes": [],
    }


def read_nodes(path: Path) -> dict[str, object]:
    if not path.exists():
        data = default_nodes_data()
        write_nodes(path, data)
        return data
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raw = {}
    return normalize_nodes_data(raw)


def write_nodes(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalize_nodes_data(data), indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def normalize_nodes_data(data: object) -> dict[str, object]:
    defaults = default_nodes_data()
    if not isinstance(data, dict):
        data = {}
    raw_settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    settings = {
        "node_id": _clean_text(raw_settings.get("node_id")) or defaults["settings"]["node_id"],
        "node_name": _clean_text(raw_settings.get("node_name")) or defaults["settings"]["node_name"],
        "announce_enabled": bool(raw_settings.get("announce_enabled", True)),
    }
    nodes = []
    for raw_node in data.get("nodes") if isinstance(data.get("nodes"), list) else []:
        if isinstance(raw_node, dict):
            node = normalize_node(raw_node)
            if node["url"]:
                nodes.append(node)
    return {"settings": settings, "nodes": nodes}


def normalize_node(node: dict[str, object]) -> dict[str, object]:
    name = _clean_text(node.get("name"))
    url = normalize_url(_clean_text(node.get("url")))
    parsed = urlparse(url) if url else None
    host = _clean_text(node.get("host")) or (parsed.hostname if parsed else "")
    port = _clean_text(node.get("port")) or (str(parsed.port) if parsed and parsed.port else "")
    node_id = _clean_text(node.get("node_id")) or secrets.token_hex(8)
    return {
        "id": _clean_text(node.get("id")) or secrets.token_hex(8),
        "node_id": node_id,
        "name": name or host or url or "Systemd Gui node",
        "url": url,
        "host": host,
        "port": port,
        "note": _clean_text(node.get("note")),
        "ssh_user": _clean_text(node.get("ssh_user")),
        "ssh_host": _clean_text(node.get("ssh_host")) or host,
        "ssh_port": _clean_text(node.get("ssh_port")) or "22",
        "ssh_key_path": _clean_text(node.get("ssh_key_path")),
        "ssh_password": str(node.get("ssh_password") or ""),
        "created_at": _clean_text(node.get("created_at")) or _now(),
        "updated_at": _clean_text(node.get("updated_at")) or _now(),
    }


def node_from_form(form, existing: dict[str, object] | None = None) -> dict[str, object]:
    existing = existing or {}
    password = form.get("ssh_password", "")
    if not password and form.get("keep_ssh_password") == "1":
        password = str(existing.get("ssh_password") or "")
    node = {
        **existing,
        "name": form.get("name", ""),
        "url": form.get("url", ""),
        "host": form.get("host", ""),
        "port": form.get("port", ""),
        "note": form.get("note", ""),
        "ssh_user": form.get("ssh_user", ""),
        "ssh_host": form.get("ssh_host", ""),
        "ssh_port": form.get("ssh_port", "22"),
        "ssh_key_path": form.get("ssh_key_path", ""),
        "ssh_password": password,
        "updated_at": _now(),
    }
    normalized = normalize_node(node)
    if existing.get("created_at"):
        normalized["created_at"] = str(existing["created_at"])
    return normalized


def normalize_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if "://" not in value:
        value = f"http://{value}"
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return value.rstrip("/")


def discover_nodes(timeout: int = 4) -> DiscoveryResult:
    avahi_browse = shutil.which("avahi-browse")
    if not avahi_browse:
        return DiscoveryResult(False, "avahi-browse is not installed. Install avahi-utils to discover LAN nodes.", [])
    try:
        result = subprocess.run(
            [avahi_browse, "-rtp", SERVICE_TYPE],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DiscoveryResult(False, f"LAN discovery failed: {exc}", [])
    if result.returncode not in {0, 1}:
        message = (result.stderr or result.stdout or "LAN discovery did not return usable data.").strip()
        return DiscoveryResult(False, message, [])
    nodes = _parse_avahi_output(result.stdout)
    return DiscoveryResult(True, "LAN discovery uses Avahi/mDNS service type _systemd-gui._tcp.local.", nodes)


def merge_discovered_with_saved(saved_nodes: list[dict[str, object]], discovered_nodes: list[dict[str, str]]) -> list[dict[str, object]]:
    saved_keys = set()
    for node in saved_nodes:
        saved_keys.add(str(node.get("node_id") or ""))
        saved_keys.add(_node_url_key(str(node.get("url") or "")))
    merged = []
    for node in discovered_nodes:
        node = {**node, "saved": str(node.get("node_id") or "") in saved_keys or _node_url_key(node.get("url", "")) in saved_keys}
        merged.append(node)
    return merged


def announcement_status(settings: dict[str, object], public_port: int) -> dict[str, object]:
    avahi_available = bool(shutil.which("avahi-daemon") or shutil.which("avahi-browse"))
    expected = avahi_service_content(settings, public_port)
    installed = AVAHI_SERVICE_FILE.exists()
    current = ""
    if installed:
        try:
            current = AVAHI_SERVICE_FILE.read_text(encoding="utf-8")
        except OSError:
            current = ""
    return {
        "available": avahi_available,
        "service_file": str(AVAHI_SERVICE_FILE),
        "installed": installed,
        "current": installed and current == expected,
        "wanted": bool(settings.get("announce_enabled", True)),
        "message": "Avahi is available." if avahi_available else "Avahi is not installed. Install avahi-daemon and avahi-utils for LAN discovery.",
    }


def install_announcement(settings: dict[str, object], public_port: int) -> None:
    if not shutil.which("avahi-daemon") and not shutil.which("avahi-browse"):
        raise OSError("Avahi is not installed. Install avahi-daemon and avahi-utils first.")
    AVAHI_SERVICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    AVAHI_SERVICE_FILE.write_text(avahi_service_content(settings, public_port), encoding="utf-8")
    _reload_avahi()


def install_discovery_support(settings: dict[str, object], public_port: int) -> NodeCommandResult:
    apt_get = shutil.which("apt-get")
    if not apt_get:
        return NodeCommandResult(False, "Automatic package installation is only available on Debian-style systems with apt-get.")
    commands = [
        [apt_get, "update"],
        [apt_get, "install", "-y", "avahi-daemon", "avahi-utils"],
    ]
    output_parts: list[str] = []
    for command in commands:
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return NodeCommandResult(False, f"LAN discovery setup failed: {exc}", "\n".join(output_parts))
        output_parts.append(f"$ {' '.join(command)}\n{result.stdout}{result.stderr}")
        if result.returncode != 0:
            return NodeCommandResult(False, f"Command failed with exit code {result.returncode}: {' '.join(command)}", "\n".join(output_parts))
    _enable_avahi()
    try:
        install_announcement(settings, public_port)
    except OSError as exc:
        return NodeCommandResult(False, f"Avahi was installed, but announcement could not be created: {exc}", "\n".join(output_parts))
    return NodeCommandResult(True, "LAN discovery support installed and announcement enabled.", "\n".join(output_parts))


def remove_announcement() -> None:
    if AVAHI_SERVICE_FILE.exists():
        AVAHI_SERVICE_FILE.unlink()
    _reload_avahi()


def avahi_service_content(settings: dict[str, object], public_port: int) -> str:
    name = _clean_text(settings.get("node_name")) or APP_NAME
    node_id = _clean_text(settings.get("node_id")) or "unknown"
    records = {
        "node_id": node_id,
        "app": "systemd-gui",
        "version": APP_VERSION,
        "path": "/",
        "https": "0",
    }
    txt_records = "\n".join(f"    <txt-record>{escape(key)}={escape(value)}</txt-record>" for key, value in records.items())
    return "\n".join(
        [
            '<?xml version="1.0" standalone="no"?>',
            '<!DOCTYPE service-group SYSTEM "avahi-service.dtd">',
            "<service-group>",
            f'  <name replace-wildcards="yes">{escape(name)} on %h</name>',
            "  <service>",
            f"    <type>{SERVICE_TYPE}</type>",
            f"    <port>{int(public_port)}</port>",
            txt_records,
            "  </service>",
            "</service-group>",
            "",
        ]
    )


def _parse_avahi_output(output: str) -> list[dict[str, str]]:
    nodes: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in output.splitlines():
        if not line.startswith("="):
            continue
        parts = line.split(";")
        if len(parts) < 9:
            continue
        name = parts[3].replace("\\032", " ").strip()
        host = parts[6].strip()
        address = parts[7].strip()
        port = parts[8].strip()
        txt = _parse_txt_records(parts[9:])
        display_host = address or host
        scheme = "https" if txt.get("https") == "1" else "http"
        url = f"{scheme}://{display_host}:{port}" if display_host and port else ""
        key = txt.get("node_id") or url or name
        if key in seen:
            continue
        seen.add(key)
        nodes.append(
            {
                "node_id": txt.get("node_id", ""),
                "name": name or display_host or "Systemd Gui node",
                "url": url,
                "host": display_host,
                "port": port,
                "version": txt.get("version", ""),
                "source": "mDNS",
            }
        )
    return nodes


def _parse_txt_records(parts: list[str]) -> dict[str, str]:
    records: dict[str, str] = {}
    raw = ";".join(parts)
    for chunk in raw.split('" "'):
        value = chunk.strip().strip('"')
        if "=" in value:
            key, record_value = value.split("=", 1)
            records[key.strip()] = record_value.strip()
    return records


def _reload_avahi() -> None:
    systemctl = shutil.which("systemctl")
    if systemctl:
        subprocess.run([systemctl, "reload", "avahi-daemon"], check=False, capture_output=True, text=True, timeout=8)


def _enable_avahi() -> None:
    systemctl = shutil.which("systemctl")
    if systemctl:
        subprocess.run([systemctl, "enable", "--now", "avahi-daemon"], check=False, capture_output=True, text=True, timeout=20)


def _node_url_key(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.netloc:
        return url.strip().lower()
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")

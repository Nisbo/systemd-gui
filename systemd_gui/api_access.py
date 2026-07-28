from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

API_SCOPES = {
    "node:read": "Node info",
    "services:read": "Service list and details",
    "logs:read": "Journal logs",
    "quick-shell:read": "Quick Shell exports",
    "updates:write": "Remote updates",
}


@dataclass(frozen=True)
class ApiTokenCheckResult:
    ok: bool
    message: str
    status: str = "unknown"
    details: dict[str, object] | None = None


@dataclass(frozen=True)
class RemoteLogsResult:
    ok: bool
    message: str
    node: dict[str, object]
    entries: list[dict[str, object]]
    status: str = "unknown"


@dataclass(frozen=True)
class RemoteUpdateResult:
    ok: bool
    message: str
    node: dict[str, object]
    status: str = "unknown"
    details: dict[str, object] | None = None


def default_api_access_data() -> dict[str, object]:
    return {
        "settings": {
            "enabled": False,
            "allowed_ips": "",
            "allow_saved_nodes": False,
        },
        "tokens": [],
    }


def read_api_access(path: Path) -> dict[str, object]:
    if not path.exists():
        data = default_api_access_data()
        write_api_access(path, data)
        return data
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raw = {}
    return normalize_api_access_data(raw)


def write_api_access(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalize_api_access_data(data), indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def normalize_api_access_data(data: object) -> dict[str, object]:
    defaults = default_api_access_data()
    if not isinstance(data, dict):
        data = {}
    raw_settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    settings = {
        "enabled": bool(raw_settings.get("enabled", defaults["settings"]["enabled"])),
        "allowed_ips": str(raw_settings.get("allowed_ips") or ""),
        "allow_saved_nodes": bool(raw_settings.get("allow_saved_nodes", defaults["settings"]["allow_saved_nodes"])),
    }
    tokens = []
    for raw_token in data.get("tokens") if isinstance(data.get("tokens"), list) else []:
        if isinstance(raw_token, dict):
            token = normalize_api_token(raw_token)
            if token["token_hash"]:
                tokens.append(token)
    return {"settings": settings, "tokens": tokens}


def normalize_api_token(token: dict[str, object]) -> dict[str, object]:
    scopes = token.get("scopes") if isinstance(token.get("scopes"), list) else []
    clean_scopes = [scope for scope in scopes if isinstance(scope, str) and scope in API_SCOPES]
    return {
        "id": str(token.get("id") or secrets.token_hex(8)),
        "name": str(token.get("name") or "Remote API token").strip() or "Remote API token",
        "prefix": str(token.get("prefix") or ""),
        "token_hash": str(token.get("token_hash") or ""),
        "scopes": clean_scopes or ["node:read"],
        "enabled": bool(token.get("enabled", True)),
        "created_at": str(token.get("created_at") or _now()),
        "last_used_at": str(token.get("last_used_at") or ""),
    }


def create_api_token(data: dict[str, object], name: str, scopes: list[str]) -> tuple[dict[str, object], str]:
    token_value = f"sdg_{secrets.token_urlsafe(32)}"
    clean_scopes = sorted({"node:read", *[scope for scope in scopes if scope in API_SCOPES]}, key=list(API_SCOPES).index)
    token = normalize_api_token(
        {
            "name": name.strip() or "Remote API token",
            "prefix": token_value[:12],
            "token_hash": hash_token(token_value),
            "scopes": clean_scopes,
            "enabled": True,
        }
    )
    data["tokens"] = [*list(data.get("tokens") or []), token]
    return token, token_value


def update_api_settings(data: dict[str, object], form) -> dict[str, object]:
    data["settings"] = {
        "enabled": form.get("enabled") == "1",
        "allowed_ips": form.get("allowed_ips", ""),
        "allow_saved_nodes": form.get("allow_saved_nodes") == "1",
    }
    return normalize_api_access_data(data)


def update_api_token(data: dict[str, object], token_id: str, name: str, scopes: list[str], enabled: bool) -> bool:
    changed = False
    clean_scopes = sorted({"node:read", *[scope for scope in scopes if scope in API_SCOPES]}, key=list(API_SCOPES).index)
    tokens = []
    for token in data.get("tokens") if isinstance(data.get("tokens"), list) else []:
        if str(token.get("id")) == token_id:
            token = {
                **token,
                "name": name.strip() or str(token.get("name") or "Remote API token"),
                "scopes": clean_scopes,
                "enabled": enabled,
            }
            changed = True
        tokens.append(token)
    data["tokens"] = tokens
    return changed


def delete_token(data: dict[str, object], token_id: str) -> bool:
    tokens = list(data.get("tokens") or [])
    kept = [token for token in tokens if str(token.get("id")) != token_id]
    data["tokens"] = kept
    return len(kept) != len(tokens)


def hash_token(token_value: str) -> str:
    return hashlib.sha256(token_value.encode("utf-8")).hexdigest()


def verify_bearer_token(data: dict[str, object], token_value: str, required_scope: str) -> tuple[bool, str, dict[str, object] | None]:
    if required_scope not in API_SCOPES:
        return False, "Unknown API scope.", None
    digest = hash_token(token_value)
    for token in data.get("tokens") if isinstance(data.get("tokens"), list) else []:
        normalized = normalize_api_token(token)
        if not normalized["enabled"]:
            continue
        if not hmac.compare_digest(normalized["token_hash"], digest):
            continue
        if required_scope not in normalized["scopes"]:
            return False, "Token is valid, but this access category is not allowed.", normalized
        token["last_used_at"] = _now()
        return True, "Token accepted.", normalized
    return False, "Token is missing or invalid.", None


def bearer_token_from_header(header: str) -> str:
    value = str(header or "").strip()
    if not value.lower().startswith("bearer "):
        return ""
    return value.split(" ", 1)[1].strip()


def client_ip_allowed(settings: dict[str, object], client_ip: str, saved_nodes: list[dict[str, object]]) -> tuple[bool, str]:
    client_ip = str(client_ip or "").strip()
    allowed_ranges = _parse_ip_rules(str(settings.get("allowed_ips") or ""))
    saved_ips = _saved_node_ips(saved_nodes) if settings.get("allow_saved_nodes") else set()
    if not allowed_ranges and not saved_ips:
        return True, "No IP filter is configured."
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return False, "Client IP could not be read."
    if any(address in network for network in allowed_ranges):
        return True, "Client IP matches the allowlist."
    if str(address) in saved_ips:
        return True, "Client IP matches a saved node."
    return False, "Client IP is not allowed for Remote API access."


def check_remote_api_access(node: dict[str, object], timeout: float = 4.0) -> ApiTokenCheckResult:
    url = str(node.get("url") or "").strip()
    token = str(node.get("api_token") or "").strip()
    if not url:
        return ApiTokenCheckResult(False, "No GUI URL is configured for this node.", "missing")
    if not token:
        return ApiTokenCheckResult(False, "No Remote API token is saved for this node.", "missing")
    try:
        request = Request(urljoin(f"{url.rstrip('/')}/", "api/v1/ping"), headers={"Authorization": f"Bearer {token}"})
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 401:
            return ApiTokenCheckResult(False, "Token was rejected by the remote node.", "denied")
        if exc.code == 403:
            return ApiTokenCheckResult(False, "Remote node denied this IP address or access category.", "denied")
        return ApiTokenCheckResult(False, f"Remote node returned HTTP {exc.code}.", "error")
    except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
        return ApiTokenCheckResult(False, f"Remote API check failed: {exc}", "error")
    if not isinstance(payload, dict) or payload.get("app") != "systemd-gui":
        return ApiTokenCheckResult(False, "Remote answer was not a Systemd Gui API response.", "error")
    return ApiTokenCheckResult(True, "Remote API token works.", "ok", payload)


def fetch_remote_logs(
    node: dict[str, object],
    service: str = "",
    lines: int = 200,
    priority: str = "all",
    timeout: float = 5.0,
) -> RemoteLogsResult:
    url = str(node.get("url") or "").strip()
    token = str(node.get("api_token") or "").strip()
    node_info = {
        "id": str(node.get("node_id") or node.get("id") or ""),
        "name": str(node.get("name") or "Remote node"),
        "url": url,
        "version": str(node.get("version") or ""),
        "remote": True,
    }
    if not url:
        return RemoteLogsResult(False, "No GUI URL is configured for this node.", node_info, [], "missing")
    if not token:
        return RemoteLogsResult(False, "No Remote API token is saved for this node.", node_info, [], "missing")
    query = urlencode({"lines": int(lines), "priority": priority or "all", "unit": service})
    try:
        request = Request(
            urljoin(f"{url.rstrip('/')}/", f"api/v1/logs?{query}"),
            headers={"Authorization": f"Bearer {token}"},
        )
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 401:
            return RemoteLogsResult(False, "Token was rejected by the remote node.", node_info, [], "denied")
        if exc.code == 403:
            return RemoteLogsResult(False, "Remote node denied this IP address or log access.", node_info, [], "denied")
        return RemoteLogsResult(False, f"Remote node returned HTTP {exc.code}.", node_info, [], "error")
    except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
        return RemoteLogsResult(False, f"Remote log fetch failed: {exc}", node_info, [], "error")
    if not isinstance(payload, dict) or payload.get("app") != "systemd-gui":
        return RemoteLogsResult(False, "Remote answer was not a Systemd Gui API response.", node_info, [], "error")
    remote_node = payload.get("node") if isinstance(payload.get("node"), dict) else {}
    entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
    return RemoteLogsResult(
        True,
        "Remote logs loaded.",
        {**node_info, **remote_node, "remote": True, "url": url},
        [entry for entry in entries if isinstance(entry, dict)],
        "ok",
    )


def trigger_remote_git_update(node: dict[str, object], timeout: float = 90.0) -> RemoteUpdateResult:
    url = str(node.get("url") or "").strip()
    token = str(node.get("api_token") or "").strip()
    node_info = {
        "id": str(node.get("node_id") or node.get("id") or ""),
        "name": str(node.get("name") or "Remote node"),
        "url": url,
        "version": str(node.get("version") or ""),
        "remote": True,
    }
    if not url:
        return RemoteUpdateResult(False, "No GUI URL is configured for this node.", node_info, "missing")
    if not token:
        return RemoteUpdateResult(False, "No Remote API token is saved for this node.", node_info, "missing")
    try:
        request = Request(
            urljoin(f"{url.rstrip('/')}/", "api/v1/update/git"),
            data=b"",
            headers={"Authorization": f"Bearer {token}"},
            method="POST",
        )
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            error_message = str(payload.get("error") or payload.get("message") or "")
        except (ValueError, json.JSONDecodeError, OSError):
            error_message = ""
        if exc.code == 401:
            return RemoteUpdateResult(False, error_message or "Token was rejected by the remote node.", node_info, "denied")
        if exc.code == 403:
            return RemoteUpdateResult(False, error_message or "Remote node denied this IP address or update access.", node_info, "denied")
        return RemoteUpdateResult(False, error_message or f"Remote node returned HTTP {exc.code}.", node_info, "error")
    except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
        return RemoteUpdateResult(False, f"Remote update request failed: {exc}", node_info, "error")
    if not isinstance(payload, dict) or payload.get("app") != "systemd-gui":
        return RemoteUpdateResult(False, "Remote answer was not a Systemd Gui API response.", node_info, "error")
    remote_node = payload.get("node") if isinstance(payload.get("node"), dict) else {}
    ok = bool(payload.get("ok"))
    return RemoteUpdateResult(
        ok,
        str(payload.get("message") or ("Remote update accepted." if ok else "Remote update failed.")),
        {**node_info, **remote_node, "remote": True, "url": url},
        "ok" if ok else "error",
        payload,
    )


def api_scopes_from_form(form) -> list[str]:
    return [scope for scope in form.getlist("scopes") if scope in API_SCOPES] or ["node:read"]


def api_scope_options(selected: list[str] | None = None) -> list[dict[str, object]]:
    selected_set = set(selected or [])
    return [{"id": scope, "label": label, "selected": scope in selected_set} for scope, label in API_SCOPES.items()]


def _parse_ip_rules(value: str) -> list[ipaddress._BaseNetwork]:
    rules = []
    for chunk in value.replace(",", "\n").splitlines():
        text = chunk.strip()
        if not text:
            continue
        try:
            rules.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            continue
    return rules


def _saved_node_ips(nodes: list[dict[str, object]]) -> set[str]:
    addresses: set[str] = set()
    for node in nodes:
        for value in (node.get("host"), node.get("ssh_host"), urlparse(str(node.get("url") or "")).hostname):
            host = str(value or "").strip()
            if not host:
                continue
            try:
                address = ipaddress.ip_address(host)
                addresses.add(str(address))
                continue
            except ValueError:
                pass
            try:
                for info in socket.getaddrinfo(host, None):
                    addresses.add(str(ipaddress.ip_address(info[4][0])))
            except (OSError, ValueError):
                continue
    return addresses


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")

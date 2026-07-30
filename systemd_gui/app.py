from __future__ import annotations

import json
import os
import secrets
import shlex
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, flash, jsonify, redirect, render_template, request, session, url_for

from .api_access import (
    API_SCOPES,
    api_scope_options,
    api_scopes_from_form,
    bearer_token_from_header,
    check_remote_api_access,
    client_ip_allowed,
    create_api_token,
    delete_token,
    fetch_remote_logs,
    fetch_remote_docker_containers,
    fetch_remote_quick_shell_export,
    regenerate_api_token,
    read_api_access,
    trigger_remote_git_update,
    update_api_settings,
    update_api_token,
    verify_bearer_token,
    write_api_access,
)
from .docker import DockerError, container_detail, container_logs, list_containers, run_docker_action
from .nodes import (
    announcement_status,
    discover_nodes,
    install_announcement,
    install_discovery_support,
    merge_discovered_with_saved,
    node_from_form,
    node_runtime_metadata,
    normalize_node,
    read_nodes,
    remove_announcement,
    saved_nodes_with_status,
    write_nodes,
)
from .quick_shell import (
    add_item,
    bash_history_timestamp_status,
    children_for_path,
    create_quick_shell_backup,
    delete_quick_shell_backup,
    delete_item,
    entry_label,
    flatten_entries,
    import_quick_shell_items,
    install_bash_history_timestamps,
    install_quick_shell_helper,
    install_shell_integration,
    install_sshpass_package,
    item_for_path,
    list_quick_shell_backups,
    move_item,
    move_item_to_category,
    move_item_to_position,
    quick_shell_export_payload,
    quick_shell_payload_items,
    quick_shell_payload_settings,
    quick_shell_helper_status,
    read_quick_shell_backup,
    read_quick_shell,
    remote_ssh_support_status,
    remove_bash_history_timestamps,
    remove_shell_integration,
    restore_quick_shell_backup,
    shell_integration_statuses,
    update_item,
    write_quick_shell,
)
from .systemd import (
    CommandResult,
    analyze_drop_in_content,
    create_unit_backup,
    delete_drop_in_override,
    delete_unit_backup,
    flattened_unit_preview,
    is_protected_service,
    is_template_unit,
    journalctl_available,
    list_drop_in_backups,
    list_unit_backups,
    list_services,
    read_drop_in_override,
    read_editable_unit,
    read_favorites,
    read_unit_backup,
    restore_unit_backup,
    run_journalctl_entries,
    run_systemctl,
    service_info,
    systemctl_available,
    unit_content,
    unit_fragment_content,
    valid_service_name,
    write_drop_in_override,
    write_editable_unit,
    write_favorites,
)
from .updater import (
    check_for_update,
    create_app_backup,
    delete_app_backup,
    git_update_state,
    list_app_backups,
    restore_app_backup,
    update_from_git,
    update_from_release,
    update_from_zip,
    update_status_to_dict,
)
from .version import APP_NAME, APP_VERSION, REPO_URL

SERVICE_ACTIONS = {"start", "stop", "restart", "reload", "enable", "disable"}
DOCKER_ACTIONS = {"start", "stop", "restart"}
RUNTIME_ACTIONS = {"start", "stop", "restart", "reload"}
AUTOSTART_ACTIONS = {"enable", "disable"}
ACTION_HELP = {
    "start": "Start this service now. This runs systemctl start and does not enable autostart.",
    "stop": "Stop this service now. It can be started again manually or by another dependency.",
    "restart": "Stop and start this service again. Useful after configuration changes.",
    "reload": "Ask the service to reload its configuration without a full restart, if the service supports it.",
    "enable": "Enable autostart so systemd starts this service automatically during boot.",
    "disable": "Disable autostart. This does not stop the currently running service.",
}
LOG_PRIORITY_OPTIONS = [
    ("all", "All (unfiltered)"),
    ("debug", "Debug and above"),
    ("info", "Info and above"),
    ("warning", "Warning and above"),
    ("err", "Error and above"),
]
NO_AUTOSTART_STATES = {"static", "alias", "unknown", "generated", "transient"}
BLOCKED_UNIT_FILE_STATES = {"bad", "masked"}


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SYSTEMD_GUI_SECRET", "dev-change-me"),
        SESSION_COOKIE_NAME=os.environ.get("SYSTEMD_GUI_SESSION_COOKIE", "systemd_gui_session"),
        ADMIN_PASSWORD=os.environ.get("SYSTEMD_GUI_PASSWORD", ""),
        ALLOW_PROTECTED=os.environ.get("SYSTEMD_GUI_ALLOW_PROTECTED", "0") == "1",
        DATA_DIR=Path(os.environ.get("SYSTEMD_GUI_DATA_DIR", "data")),
        ENV_FILE=Path(os.environ.get("SYSTEMD_GUI_ENV_FILE", "/etc/systemd-gui.env")),
        SYSTEMD_GUI_SERVICE=os.environ.get("SYSTEMD_GUI_SERVICE", "systemd-gui"),
        SYSTEMD_GUI_PUBLIC_PORT=int(os.environ.get("SYSTEMD_GUI_PUBLIC_PORT", "8850")),
        QUICK_SHELL_BIN=Path(os.environ.get("SYSTEMD_GUI_QS_BIN", "/usr/local/bin/qs")),
    )
    _sync_settings_from_env(app)

    @app.before_request
    def require_login_and_csrf():
        if request.endpoint in {"login", "login_post", "node_info", "static"} or str(request.endpoint or "").startswith("api_"):
            return None
        if app.config["ADMIN_PASSWORD"] and not session.get("logged_in"):
            return redirect(url_for("login"))
        if request.method == "POST":
            token = session.get("csrf_token")
            submitted = request.form.get("csrf_token")
            if not token or not submitted or not secrets.compare_digest(token, submitted):
                flash("Security token is invalid. Please try again.", "error")
                return redirect(url_for("index"))
        return None

    @app.context_processor
    def inject_globals():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        node_navigation = _node_navigation(app)
        return {
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "asset_version": _asset_version(app),
            "repo_url": REPO_URL,
            "csrf_token": session["csrf_token"],
            "current_node": node_navigation["current"],
            "node_navigation": node_navigation["nodes"],
            "systemctl_available": systemctl_available(),
            "journalctl_available": journalctl_available(),
            "app_update_pending_restart": session.get("app_update_pending_restart", False),
            "pending_override_reloads": sorted(_pending_override_reloads()),
            "pending_override_restarts": sorted(_pending_override_restarts()),
        }

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/login")
    def login():
        if not app.config["ADMIN_PASSWORD"]:
            flash("Login is disabled because SYSTEMD_GUI_PASSWORD is not set.", "warning")
            return redirect(url_for("index"))
        return render_template("login.html")

    @app.post("/login")
    def login_post():
        expected = app.config["ADMIN_PASSWORD"]
        password = request.form.get("password", "")
        if expected and secrets.compare_digest(password, expected):
            session.clear()
            session["logged_in"] = True
            session["csrf_token"] = secrets.token_urlsafe(32)
            flash("Signed in.", "success")
            return redirect(url_for("index"))
        flash("Password is incorrect.", "error")
        return render_template("login.html"), 401

    @app.get("/node-info.json")
    def node_info():
        data = read_nodes(_nodes_path(app))
        settings = data.get("settings") or {}
        return jsonify({
            "app": "systemd-gui",
            "version": APP_VERSION,
            "node_id": settings.get("node_id", ""),
            "node_name": settings.get("node_name", APP_NAME),
        })

    @app.get("/api/v1/ping")
    def api_ping():
        access = _require_remote_api_access(app, "node:read")
        if access:
            return access
        data = read_nodes(_nodes_path(app))
        settings = data.get("settings") or {}
        return jsonify({
            "app": "systemd-gui",
            "version": APP_VERSION,
            "node_id": settings.get("node_id", ""),
            "node_name": settings.get("node_name", APP_NAME),
            "scopes": list(API_SCOPES),
        })

    @app.get("/api/v1/logs")
    def api_logs():
        access = _require_remote_api_access(app, "logs:read")
        if access:
            return access
        unit = request.args.get("unit", "").strip()
        if unit and not valid_service_name(unit):
            return jsonify({"error": "Only .service units are supported."}), 400
        lines = _log_line_count(request.args.get("lines", "200"))
        priority = _log_priority(request.args.get("priority", "all"))
        logs = run_journalctl_entries(unit, lines, priority)
        data = read_nodes(_nodes_path(app))
        settings = data.get("settings") or {}
        node = {
            "id": settings.get("node_id", ""),
            "name": settings.get("node_name", APP_NAME),
            "version": APP_VERSION,
            "remote": False,
        }
        return jsonify({
            "app": "systemd-gui",
            "node": node,
            "ok": logs.ok,
            "entries": [_decorate_log_entry(entry, node) for entry in logs.entries],
            "output": logs.output,
        })

    @app.get("/api/v1/docker/containers")
    def api_docker_containers():
        access = _require_remote_api_access(app, "docker:read")
        if access:
            return access
        status, containers = list_containers()
        data = read_nodes(_nodes_path(app))
        settings = data.get("settings") or {}
        node = {
            "id": settings.get("node_id", ""),
            "name": settings.get("node_name", APP_NAME),
            "version": APP_VERSION,
            "remote": False,
        }
        return jsonify({
            "app": "systemd-gui",
            "node": node,
            "ok": bool(status.get("running")),
            "status": status,
            "containers": containers,
        })

    @app.get("/api/v1/quick-shell/export")
    def api_quick_shell_export():
        access = _require_remote_api_access(app, "quick-shell:read")
        if access:
            return access
        data = read_quick_shell(_quick_shell_path(app))
        nodes_data = read_nodes(_nodes_path(app))
        settings = nodes_data.get("settings") or {}
        node = {
            "id": settings.get("node_id", ""),
            "name": settings.get("node_name", APP_NAME),
            "version": APP_VERSION,
            "remote": False,
        }
        payload = quick_shell_export_payload(data, source=f"Remote API: {node['name']}")
        payload["app"] = "systemd-gui"
        payload["node"] = node
        return jsonify(payload)

    @app.post("/api/v1/update/git")
    def api_update_git():
        access = _require_remote_api_access(app, "updates:write")
        if access:
            return access
        result = update_from_git(_app_root(app))
        data = read_nodes(_nodes_path(app))
        settings = data.get("settings") or {}
        node = {
            "id": settings.get("node_id", ""),
            "name": settings.get("node_name", APP_NAME),
            "version": APP_VERSION,
            "remote": False,
        }
        restart_message = ""
        if result.ok:
            restart_ok, restart_message = _request_systemd_gui_restart(app, delay_seconds=4)
            if not restart_ok:
                result.ok = False
                result.message = f"{result.message} Restart failed: {restart_message}"
        return jsonify({
            "app": "systemd-gui",
            "node": node,
            "ok": result.ok,
            "message": result.message,
            "details": result.details,
            "backup_path": str(result.backup_path or ""),
            "restart": restart_message,
        }), 200 if result.ok else 500

    @app.post("/logout")
    def logout():
        session.clear()
        flash("Signed out.", "success")
        return redirect(url_for("login"))

    @app.get("/")
    def index():
        query = request.args.get("q", "").strip()
        state_filter = request.args.get("state", "").strip()
        sub_filter = request.args.get("sub", "").strip()
        autostart_filter = request.args.get("autostart", "").strip()
        favorites = read_favorites(_favorites_path(app))
        all_services = list_services(query, favorites)
        filter_options = _service_filter_options(all_services)
        services = list_services(query, favorites, state_filter, sub_filter, autostart_filter)
        stats = _service_stats(services)
        return render_template(
            "index.html",
            services=services,
            query=query,
            state_filter=state_filter,
            sub_filter=sub_filter,
            autostart_filter=autostart_filter,
            filter_options=filter_options,
            **stats,
        )

    @app.get("/services/fragment")
    def services_fragment():
        query = request.args.get("q", "").strip()
        state_filter = request.args.get("state", "").strip()
        sub_filter = request.args.get("sub", "").strip()
        autostart_filter = request.args.get("autostart", "").strip()
        favorites = read_favorites(_favorites_path(app))
        all_services = list_services(query, favorites)
        filter_options = _service_filter_options(all_services)
        services = list_services(query, favorites, state_filter, sub_filter, autostart_filter)
        return render_template(
            "_services_fragment.html",
            services=services,
            state_filter=state_filter,
            sub_filter=sub_filter,
            autostart_filter=autostart_filter,
            filter_options=filter_options,
            **_service_stats(services),
        )

    @app.get("/settings")
    def settings():
        active_tab = request.args.get("tab", "general")
        if active_tab not in {"general", "security", "updates", "backups"}:
            active_tab = "general"
        return render_template(
            "settings.html",
            active_tab=active_tab,
            env_file=app.config["ENV_FILE"],
            password_enabled=bool(app.config["ADMIN_PASSWORD"]),
            systemd_gui_service=app.config["SYSTEMD_GUI_SERVICE"],
            git_state=git_update_state(_app_root(app)),
            app_update_backups=list_app_backups(_app_root(app)),
            update_status=session.pop("update_status", None),
            update_result=session.pop("update_result", None),
            app_update_pending_restart=session.get("app_update_pending_restart", False),
        )

    @app.get("/nodes")
    def nodes():
        active_tab = request.args.get("tab", "nodes")
        if active_tab not in {"nodes", "local", "api"}:
            active_tab = "nodes"
        data = read_nodes(_nodes_path(app))
        api_access = read_api_access(_api_access_path(app))
        for token in api_access.get("tokens") if isinstance(api_access.get("tokens"), list) else []:
            token["scope_labels"] = [API_SCOPES.get(scope, scope) for scope in token.get("scopes", [])]
        settings = data.get("settings") or {}
        node_check_results = session.pop("node_api_check_results", {})
        saved_nodes = [
            {
                **node,
                "ssh_indicators": _node_ssh_indicators(node),
                "api_indicators": _node_api_indicators(node),
                "api_check": node_check_results.get(str(node.get("id") or "")),
            }
            for node in saved_nodes_with_status(list(data.get("nodes") or []))
        ]
        discovery = discover_nodes()
        discovered_raw = [node for node in discovery.nodes if node.get("node_id") != settings.get("node_id")]
        discovered_nodes = merge_discovered_with_saved(saved_nodes, discovered_raw)
        return render_template(
            "nodes.html",
            active_tab=active_tab,
            nodes_data=data,
            node_settings=settings,
            saved_nodes=saved_nodes,
            discovery=discovery,
            discovered_nodes=discovered_nodes,
            announcement=announcement_status(settings, app.config["SYSTEMD_GUI_PUBLIC_PORT"]),
            api_access=api_access,
            api_scope_options=api_scope_options(),
            new_api_token=session.pop("new_api_token", None),
            new_api_token_name=session.pop("new_api_token_name", None),
            nodes_install_result=session.pop("nodes_install_result", None),
            nodes_path=_nodes_path(app),
            api_access_path=_api_access_path(app),
            public_port=app.config["SYSTEMD_GUI_PUBLIC_PORT"],
        )

    @app.post("/nodes/api-access/settings")
    def update_nodes_api_access_settings():
        data = update_api_settings(read_api_access(_api_access_path(app)), request.form)
        write_api_access(_api_access_path(app), data)
        flash("Remote API access settings saved.", "success")
        return redirect(url_for("nodes", tab="api"))

    @app.post("/nodes/api-access/tokens")
    def create_nodes_api_token():
        data = read_api_access(_api_access_path(app))
        token, token_value = create_api_token(data, request.form.get("name", ""), api_scopes_from_form(request.form))
        write_api_access(_api_access_path(app), data)
        session["new_api_token"] = token_value
        session["new_api_token_name"] = token["name"]
        flash("Remote API token created. Copy it now; it will not be shown again.", "success")
        return redirect(url_for("nodes", tab="api", _anchor="new-api-token"))

    @app.post("/nodes/api-access/tokens/<token_id>/update")
    def update_nodes_api_token(token_id: str):
        data = read_api_access(_api_access_path(app))
        enabled = request.form.get("enabled") == "1"
        if update_api_token(data, token_id, request.form.get("name", ""), api_scopes_from_form(request.form), enabled):
            write_api_access(_api_access_path(app), data)
            flash("Remote API token updated.", "success")
        else:
            flash("Remote API token not found.", "error")
        return redirect(url_for("nodes", tab="api"))

    @app.post("/nodes/api-access/tokens/<token_id>/delete")
    def delete_nodes_api_token(token_id: str):
        data = read_api_access(_api_access_path(app))
        if delete_token(data, token_id):
            write_api_access(_api_access_path(app), data)
            flash("Remote API token deleted.", "success")
        else:
            flash("Remote API token not found.", "error")
        return redirect(url_for("nodes", tab="api"))

    @app.post("/nodes/api-access/tokens/<token_id>/regenerate")
    def regenerate_nodes_api_token(token_id: str):
        data = read_api_access(_api_access_path(app))
        changed, token_name, token_value = regenerate_api_token(data, token_id)
        if changed:
            write_api_access(_api_access_path(app), data)
            session["new_api_token"] = token_value
            session["new_api_token_name"] = token_name
            flash("Remote API token regenerated. Copy it now; the old token no longer works.", "success")
            return redirect(url_for("nodes", tab="api", _anchor="new-api-token"))
        flash("Remote API token not found.", "error")
        return redirect(url_for("nodes", tab="api"))

    @app.post("/nodes/settings")
    def update_nodes_settings():
        data = read_nodes(_nodes_path(app))
        settings = data.get("settings") or {}
        settings["node_name"] = request.form.get("node_name", "").strip() or settings.get("node_name")
        settings["announce_enabled"] = request.form.get("announce_enabled") == "1"
        data["settings"] = settings
        write_nodes(_nodes_path(app), data)
        if settings["announce_enabled"]:
            try:
                install_announcement(settings, app.config["SYSTEMD_GUI_PUBLIC_PORT"])
                flash("LAN announcement is enabled for this node.", "success")
            except OSError as exc:
                flash(f"LAN announcement could not be enabled: {exc}", "error")
        else:
            try:
                remove_announcement()
                flash("LAN announcement is disabled for this node.", "success")
            except OSError as exc:
                flash(f"LAN announcement could not be disabled: {exc}", "error")
        return redirect(url_for("nodes", tab="local"))

    @app.post("/nodes/install-discovery")
    def install_nodes_discovery():
        data = read_nodes(_nodes_path(app))
        settings = data.get("settings") or {}
        settings["announce_enabled"] = True
        data["settings"] = settings
        write_nodes(_nodes_path(app), data)
        result = install_discovery_support(settings, app.config["SYSTEMD_GUI_PUBLIC_PORT"])
        session["nodes_install_result"] = {
            "ok": result.ok,
            "message": result.message,
            "output": result.output[-1600:],
        }
        flash(result.message, "success" if result.ok else "error")
        return redirect(url_for("nodes", tab="local"))

    @app.post("/nodes")
    def create_node():
        data = read_nodes(_nodes_path(app))
        node = node_from_form(request.form)
        if not node["url"]:
            flash("Node URL is required.", "error")
            return redirect(url_for("nodes"))
        data["nodes"] = _upsert_node(list(data.get("nodes") or []), node)
        write_nodes(_nodes_path(app), data)
        flash(f"Node {node['name']} saved.", "success")
        return redirect(url_for("nodes"))

    @app.post("/nodes/discovered")
    def save_discovered_node():
        data = read_nodes(_nodes_path(app))
        node = normalize_node({
            "node_id": request.form.get("node_id", ""),
            "name": request.form.get("name", ""),
            "url": request.form.get("url", ""),
            "host": request.form.get("host", ""),
            "port": request.form.get("port", ""),
            "version": request.form.get("version", ""),
            "ssh_host": request.form.get("host", ""),
        })
        if not node["url"]:
            flash("Discovered node has no usable URL.", "error")
            return redirect(url_for("nodes"))
        data["nodes"] = _upsert_node(list(data.get("nodes") or []), node)
        write_nodes(_nodes_path(app), data)
        flash(f"Discovered node {node['name']} saved.", "success")
        return redirect(url_for("nodes"))

    @app.post("/nodes/<node_id>/update")
    def update_node(node_id: str):
        data = read_nodes(_nodes_path(app))
        nodes_list = list(data.get("nodes") or [])
        for index, node in enumerate(nodes_list):
            if str(node.get("id")) == node_id:
                updated = node_from_form(request.form, node)
                if not updated["url"]:
                    flash("Node URL is required.", "error")
                    return redirect(url_for("nodes"))
                nodes_list[index] = updated
                data["nodes"] = nodes_list
                write_nodes(_nodes_path(app), data)
                flash(f"Node {updated['name']} updated.", "success")
                return redirect(url_for("nodes"))
        flash("Node not found.", "error")
        return redirect(url_for("nodes"))

    @app.post("/nodes/<node_id>/delete")
    def delete_node(node_id: str):
        data = read_nodes(_nodes_path(app))
        nodes_list = list(data.get("nodes") or [])
        kept_nodes = [node for node in nodes_list if str(node.get("id")) != node_id]
        if len(kept_nodes) == len(nodes_list):
            flash("Node not found.", "error")
        else:
            data["nodes"] = kept_nodes
            write_nodes(_nodes_path(app), data)
            flash("Node deleted.", "success")
        return redirect(url_for("nodes"))

    @app.post("/nodes/<node_id>/check-api")
    def check_node_api(node_id: str):
        data = read_nodes(_nodes_path(app))
        for node in data.get("nodes") if isinstance(data.get("nodes"), list) else []:
            if str(node.get("id")) != node_id:
                continue
            result = check_remote_api_access(node)
            session["node_api_check_results"] = {
                node_id: {
                    "ok": result.ok,
                    "message": result.message,
                    "status": result.status,
                    "details": result.details or {},
                }
            }
            flash(result.message, "success" if result.ok else "error")
            return redirect(url_for("nodes"))
        flash("Node not found.", "error")
        return redirect(url_for("nodes"))

    @app.get("/logs")
    def logs():
        log_lines = _log_line_count(request.args.get("lines", "200"))
        log_per_node = _log_line_count(request.args.get("per_node", str(log_lines)))
        log_priority = _log_priority(request.args.get("priority", "all"))
        log_wrap = _log_wrap(request.args.get("wrap", "1"))
        log_refresh_interval = _log_refresh_interval(request.args.get("interval", "5"))
        log_refresh = _log_refresh_enabled(request.args.get("refresh"), request.args.get("interval"))
        selected_nodes = _selected_log_nodes()
        journal_logs, log_entries, log_node_options = _combined_journal_logs(app, "", log_lines, log_per_node, log_priority, selected_nodes)
        return render_template(
            "logs.html",
            log_lines=log_lines,
            log_per_node=log_per_node,
            log_priority=log_priority,
            log_priority_options=LOG_PRIORITY_OPTIONS,
            log_refresh=log_refresh,
            log_refresh_interval=log_refresh_interval,
            log_wrap=log_wrap,
            logs=journal_logs,
            log_entries=log_entries,
            log_node_options=log_node_options,
            selected_log_nodes=selected_nodes,
            log_source_label="All journal logs",
            log_command_label=_journalctl_label("", log_priority),
        )

    @app.get("/logs/fragment")
    def logs_fragment():
        log_lines = _log_line_count(request.args.get("lines", "200"))
        log_per_node = _log_line_count(request.args.get("per_node", str(log_lines)))
        log_priority = _log_priority(request.args.get("priority", "all"))
        log_wrap = _log_wrap(request.args.get("wrap", "1"))
        journal_logs, log_entries, log_node_options = _combined_journal_logs(app, "", log_lines, log_per_node, log_priority, _selected_log_nodes())
        return render_template("_service_logs.html", logs=journal_logs, log_entries=log_entries, log_node_options=log_node_options, log_wrap=log_wrap)

    @app.get("/logs/window")
    def logs_window():
        log_lines = _log_line_count(request.args.get("lines", "200"))
        log_per_node = _log_line_count(request.args.get("per_node", str(log_lines)))
        log_priority = _log_priority(request.args.get("priority", "all"))
        log_wrap = _log_wrap(request.args.get("wrap", "1"))
        log_refresh_interval = _log_refresh_interval(request.args.get("interval", "5"))
        log_refresh = _log_refresh_enabled(request.args.get("refresh"), request.args.get("interval"))
        selected_nodes = _selected_log_nodes()
        journal_logs, log_entries, log_node_options = _combined_journal_logs(app, "", log_lines, log_per_node, log_priority, selected_nodes)
        return render_template(
            "service_logs_window.html",
            info={"name": "All journal logs"},
            log_lines=log_lines,
            log_per_node=log_per_node,
            log_priority=log_priority,
            log_priority_options=LOG_PRIORITY_OPTIONS,
            log_command_label=_journalctl_label("", log_priority),
            log_refresh=log_refresh,
            log_refresh_interval=log_refresh_interval,
            log_wrap=log_wrap,
            logs=journal_logs,
            log_entries=log_entries,
            log_node_options=log_node_options,
            selected_log_nodes=selected_nodes,
            log_fragment_url=url_for("logs_fragment"),
            log_window_action=url_for("logs_window"),
            log_source_label="All journal logs",
        )

    @app.get("/docker")
    def docker_index():
        status, containers = list_containers()
        counts = {
            "total": len(containers),
            "running": sum(1 for item in containers if item.get("state") == "running"),
            "exited": sum(1 for item in containers if item.get("state") == "exited"),
        }
        return render_template("docker.html", status=status, containers=containers, counts=counts)

    @app.get("/docker/remote-fragment")
    def docker_remote_fragment():
        return render_template("_docker_remote.html", remote_nodes=_remote_docker_nodes(app))

    @app.get("/docker/remote-fragment/<node_id>")
    def docker_remote_node_fragment(node_id: str):
        node = _remote_docker_node(app, node_id)
        if not node:
            remote = {
                "node": {"id": node_id, "name": "Remote node", "version": ""},
                "ok": False,
                "message": "Saved node was not found.",
                "status": "missing",
                "containers": [],
                "counts": {"total": 0, "running": 0, "exited": 0},
            }
        else:
            remote = _remote_docker_result(node)
        return render_template("_docker_remote_row.html", remote=remote)

    @app.get("/docker/<container_id>")
    def docker_detail(container_id: str):
        log_lines = _log_line_count(request.args.get("lines", "200"))
        try:
            container = container_detail(container_id)
            logs_result = container_logs(container_id, log_lines)
        except DockerError as exc:
            flash(str(exc), "error")
            return redirect(url_for("docker_index"))
        return render_template(
            "docker_detail.html",
            container=container,
            log_lines=log_lines,
            logs=logs_result.output,
            logs_ok=logs_result.ok,
        )

    @app.post("/docker/<container_id>/<action>")
    def docker_action(container_id: str, action: str):
        if action not in DOCKER_ACTIONS:
            flash("Unsupported Docker action.", "error")
            return redirect(url_for("docker_detail", container_id=container_id))
        try:
            result = run_docker_action(container_id, action)
        except DockerError as exc:
            flash(str(exc), "error")
            return redirect(url_for("docker_index"))
        if result.ok:
            flash(f"Docker {action} completed.", "success")
        else:
            flash(result.output or f"Docker {action} failed.", "error")
        return redirect(url_for("docker_detail", container_id=container_id))

    @app.get("/quick-shell")
    def quick_shell():
        data = read_quick_shell(_quick_shell_path(app))
        nodes_data = read_nodes(_nodes_path(app))
        parent_path = request.args.get("path", "").strip()
        active_tab = request.args.get("tab", "menu")
        if active_tab not in {"menu", "tree", "transfer", "setup"}:
            active_tab = "menu"
        try:
            parent = item_for_path(data, parent_path) if parent_path else None
            entries = children_for_path(data, parent_path)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("quick_shell"))
        return render_template(
            "quick_shell.html",
            entries=entries,
            parent=parent,
            parent_path=parent_path,
            active_tab=active_tab,
            breadcrumbs=_quick_shell_breadcrumbs(data, parent_path),
            flat_entries=flatten_entries(data.get("items") or []),
            category_options=_quick_shell_category_options(data),
            helper_status=quick_shell_helper_status(_quick_shell_bin(app), _app_root(app), _data_dir(app)),
            remote_ssh_status=remote_ssh_support_status(),
            shell_integrations=shell_integration_statuses(_quick_shell_bin(app)),
            bash_history_status=bash_history_timestamp_status(),
            quick_shell_settings=data.get("settings") or {},
            quick_shell_data=data,
            quick_shell_remote_nodes=_quick_shell_remote_import_nodes(nodes_data),
            quick_shell_path=_quick_shell_path(app),
            quick_shell_backups=list_quick_shell_backups(_quick_shell_backup_dir(app)),
            entry_label=entry_label,
        )

    @app.post("/quick-shell/settings")
    def update_quick_shell_settings():
        data = read_quick_shell(_quick_shell_path(app))
        try:
            history_limit = int(request.form.get("history_limit", "80"))
        except ValueError:
            history_limit = 80
        data["settings"] = {
            "history_limit": history_limit,
            "history_show_timestamps": request.form.get("history_show_timestamps") == "1",
            "history_source": request.form.get("history_source", "combined"),
        }
        write_quick_shell(_quick_shell_path(app), data)
        flash("Quick Shell settings saved.", "success")
        return redirect(url_for("quick_shell", tab="setup"))

    @app.post("/quick-shell/install-helper")
    def install_quick_shell():
        try:
            install_quick_shell_helper(_quick_shell_bin(app), _app_root(app), _data_dir(app))
        except OSError as exc:
            flash(f"Quick Shell helper could not be installed: {exc}", "error")
            return redirect(url_for("quick_shell", tab="setup"))
        flash(f"Quick Shell helper installed at {_quick_shell_bin(app)}.", "success")
        return redirect(url_for("quick_shell", tab="setup"))

    @app.post("/quick-shell/install-sshpass")
    def install_quick_shell_sshpass():
        try:
            install_sshpass_package()
        except OSError as exc:
            flash(f"sshpass could not be installed: {exc}", "error")
            return redirect(url_for("quick_shell", tab="setup"))
        flash("sshpass installed. Saved Remote Quick Shell passwords can now be used automatically.", "success")
        return redirect(url_for("quick_shell", tab="setup"))

    @app.post("/quick-shell/integration/<shell_id>/install")
    def install_quick_shell_integration(shell_id: str):
        try:
            install_quick_shell_helper(_quick_shell_bin(app), _app_root(app), _data_dir(app))
            target = install_shell_integration(shell_id, _quick_shell_bin(app))
        except (OSError, ValueError) as exc:
            flash(f"Shell integration could not be installed: {exc}", "error")
            return redirect(url_for("quick_shell", tab="setup"))
        flash(f"Shell integration installed in {target}. Open a new shell or source the file.", "success")
        return redirect(url_for("quick_shell", tab="setup"))

    @app.post("/quick-shell/integration/<shell_id>/remove")
    def remove_quick_shell_integration(shell_id: str):
        try:
            target = remove_shell_integration(shell_id)
        except (OSError, ValueError) as exc:
            flash(f"Shell integration could not be removed: {exc}", "error")
            return redirect(url_for("quick_shell", tab="setup"))
        flash(f"Shell integration removed from {target}. Open a new shell for the change to take effect.", "success")
        return redirect(url_for("quick_shell", tab="setup"))

    @app.post("/quick-shell/bash-history-timestamps/install")
    def install_quick_shell_bash_history_timestamps():
        try:
            target = install_bash_history_timestamps()
        except OSError as exc:
            flash(f"Bash history timestamps could not be enabled: {exc}", "error")
            return redirect(url_for("quick_shell", tab="setup"))
        flash(f"Bash history timestamps enabled in {target}. Open a new bash shell or source the file.", "success")
        return redirect(url_for("quick_shell", tab="setup"))

    @app.post("/quick-shell/bash-history-timestamps/remove")
    def remove_quick_shell_bash_history_timestamps():
        try:
            target = remove_bash_history_timestamps()
        except OSError as exc:
            flash(f"Bash history timestamps could not be removed: {exc}", "error")
            return redirect(url_for("quick_shell", tab="setup"))
        flash(f"Bash history timestamp file removed from {target}. Open a new bash shell for the change to take effect.", "success")
        return redirect(url_for("quick_shell", tab="setup"))

    @app.post("/quick-shell/item")
    def create_quick_shell_item():
        data = read_quick_shell(_quick_shell_path(app))
        parent_path = request.form.get("parent_path", "").strip()
        try:
            add_item(data, parent_path, _quick_shell_item_from_form())
            write_quick_shell(_quick_shell_path(app), data)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("quick_shell", tab="menu", path=parent_path))
        flash("Quick Shell entry created.", "success")
        return redirect(url_for("quick_shell", tab="menu", path=parent_path))

    @app.get("/quick-shell/item/<item_path>/edit")
    def edit_quick_shell_item(item_path: str):
        data = read_quick_shell(_quick_shell_path(app))
        try:
            item = item_for_path(data, item_path)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("quick_shell"))
        return render_template(
            "quick_shell_edit.html",
            item=item,
            item_path=item_path,
            parent_path=_quick_shell_parent_path(item_path),
            entry_label=entry_label,
        )

    @app.post("/quick-shell/item/<item_path>/update")
    def update_quick_shell_item(item_path: str):
        data = read_quick_shell(_quick_shell_path(app))
        parent_path = _quick_shell_parent_path(item_path)
        next_parent_path = request.form.get("parent_path", parent_path).strip()
        try:
            update_item(data, item_path, _quick_shell_item_from_form())
            next_path = move_item_to_category(data, item_path, next_parent_path)
            write_quick_shell(_quick_shell_path(app), data)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("quick_shell", tab="menu", path=parent_path))
        flash("Quick Shell entry saved.", "success")
        return redirect(url_for("quick_shell", tab="menu", path=_quick_shell_parent_path(next_path)))

    @app.post("/quick-shell/item/<item_path>/delete")
    def delete_quick_shell_item(item_path: str):
        data = read_quick_shell(_quick_shell_path(app))
        parent_path = _quick_shell_parent_path(item_path)
        try:
            delete_item(data, item_path)
            write_quick_shell(_quick_shell_path(app), data)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("quick_shell", tab="menu", path=parent_path))
        flash("Quick Shell entry deleted.", "success")
        return redirect(url_for("quick_shell", tab="menu", path=parent_path))

    @app.post("/quick-shell/item/<item_path>/move")
    def move_quick_shell_item(item_path: str):
        data = read_quick_shell(_quick_shell_path(app))
        parent_path = _quick_shell_parent_path(item_path)
        direction = request.form.get("direction", "")
        position_raw = request.form.get("position", "").strip()
        if direction not in {"up", "down", "position"}:
            flash("Unknown move direction.", "error")
            return redirect(url_for("quick_shell", tab="menu", path=parent_path))
        try:
            if direction == "position":
                if not position_raw.isdigit():
                    raise ValueError("Position must be a whole number.")
                move_item_to_position(data, item_path, int(position_raw))
            else:
                move_item(data, item_path, direction)
            write_quick_shell(_quick_shell_path(app), data)
        except (TypeError, ValueError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("quick_shell", tab="menu", path=parent_path))

    @app.get("/quick-shell/export/full")
    def export_quick_shell_full():
        data = read_quick_shell(_quick_shell_path(app))
        payload = quick_shell_export_payload(data, source="Full menu")
        return _json_download(payload, "systemd-gui-quick-shell-full.json")

    @app.post("/quick-shell/export/selected")
    def export_quick_shell_selected():
        data = read_quick_shell(_quick_shell_path(app))
        selected_paths = request.form.getlist("selected_paths")
        items = []
        for item_path in selected_paths:
            try:
                items.append(item_for_path(data, item_path))
            except ValueError:
                continue
        if not items:
            flash("Select at least one entry to export.", "error")
            return redirect(url_for("quick_shell", tab="menu", path=request.form.get("parent_path", "")))
        payload = quick_shell_export_payload(data, items, source="Selected entries")
        return _json_download(payload, "systemd-gui-quick-shell-selected.json")

    @app.get("/quick-shell/item/<item_path>/export")
    def export_quick_shell_item(item_path: str):
        data = read_quick_shell(_quick_shell_path(app))
        try:
            item = item_for_path(data, item_path)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("quick_shell", tab="menu"))
        payload = quick_shell_export_payload(data, [item], source=f"Entry: {entry_label(item)}")
        filename = f"systemd-gui-quick-shell-{_download_slug(entry_label(item))}.json"
        return _json_download(payload, filename)

    @app.post("/quick-shell/import")
    def import_quick_shell():
        upload = request.files.get("import_file")
        if not upload or not upload.filename:
            flash("Choose a Quick Shell export file first.", "error")
            return redirect(url_for("quick_shell", tab="transfer"))
        try:
            payload = json.loads(upload.stream.read().decode("utf-8"))
            imported_items = quick_shell_payload_items(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            flash(f"Import failed: {exc}", "error")
            return redirect(url_for("quick_shell", tab="transfer"))
        return _finish_quick_shell_import(app, payload, imported_items, "Import")

    @app.post("/quick-shell/import/remote")
    def import_quick_shell_remote():
        node_id = request.form.get("node_id", "").strip()
        nodes_data = read_nodes(_nodes_path(app))
        node = _saved_node_by_id(nodes_data, node_id)
        if not node:
            flash("Choose a saved node with a Remote API token first.", "error")
            return redirect(url_for("quick_shell", tab="transfer"))
        result = fetch_remote_quick_shell_export(node)
        if not result.ok or not result.payload:
            flash(f"Remote import failed for {node.get('name') or 'remote node'}: {result.message}", "error")
            return redirect(url_for("quick_shell", tab="transfer"))
        try:
            imported_items = quick_shell_payload_items(result.payload)
        except ValueError as exc:
            flash(f"Remote import failed for {node.get('name') or 'remote node'}: {exc}", "error")
            return redirect(url_for("quick_shell", tab="transfer"))
        return _finish_quick_shell_import(app, result.payload, imported_items, f"Remote import from {result.node.get('name') or node.get('name') or 'remote node'}")

    @app.post("/quick-shell/backups")
    def create_quick_shell_backup_route():
        comment = request.form.get("comment", "").strip()
        try:
            backup_path = create_quick_shell_backup(_quick_shell_path(app), _quick_shell_backup_dir(app), comment)
        except OSError as exc:
            flash(f"Quick Shell backup could not be created: {exc}", "error")
            return redirect(url_for("quick_shell", tab="transfer"))
        flash(f"Quick Shell backup created: {backup_path}.", "success")
        return redirect(url_for("quick_shell", tab="transfer"))

    @app.get("/quick-shell/backups/<backup_id>/download")
    def download_quick_shell_backup(backup_id: str):
        try:
            _path, payload = read_quick_shell_backup(_quick_shell_backup_dir(app), backup_id)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            flash(f"Quick Shell backup download failed: {exc}", "error")
            return redirect(url_for("quick_shell", tab="transfer"))
        return _json_download(payload, backup_id)

    @app.post("/quick-shell/backups/<backup_id>/restore")
    def restore_quick_shell_backup_route(backup_id: str):
        try:
            backup_path = restore_quick_shell_backup(
                _quick_shell_path(app),
                _quick_shell_backup_dir(app),
                backup_id,
                request.form.get("backup_current") == "1",
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            flash(f"Quick Shell backup restore failed: {exc}", "error")
            return redirect(url_for("quick_shell", tab="transfer"))
        backup_note = f" Current menu was backed up first: {backup_path}." if backup_path else ""
        flash(f"Quick Shell backup restored.{backup_note}", "success")
        return redirect(url_for("quick_shell", tab="menu"))

    @app.post("/quick-shell/backups/<backup_id>/delete")
    def delete_quick_shell_backup_route(backup_id: str):
        try:
            deleted_path = delete_quick_shell_backup(_quick_shell_backup_dir(app), backup_id)
        except (OSError, ValueError) as exc:
            flash(f"Quick Shell backup delete failed: {exc}", "error")
            return redirect(url_for("quick_shell", tab="transfer"))
        flash(f"Quick Shell backup deleted: {deleted_path}.", "success")
        return redirect(url_for("quick_shell", tab="transfer"))

    @app.post("/settings/check-update")
    def check_update():
        status = check_for_update()
        session["update_status"] = update_status_to_dict(status)
        if status.error:
            flash("Update check failed. See details below.", "error")
        elif status.no_releases:
            flash("No GitHub releases have been published yet.", "warning")
        elif status.update_available:
            flash("A new version is available.", "success")
        else:
            flash("You are running the latest known version.", "success")
        return redirect(url_for("settings", tab="updates"))

    @app.post("/settings/update/git")
    def apply_git_update():
        result = update_from_git(_app_root(app))
        session["update_result"] = _update_result_dict(result)
        if result.ok:
            session["app_update_pending_restart"] = True
        flash(result.message, "success" if result.ok else "error")
        if result.ok and request.form.get("remote_update") == "1":
            _flash_remote_git_update_results(app)
            restart_ok, restart_message = _request_systemd_gui_restart(app, delay_seconds=3)
            if restart_ok:
                session.pop("app_update_pending_restart", None)
            flash(restart_message, "success" if restart_ok else "error")
        return redirect(url_for("settings", tab="updates"))

    @app.post("/settings/update/release")
    def apply_release_update():
        status = check_for_update(timeout=15)
        session["update_status"] = update_status_to_dict(status)
        if status.error:
            flash("Release update failed because the update check failed.", "error")
            return redirect(url_for("settings", tab="updates"))
        if status.no_releases:
            flash("No GitHub releases have been published yet.", "warning")
            return redirect(url_for("settings", tab="updates"))
        if not status.update_available:
            flash("No newer official release is available.", "success")
            return redirect(url_for("settings", tab="updates"))
        if not status.zipball_url or not status.latest_version:
            flash("Latest release does not provide a downloadable ZIP archive.", "error")
            return redirect(url_for("settings", tab="updates"))

        result = update_from_release(_app_root(app), status.zipball_url, status.latest_version)
        session["update_result"] = _update_result_dict(result)
        if result.ok:
            session["app_update_pending_restart"] = True
            session["update_status"] = {
                **update_status_to_dict(status),
                "update_available": False,
                "release_notes": [],
            }
        flash(result.message, "success" if result.ok else "error")
        return redirect(url_for("settings", tab="updates"))

    @app.post("/settings/update/zip")
    def apply_zip_update():
        upload = request.files.get("update_zip")
        if not upload or not upload.filename:
            flash("Choose a ZIP file before starting the update.", "error")
            return redirect(url_for("settings", tab="updates"))
        if not upload.filename.lower().endswith(".zip"):
            flash("Only ZIP update files are supported.", "error")
            return redirect(url_for("settings", tab="updates"))

        result = update_from_zip(_app_root(app), upload.stream)
        session["update_result"] = _update_result_dict(result)
        if result.ok:
            session["app_update_pending_restart"] = True
        flash(result.message, "success" if result.ok else "error")
        return redirect(url_for("settings", tab="updates"))

    @app.post("/settings/update/backups")
    def create_app_update_backup():
        comment = request.form.get("comment", "").strip()
        try:
            backup_path = create_app_backup(_app_root(app), "Manual app backup", comment)
        except OSError as exc:
            flash(f"App backup failed: {exc}", "error")
            return redirect(url_for("settings", tab="backups"))

        flash(f"App backup created: {backup_path}", "success")
        return redirect(url_for("settings", tab="backups"))

    @app.post("/settings/update/backups/<backup_id>/restore")
    def restore_app_update_backup(backup_id: str):
        result = restore_app_backup(_app_root(app), backup_id)
        session["update_result"] = _update_result_dict(result)
        if result.ok:
            session["app_update_pending_restart"] = True
        flash(result.message, "success" if result.ok else "error")
        return redirect(url_for("settings", tab="backups"))

    @app.post("/settings/update/backups/<backup_id>/delete")
    def delete_app_update_backup(backup_id: str):
        try:
            delete_app_backup(_app_root(app), backup_id)
        except (OSError, ValueError) as exc:
            flash(f"App update backup delete failed: {exc}", "error")
            return redirect(url_for("settings", tab="backups"))

        flash("App update backup deleted.", "success")
        return redirect(url_for("settings", tab="backups"))

    @app.post("/settings/update/restart-app")
    def restart_app_from_update():
        session.pop("app_update_pending_restart", None)
        return restart_app()

    @app.post("/settings/update/dismiss-restart")
    def dismiss_app_update_restart():
        session.pop("app_update_pending_restart", None)
        flash("Restart reminder dismissed.", "success")
        return redirect(request.referrer or url_for("settings", tab="updates"))

    @app.post("/settings/security/password")
    def change_password():
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if app.config["ADMIN_PASSWORD"] and not secrets.compare_digest(current_password, app.config["ADMIN_PASSWORD"]):
            flash("Current password is incorrect.", "error")
            return redirect(url_for("settings", tab="security"))
        if new_password != confirm_password:
            flash("New passwords do not match.", "error")
            return redirect(url_for("settings", tab="security"))
        if len(new_password) < 8:
            flash("New password must be at least 8 characters.", "error")
            return redirect(url_for("settings", tab="security"))
        if any(char.isspace() for char in new_password):
            flash("New password must not contain whitespace.", "error")
            return redirect(url_for("settings", tab="security"))

        try:
            backup_path = _update_env_value(
                Path(app.config["ENV_FILE"]),
                "SYSTEMD_GUI_PASSWORD",
                new_password,
                Path(app.config["DATA_DIR"]) / "env-backups",
            )
        except OSError as exc:
            flash(f"Password could not be saved: {exc}", "error")
            return redirect(url_for("settings", tab="security"))

        app.config["ADMIN_PASSWORD"] = new_password
        session.clear()
        backup_note = f" Environment backup: {backup_path}." if backup_path else ""
        flash(f"Password changed.{backup_note} Please sign in again.", "success")
        return redirect(url_for("login"))

    @app.get("/service/<name>")
    def service_detail(name: str):
        if not _valid_or_flash(name):
            return redirect(url_for("index"))
        active_tab = request.args.get("tab", "unit")
        if active_tab not in {"unit", "override", "logs", "backups", "info"}:
            active_tab = "unit"
        log_lines = _log_line_count(request.args.get("lines", "200"))
        log_per_node = _log_line_count(request.args.get("per_node", str(log_lines)))
        log_priority = _log_priority(request.args.get("priority", "all"))
        log_wrap = _log_wrap(request.args.get("wrap", "1"))
        log_refresh_interval = _log_refresh_interval(request.args.get("interval", "5"))
        log_refresh = _log_refresh_enabled(request.args.get("refresh"), request.args.get("interval"))
        selected_nodes = _selected_log_nodes()
        info = service_info(name)
        content = unit_content(name)
        original_content = unit_fragment_content(str(info.get("fragment_path") or ""))
        drop_in_paths = list(dict.fromkeys([
            *list(info.get("drop_in_path_list") or []),
            *list(info.get("local_drop_in_paths") or []),
        ]))
        flattened_unit = flattened_unit_preview(original_content, drop_in_paths) if original_content else {"lines": [], "text": ""}
        logs, log_entries, log_node_options = _combined_journal_logs(app, name, log_lines, log_per_node, log_priority, selected_nodes)
        editable = _editable(name)
        backups = list_unit_backups(name, _backup_dir(app))
        override_path, override_content, override_exists = read_drop_in_override(name)
        override_analysis = analyze_drop_in_content(override_content)
        override_backups = list_drop_in_backups(name, _drop_in_backup_dir(app))
        override_pending_reload = name in _pending_override_reloads()
        action_states = _service_action_states(app, info)
        notes = read_service_notes(_notes_path(app)).get(name, "")
        service_meta = _service_metadata(info)
        return render_template(
            "service_detail.html",
            active_tab=active_tab,
            log_lines=log_lines,
            log_per_node=log_per_node,
            log_priority=log_priority,
            log_priority_options=LOG_PRIORITY_OPTIONS,
            log_command_label=_journalctl_label(name, log_priority),
            log_refresh=log_refresh,
            log_refresh_interval=log_refresh_interval,
            log_wrap=log_wrap,
            log_entries=log_entries,
            log_node_options=log_node_options,
            selected_log_nodes=selected_nodes,
            info=info,
            content=content,
            original_content=original_content,
            flattened_unit=flattened_unit,
            logs=logs,
            editable=editable,
            backups=backups,
            override_path=override_path,
            override_content=override_content,
            override_exists=override_exists,
            override_analysis=override_analysis,
            override_backups=override_backups,
            override_pending_reload=override_pending_reload,
            action_states=action_states,
            notes=notes,
            service_meta=service_meta,
        )

    @app.post("/service/<name>/notes")
    def save_service_notes(name: str):
        if not _valid_or_flash(name):
            return redirect(url_for("index"))
        notes_path = _notes_path(app)
        notes = read_service_notes(notes_path)
        note = request.form.get("notes", "").strip()
        if note:
            notes[name] = note
        else:
            notes.pop(name, None)
        write_service_notes(notes_path, notes)
        flash("Service notes saved.", "success")
        return redirect(url_for("service_detail", name=name, tab="info"))

    @app.get("/service/<name>/logs/fragment")
    def service_logs_fragment(name: str):
        if not valid_service_name(name):
            return "Only .service units are supported.", 400
        log_lines = _log_line_count(request.args.get("lines", "200"))
        log_per_node = _log_line_count(request.args.get("per_node", str(log_lines)))
        log_priority = _log_priority(request.args.get("priority", "all"))
        log_wrap = _log_wrap(request.args.get("wrap", "1"))
        logs, log_entries, log_node_options = _combined_journal_logs(app, name, log_lines, log_per_node, log_priority, _selected_log_nodes())
        return render_template("_service_logs.html", logs=logs, log_entries=log_entries, log_node_options=log_node_options, log_wrap=log_wrap)

    @app.get("/service/<name>/logs")
    def service_logs_window(name: str):
        if not _valid_or_flash(name):
            return redirect(url_for("index"))
        log_lines = _log_line_count(request.args.get("lines", "200"))
        log_per_node = _log_line_count(request.args.get("per_node", str(log_lines)))
        log_priority = _log_priority(request.args.get("priority", "all"))
        log_wrap = _log_wrap(request.args.get("wrap", "1"))
        log_refresh_interval = _log_refresh_interval(request.args.get("interval", "5"))
        log_refresh = _log_refresh_enabled(request.args.get("refresh"), request.args.get("interval"))
        selected_nodes = _selected_log_nodes()
        info = service_info(name)
        logs, log_entries, log_node_options = _combined_journal_logs(app, name, log_lines, log_per_node, log_priority, selected_nodes)
        return render_template(
            "service_logs_window.html",
            info=info,
            log_lines=log_lines,
            log_per_node=log_per_node,
            log_priority=log_priority,
            log_priority_options=LOG_PRIORITY_OPTIONS,
            log_refresh=log_refresh,
            log_refresh_interval=log_refresh_interval,
            log_wrap=log_wrap,
            logs=logs,
            log_entries=log_entries,
            log_node_options=log_node_options,
            selected_log_nodes=selected_nodes,
            log_fragment_url=url_for("service_logs_fragment", name=name),
            log_window_action=url_for("service_logs_window", name=name),
            log_source_label=f"{name} logs",
        )

    @app.post("/service/<name>/backup/create")
    def create_service_backup(name: str):
        if not _valid_or_flash(name):
            return redirect(url_for("index"))
        try:
            backup_path = create_unit_backup(name, _backup_dir(app))
        except (OSError, ValueError) as exc:
            flash(f"Backup could not be created: {exc}", "error")
            return redirect(request.referrer or url_for("service_detail", name=name, tab="backups"))
        flash(f"Backup created: {backup_path}.", "success")
        return redirect(request.referrer or url_for("service_detail", name=name, tab="backups"))

    @app.post("/service/<name>/<action>")
    def service_action(name: str, action: str):
        if not _valid_or_flash(name):
            return redirect(url_for("index"))
        if action not in SERVICE_ACTIONS:
            flash("Unknown service action.", "error")
            return redirect(url_for("service_detail", name=name))
        if _blocked_protected(app, name):
            return redirect(url_for("service_detail", name=name))
        if _blocked_template(name):
            return redirect(url_for("service_detail", name=name))
        info = service_info(name)
        blocked_reason = _action_block_reason(app, info, action)
        if blocked_reason:
            flash(blocked_reason, "error")
            return redirect(url_for("service_detail", name=name))

        if action in RUNTIME_ACTIONS:
            result = run_systemctl([action, name])
        elif action in AUTOSTART_ACTIONS:
            result = run_systemctl([action, name])
        else:
            result = run_systemctl([action, name])
        if result.ok and action == "restart":
            _clear_override_restart_pending(name)
        flash(result.output or f"systemctl {action} completed.", "success" if result.ok else "error")
        return redirect(url_for("service_detail", name=name))

    @app.post("/service/<name>/favorite")
    def toggle_favorite(name: str):
        if not _valid_or_flash(name):
            return redirect(url_for("index"))
        path = _favorites_path(app)
        favorites = read_favorites(path)
        if name in favorites:
            favorites.remove(name)
            flash("Removed from favorites.", "success")
        else:
            favorites.add(name)
            flash("Added to favorites.", "success")
        write_favorites(path, favorites)
        return redirect(request.referrer or url_for("index"))

    @app.get("/service/<name>/edit")
    def edit_service(name: str):
        if not _valid_or_flash(name):
            return redirect(url_for("index"))
        if _blocked_protected(app, name):
            return redirect(url_for("service_detail", name=name))
        try:
            path, content = read_editable_unit(name)
        except (OSError, ValueError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("service_detail", name=name))
        backups = list_unit_backups(name, _backup_dir(app))
        return render_template("service_edit.html", name=name, path=path, content=content, backups=backups)

    @app.post("/service/<name>/override")
    def save_service_override(name: str):
        if not _valid_or_flash(name):
            return redirect(url_for("index"))
        info = service_info(name)
        if not info.get("available"):
            flash("This service was not found by systemd. Override editing is disabled.", "error")
            return redirect(url_for("service_detail", name=name, tab="override"))
        if _blocked_protected(app, name):
            return redirect(url_for("service_detail", name=name, tab="override"))
        if _blocked_template(name):
            return redirect(url_for("service_detail", name=name, tab="override"))
        content = request.form.get("content", "")
        try:
            backup_path = write_drop_in_override(name, content, Path(app.config["DATA_DIR"]) / "drop-in-backups")
        except (OSError, ValueError) as exc:
            flash(f"Override could not be saved: {exc}", "error")
            return redirect(url_for("service_detail", name=name, tab="override"))
        backup_note = f" Previous override backup: {backup_path}." if backup_path else ""
        _mark_override_reload_pending(name)
        flash(f"Override saved.{backup_note} Run daemon-reload before restarting the service.", "success")
        return redirect(url_for("service_detail", name=name, tab="override"))

    @app.post("/service/<name>/override/delete")
    def delete_service_override(name: str):
        if not _valid_or_flash(name):
            return redirect(url_for("index"))
        info = service_info(name)
        if not info.get("available"):
            flash("This service was not found by systemd. Override editing is disabled.", "error")
            return redirect(url_for("service_detail", name=name, tab="override"))
        if _blocked_protected(app, name):
            return redirect(url_for("service_detail", name=name, tab="override"))
        try:
            backup_path = delete_drop_in_override(name, Path(app.config["DATA_DIR"]) / "drop-in-backups")
        except (OSError, ValueError) as exc:
            flash(f"Override could not be deleted: {exc}", "error")
            return redirect(url_for("service_detail", name=name, tab="override"))
        _mark_override_reload_pending(name)
        flash(f"Override deleted. Backup: {backup_path}. Run daemon-reload before restarting the service.", "success")
        return redirect(url_for("service_detail", name=name, tab="override"))

    @app.post("/service/<name>/override/restart-dismiss")
    def dismiss_override_restart(name: str):
        if not _valid_or_flash(name):
            return redirect(url_for("index"))
        _clear_override_restart_pending(name)
        flash("Service restart reminder dismissed.", "success")
        return redirect(request.referrer or url_for("service_detail", name=name, tab="override"))

    @app.post("/service/<name>/edit")
    def save_service(name: str):
        if not _valid_or_flash(name):
            return redirect(url_for("index"))
        if _blocked_protected(app, name):
            return redirect(url_for("service_detail", name=name))
        content = request.form.get("content", "")
        try:
            backup_path = write_editable_unit(name, content, Path(app.config["DATA_DIR"]) / "unit-backups")
        except (OSError, ValueError) as exc:
            flash(f"Unit file could not be saved: {exc}", "error")
            return redirect(url_for("service_detail", name=name))
        flash(f"Unit file saved. Backup: {backup_path}. Run daemon-reload before restarting the service.", "success")
        return redirect(url_for("service_detail", name=name))

    @app.get("/service/<name>/backup/<backup_name>")
    def service_backup(name: str, backup_name: str):
        if not _valid_or_flash(name):
            return redirect(url_for("index"))
        try:
            path, content = read_unit_backup(name, backup_name, _backup_dir(app))
        except (OSError, ValueError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("service_detail", name=name))
        return render_template(
            "service_backup.html",
            name=name,
            backup_name=backup_name,
            path=path,
            content=content,
            editable=_editable(name),
            restored=request.args.get("restored") == "1",
        )

    @app.get("/service/<name>/backup/<backup_name>/download")
    def download_service_backup(name: str, backup_name: str):
        if not _valid_or_flash(name):
            return redirect(url_for("index"))
        try:
            _path, content = read_unit_backup(name, backup_name, _backup_dir(app))
        except (OSError, ValueError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("service_detail", name=name, tab="backups"))

        filename = name if request.args.get("filename") == "unit" else backup_name
        return Response(
            content,
            mimetype="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/service/<name>/backup/<backup_name>/restore")
    def restore_service_backup(name: str, backup_name: str):
        if not _valid_or_flash(name):
            return redirect(url_for("index"))
        if _blocked_protected(app, name):
            return redirect(url_for("service_backup", name=name, backup_name=backup_name))
        backup_current = request.form.get("backup_current") == "1"
        try:
            current_backup = restore_unit_backup(name, backup_name, _backup_dir(app), backup_current)
        except (OSError, ValueError) as exc:
            flash(f"Backup could not be restored: {exc}", "error")
            return redirect(url_for("service_backup", name=name, backup_name=backup_name))
        note = f" Current unit was backed up first: {current_backup}." if current_backup else ""
        flash(f"Backup restored.{note} Run daemon-reload and restart the service when you are ready.", "success")
        return redirect(url_for("service_backup", name=name, backup_name=backup_name, restored="1"))

    @app.post("/service/<name>/backup/<backup_name>/delete")
    def delete_service_backup(name: str, backup_name: str):
        if not _valid_or_flash(name):
            return redirect(url_for("index"))
        try:
            deleted_path = delete_unit_backup(name, backup_name, _backup_dir(app))
        except (OSError, ValueError) as exc:
            flash(f"Backup could not be deleted: {exc}", "error")
            return redirect(url_for("service_backup", name=name, backup_name=backup_name))
        flash(f"Backup deleted: {deleted_path}.", "success")
        return redirect(url_for("service_detail", name=name, tab="backups"))

    @app.post("/daemon-reload")
    def daemon_reload():
        result = run_systemctl(["daemon-reload"])
        flash(result.output or "systemctl daemon-reload completed.", "success" if result.ok else "error")
        service_name = request.form.get("service_name", "").strip()
        if service_name and not valid_service_name(service_name):
            service_name = ""
        pending_names = sorted(_pending_override_reloads())
        next_url = ""
        if result.ok:
            if service_name:
                _clear_override_reload_pending(service_name)
                _mark_override_restart_pending(service_name)
            elif len(pending_names) == 1:
                service_name = pending_names[0]
                _clear_override_reload_pending(service_name)
                _mark_override_restart_pending(service_name)
            next_url = _safe_next_url(request.form.get("next", ""))
            if not next_url and service_name:
                next_url = url_for("service_detail", name=service_name, tab="override", restart_prompt="1")
        return redirect(next_url or request.referrer or url_for("index"))

    @app.post("/restart-app")
    def restart_app():
        ok, message = _request_systemd_gui_restart(app)
        flash(message, "success" if ok else "error")
        return redirect(request.referrer or url_for("index"))

    return app


def _favorites_path(app: Flask) -> Path:
    return Path(app.config["DATA_DIR"]) / "favorites.json"


def _notes_path(app: Flask) -> Path:
    return Path(app.config["DATA_DIR"]) / "service-notes.json"


def _data_dir(app: Flask) -> Path:
    data_dir = Path(app.config["DATA_DIR"])
    if data_dir.is_absolute():
        return data_dir
    return _app_root(app) / data_dir


def _quick_shell_path(app: Flask) -> Path:
    return _data_dir(app) / "quick-shell.json"


def _quick_shell_backup_dir(app: Flask) -> Path:
    return _data_dir(app) / "quick-shell-backups"


def _nodes_path(app: Flask) -> Path:
    return _data_dir(app) / "nodes.json"


def _api_access_path(app: Flask) -> Path:
    return _data_dir(app) / "api-access.json"


def _require_remote_api_access(app: Flask, scope: str) -> tuple[Response, int] | None:
    access_data = read_api_access(_api_access_path(app))
    settings = access_data.get("settings") if isinstance(access_data.get("settings"), dict) else {}
    if not settings.get("enabled"):
        return jsonify({"error": "Remote API access is disabled."}), 403
    nodes_data = read_nodes(_nodes_path(app))
    allowed, ip_message = client_ip_allowed(settings, request.remote_addr or "", list(nodes_data.get("nodes") or []))
    if not allowed:
        return jsonify({"error": ip_message}), 403
    token_value = bearer_token_from_header(request.headers.get("Authorization", ""))
    ok, token_message, _token = verify_bearer_token(access_data, token_value, scope)
    if not ok:
        status = 403 if "scope" in token_message.lower() or "category" in token_message.lower() else 401
        return jsonify({"error": token_message}), status
    write_api_access(_api_access_path(app), access_data)
    return None


def _request_systemd_gui_restart(app: Flask, delay_seconds: int = 1) -> tuple[bool, str]:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return False, "systemctl is not available in this environment."
    service = app.config["SYSTEMD_GUI_SERVICE"]
    delay = max(1, int(delay_seconds))
    command = f"sleep {delay}; exec {shlex.quote(systemctl)} restart {shlex.quote(service)}"
    try:
        subprocess.Popen(["/bin/sh", "-c", command], start_new_session=True)
    except OSError as exc:
        return False, f"Systemd Gui restart could not be requested: {exc}"
    return True, "Systemd Gui restart requested. Reload the page in a few seconds."


def _flash_remote_git_update_results(app: Flask) -> None:
    data = read_nodes(_nodes_path(app))
    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    local_node_id = str(settings.get("node_id") or "").strip()
    candidates = []
    for node in data.get("nodes") if isinstance(data.get("nodes"), list) else []:
        if not isinstance(node, dict):
            continue
        if local_node_id and str(node.get("node_id") or "").strip() == local_node_id:
            continue
        if not str(node.get("api_token") or "").strip():
            continue
        candidates.append(node)

    if not candidates:
        flash("Remote update skipped: no saved remote nodes with an API token were found.", "warning")
        return

    with ThreadPoolExecutor(max_workers=min(6, len(candidates))) as executor:
        results = list(executor.map(trigger_remote_git_update, candidates))

    ok_results = [result for result in results if result.ok]
    failed = [result for result in results if not result.ok]
    if ok_results:
        names = ", ".join(str(result.node.get("name") or "Remote node") for result in ok_results)
        flash(f"Remote git update requested for {len(ok_results)} node(s): {names}.", "success")
    if failed:
        parts = [
            f"{result.node.get('name') or 'Remote node'}: {result.message}"
            for result in failed[:4]
        ]
        if len(failed) > 4:
            parts.append(f"{len(failed) - 4} more failed")
        flash(f"Remote git update failed for {len(failed)} node(s): {'; '.join(parts)}.", "error")


def _node_navigation(app: Flask) -> dict[str, object]:
    try:
        data = read_nodes(_nodes_path(app))
    except OSError:
        data = {"settings": {}, "nodes": []}
    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    local_name = str(settings.get("node_name") or APP_NAME).strip() or APP_NAME
    local_id = str(settings.get("node_id") or "").strip()
    local_url = request.host_url.rstrip("/") if request else ""
    current = {
        "name": local_name,
        "url": _node_switch_url(""),
        "absolute_url": local_url,
        "node_id": local_id,
        "version": APP_VERSION,
        "ssh_indicators": [],
        "current": True,
    }
    nodes: list[dict[str, object]] = [current]
    seen_ids = {local_id} if local_id else set()
    seen_urls = {_nav_node_url_key(local_url)} if local_url else set()
    saved_navigation_nodes = []
    for node in data.get("nodes") if isinstance(data.get("nodes"), list) else []:
        if not isinstance(node, dict):
            continue
        node_url = str(node.get("url") or "").strip()
        node_id = str(node.get("node_id") or "").strip()
        url_key = _nav_node_url_key(node_url)
        if (node_id and node_id in seen_ids) or (url_key and url_key in seen_urls):
            continue
        if node_id:
            seen_ids.add(node_id)
        if url_key:
            seen_urls.add(url_key)
        saved_navigation_nodes.append(node)
    with ThreadPoolExecutor(max_workers=min(6, len(saved_navigation_nodes) or 1)) as executor:
        metadata_list = list(executor.map(lambda node: node_runtime_metadata(node, timeout=0.35), saved_navigation_nodes))
    for node, metadata in zip(saved_navigation_nodes, metadata_list):
        node_url = str(node.get("url") or "").strip()
        node_id = str(node.get("node_id") or "").strip()
        version = str(metadata.get("version") or node.get("version") or "").strip()
        nodes.append({
            "name": str(node.get("name") or node_url or "Systemd Gui node").strip(),
            "url": _node_switch_url(node_url),
            "absolute_url": node_url,
            "node_id": node_id,
            "version": version,
            "ssh_indicators": _node_ssh_indicators(node) + _node_api_indicators(node),
            "current": False,
        })
    nodes = sorted(nodes, key=lambda item: str(item.get("name") or "").lower())
    return {"current": current, "nodes": nodes}


def _node_switch_url(base_url: str) -> str:
    path = "/"
    if request and request.method == "GET" and str(request.endpoint or "") not in {"login", "static"}:
        path = request.path or "/"
        query = request.query_string.decode("utf-8", "ignore")
        if query:
            path = f"{path}?{query}"
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return path
    return f"{base}{path if path.startswith('/') else '/' + path}"


def _nav_node_url_key(url: str) -> str:
    value = url.strip().rstrip("/").lower()
    if not value:
        return ""
    if "://" not in value:
        value = f"http://{value}"
    return value


def _node_ssh_indicators(node: dict[str, object]) -> list[dict[str, str]]:
    indicators = []
    if str(node.get("ssh_user") or "").strip():
        indicators.append({"label": "U", "title": "SSH user is saved"})
    if str(node.get("ssh_password") or ""):
        indicators.append({"label": "P", "title": "SSH password is saved"})
    if str(node.get("ssh_key_path") or "").strip():
        indicators.append({"label": "K", "title": "SSH key path is saved"})
    return indicators


def _node_api_indicators(node: dict[str, object]) -> list[dict[str, str]]:
    indicators = []
    if str(node.get("api_token") or "").strip():
        indicators.append({"label": "T", "title": "Remote API token is saved"})
    return indicators


def _upsert_node(nodes: list[dict[str, object]], node: dict[str, object]) -> list[dict[str, object]]:
    node_id = str(node.get("node_id") or "")
    node_url = str(node.get("url") or "").rstrip("/").lower()
    for index, existing in enumerate(nodes):
        existing_id = str(existing.get("node_id") or "")
        existing_url = str(existing.get("url") or "").rstrip("/").lower()
        if (node_id and existing_id == node_id) or (node_url and existing_url == node_url):
            merged = {**existing, **node, "id": existing.get("id") or node.get("id")}
            nodes[index] = merged
            return nodes
    return [*nodes, node]


def _quick_shell_bin(app: Flask) -> Path:
    return Path(app.config["QUICK_SHELL_BIN"])


def _json_download(payload: dict[str, object], filename: str) -> Response:
    content = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    return Response(
        content,
        mimetype="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{_download_slug(filename, keep_extension=True)}"'},
    )


def _download_slug(value: str, keep_extension: bool = False) -> str:
    allowed = []
    for char in value.strip():
        if char.isalnum() or char in {"-", "_"} or (keep_extension and char == "."):
            allowed.append(char)
        elif char.isspace():
            allowed.append("-")
    slug = "".join(allowed).strip("-._")
    if not slug:
        slug = "quick-shell-export"
    if keep_extension:
        return slug
    return slug[:80]


def _quick_shell_item_from_form() -> dict[str, object]:
    item_type = request.form.get("type", "command").strip()
    item: dict[str, object] = {
        "type": item_type,
        "name": request.form.get("name", "").strip(),
        "enabled": request.form.get("enabled") == "1",
    }
    if item_type == "category":
        item["items"] = []
    elif item_type == "sequence":
        commands = request.form.get("commands", "").strip()
        if not any(line.strip() and not line.strip().startswith("#") for line in commands.splitlines()):
            raise ValueError("Sequences need at least one command line.")
        item["commands"] = commands
        item["confirm"] = request.form.get("confirm") == "1"
        item["confirm_each"] = request.form.get("confirm_each") == "1"
        item["print_comments"] = request.form.get("print_comments") == "1"
        item["stop_on_error"] = request.form.get("stop_on_error") == "1"
        item["show_menu_after"] = request.form.get("show_menu_after") == "1"
    else:
        command = request.form.get("command", "").strip()
        if not command:
            raise ValueError("Commands need a command line.")
        item["command"] = command
        item["confirm"] = request.form.get("confirm") == "1"
        item["show_menu_after"] = request.form.get("show_menu_after") == "1"
    return item


def _quick_shell_category_options(data: dict[str, object]) -> list[dict[str, object]]:
    options: list[dict[str, object]] = [{"path": "", "label": "Root category", "depth": 0}]
    for entry in flatten_entries(list(data.get("items") or [])):
        if entry.item.get("type") != "category":
            continue
        options.append({"path": entry.path, "label": entry_label(entry.item), "depth": entry.depth + 1})
    return options


def _quick_shell_remote_import_nodes(nodes_data: dict[str, object]) -> list[dict[str, object]]:
    nodes = []
    for raw_node in nodes_data.get("nodes") if isinstance(nodes_data.get("nodes"), list) else []:
        if not isinstance(raw_node, dict):
            continue
        node = normalize_node(raw_node)
        if str(node.get("api_token") or "").strip():
            nodes.append(node)
    return sorted(nodes, key=lambda item: str(item.get("name") or "Remote node").lower())


def _saved_node_by_id(nodes_data: dict[str, object], node_id: str) -> dict[str, object] | None:
    for raw_node in nodes_data.get("nodes") if isinstance(nodes_data.get("nodes"), list) else []:
        if not isinstance(raw_node, dict):
            continue
        node = normalize_node(raw_node)
        if str(node.get("id") or "") == node_id:
            return node
    return None


def _finish_quick_shell_import(app: Flask, payload: dict[str, object], imported_items: list[dict[str, object]], source_label: str) -> Response:
    if not imported_items:
        flash(f"{source_label} does not contain any entries.", "error")
        return redirect(url_for("quick_shell", tab="transfer"))

    data = read_quick_shell(_quick_shell_path(app))
    mode = request.form.get("import_mode", "add_to_target")
    target_path = request.form.get("target_path", "").strip()
    duplicate_mode = request.form.get("duplicate_mode", "replace_conflicts")
    backup_path = None
    try:
        if request.form.get("backup_current") == "1":
            backup_path = create_quick_shell_backup(_quick_shell_path(app), _quick_shell_backup_dir(app), "Before Quick Shell import")
        data, stats = import_quick_shell_items(data, imported_items, target_path, mode, duplicate_mode)
        if mode == "replace_all":
            data["settings"] = quick_shell_payload_settings(payload)
        write_quick_shell(_quick_shell_path(app), data)
    except (OSError, ValueError) as exc:
        flash(f"{source_label} failed: {exc}", "error")
        return redirect(url_for("quick_shell", tab="transfer"))

    backup_note = f" Backup created: {backup_path}." if backup_path else ""
    flash(
        f"{source_label} completed. Imported: {stats['imported']}, replaced: {stats.get('replaced', 0)}, "
        f"renamed: {stats['renamed']}, skipped: {stats['skipped']}.{backup_note}",
        "success",
    )
    next_path = "" if mode == "replace_all" else target_path
    return redirect(url_for("quick_shell", tab="menu", path=next_path))


def _quick_shell_parent_path(item_path: str) -> str:
    parts = item_path.split(".")
    if len(parts) <= 1:
        return ""
    return ".".join(parts[:-1])


def _quick_shell_breadcrumbs(data: dict[str, object], item_path: str) -> list[dict[str, str]]:
    breadcrumbs = [{"label": "Root", "path": ""}]
    parts: list[str] = []
    for part in item_path.split(".") if item_path else []:
        parts.append(part)
        path = ".".join(parts)
        try:
            item = item_for_path(data, path)
        except ValueError:
            break
        breadcrumbs.append({"label": entry_label(item), "path": path})
    return breadcrumbs


def read_service_notes(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items() if isinstance(value, str)}


def write_service_notes(path: Path, notes: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(notes, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _service_stats(services: list[dict[str, str | bool]]) -> dict[str, int]:
    return {
        "total": len(services),
        "active_count": sum(1 for item in services if item["active"] == "active"),
        "failed_count": sum(1 for item in services if item["active"] == "failed"),
        "protected_count": sum(1 for item in services if item["protected"]),
    }


def _service_filter_options(services: list[dict[str, str | bool]]) -> dict[str, list[str]]:
    return {
        "states": sorted({str(item["active"]) for item in services if item.get("active")}),
        "subs": sorted({str(item["sub"]) for item in services if item.get("sub") and item["sub"] != "-"}),
        "autostarts": sorted({str(item["enabled"]) for item in services if item.get("enabled")}),
    }


def _backup_dir(app: Flask) -> Path:
    return Path(app.config["DATA_DIR"]) / "unit-backups"


def _drop_in_backup_dir(app: Flask) -> Path:
    return Path(app.config["DATA_DIR"]) / "drop-in-backups"


def _pending_override_reloads() -> set[str]:
    values = session.get("override_reload_pending", [])
    if not isinstance(values, list):
        return set()
    return {item for item in values if isinstance(item, str) and valid_service_name(item)}


def _write_pending_override_reloads(values: set[str]) -> None:
    if values:
        session["override_reload_pending"] = sorted(values)
    else:
        session.pop("override_reload_pending", None)


def _mark_override_reload_pending(name: str) -> None:
    values = _pending_override_reloads()
    values.add(name)
    _write_pending_override_reloads(values)


def _clear_override_reload_pending(name: str) -> None:
    values = _pending_override_reloads()
    values.discard(name)
    _write_pending_override_reloads(values)


def _pending_override_restarts() -> set[str]:
    values = session.get("override_restart_pending", [])
    if not isinstance(values, list):
        return set()
    return {item for item in values if isinstance(item, str) and valid_service_name(item)}


def _write_pending_override_restarts(values: set[str]) -> None:
    if values:
        session["override_restart_pending"] = sorted(values)
    else:
        session.pop("override_restart_pending", None)


def _mark_override_restart_pending(name: str) -> None:
    values = _pending_override_restarts()
    values.add(name)
    _write_pending_override_restarts(values)


def _clear_override_restart_pending(name: str) -> None:
    values = _pending_override_restarts()
    values.discard(name)
    _write_pending_override_restarts(values)


def _service_metadata(info: dict[str, object]) -> dict[str, str]:
    fragment_path = str(info.get("fragment_path") or "")
    metadata = {
        "unit_file_modified": "",
        "unit_file_metadata_changed": "",
        "active_since": str(info.get("active_enter_timestamp") or ""),
        "state_changed": str(info.get("state_change_timestamp") or ""),
    }
    if fragment_path:
        try:
            stat = Path(fragment_path).stat()
        except OSError:
            return metadata
        metadata["unit_file_modified"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        metadata["unit_file_metadata_changed"] = datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S")
    return metadata


def _app_root(app: Flask) -> Path:
    return Path(app.root_path).parent


def _asset_version(app: Flask) -> str:
    static_folder = Path(app.static_folder or "")
    newest_mtime = 0
    for filename in ("styles.css", "app.js"):
        try:
            newest_mtime = max(newest_mtime, int((static_folder / filename).stat().st_mtime))
        except OSError:
            continue
    return f"{APP_VERSION}-{newest_mtime}"


def _update_result_dict(result) -> dict[str, object]:
    return {
        "ok": result.ok,
        "message": result.message,
        "details": result.details,
        "backup_path": str(result.backup_path) if result.backup_path else "",
    }


def _sync_settings_from_env(app: Flask) -> None:
    env_values = _read_env_file(Path(app.config["ENV_FILE"]))
    if not env_values:
        return
    if "SYSTEMD_GUI_PASSWORD" in env_values:
        app.config["ADMIN_PASSWORD"] = env_values["SYSTEMD_GUI_PASSWORD"]
    if "SYSTEMD_GUI_ALLOW_PROTECTED" in env_values:
        app.config["ALLOW_PROTECTED"] = env_values["SYSTEMD_GUI_ALLOW_PROTECTED"] == "1"
    if env_values.get("SYSTEMD_GUI_SERVICE"):
        app.config["SYSTEMD_GUI_SERVICE"] = env_values["SYSTEMD_GUI_SERVICE"]


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _update_env_value(path: Path, key: str, value: str, backup_dir: Path | None = None) -> Path | None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    backup_path = _backup_file(path, backup_dir) if backup_dir else None
    replacement = f"{key}={value}"
    updated = False
    output: list[str] = []

    for line in lines:
        if line.startswith(f"{key}="):
            output.append(replacement)
            updated = True
        else:
            output.append(line)

    if not updated:
        output.append(replacement)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(output) + "\n", encoding="utf-8")
    return backup_path


def _backup_file(path: Path, backup_dir: Path | None) -> Path | None:
    if not backup_dir or not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{path.name}.{stamp}.bak"
    backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup_path


def _safe_next_url(value: str) -> str:
    if value.startswith("/") and not value.startswith("//"):
        return value
    return ""


def _valid_or_flash(name: str) -> bool:
    if valid_service_name(name):
        return True
    flash("Only .service units are supported.", "error")
    return False


def _log_line_count(value: str) -> int:
    try:
        lines = int(value)
    except (TypeError, ValueError):
        return 200
    return lines if lines in {50, 100, 200, 500, 1000} else 200


def _log_priority(value: str) -> str:
    value = (value or "all").strip()
    allowed = {option[0] for option in LOG_PRIORITY_OPTIONS}
    return value if value in allowed else "all"


def _log_wrap(value: str) -> bool:
    return value != "0"


def _selected_log_nodes() -> list[str]:
    selected = [value for value in request.args.getlist("node") if value]
    return selected or ["local"]


def _log_node_options(app: Flask, selected: list[str]) -> list[dict[str, object]]:
    data = read_nodes(_nodes_path(app))
    settings = data.get("settings") or {}
    local_log_label = str(settings.get("node_name") or APP_NAME)
    options = [{
        "id": "local",
        "name": "Local",
        "label": "This node",
        "log_label": local_log_label,
        "selected": "local" in selected,
        "api_ok": True,
        "local": True,
        "log_color_class": "log-node-local",
    }]
    raw_nodes = data.get("nodes") if isinstance(data.get("nodes"), list) else []
    saved_nodes = [node for node in raw_nodes if isinstance(node, dict)]
    saved_nodes.sort(key=lambda item: str(item.get("name") or "Remote node").lower())
    for index, node in enumerate(saved_nodes):
        if not isinstance(node, dict):
            continue
        token_saved = bool(str(node.get("api_token") or "").strip())
        options.append({
            "id": str(node.get("id") or ""),
            "name": str(node.get("name") or "Remote node"),
            "label": str(node.get("name") or "Remote node"),
            "log_label": str(node.get("name") or "Remote node"),
            "url": str(node.get("url") or ""),
            "version": str(node.get("version") or ""),
            "selected": str(node.get("id") or "") in selected,
            "api_ok": token_saved,
            "disabled": not token_saved,
            "local": False,
            "log_color_class": _log_node_color_class(index),
        })
    return options


def _combined_journal_logs(
    app: Flask,
    service: str,
    display_lines: int,
    per_node_lines: int,
    priority: str,
    selected_nodes: list[str],
) -> tuple[CommandResult, list[dict[str, object]], list[dict[str, object]]]:
    options = _log_node_options(app, selected_nodes)
    color_by_id = {str(option.get("id") or ""): str(option.get("log_color_class") or "") for option in options}
    loaded_by_id = {str(option.get("id") or ""): 0 for option in options}
    status_by_id = {str(option.get("id") or ""): "ok" for option in options}
    message_by_id = {str(option.get("id") or ""): "" for option in options}
    selected_set = set(selected_nodes)
    entries: list[dict[str, object]] = []
    ok = True

    if "local" in selected_set:
        data = read_nodes(_nodes_path(app))
        settings = data.get("settings") or {}
        local_node = {
            "id": settings.get("node_id", "local") or "local",
            "name": settings.get("node_name", APP_NAME) or APP_NAME,
            "version": APP_VERSION,
            "remote": False,
            "log_color_class": color_by_id.get("local", "log-node-local"),
        }
        local_logs = run_journalctl_entries(service, per_node_lines, priority)
        ok = ok and local_logs.ok
        loaded_by_id["local"] = len(local_logs.entries)
        if not local_logs.ok:
            status_by_id["local"] = "error"
            message_by_id["local"] = local_logs.output or "Local journalctl failed."
        entries.extend(_decorate_log_entry(entry, local_node) for entry in local_logs.entries)
        if not local_logs.ok and not local_logs.entries:
            entries.append(_log_error_entry(local_node, local_logs.output or "Local journalctl failed."))

    data = read_nodes(_nodes_path(app))
    for node in data.get("nodes") if isinstance(data.get("nodes"), list) else []:
        if not isinstance(node, dict) or str(node.get("id") or "") not in selected_set:
            continue
        result = fetch_remote_logs(node, service, per_node_lines, priority)
        ok = ok and result.ok
        option_id = str(node.get("id") or "")
        loaded_by_id[option_id] = len(result.entries)
        if not result.ok:
            status_by_id[option_id] = "error"
            message_by_id[option_id] = result.message
        remote_node = {**result.node, "id": option_id, "log_color_class": color_by_id.get(option_id, "")}
        if result.entries:
            entries.extend(_decorate_log_entry(entry, remote_node) for entry in result.entries)
        if not result.ok:
            entries.append(_log_error_entry(remote_node, result.message))

    newest = sorted(entries, key=lambda entry: int(entry.get("timestamp_sort") or 0), reverse=True)[:display_lines]
    visible_entries = sorted(newest, key=lambda entry: int(entry.get("timestamp_sort") or 0))
    output = "\n".join(str(entry.get("text") or entry.get("formatted") or entry.get("message") or "") for entry in visible_entries)
    for option in options:
        option_id = str(option.get("id") or "")
        option["loaded_count"] = loaded_by_id.get(option_id, 0)
        option["log_status"] = status_by_id.get(option_id, "ok")
        option["log_status_message"] = message_by_id.get(option_id, "")
    return CommandResult(ok, output, 0 if ok else 1), visible_entries, options


def _remote_docker_overview(app: Flask) -> list[dict[str, object]]:
    nodes = _remote_docker_nodes(app)
    if not nodes:
        return []

    def load(node: dict[str, object]):
        return fetch_remote_docker_containers(node)

    with ThreadPoolExecutor(max_workers=min(6, len(nodes))) as executor:
        results = list(executor.map(load, nodes))

    return [_remote_docker_payload(node, result) for node, result in zip(nodes, results)]


def _remote_docker_nodes(app: Flask) -> list[dict[str, object]]:
    data = read_nodes(_nodes_path(app))
    raw_nodes = data.get("nodes") if isinstance(data.get("nodes"), list) else []
    nodes = [node for node in raw_nodes if isinstance(node, dict)]
    nodes.sort(key=lambda item: str(item.get("name") or "Remote node").lower())
    return nodes


def _remote_docker_node(app: Flask, node_id: str) -> dict[str, object] | None:
    for node in _remote_docker_nodes(app):
        if str(node.get("id") or "") == node_id:
            return node
    return None


def _remote_docker_result(node: dict[str, object]) -> dict[str, object]:
    return _remote_docker_payload(node, fetch_remote_docker_containers(node))


def _remote_docker_payload(node: dict[str, object], result) -> dict[str, object]:
    containers = list(result.containers)
    configured_name = str(node.get("name") or "").strip()
    node_info = {
        **result.node,
        "id": str(node.get("id") or result.node.get("id") or ""),
        "name": configured_name or str(result.node.get("name") or "Remote node"),
    }
    if not str(node_info.get("version") or "").strip():
        metadata = node_runtime_metadata(node, timeout=0.7)
        version = str(metadata.get("version") or "").strip()
        if version:
            node_info["version"] = version
    return {
        "node": node_info,
        "ok": result.ok,
        "message": result.message,
        "status": result.status,
        "containers": containers,
        "counts": {
            "total": len(containers),
            "running": sum(1 for item in containers if item.get("state") == "running"),
            "exited": sum(1 for item in containers if item.get("state") == "exited"),
        },
    }


def _decorate_log_entry(entry: dict[str, object], node: dict[str, object]) -> dict[str, object]:
    node_name = str(node.get("name") or "Node")
    priority = str(entry.get("priority") or "UNKNOWN").upper()
    formatted = str(entry.get("formatted") or entry.get("message") or "")
    return {
        **entry,
        "node": {
            "id": str(node.get("id") or ""),
            "name": node_name,
            "version": str(node.get("version") or ""),
            "remote": bool(node.get("remote")),
            "color_class": str(node.get("log_color_class") or ""),
        },
        "node_label": node_name,
        "node_color_class": str(node.get("log_color_class") or ""),
        "level_class": _log_level_class(priority),
        "text": f"[{node_name}] {formatted}",
    }


def _log_error_entry(node: dict[str, object], message: str) -> dict[str, object]:
    node_name = str(node.get("name") or "Node")
    return {
        "timestamp": "-",
        "timestamp_sort": 0,
        "host": "-",
        "process": "systemd-gui",
        "unit": "",
        "priority": "ERROR",
        "message": message,
        "formatted": f"- - systemd-gui: [ERROR] {message}",
        "node": {
            "id": str(node.get("id") or ""),
            "name": node_name,
            "remote": bool(node.get("remote")),
            "color_class": str(node.get("log_color_class") or ""),
        },
        "node_label": node_name,
        "node_color_class": str(node.get("log_color_class") or ""),
        "level_class": "error",
        "text": f"[{node_name}] - - systemd-gui: [ERROR] {message}",
    }


def _log_node_color_class(index: int) -> str:
    return f"log-node-color-{index % 10 + 1}"


def _log_level_class(priority: str) -> str:
    value = priority.lower()
    if value == "critical":
        return "critical"
    if value == "error":
        return "error"
    return value


def _journalctl_label(service: str = "", priority: str = "all") -> str:
    parts = ["journalctl"]
    if service:
        parts.extend(["-u", service])
    if priority and priority != "all":
        parts.extend(["-p", priority])
    return " ".join(parts)


def _log_refresh_enabled(refresh_value: str | None, interval_value: str | None) -> bool:
    if refresh_value == "1":
        return True
    if refresh_value == "0" or interval_value in {"", "off", "0", None}:
        return False
    try:
        return int(str(interval_value)) in {1, 2, 5, 10, 30}
    except (TypeError, ValueError):
        return False


def _log_refresh_interval(value: str) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return 5
    return seconds if seconds in {1, 2, 5, 10, 30} else 5


def _blocked_protected(app: Flask, name: str) -> bool:
    if is_protected_service(name) and not app.config["ALLOW_PROTECTED"]:
        flash("This service is protected. Actions and editing are blocked by default.", "error")
        return True
    return False


def _blocked_template(name: str) -> bool:
    if is_template_unit(name):
        flash("Template units are blueprints. Open a concrete instance before running service actions.", "error")
        return True
    return False


def _service_action_states(app: Flask, info: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        action: {
            "disabled": bool(_action_block_reason(app, info, action)),
            "help": _action_block_reason(app, info, action) or ACTION_HELP[action],
        }
        for action in ["start", "stop", "restart", "reload", "enable", "disable"]
    }


def _action_block_reason(app: Flask, info: dict[str, object], action: str) -> str:
    name = str(info.get("name") or "")
    if not bool(info.get("available")):
        return "This service was not found by systemd. Check the service name or return to the service list."
    if bool(info.get("protected")) and not app.config["ALLOW_PROTECTED"]:
        return "This service is protected. Actions are blocked by default to avoid losing access or breaking core system functions."
    if bool(info.get("template_unit")):
        return "Template units are blueprints. Use a concrete instance before running this action."
    unit_file_state = str(info.get("enabled") or "unknown")
    if unit_file_state in BLOCKED_UNIT_FILE_STATES:
        return f"{unit_file_state}: this unit-file state is blocked from actions in Systemd Gui."
    if action in AUTOSTART_ACTIONS and unit_file_state in NO_AUTOSTART_STATES:
        return f"{unit_file_state}: this unit cannot be enabled or disabled directly. It may still be startable manually or by another unit."
    if name.endswith("@.service"):
        return "Template units are blueprints. Use a concrete instance before running this action."
    return ""


def _editable(name: str) -> bool:
    try:
        read_editable_unit(name)
        return True
    except (OSError, ValueError):
        return False

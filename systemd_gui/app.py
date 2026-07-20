from __future__ import annotations

import json
import os
import secrets
import shlex
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, flash, redirect, render_template, request, session, url_for

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
    item_for_path,
    list_quick_shell_backups,
    move_item,
    move_item_to_position,
    quick_shell_export_payload,
    quick_shell_payload_items,
    quick_shell_payload_settings,
    quick_shell_helper_status,
    read_quick_shell_backup,
    read_quick_shell,
    remove_bash_history_timestamps,
    remove_shell_integration,
    restore_quick_shell_backup,
    shell_integration_statuses,
    update_item,
    write_quick_shell,
)
from .systemd import (
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
    run_journalctl,
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
        QUICK_SHELL_BIN=Path(os.environ.get("SYSTEMD_GUI_QS_BIN", "/usr/local/bin/qs")),
    )
    _sync_settings_from_env(app)

    @app.before_request
    def require_login_and_csrf():
        if request.endpoint in {"login", "login_post", "static"}:
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
        return {
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "repo_url": REPO_URL,
            "csrf_token": session["csrf_token"],
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

    @app.get("/quick-shell")
    def quick_shell():
        data = read_quick_shell(_quick_shell_path(app))
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
            shell_integrations=shell_integration_statuses(_quick_shell_bin(app)),
            bash_history_status=bash_history_timestamp_status(),
            quick_shell_settings=data.get("settings") or {},
            quick_shell_data=data,
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
        try:
            update_item(data, item_path, _quick_shell_item_from_form())
            write_quick_shell(_quick_shell_path(app), data)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("quick_shell", tab="menu", path=parent_path))
        flash("Quick Shell entry saved.", "success")
        return redirect(url_for("quick_shell", tab="menu", path=parent_path))

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
        if not imported_items:
            flash("Import file does not contain any entries.", "error")
            return redirect(url_for("quick_shell", tab="transfer"))

        data = read_quick_shell(_quick_shell_path(app))
        mode = request.form.get("import_mode", "add_to_target")
        target_path = request.form.get("target_path", "").strip()
        duplicate_mode = request.form.get("duplicate_mode", "rename_conflicts")
        backup_path = None
        try:
            if request.form.get("backup_current") == "1":
                backup_path = create_quick_shell_backup(_quick_shell_path(app), _quick_shell_backup_dir(app), "Before Quick Shell import")
            data, stats = import_quick_shell_items(data, imported_items, target_path, mode, duplicate_mode)
            if mode == "replace_all":
                data["settings"] = quick_shell_payload_settings(payload)
            write_quick_shell(_quick_shell_path(app), data)
        except (OSError, ValueError) as exc:
            flash(f"Import failed: {exc}", "error")
            return redirect(url_for("quick_shell", tab="transfer"))

        backup_note = f" Backup created: {backup_path}." if backup_path else ""
        flash(f"Import completed. Imported: {stats['imported']}, renamed: {stats['renamed']}, skipped: {stats['skipped']}.{backup_note}", "success")
        next_path = "" if mode == "replace_all" else target_path
        return redirect(url_for("quick_shell", tab="menu", path=next_path))

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
        log_refresh = request.args.get("refresh") == "1"
        log_refresh_interval = _log_refresh_interval(request.args.get("interval", "5"))
        info = service_info(name)
        content = unit_content(name)
        original_content = unit_fragment_content(str(info.get("fragment_path") or ""))
        drop_in_paths = list(dict.fromkeys([
            *list(info.get("drop_in_path_list") or []),
            *list(info.get("local_drop_in_paths") or []),
        ]))
        flattened_unit = flattened_unit_preview(original_content, drop_in_paths) if original_content else {"lines": [], "text": ""}
        logs = run_journalctl(name, log_lines)
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
            log_refresh=log_refresh,
            log_refresh_interval=log_refresh_interval,
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
        logs = run_journalctl(name, log_lines)
        return render_template("_service_logs.html", logs=logs)

    @app.get("/service/<name>/logs")
    def service_logs_window(name: str):
        if not _valid_or_flash(name):
            return redirect(url_for("index"))
        log_lines = _log_line_count(request.args.get("lines", "200"))
        log_refresh = request.args.get("refresh") == "1"
        log_refresh_interval = _log_refresh_interval(request.args.get("interval", "5"))
        info = service_info(name)
        logs = run_journalctl(name, log_lines)
        return render_template(
            "service_logs_window.html",
            info=info,
            log_lines=log_lines,
            log_refresh=log_refresh,
            log_refresh_interval=log_refresh_interval,
            logs=logs,
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
        systemctl = shutil.which("systemctl")
        if not systemctl:
            flash("systemctl is not available in this environment.", "error")
            return redirect(request.referrer or url_for("index"))
        service = app.config["SYSTEMD_GUI_SERVICE"]
        command = f"sleep 1; exec {shlex.quote(systemctl)} restart {shlex.quote(service)}"
        subprocess.Popen(["/bin/sh", "-c", command], start_new_session=True)
        flash("Systemd Gui restart requested. Reload the page in a few seconds.", "success")
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
    options: list[dict[str, object]] = [{"path": "", "label": "Root menu", "depth": 0}]
    for entry in flatten_entries(list(data.get("items") or [])):
        if entry.item.get("type") != "category":
            continue
        options.append({"path": entry.path, "label": entry_label(entry.item), "depth": entry.depth + 1})
    return options


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

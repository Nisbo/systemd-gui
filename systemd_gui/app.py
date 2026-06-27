from __future__ import annotations

import os
import secrets
import shlex
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, session, url_for

from .systemd import (
    is_protected_service,
    journalctl_available,
    list_unit_backups,
    list_services,
    read_editable_unit,
    read_favorites,
    read_unit_backup,
    run_journalctl,
    run_systemctl,
    service_info,
    systemctl_available,
    unit_content,
    valid_service_name,
    write_editable_unit,
    write_favorites,
)
from .updater import (
    check_for_update,
    git_update_state,
    update_from_git,
    update_from_release,
    update_from_zip,
    update_status_to_dict,
)
from .version import APP_NAME, APP_VERSION, REPO_URL

SERVICE_ACTIONS = {"start", "stop", "restart", "reload", "enable", "disable"}
RUNTIME_ACTIONS = {"start", "stop", "restart", "reload"}
AUTOSTART_ACTIONS = {"enable", "disable"}


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
        favorites = read_favorites(_favorites_path(app))
        services = list_services(query, favorites)
        stats = _service_stats(services)
        return render_template(
            "index.html",
            services=services,
            query=query,
            **stats,
        )

    @app.get("/services/fragment")
    def services_fragment():
        query = request.args.get("q", "").strip()
        favorites = read_favorites(_favorites_path(app))
        services = list_services(query, favorites)
        return render_template("_services_fragment.html", services=services, **_service_stats(services))

    @app.get("/settings")
    def settings():
        active_tab = request.args.get("tab", "general")
        if active_tab not in {"general", "security", "updates"}:
            active_tab = "general"
        return render_template(
            "settings.html",
            active_tab=active_tab,
            env_file=app.config["ENV_FILE"],
            password_enabled=bool(app.config["ADMIN_PASSWORD"]),
            systemd_gui_service=app.config["SYSTEMD_GUI_SERVICE"],
            git_state=git_update_state(_app_root(app)),
            update_status=session.pop("update_status", None),
            update_result=session.pop("update_result", None),
            app_update_pending_restart=session.get("app_update_pending_restart", False),
        )

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
        if active_tab not in {"unit", "logs", "backups"}:
            active_tab = "unit"
        log_lines = _log_line_count(request.args.get("lines", "200"))
        log_refresh = request.args.get("refresh") == "1"
        log_refresh_interval = _log_refresh_interval(request.args.get("interval", "5"))
        info = service_info(name)
        content = unit_content(name)
        logs = run_journalctl(name, log_lines)
        editable = _editable(name)
        backups = list_unit_backups(name, _backup_dir(app))
        return render_template(
            "service_detail.html",
            active_tab=active_tab,
            log_lines=log_lines,
            log_refresh=log_refresh,
            log_refresh_interval=log_refresh_interval,
            info=info,
            content=content,
            logs=logs,
            editable=editable,
            backups=backups,
        )

    @app.get("/service/<name>/logs/fragment")
    def service_logs_fragment(name: str):
        if not valid_service_name(name):
            return "Only .service units are supported.", 400
        log_lines = _log_line_count(request.args.get("lines", "200"))
        logs = run_journalctl(name, log_lines)
        return render_template("_service_logs.html", logs=logs)

    @app.post("/service/<name>/<action>")
    def service_action(name: str, action: str):
        if not _valid_or_flash(name):
            return redirect(url_for("index"))
        if action not in SERVICE_ACTIONS:
            flash("Unknown service action.", "error")
            return redirect(url_for("service_detail", name=name))
        if _blocked_protected(app, name):
            return redirect(url_for("service_detail", name=name))

        if action in RUNTIME_ACTIONS:
            result = run_systemctl([action, name])
        elif action in AUTOSTART_ACTIONS:
            result = run_systemctl([action, name])
        else:
            result = run_systemctl([action, name])
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
        return render_template("service_backup.html", name=name, backup_name=backup_name, path=path, content=content)

    @app.post("/daemon-reload")
    def daemon_reload():
        result = run_systemctl(["daemon-reload"])
        flash(result.output or "systemctl daemon-reload completed.", "success" if result.ok else "error")
        return redirect(request.referrer or url_for("index"))

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


def _service_stats(services: list[dict[str, str | bool]]) -> dict[str, int]:
    return {
        "total": len(services),
        "active_count": sum(1 for item in services if item["active"] == "active"),
        "failed_count": sum(1 for item in services if item["active"] == "failed"),
        "protected_count": sum(1 for item in services if item["protected"]),
    }


def _backup_dir(app: Flask) -> Path:
    return Path(app.config["DATA_DIR"]) / "unit-backups"


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


def _editable(name: str) -> bool:
    try:
        read_editable_unit(name)
        return True
    except (OSError, ValueError):
        return False

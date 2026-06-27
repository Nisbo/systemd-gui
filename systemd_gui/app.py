from __future__ import annotations

import os
import secrets
import shlex
import shutil
import subprocess
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, session, url_for

from .systemd import (
    is_protected_service,
    journalctl_available,
    list_services,
    read_editable_unit,
    read_favorites,
    run_journalctl,
    run_systemctl,
    service_info,
    systemctl_available,
    unit_content,
    valid_service_name,
    write_editable_unit,
    write_favorites,
)
from .version import APP_NAME, APP_VERSION, REPO_URL

SERVICE_ACTIONS = {"start", "stop", "restart", "reload", "enable", "disable"}
RUNTIME_ACTIONS = {"start", "stop", "restart", "reload"}
AUTOSTART_ACTIONS = {"enable", "disable"}


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SYSTEMD_GUI_SECRET", "dev-change-me"),
        ADMIN_PASSWORD=os.environ.get("SYSTEMD_GUI_PASSWORD", ""),
        ALLOW_PROTECTED=os.environ.get("SYSTEMD_GUI_ALLOW_PROTECTED", "0") == "1",
        DATA_DIR=Path(os.environ.get("SYSTEMD_GUI_DATA_DIR", "data")),
        ENV_FILE=Path(os.environ.get("SYSTEMD_GUI_ENV_FILE", "/etc/systemd-gui.env")),
        SYSTEMD_GUI_SERVICE=os.environ.get("SYSTEMD_GUI_SERVICE", "systemd-gui"),
    )

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
        return render_template(
            "index.html",
            services=services,
            query=query,
            total=len(services),
            active_count=sum(1 for item in services if item["active"] == "active"),
            failed_count=sum(1 for item in services if item["active"] == "failed"),
            protected_count=sum(1 for item in services if item["protected"]),
        )

    @app.get("/service/<name>")
    def service_detail(name: str):
        if not _valid_or_flash(name):
            return redirect(url_for("index"))
        info = service_info(name)
        content = unit_content(name)
        logs = run_journalctl(name, 200)
        editable = _editable(name)
        return render_template("service_detail.html", info=info, content=content, logs=logs, editable=editable)

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
        return render_template("service_edit.html", name=name, path=path, content=content)

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


def _valid_or_flash(name: str) -> bool:
    if valid_service_name(name):
        return True
    flash("Only .service units are supported.", "error")
    return False


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

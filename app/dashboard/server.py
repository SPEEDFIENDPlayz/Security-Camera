from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import wraps
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, session, url_for
from flask_wtf.csrf import CSRFProtect

from app.config import Settings
from app.database import Database
from app.storage.mounts import validate_mount
from app.live_view.manager import PreviewManager


def create_app(settings: Settings, db: Database) -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config.update(SECRET_KEY=settings.dashboard.get("session_secret", "change-me"), SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=bool(settings.dashboard.get("lan_enabled", False)), MAX_CONTENT_LENGTH=1024 * 1024)
    csrf = CSRFProtect(app)
    hasher = PasswordHasher()
    lan = bool(settings.dashboard.get("lan_enabled", False))
    previews = PreviewManager(settings)

    def safe_next(value: str | None) -> str:
        if not value:
            return url_for("index")
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or "\\" in value or not value.startswith("/") or value.startswith("//"):
            return url_for("index")
        return value

    def authenticated(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if lan and not session.get("authenticated"):
                return redirect(url_for("login", next=request.path))
            return fn(*args, **kwargs)
        return wrapped

    def reauthenticated(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not session.get("reauth_until") or datetime.fromisoformat(session["reauth_until"]) < datetime.now(UTC):
                return redirect(url_for("reauth", next=request.path))
            return fn(*args, **kwargs)
        return wrapped

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not lan: return redirect(url_for("index"))
        if request.method == "POST":
            ip = request.remote_addr or "unknown"
            with db.connect() as con:
                failure = con.execute("SELECT * FROM login_failures WHERE ip=?", (ip,)).fetchone()
            if failure and failure["blocked_until"] and datetime.fromisoformat(failure["blocked_until"]) > datetime.now(UTC):
                flash("Too many failed attempts; try again later", "error")
                return render_template("login.html", title="Sign in"), 429
            stored = settings.dashboard.get("admin_password_hash", "")
            try: ok = bool(stored) and hasher.verify(stored, request.form.get("password", ""))
            except Exception: ok = False
            if ok:
                with db.connect() as con: con.execute("DELETE FROM login_failures WHERE ip=?", (ip,))
                session.clear(); session["authenticated"] = True
                return redirect(safe_next(request.args.get("next")))
            failures = (failure["failures"] if failure else 0) + 1
            blocked = (datetime.now(UTC) + timedelta(minutes=15)).isoformat() if failures >= 5 else None
            with db.connect() as con:
                con.execute("""INSERT INTO login_failures(ip,failures,blocked_until,updated_at) VALUES(?,?,?,?)
                    ON CONFLICT(ip) DO UPDATE SET failures=excluded.failures,blocked_until=excluded.blocked_until,updated_at=excluded.updated_at""", (ip, failures, blocked, datetime.now(UTC).isoformat()))
            flash("Invalid credentials", "error")
        return render_template("login.html", title="Sign in")

    @app.route("/reauth", methods=["GET", "POST"])
    def reauth():
        if request.method == "POST":
            try: ok = hasher.verify(settings.dashboard.get("admin_password_hash", ""), request.form.get("password", ""))
            except Exception: ok = False
            if ok:
                session["reauth_until"] = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
                return redirect(safe_next(request.args.get("next")))
            flash("Invalid password", "error")
        return render_template("login.html", title="Confirm administrator password")

    @app.get("/")
    @authenticated
    def index():
        previews.reap()
        mounts = [validate_mount(item) for item in (settings.recording, settings.archive)]
        return render_template("index.html", mounts=mounts, clips=db.list_clips(10), config_cameras=settings.cameras, now=datetime.now(UTC))

    @app.get("/clips")
    @authenticated
    def clips():
        previews.reap()
        try:
            page = max(0, int(request.args.get("page", 0)))
        except ValueError:
            page = 0
        return render_template("clips.html", clips=db.list_clips(100, page * 100), page=page)

    @app.post("/clips/<clip_id>/archive")
    @authenticated
    def archive(clip_id: str):
        try: db.queue_archive(clip_id); flash("Archive transfer queued", "ok")
        except ValueError as exc: flash(str(exc), "error")
        return redirect(url_for("clips"))

    @app.post("/clips/<clip_id>/delete-archive")
    @authenticated
    @reauthenticated
    def delete_archive(clip_id: str):
        if request.form.get("confirm") != "DELETE": abort(400, "explicit DELETE confirmation required")
        try: db.queue_delete_archive(clip_id); flash("Archive deletion queued", "ok")
        except ValueError as exc: flash(str(exc), "error")
        return redirect(url_for("clips"))

    @app.get("/settings")
    @authenticated
    def settings_page():
        return render_template("settings.html", dashboard=settings.dashboard, cameras=settings.cameras)

    @app.post("/preview/<camera_id>/start")
    @authenticated
    def preview_start(camera_id: str):
        camera = next((item for item in settings.cameras if item.id == camera_id), None)
        if not camera: abort(404)
        try:
            previews.start(camera, int(request.form.get("timeout", settings.preview.get("default_timeout_seconds", 300))))
            return redirect(url_for("preview_view", camera_id=camera_id))
        except (RuntimeError, ValueError) as exc:
            flash(str(exc), "error"); return redirect(url_for("index"))

    @app.get("/preview/<camera_id>")
    @authenticated
    def preview_view(camera_id: str):
        previews.reap()
        if camera_id not in previews.active: abort(404)
        return render_template("preview.html", camera_id=camera_id)

    @app.post("/preview/<camera_id>/stop")
    @authenticated
    def preview_stop(camera_id: str):
        previews.stop(camera_id)
        return ("", 204)

    @app.get("/preview-files/<camera_id>/<path:name>")
    @authenticated
    def preview_file(camera_id: str, name: str):
        previews.reap()
        if camera_id not in previews.active or "/" in name or "\\" in name: abort(404)
        return send_from_directory(previews.active[camera_id].directory, name)

    return app

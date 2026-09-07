from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from app.config import MountConfig
from app.storage.mounts import validate_mount

CONFIG_PATH = Path("/etc/security-camera/config.toml")
SYSTEMD_ROOT = Path("/etc/systemd/system")
STORAGE_SERVICES = (
    "security-camera-recorder.service",
    "security-camera-archive.service",
    "security-camera-maintenance.service",
)


@dataclass(frozen=True)
class MountedDrive:
    label: str
    path: str
    mountpoint: str
    uuid: str
    size: str
    available: str
    read_only: bool


def _flatten_lsblk(nodes: list[dict]) -> list[dict]:
    result: list[dict] = []
    for node in nodes:
        result.append(node)
        result.extend(_flatten_lsblk(node.get("children", [])))
    return result


def discover_mounted_drives() -> list[MountedDrive]:
    """Return writable, non-root mounted filesystems suitable for setup."""
    result = subprocess.run(
        ["lsblk", "--json", "--output", "PATH,UUID,MOUNTPOINT,SIZE,FSAVAIL,RO"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=15,
    )
    drives: list[MountedDrive] = []
    for node in _flatten_lsblk(json.loads(result.stdout).get("blockdevices", [])):
        mountpoint, uuid = node.get("mountpoint"), node.get("uuid")
        if not mountpoint or mountpoint == "/" or not uuid or bool(node.get("ro")):
            continue
        drives.append(MountedDrive(
            label=f"{mountpoint} — {node.get('size', '?')} total, {node.get('fsavail', '?')} free",
            path=str(node.get("path", "")), mountpoint=mountpoint, uuid=uuid,
            size=str(node.get("size", "?")), available=str(node.get("fsavail", "?")), read_only=False,
        ))
    return drives


def build_rtsp_url(host: str, port: str, username: str, password: str, stream_path: str) -> str:
    host, port, stream_path = host.strip(), port.strip(), stream_path.strip()
    if not host or not port or not username or not password or not stream_path:
        raise ValueError("host, port, username, password, and main stream path are required")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("RTSP port must be between 1 and 65535")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"rtsp://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{port}/{stream_path.lstrip('/')}"


def probe_rtsp(ffprobe: str, url: str, transport: str = "tcp") -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-rtsp_transport", transport, "-rw_timeout", "10000000", "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "default=nw=1:nk=1", url],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if result.returncode == 0 and result.stdout.strip():
        return True, f"Connected: {result.stdout.strip()} video stream"
    return False, result.stderr.strip() or "Camera did not provide a video stream"


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _camera_url_parts(url: str) -> tuple[str, str, str, str]:
    parts = urlsplit(url)
    return parts.hostname or "", str(parts.port or 554), unquote(parts.username or ""), parts.path.lstrip("/")


def _write_config(content: str) -> None:
    import grp
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    previous = CONFIG_PATH.with_suffix(".toml.previous")
    if CONFIG_PATH.exists():
        shutil.copy2(CONFIG_PATH, previous)
    fd, name = tempfile.mkstemp(prefix="config.", suffix=".toml", dir=CONFIG_PATH.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush(); os.fsync(handle.fileno())
        from app.config import load
        load(temp)
        os.chown(temp, 0, grp.getgrnam("securitycam").gr_gid)
        os.chmod(temp, 0o640)
        os.replace(temp, CONFIG_PATH)
        directory_fd = os.open(CONFIG_PATH.parent, os.O_DIRECTORY)
        try: os.fsync(directory_fd)
        finally: os.close(directory_fd)
    finally:
        temp.unlink(missing_ok=True)


def _systemd_escape_path(path: str) -> str:
    return path.replace("\\", "\\\\").replace(" ", "\\x20").replace("\t", "\\x09").replace("\r", "\\r").replace("\n", "\\n")


def _storage_override_paths() -> list[Path]:
    return [SYSTEMD_ROOT / f"{service}.d" / "storage.conf" for service in STORAGE_SERVICES]


def _write_storage_overrides(recording: str, archive: str) -> None:
    content = "[Service]\nReadWritePaths=\nReadWritePaths=/var/lib/security-camera /srv/security {} {}\n".format(
        _systemd_escape_path(recording), _systemd_escape_path(archive)
    )
    for path in _storage_override_paths():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="storage.", suffix=".conf", dir=path.parent)
        temp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush(); os.fsync(handle.fileno())
            os.chmod(temp, 0o644)
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def _restore_file(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="restore.", suffix=path.suffix, dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush(); os.fsync(handle.fileno())
        if path == CONFIG_PATH:
            import grp
            os.chown(temp, 0, grp.getgrnam("securitycam").gr_gid)
            os.chmod(temp, 0o640)
        else:
            os.chmod(temp, 0o644)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class SetupWizard:
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import messagebox, ttk
        self.tk, self.messagebox, self.ttk = tk, messagebox, ttk
        self.root = tk.Tk(); self.root.title("Security Camera Setup"); self.root.geometry("760x560"); self.root.minsize(700, 500)
        self.page = 0; self.camera_tests = [False, False]; self.drives = discover_mounted_drives()
        self.raw = self._read_existing(); self.vars: dict[str, object] = {}
        self.content = ttk.Frame(self.root, padding=20); self.content.pack(fill="both", expand=True)
        controls = ttk.Frame(self.root, padding=(20, 0, 20, 20)); controls.pack(fill="x")
        self.back = ttk.Button(controls, text="Back", command=self._back); self.back.pack(side="left")
        self.next = ttk.Button(controls, text="Next", command=self._next); self.next.pack(side="right")
        self.status = ttk.Label(controls, foreground="#a00000"); self.status.pack(side="left", padx=16)
        self._init_vars(); self._render()

    def _read_existing(self) -> dict:
        import tomllib
        if not CONFIG_PATH.exists(): return {}
        try:
            with CONFIG_PATH.open("rb") as handle: return tomllib.load(handle)
        except Exception: return {}

    def _init_vars(self) -> None:
        tk = self.tk; raw = self.raw
        self.vars["recording"] = tk.StringVar(); self.vars["archive"] = tk.StringVar()
        storage = raw.get("storage", {})
        for key in ("recording", "archive"):
            selected = storage.get(key, {}).get("path", "")
            self.vars[key].set(next((drive.label for drive in self.drives if drive.mountpoint == selected), ""))
        dashboard = raw.get("dashboard", {})
        self.vars["lan"] = tk.BooleanVar(value=bool(dashboard.get("lan_enabled", False)))
        self.vars["bind"] = tk.StringVar(value=str(dashboard.get("bind", "127.0.0.1")))
        self.vars["port"] = tk.StringVar(value=str(dashboard.get("port", 8080)))
        self.vars["tls_cert"] = tk.StringVar(value=str(dashboard.get("tls_cert", "")))
        self.vars["tls_key"] = tk.StringVar(value=str(dashboard.get("tls_key", "")))
        for index, camera_id in enumerate(("door", "garage")):
            old = next((item for item in raw.get("cameras", []) if item.get("id") == camera_id), {})
            host, port, username, main_path = _camera_url_parts(old.get("main_url", "")) if old else ("", "554", "", "")
            _, _, _, sub_path = _camera_url_parts(old.get("sub_url", "")) if old.get("sub_url") else ("", "", "", "")
            prefix = f"camera{index}_"
            values = {"name": old.get("name", "Main Door Walkway" if index == 0 else "Outside Garage"), "host": host, "port": port, "username": username, "password": "", "main_path": main_path, "sub_path": sub_path, "transport": old.get("transport", "tcp")}
            for name, value in values.items(): self.vars[prefix + name] = tk.StringVar(value=value)
            for name in ("host", "port", "username", "password", "main_path", "sub_path", "transport"):
                self.vars[prefix + name].trace_add("write", lambda *_args, i=index: self._invalidate_camera(i))
        self.vars["dashboard_password"] = tk.StringVar(); self.vars["dashboard_password_confirm"] = tk.StringVar()

    def _invalidate_camera(self, index: int) -> None:
        self.camera_tests[index] = False

    def _clear(self) -> None:
        for child in self.content.winfo_children(): child.destroy()
        self.status.configure(text="")

    def _render(self) -> None:
        self._clear(); self.back.configure(state="normal" if self.page else "disabled")
        pages = [self._welcome, self._drive_page, lambda: self._camera_page(0), lambda: self._camera_page(1), self._dashboard_page, self._review]
        pages[self.page](); self.next.configure(text="Finish setup" if self.page == len(pages) - 1 else "Next")

    def _welcome(self) -> None:
        ttk = self.ttk
        ttk.Label(self.content, text="Security Camera Setup", font=("Sans", 20, "bold")).pack(anchor="w", pady=(0, 12))
        mode = "Reconfigure this appliance" if self.raw else "First-time installation"
        ttk.Label(self.content, text=mode, font=("Sans", 13, "bold")).pack(anchor="w")
        ttk.Label(self.content, justify="left", wraplength=680, text="This wizard configures two already-mounted drives and two RTSP cameras. It never formats, mounts, or changes Debian boot mounts. Both cameras must pass a short connection test before the recorder is enabled.").pack(anchor="w", pady=18)

    def _drive_page(self) -> None:
        ttk = self.ttk
        ttk.Label(self.content, text="Choose mounted drives", font=("Sans", 18, "bold")).pack(anchor="w")
        ttk.Label(self.content, text="Only writable, already-mounted non-root filesystems are shown. Debian must continue mounting these paths after reboot.", wraplength=680).pack(anchor="w", pady=(8, 18))
        values = [drive.label for drive in self.drives]
        if len(values) < 2: self.status.configure(text="Connect and mount two writable drives, then restart setup.")
        for key, title in (("recording", "2 TB recording drive"), ("archive", "Separate archive drive")):
            frame = ttk.LabelFrame(self.content, text=title, padding=12); frame.pack(fill="x", pady=8)
            ttk.Combobox(frame, textvariable=self.vars[key], values=values, state="readonly", width=82).pack(fill="x")

    def _camera_page(self, index: int) -> None:
        ttk = self.ttk; prefix = f"camera{index}_"; title = "Camera 1 — Main Door" if index == 0 else "Camera 2 — Outside Garage"
        ttk.Label(self.content, text=title, font=("Sans", 18, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        labels = [("name", "Camera name"), ("host", "IP address or hostname"), ("port", "RTSP port"), ("username", "Username"), ("password", "Password (blank keeps configured value)"), ("main_path", "Main stream path"), ("sub_path", "Sub-stream path (optional)"), ("transport", "Transport")]
        for row, (key, label) in enumerate(labels, 1):
            ttk.Label(self.content, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            show = "*" if key == "password" else ""
            entry = ttk.Entry(self.content, textvariable=self.vars[prefix + key], width=58, show=show)
            entry.grid(row=row, column=1, sticky="ew", pady=5)
        self.content.columnconfigure(1, weight=1)
        test = ttk.Button(self.content, text="Test camera connection", command=lambda: self._test_camera(index)); test.grid(row=len(labels) + 1, column=1, sticky="e", pady=16)
        state = "Passed" if self.camera_tests[index] else "Not tested"
        ttk.Label(self.content, text=f"Connection test: {state}").grid(row=len(labels) + 2, column=1, sticky="w")

    def _dashboard_page(self) -> None:
        ttk = self.ttk
        ttk.Label(self.content, text="Dashboard access", font=("Sans", 18, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Label(self.content, text="Administrator password").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(self.content, textvariable=self.vars["dashboard_password"], show="*", width=48).grid(row=1, column=1, sticky="w", pady=5)
        ttk.Label(self.content, text="Confirm password").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(self.content, textvariable=self.vars["dashboard_password_confirm"], show="*", width=48).grid(row=2, column=1, sticky="w", pady=5)
        ttk.Checkbutton(self.content, text="Allow dashboard access from trusted LAN devices", variable=self.vars["lan"]).grid(row=3, column=0, columnspan=2, sticky="w", pady=(14, 6))
        for row, (key, label) in enumerate((("bind", "Bind address"), ("port", "Port"), ("tls_cert", "TLS certificate path (required for LAN)"), ("tls_key", "TLS key path (required for LAN)")), 4):
            ttk.Label(self.content, text=label).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(self.content, textvariable=self.vars[key], width=48).grid(row=row, column=1, sticky="w", pady=5)

    def _review(self) -> None:
        ttk = self.ttk
        ttk.Label(self.content, text="Review and finish", font=("Sans", 18, "bold")).pack(anchor="w", pady=(0, 12))
        recording, archive = self._selected_drive("recording"), self._selected_drive("archive")
        text = f"Recording: {recording.mountpoint if recording else 'not selected'}\nArchive: {archive.mountpoint if archive else 'not selected'}\nCamera tests: {'passed' if all(self.camera_tests) else 'incomplete'}\n\nFinish writes protected configuration, enables the services, and starts recording."
        ttk.Label(self.content, text=text, justify="left").pack(anchor="w")

    def _selected_drive(self, key: str) -> MountedDrive | None:
        return next((drive for drive in self.drives if drive.label == self.vars[key].get()), None)

    def _test_camera(self, index: int) -> None:
        try:
            url = self._camera_url(index, main=True)
        except ValueError as exc:
            self.messagebox.showerror("Camera details", str(exc)); return
        ok, message = probe_rtsp("/usr/bin/ffprobe", url, self.vars[f"camera{index}_transport"].get())
        self.camera_tests[index] = ok
        if ok: self.messagebox.showinfo("Camera test passed", message)
        else: self.messagebox.showerror("Camera test failed", message)
        self._render()

    def _camera_url(self, index: int, main: bool) -> str:
        prefix = f"camera{index}_"; password = self.vars[prefix + "password"].get()
        if not password:
            old = next((item for item in self.raw.get("cameras", []) if item.get("id") == ("door" if index == 0 else "garage")), {})
            password = unquote(urlsplit(old.get("main_url", "")).password or "")
        path = self.vars[prefix + ("main_path" if main else "sub_path")].get()
        if not path: return ""
        return build_rtsp_url(self.vars[prefix + "host"].get(), self.vars[prefix + "port"].get(), self.vars[prefix + "username"].get(), password, path)

    def _next(self) -> None:
        if self.page == 1:
            recording, archive = self._selected_drive("recording"), self._selected_drive("archive")
            if not recording or not archive or recording.uuid == archive.uuid:
                self.status.configure(text="Choose two different mounted drives."); return
            for item in (recording, archive):
                status = validate_mount(MountConfig("recording" if item is recording else "archive", Path(item.mountpoint), item.uuid, item.path, 15 * 1024**3))
                if not (status.available and status.writable and not status.reason):
                    self.status.configure(text=f"{item.mountpoint}: {status.reason}"); return
        if self.page in (2, 3) and not self.camera_tests[self.page - 2]:
            self.status.configure(text="Test this enabled camera successfully before continuing."); return
        if self.page == 4:
            password, confirm = self.vars["dashboard_password"].get(), self.vars["dashboard_password_confirm"].get()
            existing_hash = self.raw.get("dashboard", {}).get("admin_password_hash", "")
            if (not password and not existing_hash) or (password and (len(password) < 8 or password != confirm)):
                self.status.configure(text="Set and confirm an administrator password of at least 8 characters."); return
            if self.vars["lan"].get() and (not self.vars["tls_cert"].get() or not self.vars["tls_key"].get()):
                self.status.configure(text="LAN access requires TLS certificate and key paths."); return
            if not self.vars["lan"].get() and self.vars["bind"].get().strip() not in {"127.0.0.1", "::1", "localhost"}:
                self.status.configure(text="Local-only mode must bind to localhost."); return
            try:
                port = int(self.vars["port"].get())
            except ValueError:
                self.status.configure(text="Dashboard port must be a number between 1 and 65535."); return
            if not 1 <= port <= 65535:
                self.status.configure(text="Dashboard port must be between 1 and 65535."); return
            if self.vars["lan"].get() and (not Path(self.vars["tls_cert"].get()).is_file() or not Path(self.vars["tls_key"].get()).is_file()):
                self.status.configure(text="LAN TLS certificate and key must point to existing files."); return
        if self.page == 5:
            self._apply(); return
        self.page += 1; self._render()

    def _back(self) -> None:
        if self.page: self.page -= 1; self._render()

    def _config_text(self) -> str:
        recording, archive = self._selected_drive("recording"), self._selected_drive("archive")
        password = self.vars["dashboard_password"].get()
        old_dashboard = self.raw.get("dashboard", {})
        if password:
            from argon2 import PasswordHasher
            password_hash = PasswordHasher().hash(password)
        else:
            password_hash = old_dashboard["admin_password_hash"]
        session_secret = old_dashboard.get("session_secret") or secrets.token_urlsafe(48)
        lines = ["[general]", 'data_dir = "/var/lib/security-camera"', 'timezone = "America/Los_Angeles"', 'ffmpeg = "/usr/bin/ffmpeg"', 'ffprobe = "/usr/bin/ffprobe"', "retention_hours = 168", "expected_segment_gib = 8", ""]
        for key, drive, reserve in (("recording", recording, 15), ("archive", archive, 50)):
            lines.extend([f"[storage.{key}]", f"path = {_toml_string(drive.mountpoint)}", f"fs_uuid = {_toml_string(drive.uuid)}", f"source = {_toml_string(drive.path)}", f"reserve_gib = {reserve}", ""])
        for index, camera_id in enumerate(("door", "garage")):
            prefix = f"camera{index}_"; main = self._camera_url(index, True); sub = self._camera_url(index, False)
            lines.extend([["[[cameras]]", f"id = {_toml_string(camera_id)}", f"name = {_toml_string(self.vars[prefix + 'name'].get())}", "enabled = true", f"main_url = {_toml_string(main)}", f"sub_url = {_toml_string(sub)}" if sub else "", f"transport = {_toml_string(self.vars[prefix + 'transport'].get())}", "timeout_us = 15000000", "reconnect_initial_seconds = 2", "reconnect_max_seconds = 60", ""]][0])
        lines.extend(["[dashboard]", f"bind = {_toml_string(self.vars['bind'].get())}", f"port = {int(self.vars['port'].get())}", f"lan_enabled = {'true' if self.vars['lan'].get() else 'false'}", f"admin_password_hash = {_toml_string(password_hash)}", f"session_secret = {_toml_string(session_secret)}", f"tls_cert = {_toml_string(self.vars['tls_cert'].get())}", f"tls_key = {_toml_string(self.vars['tls_key'].get())}", "", "[preview]", "enabled = true", "default_timeout_seconds = 300", "maximum_timeout_seconds = 3600", "max_concurrent = 1", "allow_transcode_fallback = false", ""])
        return "\n".join(line for line in lines if line is not None)

    def _apply(self) -> None:
        tracked = [CONFIG_PATH, *_storage_override_paths()]
        previous = {path: path.read_bytes() if path.exists() else None for path in tracked}
        services = (*STORAGE_SERVICES, "security-camera-dashboard.service", "security-camera-maintenance.timer")
        was_active: dict[str, bool] = {}
        was_enabled: dict[str, bool] = {}
        try:
            was_active = {
                service: subprocess.run(["systemctl", "is-active", "--quiet", service], check=False, timeout=30).returncode == 0
                for service in services
            }
            was_enabled = {
                service: subprocess.run(["systemctl", "is-enabled", "--quiet", service], check=False, timeout=30).returncode == 0
                for service in services
            }
            for service in services:
                subprocess.run(["systemctl", "stop", service], check=False, timeout=30)
            _write_config(self._config_text())
            recording, archive = self._selected_drive("recording"), self._selected_drive("archive")
            if not recording or not archive:
                raise RuntimeError("storage drives were not selected")
            _write_storage_overrides(recording.mountpoint, archive.mountpoint)
            subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=30)
            subprocess.run(["systemctl", "enable", "--now", "security-camera-recorder.service", "security-camera-archive.service", "security-camera-dashboard.service", "security-camera-maintenance.timer"], check=True, timeout=60)
        except Exception as exc:
            for path, content in previous.items():
                _restore_file(path, content)
            subprocess.run(["systemctl", "daemon-reload"], check=False, timeout=30)
            for service, enabled in was_enabled.items():
                subprocess.run(["systemctl", "enable" if enabled else "disable", service], check=False, timeout=30)
            for service, active in was_active.items():
                if active:
                    subprocess.run(["systemctl", "start", service], check=False, timeout=30)
            self.messagebox.showerror("Setup failed", f"Configuration was not completed:\n{exc}"); return
        self.messagebox.showinfo("Setup complete", "Recording services are running. Open http://127.0.0.1:8080 on this computer.")
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def run_setup() -> None:
    if os.geteuid() != 0:
        raise SystemExit("Run setup through setup.sh so administrator permission can be requested.")
    SetupWizard().run()

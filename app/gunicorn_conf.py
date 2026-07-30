from app.config import load

settings = load()
bind = f"{settings.dashboard.get('bind', '127.0.0.1')}:{int(settings.dashboard.get('port', 8080))}"
workers = 1
threads = 4
timeout = 30
accesslog = "-"
errorlog = "-"
if settings.dashboard.get("lan_enabled", False):
    certfile = settings.dashboard.get("tls_cert") or None
    keyfile = settings.dashboard.get("tls_key") or None
    if not certfile or not keyfile:
        raise RuntimeError("LAN dashboard mode requires tls_cert and tls_key")

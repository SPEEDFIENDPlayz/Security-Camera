from __future__ import annotations

import logging
import re

_URL_SECRET = re.compile(r"(rtsp://)([^/@]+)@", re.IGNORECASE)
_TOKEN = re.compile(r"(?i)(access_token|refresh_token|client_secret|upload_id)=([^&\\s]+)")


def redact(value: object) -> str:
    """Return a log-safe representation without credentials or upload URLs."""
    text = str(value)
    text = _URL_SECRET.sub(r"\\1***:***@", text)
    return _TOKEN.sub(r"\\1=***", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.msg)
        if record.args:
            record.args = tuple(redact(arg) for arg in record.args)
        return True


def configure_logging(level: str = "INFO") -> logging.Logger:
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.addFilter(RedactingFilter())
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logging.getLogger("security_camera")

from __future__ import annotations

import json
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials

from app.config import Settings
from app.database import Database
from app.storage.mounts import safe_child

SCOPE = "https://www.googleapis.com/auth/drive.file"


class ExpiredUploadSession(RuntimeError):
    pass


class UploadCancelled(RuntimeError):
    pass


class DriveUploader:
    """Drive v3 resumable uploader with durable session metadata in the job payload."""
    def __init__(self, settings: Settings, db: Database):
        self.settings, self.db = settings, db

    def _session(self) -> AuthorizedSession:
        token_file = Path(self.settings.google["token_file"])
        creds = Credentials.from_authorized_user_file(token_file, [SCOPE])
        if not creds.valid:
            creds.refresh(Request())
            token_file.write_text(creds.to_json())
        return AuthorizedSession(creds)

    def _existing(self, http: AuthorizedSession, clip_id: str) -> dict | None:
        query = f"appProperties has {{ key='security_camera_clip_id' and value='{clip_id}' }} and trashed=false"
        response = http.get("https://www.googleapis.com/drive/v3/files", params={"q": query, "fields": "files(id,size,md5Checksum)"}, timeout=30)
        response.raise_for_status()
        files = response.json().get("files", [])
        return files[0] if files else None

    def upload(self, job, clip) -> dict:
        path = safe_child(self.settings.archive.path, Path(clip["archive_path"]))
        if not path.is_file(): raise FileNotFoundError(path)
        http = self._session(); existing = self._existing(http, clip["id"])
        if existing: return existing
        payload = json.loads(job["payload"] or "{}")
        total, chunk = path.stat().st_size, int(self.settings.google.get("chunk_bytes", 8 * 1024 * 1024))
        uri = payload.get("session_uri"); offset = int(payload.get("offset", 0))
        if not uri:
            metadata = {"name": path.name, "parents": [self.settings.google["folder_id"]], "appProperties": {"security_camera_clip_id": clip["id"]}}
            response = http.post("https://www.googleapis.com/upload/drive/v3/files", params={"uploadType": "resumable"}, headers={"X-Upload-Content-Type": "video/x-matroska", "X-Upload-Content-Length": str(total), "Content-Type": "application/json"}, json=metadata, timeout=30)
            response.raise_for_status(); uri = response.headers["Location"]
            payload = {"session_uri": uri, "offset": offset}; self.db.transition_job(job["id"], job["lease_token"], "Uploading", payload=payload)
        with path.open("rb") as stream:
            stream.seek(offset)
            while offset < total:
                if not self.db.owns_job(job["id"], job["lease_token"]):
                    raise UploadCancelled("upload cancelled before next chunk")
                block = stream.read(min(chunk, total - offset))
                end = offset + len(block) - 1
                response = http.put(uri, data=block, headers={"Content-Length": str(len(block)), "Content-Range": f"bytes {offset}-{end}/{total}"}, timeout=300)
                if response.status_code in (200, 201): return response.json()
                if response.status_code in (404, 410):
                    # Session URLs expire. Re-check idempotency before a later retry
                    # creates a new session, then discard only the stale session data.
                    existing = self._existing(http, clip["id"])
                    if existing: return existing
                    self.db.transition_job(job["id"], job["lease_token"], "Uploading", payload={})
                    raise ExpiredUploadSession("Google Drive resumable session expired")
                if response.status_code != 308: response.raise_for_status()
                offset = end + 1; payload = {"session_uri": uri, "offset": offset}
                self.db.transition_job(job["id"], job["lease_token"], "Uploading", payload=payload)
        raise RuntimeError("Drive upload finished without completion response")

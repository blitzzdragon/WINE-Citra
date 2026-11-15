"""Supabase-backed anonymous bug reports."""

import errno
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from Core.Runtime.application_runtime import DATA_DIR, log

REPORT_CONFIG_FILE = Path.home() / ".config/wine-citra/supabase.json"
ATTACHMENT_BUCKET = "report-attachments"
MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 100 * 1024 * 1024
ALLOWED_ATTACHMENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
}
class ReportServiceError(RuntimeError):
    """A safe, user-presentable reporting service error."""


def _load_supabase_settings():
    settings = {}
    try:
        loaded = json.loads(REPORT_CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ReportServiceError(
                f"Supabase settings in {REPORT_CONFIG_FILE} must be a JSON object.")
        settings = loaded
    except FileNotFoundError:
        pass
    except OSError as error:
        raise ReportServiceError(
            f"Could not read Supabase settings: {error}") from error
    except ValueError as error:
        raise ReportServiceError(
            f"Supabase settings in {REPORT_CONFIG_FILE} are invalid JSON.") from error

    url = (os.environ.get("WINE_CITRA_SUPABASE_URL")
           or settings.get("url", ""))
    key = (os.environ.get("WINE_CITRA_SUPABASE_KEY")
           or settings.get("publishable_key", ""))
    return str(url).strip().rstrip("/"), str(key).strip()


class SupabaseReportService:
    def __init__(self):
        self.supabase_url, self.publishable_key = _load_supabase_settings()
        if not self.supabase_url:
            raise ReportServiceError(
                f"Supabase is not configured. Add the project URL and publishable "
                f"key to {REPORT_CONFIG_FILE}.")
        if not self.supabase_url.startswith("https://"):
            raise ReportServiceError("The Supabase URL must use HTTPS.")
        if not self.publishable_key.startswith("sb_publishable_"):
            raise ReportServiceError(
                "Supabase is not configured. Add the project URL and publishable "
                f"key to {REPORT_CONFIG_FILE}.")
        self._session_file = DATA_DIR / "report-session.json"
        self._anonymous_session = self._load_anonymous_session()

    def _load_anonymous_session(self):
        try:
            data = json.loads(self._session_file.read_text(encoding="utf-8"))
            if (isinstance(data, dict) and data.get("access_token")
                    and data.get("refresh_token") and data.get("user")):
                return data
            raise ReportServiceError(
                "The saved anonymous report session is invalid. The reports "
                "may be inaccessible; remove report-session.json only if you "
                "are willing to start a new anonymous identity.")
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ReportServiceError(
                f"Could not read the saved report session: {error}") from error
        except ValueError as error:
            raise ReportServiceError(
                "The saved anonymous report session is unreadable. The reports "
                "may be inaccessible; remove report-session.json only if you "
                "are willing to start a new anonymous identity.") from error

    def _save_anonymous_session(self):
        if self._anonymous_session is None:
            return
        self._session_file.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix="report-session-", dir=self._session_file.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self._anonymous_session, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._session_file)
            self._session_file.chmod(0o600)
        except OSError as error:
            try:
                os.close(descriptor)
            except OSError as close_error:
                if close_error.errno != errno.EBADF:
                    log("Could not close the temporary report-session file: "
                        + str(close_error))
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError as cleanup_error:
                log("Could not remove the temporary report-session file: "
                    + str(cleanup_error))
            raise ReportServiceError(
                f"Could not securely save the report session: {error}") from error

    def _active_session(self):
        return self._anonymous_session

    def _request(self, method, endpoint, data=None, *, raw_data=None,
                 content_type="application/json", extra_headers=None,
                 token=None, retry_auth=True, include_bearer=True):
        session = self._active_session()
        access_token = token or (
            session.get("access_token") if session else None)
        headers = {"apikey": self.publishable_key, "Accept": "application/json"}
        if include_bearer and (access_token or self.publishable_key):
            headers["Authorization"] = (
                f"Bearer {access_token or self.publishable_key}")
        if content_type:
            headers["Content-Type"] = content_type
        if extra_headers:
            headers.update(extra_headers)
        if raw_data is not None:
            body = raw_data
        elif data is not None:
            body = json.dumps(data).encode("utf-8")
        else:
            body = None
        request = urllib.request.Request(
            self.supabase_url + endpoint, data=body, headers=headers,
            method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            if (error.code == 401 and retry_auth and session
                    and not endpoint.startswith("/auth/v1/")):
                self._refresh_active_session()
                return self._request(
                    method, endpoint, data, raw_data=raw_data,
                    content_type=content_type, extra_headers=extra_headers,
                    retry_auth=False)
            raise ReportServiceError(self._format_error(detail, error.code))
        except (OSError, urllib.error.URLError, TimeoutError) as error:
            raise ReportServiceError(
                f"Could not connect to the report service: {error}") from error

        if not payload:
            return None
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return payload

    @staticmethod
    def _format_error(detail, status):
        try:
            payload = json.loads(detail)
            message = (payload.get("message") or payload.get("msg")
                       or payload.get("error_description")
                       or payload.get("error"))
            if message:
                return str(message)
        except (ValueError, AttributeError):
            pass
        return f"Report service request failed (HTTP {status})."

    def _refresh_active_session(self):
        session = self._active_session()
        if not session or not session.get("refresh_token"):
            raise ReportServiceError(
                "Your reporting session expired. Please try again.")
        refreshed = self._request(
            "POST", "/auth/v1/token?grant_type=refresh_token",
            {"refresh_token": session["refresh_token"]},
            retry_auth=False, include_bearer=False)
        if not isinstance(refreshed, dict) or not refreshed.get("access_token"):
            raise ReportServiceError("The reporting session could not be renewed.")
        session.update(refreshed)
        self._anonymous_session = session
        self._save_anonymous_session()

    def _ensure_anonymous_session(self):
        if self._anonymous_session:
            return
        session = self._request(
            "POST", "/auth/v1/signup", {"data": {}},
            retry_auth=False, include_bearer=False)
        if (not isinstance(session, dict) or not session.get("access_token")
                or not session.get("user")):
            raise ReportServiceError(
                "Anonymous sign-in did not return a session. Enable anonymous "
                "sign-ins in the Supabase Auth settings.")
        self._anonymous_session = session
        self._save_anonymous_session()

    def _ensure_session(self):
        self._ensure_anonymous_session()
        if not self._active_session():
            raise ReportServiceError("The reporting session is unavailable.")

    def _validate_attachments(self, paths):
        if len(paths) > MAX_ATTACHMENTS:
            raise ReportServiceError(
                f"Attach no more than {MAX_ATTACHMENTS} files.")
        result = []
        total_size = 0
        for value in paths:
            path = Path(value).expanduser()
            suffix = path.suffix.lower()
            if suffix not in ALLOWED_ATTACHMENT_TYPES:
                raise ReportServiceError(
                    f"Unsupported report attachment: {path.name}")
            try:
                size = path.stat().st_size
            except OSError as error:
                raise ReportServiceError(
                    f"Could not read attachment '{path.name}': {error}") from error
            if size > MAX_ATTACHMENT_BYTES:
                raise ReportServiceError(
                    f"'{path.name}' exceeds the 50 MB per-file limit.")
            total_size += size
            result.append((path, size, ALLOWED_ATTACHMENT_TYPES[suffix]))
        if total_size > MAX_TOTAL_ATTACHMENT_BYTES:
            raise ReportServiceError(
                "Attachments exceed the combined 100 MB limit.")
        return result

    @staticmethod
    def _safe_filename(name):
        name = Path(name).name
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
        return cleaned[:120] or "attachment"

    def _upload_attachment(self, report_id, file_info):
        path, size, content_type = file_info
        user_id = self._active_session()["user"]["id"]
        object_path = (
            f"{user_id}/{report_id}/{uuid.uuid4()}-"
            f"{self._safe_filename(path.name)}")
        encoded_path = urllib.parse.quote(object_path, safe="/")
        uploaded = False
        try:
            with path.open("rb") as handle:
                payload = handle.read(MAX_ATTACHMENT_BYTES + 1)
            if len(payload) > MAX_ATTACHMENT_BYTES:
                raise ReportServiceError(
                    f"'{path.name}' exceeds the 50 MB per-file limit.")
            if len(payload) != size:
                raise ReportServiceError(
                    f"'{path.name}' changed while the report was being sent.")
            self._request(
                "POST",
                f"/storage/v1/object/{ATTACHMENT_BUCKET}/{encoded_path}",
                raw_data=payload, content_type=content_type,
                extra_headers={"x-upsert": "false"})
            uploaded = True
            query = urllib.parse.urlencode({"select": "id"})
            rows = self._request(
                "POST", f"/rest/v1/report_attachments?{query}",
                {
                    "report_id": report_id,
                    "storage_path": object_path,
                    "original_name": path.name,
                    "content_type": content_type,
                    "file_size": size,
                    "uploaded_by": user_id,
                },
                extra_headers={"Prefer": "return=representation"})
            if not isinstance(rows, list) or not rows:
                raise ReportServiceError(
                    f"Could not register attachment '{path.name}'.")
            return rows[0]
        except Exception:
            if uploaded:
                try:
                    self._request(
                        "DELETE",
                        f"/storage/v1/object/{ATTACHMENT_BUCKET}/{encoded_path}",
                        content_type=None)
                except ReportServiceError as cleanup_error:
                    log("Could not remove an unregistered report attachment: "
                        + str(cleanup_error))
            raise

    def create_report(self, title, description, attachment_paths):
        self._ensure_session()
        title = str(title or "").strip()
        description = str(description or "").strip()
        if not title or len(title) > 160:
            raise ReportServiceError(
                "Enter a title between 1 and 160 characters.")
        if not description or len(description) > 12000:
            raise ReportServiceError(
                "Enter a description between 1 and 12,000 characters.")
        attachments = self._validate_attachments(attachment_paths)
        user_id = self._active_session()["user"]["id"]
        query = urllib.parse.urlencode({"select": "*"})
        rows = self._request(
            "POST", f"/rest/v1/reports?{query}",
            {
                "created_by": user_id,
                "category": "bug",
                "title": title,
                "description": description,
            },
            extra_headers={"Prefer": "return=representation"})
        if not isinstance(rows, list) or not rows:
            raise ReportServiceError("The report was not created.")
        report = rows[0]
        attachment_errors = []
        for attachment in attachments:
            try:
                self._upload_attachment(report["id"], attachment)
            except (OSError, ReportServiceError) as error:
                attachment_errors.append(str(error))
        return {
            "report": report,
            "attachment_errors": attachment_errors,
        }

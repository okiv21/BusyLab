"""Where uploaded files live.

Spec 8 is blunt about this: raw uploads go to Supabase Storage, **never the
Render disk, it is ephemeral**. A free Render service spins down after
inactivity and comes back with an empty filesystem, so a local path works
perfectly in development and silently loses every upload in production.

So the path is an abstraction with two implementations. Local disk for
development, where it is simpler and there is nothing to lose. Supabase Storage
for production, reached over its plain HTTP API rather than through the
supabase SDK - one fewer dependency, and the S3-style calls involved are a PUT
and a GET.

Both return a key rather than a filesystem path, because the caller must not
assume the bytes are reachable through the filesystem at all.
"""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


class StorageError(RuntimeError):
    """The file could not be stored or retrieved."""


#: The real content type per accepted extension.
#:
#: Sending everything as ``application/octet-stream`` would work until the
#: bucket is configured to restrict MIME types, at which point every upload is
#: rejected for being the wrong kind of file. Storing the true type also means
#: anything else reading the bucket sees a spreadsheet rather than a blob.
CONTENT_TYPES: dict[str, str] = {
    ".xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
}


def content_type_for(key: str) -> str:
    """Best-known content type for a stored key."""
    return CONTENT_TYPES.get(Path(key).suffix.lower(), "application/octet-stream")


def _explain_http(exc: "urllib.error.HTTPError", bucket: str, key: str) -> str:
    """Turn a storage HTTP error into the thing that is actually wrong.

    Supabase puts a usable message in the response body and the bare status
    line throws it away, so a misconfigured bucket arrives as "400 Bad Request"
    with nothing to act on. Every one of these is a setup mistake made once,
    during deployment, by someone who cannot see the bucket settings from here.
    """
    try:
        body = exc.read().decode("utf-8", "replace").strip()
    except Exception:  # the body is a nicety, never worth masking the status
        body = ""

    lowered = body.lower()
    detail = f" Supabase said: {body}" if body else ""

    if exc.code in (401, 403):
        return (
            f"{exc.code} {exc.reason}. The key was rejected. SUPABASE_SERVICE_KEY "
            f"must be the service_role key from Settings, API - not the anon "
            f"key, which cannot write, and not the database password.{detail}"
        )
    if exc.code == 404 or "not found" in lowered:
        if "bucket" in lowered:
            return (
                f"{exc.code} {exc.reason}. There is no bucket called {bucket!r}. "
                f"Create it under Storage, or correct SUPABASE_BUCKET.{detail}"
            )
        return f"{exc.code} {exc.reason}. No object at {key!r}.{detail}"
    if exc.code == 413 or "too large" in lowered or "maximum size" in lowered:
        return (
            f"{exc.code} {exc.reason}. The file is larger than the bucket's "
            f"size limit. Raise it under Storage, bucket settings.{detail}"
        )
    if "mime" in lowered or exc.code == 415:
        return (
            f"{exc.code} {exc.reason}. The bucket restricts MIME types and "
            f"{content_type_for(key)!r} is not on the list. Add it, or turn the "
            f"restriction off.{detail}"
        )
    return f"{exc.code} {exc.reason}.{detail}"


@runtime_checkable
class FileStore(Protocol):
    """Somewhere to put an uploaded spreadsheet."""

    name: str

    def put(self, key: str, data: bytes) -> str:
        """Store bytes under ``key``. Returns the key actually used."""
        ...

    def get(self, key: str) -> bytes:
        ...

    def delete(self, key: str) -> None:
        ...

    def exists(self, key: str) -> bool:
        ...


@dataclass
class LocalFileStore:
    """Development storage. Fine locally, wrong on an ephemeral host."""

    root: Path = field(default_factory=lambda: Path(os.environ.get("BUSYLAB_STORAGE", "storage")))
    name: str = "local"

    def _path(self, key: str) -> Path:
        # Keys come from our own code, but a traversal here would read
        # arbitrary files, so the resolved path is checked to stay inside root.
        root = self.root.resolve()
        candidate = (root / key).resolve()
        if root not in candidate.parents and candidate != root:
            raise StorageError(f"Refusing to touch {key!r} outside the store.")
        return candidate

    def put(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except OSError as exc:
            raise StorageError(f"Could not read {key!r}: {exc}") from exc

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


@dataclass
class SupabaseFileStore:
    """Supabase Storage over its REST API.

    Uses the service role key, which is a server-side secret and must never
    reach the browser. That is why uploads go through our API rather than
    straight from the frontend.
    """

    url: str = field(default_factory=lambda: os.environ.get("SUPABASE_URL", "").rstrip("/"))
    key: str = field(default_factory=lambda: os.environ.get("SUPABASE_SERVICE_KEY", ""))
    bucket: str = field(default_factory=lambda: os.environ.get("SUPABASE_BUCKET", "uploads"))
    timeout: float = 30.0
    name: str = "supabase"

    def available(self) -> bool:
        return bool(self.url and self.key)

    def _endpoint(self, key: str) -> str:
        return f"{self.url}/storage/v1/object/{self.bucket}/{urllib.parse.quote(key)}"

    def _request(self, method: str, key: str, data: bytes | None = None) -> bytes:
        request = urllib.request.Request(
            self._endpoint(key),
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.key}",
                "apikey": self.key,
                # Upsert so a re-uploaded dataset overwrites rather than 409s.
                "x-upsert": "true",
                **({"Content-Type": content_type_for(key)} if data else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise StorageError(
                f"Supabase Storage {method} {key!r} failed: "
                f"{_explain_http(exc, self.bucket, key)}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise StorageError(
                f"Supabase Storage unreachable: {exc}. Check SUPABASE_URL - it "
                f"should be the project URL, https://<ref>.supabase.co, with no "
                f"path after it."
            ) from exc

    def put(self, key: str, data: bytes) -> str:
        self._request("POST", key, data)
        return key

    def get(self, key: str) -> bytes:
        return self._request("GET", key)

    def delete(self, key: str) -> None:
        try:
            self._request("DELETE", key)
        except StorageError as exc:
            log.warning("could not delete %s: %s", key, exc)

    def exists(self, key: str) -> bool:
        try:
            self._request("GET", key)
            return True
        except StorageError:
            return False


def store_from_env() -> FileStore:
    """Pick a file store. Supabase when configured, local otherwise."""
    supabase = SupabaseFileStore()
    if supabase.available():
        return supabase
    return LocalFileStore()

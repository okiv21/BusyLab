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
                **({"Content-Type": "application/octet-stream"} if data else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise StorageError(
                f"Supabase Storage {method} {key!r} failed: {exc.code} {exc.reason}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise StorageError(f"Supabase Storage unreachable: {exc}") from exc

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

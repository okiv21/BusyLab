"""Check Supabase Storage, before deploying anything.

    python check_storage.py

Reads SUPABASE_URL, SUPABASE_SERVICE_KEY and SUPABASE_BUCKET from .env or the
environment, and asks for anything missing. Nothing is echoed and nothing is
saved unless you say so.

The companion to check_db.py, and for the same reason. Storage is the other
production dependency that cannot be exercised locally, so without this the
first thing to discover a wrong bucket name or an anon key is a real upload
from a real person, several minutes after a deploy, reported as a 400.

It round-trips an actual file - put, get, verify the bytes, delete - because
the interesting failures are not connection failures. A bucket with a MIME
restriction accepts the request and rejects the spreadsheet; an anon key
authenticates fine and cannot write.
"""

from __future__ import annotations

import getpass
import os
import sys
import textwrap
import uuid

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
OFF = "\033[0m"

#: The real thing the app stores, not a text file. A bucket that restricts
#: MIME types will happily take text/plain and reject this, so testing with
#: anything smaller would pass here and fail on the first genuine upload.
SAMPLE_KEY = f"busylab-check-{uuid.uuid4().hex[:8]}.csv"
SAMPLE = b"date,revenue\n2026-01-01,100\n"


def _fill(text: str) -> str:
    return textwrap.fill(
        " ".join(text.split()),
        width=76,
        initial_indent="  ",
        subsequent_indent="  ",
        # Hyphenated terms are usually the searchable part of the advice -
        # "case-sensitive", "service_role", "percent-encoded" - and splitting
        # them across a line break makes them unreadable and ungreppable.
        break_on_hyphens=False,
    )


def _load_env(path: str = ".env") -> None:
    """Read .env without adding a dependency, and never overwrite the real env."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            os.environ.setdefault(name.strip(), value.strip())


def _check_url(url: str) -> str:
    """The two ways SUPABASE_URL is habitually wrong."""
    if "/storage/v1" in url or url.rstrip("/").endswith("/storage"):
        return (
            "SUPABASE_URL should be the project URL only - "
            "https://<ref>.supabase.co - with no path. The storage path is "
            "added by the code."
        )
    if not url.startswith("http"):
        return "SUPABASE_URL should start with https://."
    return ""


def _check_key(key: str) -> str:
    """Catch the anon key before it fails as a confusing 403.

    Supabase hands you two keys on the same page and only one of them can
    write. Picking the wrong one is the single most common storage mistake,
    and the resulting error says nothing about which key is in use.
    """
    if key.count(".") == 2:  # a JWT: header.payload.signature
        import base64
        import json

        try:
            payload = key.split(".")[1]
            payload += "=" * (-len(payload) % 4)  # restore stripped padding
            role = json.loads(base64.urlsafe_b64decode(payload)).get("role", "")
        except Exception:
            return ""
        if role == "anon":
            return (
                "That is the anon key. It can read a public bucket but cannot "
                "write, so uploads will fail. Use the service_role key from "
                "Settings, API - it is on the same page, marked secret."
            )
        if role and role != "service_role":
            return f"That key carries the role {role!r}, not service_role."
    elif key.startswith("sb_publishable_"):
        return (
            "That is a publishable key, which cannot write. Use the secret "
            "key - sb_secret_... - from Settings, API keys."
        )
    return ""


def main() -> int:
    _load_env()

    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    bucket = os.environ.get("SUPABASE_BUCKET", "") or "uploads"

    if not url:
        print("SUPABASE_URL - the project URL, https://<ref>.supabase.co")
        url = input("> ").strip().rstrip("/")
    if not key:
        print("\nSUPABASE_SERVICE_KEY - the service_role key from Settings, API")
        print(f"{DIM}Not shown as you type, and not saved.{OFF}")
        key = getpass.getpass("> ").strip()
    if not url or not key:
        print(f"{RED}Both are needed.{OFF}")
        return 2

    for problem in (_check_url(url), _check_key(key)):
        if problem:
            print(f"\n{YELLOW}{_fill(problem)}{OFF}")

    print()
    print(f"  project   {url}")
    print(f"  bucket    {bucket}")
    print(f"  key       {DIM}{'set' if key else 'MISSING'}{OFF}")
    print()

    from api.storage import StorageError, SupabaseFileStore

    # Ask the project what buckets it has before assuming one. "Bucket not
    # found" is unhelpful precisely when you are sure you created it, because
    # the mistake is almost always the name - a capital letter, a plural, or a
    # bucket made in a different project. Showing the real list ends that.
    names = _list_buckets(url, key)
    if names is not None and bucket not in names:
        print(f"{RED}There is no bucket called {bucket!r}.{OFF}\n")
        if names:
            print("  This project has:")
            for name in names:
                print(f"    - {name}")
            print()
            print(_fill(
                f"If one of those is the one you meant, set SUPABASE_BUCKET to "
                f"it - on Render, and in .env so this check tests the same one. "
                f"Names are case-sensitive."
            ))
        else:
            print(_fill(
                "This project has no buckets at all. If you created one, it "
                "was in a different Supabase project - check the project "
                "selector at the top of the dashboard against SUPABASE_URL."
            ))
        print()
        return 1

    store = SupabaseFileStore(url=url, key=key, bucket=bucket)

    # Round-trip, in the order the application does it. Each step is reported
    # separately, because "storage is broken" and "storage cannot delete" are
    # very different problems and only one of them blocks a deploy.
    print(f"Uploading {SAMPLE_KEY}…")
    try:
        store.put(SAMPLE_KEY, SAMPLE)
    except StorageError as exc:
        print(f"\n{RED}Upload failed.{OFF}\n")
        print(_fill(str(exc)))
        print()
        return 1
    print(f"  {GREEN}stored{OFF}")

    print("Downloading it again…")
    try:
        returned = store.get(SAMPLE_KEY)
    except StorageError as exc:
        print(f"\n{RED}Download failed.{OFF}\n")
        print(_fill(str(exc)))
        print()
        _cleanup(store)
        return 1

    if returned != SAMPLE:
        print(f"\n{RED}The bytes came back different.{OFF}\n")
        print(_fill(
            f"Sent {len(SAMPLE)} bytes, got {len(returned)} back. Something "
            f"between here and the bucket is rewriting the file, which would "
            f"corrupt every upload."
        ))
        print()
        _cleanup(store)
        return 1
    print(f"  {GREEN}identical{OFF}")

    print("Deleting it…")
    _cleanup(store)
    if store.exists(SAMPLE_KEY):
        # Not fatal: uploads work, the bucket just accumulates. Worth saying,
        # because it is a slow leak rather than a visible failure.
        print(
            f"  {YELLOW}still present - deletes are not working, so old files "
            f"will build up.{OFF}"
        )
    else:
        print(f"  {GREEN}gone{OFF}")

    print(f"\n{GREEN}Storage works.{OFF} Set these three on Render:\n")
    print(f"  SUPABASE_URL           {url}")
    print(f"  SUPABASE_BUCKET        {bucket}")
    print(f"  SUPABASE_SERVICE_KEY   {DIM}(the key you just used){OFF}")
    print()
    return 0


def _list_buckets(url: str, key: str) -> list[str] | None:
    """Bucket names in this project, or None if they could not be listed.

    None is deliberately different from an empty list: "I could not ask" must
    not be reported as "you have no buckets", which would send someone off to
    recreate something that already exists.
    """
    import json
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"{url}/storage/v1/bucket",
        headers={"Authorization": f"Bearer {key}", "apikey": key},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    if not isinstance(payload, list):
        return None
    return [b.get("name", "") for b in payload if isinstance(b, dict)]


def _cleanup(store) -> None:
    """Remove the probe file. Never raises - the check has already reported."""
    try:
        store.delete(SAMPLE_KEY)
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())

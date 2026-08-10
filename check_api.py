"""Check the deployed API, and whether the frontend is allowed to talk to it.

    python check_api.py https://busylab-api.onrender.com
    python check_api.py https://busylab-api.onrender.com https://busylab.vercel.app

The third of the deploy checks, and the one that untangles the most confusing
failure of the three. A browser will not tell you why a request failed - it
reports a generic network error whether the host was wrong, the server was
asleep, or the origin was refused - so from the frontend all three look
identical. This separates them.

Cold starts are treated as normal rather than as failure. A free Render
instance spins down after fifteen minutes and takes the better part of a
minute to come back, which in a browser is indistinguishable from a dead
service, and is the single most common reason to conclude wrongly that a
deploy is broken.
"""

from __future__ import annotations

import json
import socket
import sys
import textwrap
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
OFF = "\033[0m"

#: Generous, because waking a sleeping free instance is the normal case here
#: and giving up early is exactly the wrong answer.
WAKE_SECONDS = 90


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


def normalise(url: str) -> str:
    """Reduce whatever was pasted to a scheme and host."""
    url = url.strip().strip('"').strip("'")
    if not url:
        return ""
    # Test for the scheme before touching trailing slashes: stripping them
    # first turns "https://" into "https:", which then looks like a bare
    # hostname and gets a second scheme bolted onto the front.
    if "://" not in url:
        url = f"https://{url}"
    parts = urlsplit(url)
    if not parts.hostname:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def _resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, None)
        return True
    except socket.gaierror:
        return False


def _get(url: str, timeout: float):
    """Return (status, body). Never raises for an HTTP error status."""
    request = urllib.request.Request(url, headers={"User-Agent": "busylab-check"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def main() -> int:
    api = normalise(sys.argv[1] if len(sys.argv) > 1 else "")
    site = normalise(sys.argv[2] if len(sys.argv) > 2 else "")

    if not api:
        print("The API's public URL, e.g. https://busylab-api.onrender.com")
        api = normalise(input("> "))
    if not api:
        print(f"{RED}Nothing to check.{OFF}")
        return 2

    host = urlsplit(api).hostname or ""
    print(f"\n  api       {api}")
    if site:
        print(f"  frontend  {site}")
    print()

    if not _resolves(host):
        print(f"{RED}That host does not exist.{OFF}\n")
        print(_fill(
            f"{host!r} does not resolve, so nothing was contacted. The URL is "
            f"wrong or mistyped - copy it from the top of the Render service "
            f"page. It is not always what the service name suggests."
        ))
        print()
        return 1
    print(f"  {GREEN}host resolves{OFF}")

    # A sleeping free instance answers eventually. Report the wait rather than
    # hiding it, because the wait is the answer people most often need.
    print(f"\nRequesting /health{DIM} (waking a sleeping instance can take a "
          f"minute){OFF}")
    started = time.monotonic()
    status: int | None = None
    body = ""
    while time.monotonic() - started < WAKE_SECONDS:
        try:
            status, body = _get(f"{api}/health", timeout=20)
            break
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            print(f"  {DIM}no answer yet, {int(time.monotonic() - started)}s{OFF}")
            time.sleep(3)

    elapsed = time.monotonic() - started
    if status is None:
        print(f"\n{RED}No answer after {int(elapsed)}s.{OFF}\n")
        print(_fill(
            "The host exists but nothing responded. On Render that usually "
            "means the service is suspended or failed to start - check the "
            "Logs tab. A service can show Live and still be refusing "
            "connections if it crashed after the last successful deploy."
        ))
        print()
        return 1

    if elapsed > 8:
        print(f"  {YELLOW}answered after {int(elapsed)}s - it was asleep.{OFF}")
        print(_fill(
            "That is normal on a free instance and not a fault, but the first "
            "visitor after an idle period waits this long. The scheduled "
            "GitHub Action keeps it awake during the week."
        ))
    else:
        print(f"  {GREEN}answered in {elapsed:.1f}s{OFF}")

    if status != 200:
        print(f"\n{RED}/health returned {status}.{OFF}\n")
        if status == 404:
            # Render wildcards *.onrender.com, so a mistyped service name
            # resolves perfectly and answers 404. DNS succeeding proves
            # nothing about the service existing, which is worth saying
            # plainly - it is the reason a wrong URL is hard to spot.
            print(_fill(
                f"Nothing is served at {api}. Every name under onrender.com "
                f"resolves whether or not the service exists, so a mistyped "
                f"URL looks like a working host that has lost its API. Copy "
                f"the URL from the top of the Render service page."
            ))
            print()
            print(_fill(
                "If the URL is definitely right, then the service is not "
                "running the API - check the Logs tab for a crash after the "
                "last deploy."
            ))
        elif status in (401, 403):
            print(_fill("Something in front of the service is refusing the request."))
        elif status >= 500:
            print(_fill(
                "The service is running but the request failed inside it. The "
                "most likely cause is DATABASE_URL: the store is created on "
                "the first request, not at startup, so a bad value deploys "
                "cleanly and then fails here. The Render logs will name it."
            ))
        if body:
            print()
            print(_fill(body[:600]))
        print()
        return 1

    try:
        health = json.loads(body)
    except json.JSONDecodeError:
        print(f"\n{YELLOW}/health answered 200 but not with JSON.{OFF}")
        print(_fill(
            "Something other than BusyLab is serving this URL - a proxy, a "
            "placeholder page, or the wrong service."
        ))
        print()
        return 1

    print(f"\n{GREEN}The API is up.{OFF}\n")
    print(f"  version   {health.get('version', '?')}")
    print(f"  storage   {_storage_note(health.get('storage'))}")
    if health.get("bucket"):
        print(f"  bucket    {health['bucket']}")
    print(f"  narration {health.get('narration', '?')}")
    print(f"  queued    {health.get('pending_jobs', '?')}")

    origins = health.get("cors_allows")
    if origins is None:
        print(f"\n  {DIM}This build predates the CORS report; redeploy the API")
        print(f"  to check origins from here.{OFF}\n")
        return 0

    print(f"\n  allows    {', '.join(origins) or '(nothing)'}")
    return _check_origin(origins, site)


def _storage_note(name: str | None) -> str:
    if name == "local":
        return (
            f"{YELLOW}local - uploads are on Render's disk and will be lost "
            f"when it restarts. Set SUPABASE_URL and SUPABASE_SERVICE_KEY.{OFF}"
        )
    return name or "?"


def _check_origin(origins: list[str], site: str) -> int:
    """Compare the frontend's origin with what the API will accept."""
    if not site:
        print()
        print(_fill(
            "Pass your Vercel URL as a second argument to check whether the "
            "browser will be allowed to call this API."
        ))
        print()
        return 0

    if "*" in origins or site in origins:
        print(f"\n{GREEN}{site} is allowed.{OFF} The frontend can call this API.\n")
        return 0

    print(f"\n{RED}{site} is not allowed.{OFF}\n")
    print(_fill(
        f"The browser sends {site} as its Origin, the API does not have that "
        f"in its list, and the request is refused before it runs. Neither end "
        f"reports this usefully - the browser calls it a network error and the "
        f"server logs an ordinary request - which is why it is worth checking "
        f"here."
    ))
    print()
    print(_fill(
        f"Set BUSYLAB_CORS on Render to {site} and redeploy. If you are "
        f"testing a Vercel preview deployment, note that previews get their "
        f"own hostname and it will not match your production URL."
    ))
    print()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

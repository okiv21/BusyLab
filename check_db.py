"""Check a database connection string, before deploying anything.

    python check_db.py

Paste the connection string when it asks. Nothing is stored and nothing is
echoed to the screen, so the password does not end up in your shell history.

The point of this is the feedback loop. Testing a connection string by
deploying takes several minutes and reports failures through a wall of driver
output; this takes two seconds and says the one thing that is wrong.
"""

from __future__ import annotations

import getpass
import sys
from urllib.parse import urlparse


GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
OFF = "\033[0m"


#: Characters that mean something structural inside a URL, so a password
#: containing one is silently mis-parsed rather than rejected.
_MUST_ENCODE = {
    "@": "%40",
    "/": "%2F",
    "?": "%3F",
    "#": "%23",
    "[": "%5B",
    "]": "%5D",
}


def _encoding_problem(dsn: str) -> str:
    """Spot a password that a URL parser will mangle.

    This is worth checking explicitly rather than leaving to the connection
    error, because the failure is silent: a `#` truncates the password at that
    point and the database simply reports the wrong password, with nothing
    hinting that half of it never arrived.
    """
    body = dsn.split("://", 1)[-1]
    if "@" not in body:
        return ""

    # The host is after the *last* @, so anything before it is credentials.
    credentials = body.rsplit("@", 1)[0]
    if ":" not in credentials:
        return ""

    password = credentials.split(":", 1)[1]
    found = [c for c in _MUST_ENCODE if c in password]
    if not found:
        return ""

    fixes = ", ".join(f"{c} as {_MUST_ENCODE[c]}" for c in found)
    return (
        f"Your password contains {' and '.join(found)}, which a URL reads as "
        f"punctuation rather than as part of the password.\n"
        f"Either write {fixes}, or reset the password to letters and numbers "
        f"only, which avoids the question."
    )


def _describe(dsn: str) -> None:
    """Show what the string says, without ever showing the password.

    Defensive throughout: this runs on strings that are already suspect, and a
    traceback here would hide the diagnosis it exists to give. A malformed URL
    is the normal case, not the exceptional one.
    """
    try:
        parsed = urlparse(dsn)
        host = parsed.hostname
        port = parsed.port
        user = parsed.username
        database = (parsed.path or "/").lstrip("/")
        has_password = bool(parsed.password)
    except ValueError:
        print(f"\n  {YELLOW}This does not parse as a URL at all.{OFF}")
        print(
            "  It should begin postgresql:// and look like\n"
            "  postgresql://user:password@host:6543/postgres\n"
        )
        return

    pooled = "pooler.supabase.com" in (host or "")
    print()
    print(f"  host      {host}")
    print(f"  port      {port}")
    print(f"  user      {user}")
    print(f"  database  {database}")
    print(f"  password  {DIM}{'set' if has_password else 'MISSING'}{OFF}")
    print(f"  kind      {'connection pooler' if pooled else 'direct connection'}")
    print()

    # Cheap sanity checks that catch a mangled string before the network does.
    if pooled and user and "." not in user:
        print(
            f"  {YELLOW}The username has no project ref. On the pooler it "
            f"must be postgres.<your-project-ref>.{OFF}\n"
        )
    if pooled and port != 6543:
        print(
            f"  {YELLOW}The pooler usually listens on 6543, not {port}.{OFF}\n"
        )


def main() -> int:
    dsn = sys.argv[1] if len(sys.argv) > 1 else ""

    if not dsn:
        print("Paste your Supabase connection string and press Enter.")
        print(
            f"{DIM}It will not be shown as you type, and it is not saved "
            f"anywhere.{OFF}"
        )
        dsn = getpass.getpass("> ").strip()

    if not dsn:
        print(f"{RED}Nothing entered.{OFF}")
        return 2

    if "[YOUR-PASSWORD]" in dsn or "YOUR-PASSWORD" in dsn:
        print(
            f"\n{RED}The placeholder is still in the string.{OFF}\n"
            "Replace [YOUR-PASSWORD] with your actual database password - the "
            "one you set when creating the Supabase project, not your Supabase\n"
            "account password. Reset it under Settings, Database if you have "
            "lost it."
        )
        return 1

    problem = _encoding_problem(dsn)
    if problem:
        print(f"\n{YELLOW}{problem}{OFF}")

    _describe(dsn)
    print("Connecting…")

    try:
        from api.pg import DatabaseConfigError, _verify_credentials
    except ImportError:
        print(
            f"{RED}Could not import the project.{OFF} Run this from the "
            "BusyLab folder, using the project's Python:\n"
            "  .venv\\Scripts\\python check_db.py"
        )
        return 2

    try:
        _verify_credentials(dsn)
    except DatabaseConfigError as exc:
        print(f"\n{RED}Did not connect.{OFF}\n")
        print(f"  {exc}\n")
        return 1
    except Exception as exc:  # anything the translator did not anticipate
        print(f"\n{RED}Did not connect.{OFF}\n\n  {exc}\n")
        return 1

    print(f"\n{GREEN}Connected.{OFF} This string will work on Render.\n")
    print("Next:")
    print("  1. Put it in DATABASE_URL on the Render service.")
    print("  2. Run the full contract against it, which exercises every")
    print("     query the app makes:")
    print()
    print(f"{DIM}     PowerShell:{OFF}")
    print('       $env:TEST_DATABASE_URL = "<the same string>"')
    print("       .venv\\Scripts\\python -m pytest tests/test_stores.py -q")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

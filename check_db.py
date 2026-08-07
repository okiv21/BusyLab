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


def _describe(dsn: str) -> None:
    """Show what the string says, without ever showing the password."""
    parsed = urlparse(dsn)
    pooled = "pooler.supabase.com" in (parsed.hostname or "")
    print()
    print(f"  host      {parsed.hostname}")
    print(f"  port      {parsed.port}")
    print(f"  user      {parsed.username}")
    print(f"  database  {(parsed.path or '/').lstrip('/')}")
    print(f"  password  {DIM}{'set' if parsed.password else 'MISSING'}{OFF}")
    print(f"  kind      {'connection pooler' if pooled else 'direct connection'}")
    print()


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

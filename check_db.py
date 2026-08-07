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
import textwrap
from urllib.parse import quote, urlparse


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


def _fill(text: str) -> str:
    """Wrap a long message to a readable width, indented under the heading."""
    return textwrap.fill(
        " ".join(text.split()), width=76, initial_indent="  ", subsequent_indent="  "
    )


def _assemble(template: str) -> str:
    """Fill the placeholder in Supabase's string with a separately-typed password.

    Two whole classes of failure disappear here. The password never travels
    through a shell, so PowerShell cannot expand a `$` inside it or strip a
    backtick; and it is percent-encoded on the way in, so a `#` or `?` cannot
    truncate it. Both of those fail *silently* - the database just reports the
    wrong password - which is why they are worth designing out rather than
    warning about.
    """
    print()
    print("The string still has [YOUR-PASSWORD] in it, which is the easy way.")
    print(f"{DIM}Type the database password now and it will be inserted and")
    print(f"encoded correctly. It is not shown, and not saved anywhere.{OFF}")
    password = getpass.getpass("password > ")
    if not password:
        return ""
    if password != password.strip():
        # Almost always a copy that caught a trailing space or newline.
        print(f"{YELLOW}  Trimming whitespace around the password.{OFF}")
        password = password.strip()
    # safe="" so that every reserved character is encoded, not just some.
    return template.replace("[YOUR-PASSWORD]", quote(password, safe="")).replace(
        "YOUR-PASSWORD", quote(password, safe="")
    )


def main() -> int:
    dsn = sys.argv[1] if len(sys.argv) > 1 else ""

    if not dsn:
        print("Paste your Supabase connection string and press Enter.")
        print(
            f"{DIM}Leave [YOUR-PASSWORD] in it - you will be asked for the "
            f"password\nseparately. Nothing is shown as you type, and nothing "
            f"is saved.{OFF}"
        )
        dsn = getpass.getpass("> ").strip()

    if not dsn:
        print(f"{RED}Nothing entered.{OFF}")
        return 2

    # Quotes survive a copy out of a terminal command more often than not.
    dsn = dsn.strip().strip('"').strip("'")

    assembled = "YOUR-PASSWORD" in dsn
    if assembled:
        dsn = _assemble(dsn)
        if not dsn:
            print(f"{RED}No password entered.{OFF}")
            return 2

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
        message = str(exc)
        if assembled and "percent-encoded" in message:
            # This tool encoded the password itself, so that advice is stale
            # and would send the reader after a problem that cannot exist here.
            message = message.split("Note that a password")[0]
            print(_fill(message))
            print()
            print(_fill(
                "The encoding is not the issue - this tool encoded the "
                "password for you. The password itself is wrong. Reset it "
                "under Settings, Database, wait a few seconds for the pooler "
                "to pick up the change, and run this again."
            ))
        else:
            print(_fill(message))
        print()
        return 1
    except Exception as exc:  # anything the translator did not anticipate
        print(f"\n{RED}Did not connect.{OFF}\n")
        print(_fill(str(exc)))
        print()
        return 1

    print(f"\n{GREEN}Connected.{OFF} This string will work on Render.\n")

    # The string was assembled in here, so the caller does not have a copy of
    # it - and rebuilding it by hand reintroduces exactly the encoding mistake
    # this tool just removed. Offer the two things they need it for instead.
    if _ask("Save it to .env, so you can copy it into Render?"):
        _save_to_env(dsn)

    if _ask("Run the full store contract against it now?", default=True):
        return _run_contract(dsn)

    print()
    print("When you want the contract - every query the app makes, against")
    print("this database - run this again and answer yes, or:")
    print()
    print(f"{DIM}  PowerShell:{OFF}")
    print('    $env:TEST_DATABASE_URL = (Select-String DATABASE_URL .env)')
    print("    .venv\\Scripts\\python -m pytest tests/test_stores.py -q")
    print()
    return 0


def _ask(question: str, default: bool = False) -> bool:
    """A yes/no prompt that behaves when there is nobody to answer it."""
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        reply = input(f"{question} {suffix} ").strip().lower()
    except EOFError:  # piped or redirected input
        return default
    if not reply:
        return default
    return reply.startswith("y")


def _save_to_env(dsn: str, path: str = ".env") -> None:
    """Upsert DATABASE_URL in .env, replacing any existing line.

    Writing rather than printing is deliberate: the string carries the
    password, and printing it puts it in the scrollback and quite possibly in
    a screenshot. .env is gitignored, so it stays local.
    """
    import os

    lines: list[str] = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()

    entry = f"DATABASE_URL={dsn}"
    for index, line in enumerate(lines):
        if line.startswith("DATABASE_URL="):
            if line == entry:
                print(f"  {DIM}.env already has this string.{OFF}")
                return
            lines[index] = entry
            break
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# The working connection string. Gitignored - keep it that way.")
        lines.append(entry)

    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"  {GREEN}Written to {path}{OFF} as DATABASE_URL. Copy the value from")
    print(f"  there into Render, rather than retyping it.")


def _run_contract(dsn: str) -> int:
    """Run the Postgres contract with the string already verified.

    This is the step that matters. Connecting proves the credentials; the
    contract proves every query the application makes actually works against
    this database, which is a different and larger claim.
    """
    import os
    import subprocess

    print(f"\n{DIM}Running tests/test_stores.py against the database. This")
    print(f"creates and drops its own tables, and takes a minute.{OFF}\n")

    environment = dict(os.environ, TEST_DATABASE_URL=dsn)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_stores.py", "-q"],
        env=environment,
    )
    if result.returncode == 0:
        print(f"\n{GREEN}The contract passes.{OFF} The database is ready.\n")
    else:
        print(
            f"\n{RED}The contract failed.{OFF} The credentials are fine - "
            "something\nabout the schema or a query is not. The output above "
            "says which.\n"
        )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

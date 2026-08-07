"""Tests for the connection-string checker.

This tool exists to diagnose a string that is already wrong, which means every
interesting input is malformed. That inverts the usual balance: the unhappy
paths are the feature, and a crash while describing a bad string is worse than
no tool at all, because it hides the diagnosis it was written to give.
"""

from __future__ import annotations

import check_db


POOLER = (
    "postgresql://postgres.abcdefghijklmnop:{pw}"
    "@aws-1-eu-west-1.pooler.supabase.com:6543/postgres"
)


class TestEncodingProblem:
    """A password a URL parser will silently mangle."""

    def test_plain_password_is_fine(self):
        assert check_db._encoding_problem(POOLER.format(pw="abc123")) == ""

    def test_hash_truncates_so_it_is_reported(self):
        problem = check_db._encoding_problem(POOLER.format(pw="pa#ss"))
        assert "#" in problem and "%23" in problem

    def test_every_reserved_character_is_named(self):
        # Each of these ends the password early or redirects the parse; a
        # partial warning would send the reader back for a second round.
        for char, encoded in check_db._MUST_ENCODE.items():
            problem = check_db._encoding_problem(POOLER.format(pw=f"pa{char}ss"))
            assert encoded in problem, f"{char} not flagged"

    def test_reports_several_at_once(self):
        problem = check_db._encoding_problem(POOLER.format(pw="a#b?c"))
        assert "%23" in problem and "%3F" in problem

    def test_at_sign_in_password_is_caught(self):
        # Split on the *last* @, or the host is mistaken for part of the
        # password and the check silently passes.
        problem = check_db._encoding_problem(POOLER.format(pw="pa@ss"))
        assert "%40" in problem

    def test_symbols_in_the_host_are_not_the_password(self):
        dsn = "postgresql://user:plain@some-host.example.com:6543/postgres"
        assert check_db._encoding_problem(dsn) == ""

    def test_no_credentials_at_all(self):
        assert check_db._encoding_problem("postgresql://host:6543/postgres") == ""

    def test_nonsense_does_not_raise(self):
        for junk in ("", "not a url", "://", "postgresql://", "@", ":@"):
            check_db._encoding_problem(junk)


class TestDescribe:
    """Describing must never raise - it runs on strings that are already bad."""

    def test_survives_input_urlparse_cannot_handle(self, capsys):
        for junk in (
            "",
            "not a url at all",
            "postgresql://",
            POOLER.format(pw="pa#ss"),
            "postgresql://user:pw@host:notaport/db",
            "postgresql://user:pw@[unclosed:6543/db",
        ):
            check_db._describe(junk)  # must not raise
        capsys.readouterr()

    def test_never_prints_the_password(self, capsys):
        check_db._describe(POOLER.format(pw="sup3rsecret"))
        assert "sup3rsecret" not in capsys.readouterr().out

    def test_flags_a_username_without_a_project_ref(self, capsys):
        dsn = "postgresql://postgres:pw@aws-1-eu-west-1.pooler.supabase.com:6543/postgres"
        check_db._describe(dsn)
        assert "project ref" in capsys.readouterr().out

    def test_accepts_a_username_with_a_ref(self, capsys):
        check_db._describe(POOLER.format(pw="pw"))
        assert "project ref" not in capsys.readouterr().out

    def test_flags_the_wrong_pooler_port(self, capsys):
        dsn = POOLER.format(pw="pw").replace(":6543", ":5432")
        check_db._describe(dsn)
        assert "6543" in capsys.readouterr().out

    def test_direct_connection_is_not_judged_on_pooler_rules(self, capsys):
        # A direct connection legitimately uses 5432 and a bare username.
        dsn = "postgresql://postgres:pw@db.abcdefg.supabase.co:5432/postgres"
        check_db._describe(dsn)
        out = capsys.readouterr().out
        assert "project ref" not in out and "direct connection" in out


class TestAssemble:
    """Inserting the password ourselves, rather than asking for it inline."""

    def _assemble(self, monkeypatch, password):
        monkeypatch.setattr(check_db.getpass, "getpass", lambda prompt="": password)
        return check_db._assemble(POOLER.format(pw="[YOUR-PASSWORD]"))

    def test_plain_password_is_inserted(self, monkeypatch, capsys):
        dsn = self._assemble(monkeypatch, "abc123")
        capsys.readouterr()
        assert "abc123" in dsn and "YOUR-PASSWORD" not in dsn

    def test_reserved_characters_are_encoded(self, monkeypatch, capsys):
        dsn = self._assemble(monkeypatch, "pa#s/w?d")
        capsys.readouterr()
        assert "%23" in dsn and "%2F" in dsn and "%3F" in dsn
        # And the result no longer trips the warning it was written to avoid.
        assert check_db._encoding_problem(dsn) == ""

    def test_encoded_password_survives_a_round_trip(self, monkeypatch, capsys):
        from urllib.parse import unquote, urlparse

        # urlparse does not percent-decode; libpq does, per RFC 3986. So the
        # check is that decoding what we built returns the original exactly -
        # nothing lost, and nothing double-encoded.
        secret = "p@ss#w/rd?1[2]"
        dsn = self._assemble(monkeypatch, secret)
        capsys.readouterr()
        assert unquote(urlparse(dsn).password) == secret

    def test_the_host_is_still_reachable_after_encoding(self, monkeypatch, capsys):
        from urllib.parse import urlparse

        dsn = self._assemble(monkeypatch, "p@ss")
        capsys.readouterr()
        # Encoding the @ is the whole point: the host must not shift.
        assert urlparse(dsn).hostname == "aws-1-eu-west-1.pooler.supabase.com"
        assert urlparse(dsn).port == 6543

    def test_surrounding_whitespace_is_trimmed(self, monkeypatch, capsys):
        from urllib.parse import urlparse

        dsn = self._assemble(monkeypatch, "  abc123\n")
        capsys.readouterr()
        assert urlparse(dsn).password == "abc123"

    def test_empty_password_is_refused(self, monkeypatch, capsys):
        assert self._assemble(monkeypatch, "") == ""
        capsys.readouterr()

    def test_placeholder_without_brackets(self, monkeypatch, capsys):
        monkeypatch.setattr(check_db.getpass, "getpass", lambda prompt="": "abc")
        dsn = check_db._assemble(POOLER.format(pw="YOUR-PASSWORD"))
        capsys.readouterr()
        assert "YOUR-PASSWORD" not in dsn and "abc" in dsn

    def test_the_password_is_never_echoed(self, monkeypatch, capsys):
        self._assemble(monkeypatch, "sup3rsecret")
        assert "sup3rsecret" not in capsys.readouterr().out


class TestMain:
    """The paths a person actually walks through."""

    def _run(self, monkeypatch, answers):
        replies = iter(answers)
        monkeypatch.setattr(
            check_db.getpass, "getpass", lambda prompt="": next(replies)
        )
        monkeypatch.setattr(check_db.sys, "argv", ["check_db.py"])
        return check_db.main()

    def test_empty_input_stops_early(self, monkeypatch, capsys):
        assert self._run(monkeypatch, [""]) == 2
        assert "Nothing entered" in capsys.readouterr().out

    def test_quotes_from_a_copied_command_are_stripped(self, monkeypatch, capsys):
        # Never reaches the network: no password means we stop first.
        self._run(monkeypatch, ['"' + POOLER.format(pw="[YOUR-PASSWORD]") + '"', ""])
        assert "No password entered" in capsys.readouterr().out

    def test_connection_failure_is_reported_not_raised(self, monkeypatch, capsys):
        def explode(dsn):
            from api.pg import DatabaseConfigError

            raise DatabaseConfigError("something went wrong")

        monkeypatch.setattr("api.pg._verify_credentials", explode)
        code = self._run(monkeypatch, [POOLER.format(pw="[YOUR-PASSWORD]"), "abc"])
        out = capsys.readouterr().out
        assert code == 1
        assert "Did not connect" in out and "something went wrong" in out

    def test_stale_encoding_advice_is_dropped_when_we_encoded(
        self, monkeypatch, capsys
    ):
        # We encoded the password ourselves, so telling the reader to check
        # their encoding sends them after a problem that cannot exist.
        def explode(dsn):
            from api.pg import DatabaseConfigError

            raise DatabaseConfigError(
                "The username looks right, so this is the password. Note that "
                "a password containing @ / ? or # must be percent-encoded "
                "inside a URL."
            )

        monkeypatch.setattr("api.pg._verify_credentials", explode)
        self._run(monkeypatch, [POOLER.format(pw="[YOUR-PASSWORD]"), "abc"])
        out = capsys.readouterr().out
        assert "The encoding is not the issue" in out
        assert "must be percent-encoded" not in out

    def test_encoding_advice_is_kept_when_the_user_pasted_it_themselves(
        self, monkeypatch, capsys
    ):
        def explode(dsn):
            from api.pg import DatabaseConfigError

            raise DatabaseConfigError(
                "This is the password. Note that a password containing @ / ? "
                "or # must be percent-encoded inside a URL."
            )

        monkeypatch.setattr("api.pg._verify_credentials", explode)
        self._run(monkeypatch, [POOLER.format(pw="abc")])
        out = capsys.readouterr().out
        assert "percent-encoded" in out
        assert "The encoding is not the issue" not in out

    def test_success_explains_the_next_step(self, monkeypatch, capsys):
        monkeypatch.setattr("api.pg._verify_credentials", lambda dsn: None)
        monkeypatch.setattr(check_db, "_ask", lambda *a, **k: False)
        code = self._run(monkeypatch, [POOLER.format(pw="[YOUR-PASSWORD]"), "abc"])
        out = capsys.readouterr().out
        assert code == 0
        assert "Connected" in out and "TEST_DATABASE_URL" in out

    def test_success_can_save_and_run_the_contract(self, monkeypatch, capsys):
        saved: dict = {}
        monkeypatch.setattr("api.pg._verify_credentials", lambda dsn: None)
        monkeypatch.setattr(check_db, "_ask", lambda *a, **k: True)
        monkeypatch.setattr(check_db, "_save_to_env", lambda dsn: saved.update(dsn=dsn))
        monkeypatch.setattr(check_db, "_run_contract", lambda dsn: 0)
        code = self._run(monkeypatch, [POOLER.format(pw="[YOUR-PASSWORD]"), "abc"])
        capsys.readouterr()
        assert code == 0
        # What gets saved must be the assembled string, not the template.
        assert "YOUR-PASSWORD" not in saved["dsn"] and "abc" in saved["dsn"]

    def test_a_failing_contract_is_not_reported_as_success(self, monkeypatch, capsys):
        monkeypatch.setattr("api.pg._verify_credentials", lambda dsn: None)
        monkeypatch.setattr(check_db, "_ask", lambda *a, **k: True)
        monkeypatch.setattr(check_db, "_save_to_env", lambda dsn: None)
        monkeypatch.setattr(check_db, "_run_contract", lambda dsn: 1)
        code = self._run(monkeypatch, [POOLER.format(pw="[YOUR-PASSWORD]"), "abc"])
        capsys.readouterr()
        assert code == 1


class TestAsk:
    """A prompt that has to cope with nobody being there."""

    def test_default_is_used_when_input_is_closed(self, monkeypatch):
        def closed(prompt=""):
            raise EOFError

        monkeypatch.setattr("builtins.input", closed)
        assert check_db._ask("q?", default=True) is True
        assert check_db._ask("q?", default=False) is False

    def test_empty_reply_takes_the_default(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": "")
        assert check_db._ask("q?", default=True) is True

    def test_explicit_answers_win(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")
        assert check_db._ask("q?", default=True) is False
        monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
        assert check_db._ask("q?", default=False) is True


class TestSaveToEnv:
    """Writing the string to .env rather than printing it."""

    def test_appends_when_absent(self, tmp_path, capsys):
        env = tmp_path / ".env"
        env.write_text("GROQ_API_KEY=\n", encoding="utf-8")
        check_db._save_to_env("postgresql://x", str(env))
        capsys.readouterr()
        body = env.read_text(encoding="utf-8")
        assert "DATABASE_URL=postgresql://x" in body
        assert "GROQ_API_KEY=" in body  # nothing else was disturbed

    def test_replaces_an_existing_line(self, tmp_path, capsys):
        env = tmp_path / ".env"
        env.write_text("DATABASE_URL=old\nGROQ_API_KEY=k\n", encoding="utf-8")
        check_db._save_to_env("postgresql://new", str(env))
        capsys.readouterr()
        body = env.read_text(encoding="utf-8")
        assert "old" not in body
        assert body.count("DATABASE_URL=") == 1
        assert "GROQ_API_KEY=k" in body

    def test_creates_the_file_when_missing(self, tmp_path, capsys):
        env = tmp_path / ".env"
        check_db._save_to_env("postgresql://x", str(env))
        capsys.readouterr()
        assert "DATABASE_URL=postgresql://x" in env.read_text(encoding="utf-8")

    def test_rewriting_the_same_value_is_a_no_op(self, tmp_path, capsys):
        env = tmp_path / ".env"
        check_db._save_to_env("postgresql://x", str(env))
        before = env.read_text(encoding="utf-8")
        capsys.readouterr()
        check_db._save_to_env("postgresql://x", str(env))
        assert "already has this string" in capsys.readouterr().out
        assert env.read_text(encoding="utf-8") == before

    def test_the_password_is_not_printed(self, tmp_path, capsys):
        env = tmp_path / ".env"
        check_db._save_to_env("postgresql://u:sup3rsecret@h/db", str(env))
        assert "sup3rsecret" not in capsys.readouterr().out

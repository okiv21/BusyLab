"""Tests for the deployed-API checker.

What is under test is the diagnosis, not the network, so every response is
stubbed. The cases are the three failures a browser refuses to distinguish:
a wrong URL, a sleeping instance, and a refused origin.
"""

from __future__ import annotations

import json

import pytest

import check_api


class TestNormalise:
    def test_a_trailing_slash_goes(self):
        assert check_api.normalise("https://a.onrender.com/") == "https://a.onrender.com"

    def test_a_path_is_dropped(self):
        assert (
            check_api.normalise("https://a.onrender.com/health")
            == "https://a.onrender.com"
        )

    def test_https_is_assumed(self):
        assert check_api.normalise("a.onrender.com") == "https://a.onrender.com"

    def test_quotes_and_spaces_survive_a_copy(self):
        assert (
            check_api.normalise('  "https://a.onrender.com/"  ')
            == "https://a.onrender.com"
        )

    def test_an_explicit_port_is_kept(self):
        # Part of the origin, so dropping it would compare the wrong thing.
        assert check_api.normalise("http://localhost:8000") == "http://localhost:8000"

    def test_nonsense_returns_empty(self):
        for junk in ("", "   ", "///", "https://"):
            assert check_api.normalise(junk) == ""


class TestCheckOrigin:
    def test_a_matching_origin_passes(self, capsys):
        code = check_api._check_origin(["https://busylab.vercel.app"], "https://busylab.vercel.app")
        assert code == 0
        assert "is allowed" in capsys.readouterr().out

    def test_a_wildcard_allows_anything(self, capsys):
        assert check_api._check_origin(["*"], "https://anything.dev") == 0
        capsys.readouterr()

    def test_a_missing_origin_fails_and_explains(self, capsys):
        code = check_api._check_origin(
            ["http://localhost:3000"], "https://busylab.vercel.app"
        )
        out = capsys.readouterr().out
        assert code == 1
        assert "not allowed" in out
        assert "BUSYLAB_CORS" in out
        assert "preview" in out  # previews get their own hostname

    def test_without_a_frontend_url_nothing_is_claimed(self, capsys):
        # Silence would imply success; saying what is unchecked is better.
        assert check_api._check_origin(["http://localhost:3000"], "") == 0
        assert "second argument" in capsys.readouterr().out


class TestMain:
    def _stub(self, monkeypatch, status, body, *, resolves=True, delay=0.0):
        monkeypatch.setattr(check_api, "_resolves", lambda host: resolves)

        def fake_get(url, timeout):
            if delay:
                monkeypatch.setattr(check_api.time, "monotonic", lambda: delay)
            return status, body

        monkeypatch.setattr(check_api, "_get", fake_get)

    def _health(self, **overrides):
        base = {
            "ok": True,
            "version": "0.1.0",
            "pending_jobs": 0,
            "narration": "groq",
            "storage": "supabase",
            "cors_allows": ["https://busylab.vercel.app"],
        }
        base.update(overrides)
        return json.dumps(base)

    def _argv(self, monkeypatch, *args):
        monkeypatch.setattr(check_api.sys, "argv", ["check_api.py", *args])

    def test_a_healthy_api_with_a_matching_frontend(self, monkeypatch, capsys):
        self._stub(monkeypatch, 200, self._health())
        self._argv(monkeypatch, "https://a.onrender.com", "https://busylab.vercel.app")
        assert check_api.main() == 0
        out = capsys.readouterr().out
        assert "The API is up" in out and "is allowed" in out

    def test_a_refused_origin_is_reported(self, monkeypatch, capsys):
        self._stub(monkeypatch, 200, self._health(cors_allows=["http://localhost:3000"]))
        self._argv(monkeypatch, "https://a.onrender.com", "https://busylab.vercel.app")
        assert check_api.main() == 1
        assert "not allowed" in capsys.readouterr().out

    def test_a_404_blames_the_url_not_the_service(self, monkeypatch, capsys):
        # Render wildcards its DNS, so a mistyped name resolves and 404s.
        self._stub(monkeypatch, 404, "Not Found")
        self._argv(monkeypatch, "https://wrong-name.onrender.com")
        assert check_api.main() == 1
        out = capsys.readouterr().out
        assert "resolves whether or not the service exists" in out

    def test_a_500_points_at_the_database(self, monkeypatch, capsys):
        # The store is lazy, so a bad DATABASE_URL deploys clean and fails here.
        self._stub(monkeypatch, 500, "Internal Server Error")
        self._argv(monkeypatch, "https://a.onrender.com")
        assert check_api.main() == 1
        assert "DATABASE_URL" in capsys.readouterr().out

    def test_a_non_json_200_is_not_taken_as_success(self, monkeypatch, capsys):
        self._stub(monkeypatch, 200, "<html>hello</html>")
        self._argv(monkeypatch, "https://a.onrender.com")
        assert check_api.main() == 1
        assert "not with JSON" in capsys.readouterr().out

    def test_a_dead_host_is_named_as_such(self, monkeypatch, capsys):
        self._stub(monkeypatch, 200, "", resolves=False)
        self._argv(monkeypatch, "https://nope.example")
        assert check_api.main() == 1
        assert "does not exist" in capsys.readouterr().out

    def test_local_storage_is_flagged_as_a_data_loss_risk(self, monkeypatch, capsys):
        # Deploys fine, works fine, loses every upload on restart.
        self._stub(monkeypatch, 200, self._health(storage="local"))
        self._argv(monkeypatch, "https://a.onrender.com")
        check_api.main()
        assert "will be lost" in capsys.readouterr().out

    def test_an_older_build_without_the_cors_field_is_handled(self, monkeypatch, capsys):
        body = json.dumps({"ok": True, "version": "0.1.0", "storage": "supabase"})
        self._stub(monkeypatch, 200, body)
        self._argv(monkeypatch, "https://a.onrender.com", "https://busylab.vercel.app")
        assert check_api.main() == 0
        assert "predates" in capsys.readouterr().out

    def test_no_url_at_all_stops(self, monkeypatch, capsys):
        self._argv(monkeypatch)
        monkeypatch.setattr("builtins.input", lambda prompt="": "")
        assert check_api.main() == 2
        assert "Nothing to check" in capsys.readouterr().out


class TestNoAnswer:
    def test_a_silent_host_is_not_called_dead_too_early(self, monkeypatch, capsys):
        """A service that never answers must exhaust the wake window first."""
        import urllib.error

        attempts = []

        monkeypatch.setattr(check_api, "_resolves", lambda host: True)
        monkeypatch.setattr(check_api, "WAKE_SECONDS", 10)
        monkeypatch.setattr(check_api.time, "sleep", lambda s: None)

        clock = iter([0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14])
        monkeypatch.setattr(check_api.time, "monotonic", lambda: next(clock))

        def never(url, timeout):
            attempts.append(url)
            raise urllib.error.URLError("timed out")

        monkeypatch.setattr(check_api, "_get", never)
        monkeypatch.setattr(check_api.sys, "argv", ["check_api.py", "https://a.onrender.com"])

        assert check_api.main() == 1
        out = capsys.readouterr().out
        assert len(attempts) > 1, "gave up after a single try"
        assert "Logs tab" in out
        assert "can show Live and still" in out

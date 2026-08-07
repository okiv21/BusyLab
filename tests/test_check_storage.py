"""Tests for the storage checker and its error translation.

The value of this tool is entirely in what it says when something is wrong, so
that is what is tested. A real bucket is never contacted; the store is stubbed,
because the point under test is the diagnosis rather than Supabase itself.
"""

from __future__ import annotations

import io
import urllib.error

import pytest

import check_storage
from api.storage import StorageError, SupabaseFileStore, _explain_http


def _http_error(code: int, reason: str, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://x.supabase.co/storage/v1/object/uploads/f.csv",
        code=code,
        msg=reason,
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(body.encode()),
    )


class TestExplainHttp:
    """Every one of these is a deployment mistake made once, in the dark."""

    def test_401_names_the_right_key(self):
        message = _explain_http(_http_error(401, "Unauthorized"), "uploads", "f.csv")
        assert "service_role" in message and "anon" in message

    def test_403_is_treated_as_a_key_problem_too(self):
        message = _explain_http(_http_error(403, "Forbidden"), "uploads", "f.csv")
        assert "SUPABASE_SERVICE_KEY" in message

    def test_missing_bucket_names_the_bucket(self):
        message = _explain_http(
            _http_error(404, "Not Found", '{"message":"Bucket not found"}'),
            "uploads",
            "f.csv",
        )
        assert "'uploads'" in message and "Storage" in message

    def test_missing_object_is_not_confused_with_a_missing_bucket(self):
        message = _explain_http(
            _http_error(404, "Not Found", '{"message":"Object not found"}'),
            "uploads",
            "f.csv",
        )
        assert "No object" in message
        assert "Create it" not in message

    def test_size_limit(self):
        message = _explain_http(_http_error(413, "Payload Too Large"), "u", "f.csv")
        assert "size limit" in message

    def test_size_limit_recognised_from_the_body(self):
        # Supabase reports this as 400 with the reason in the body.
        message = _explain_http(
            _http_error(400, "Bad Request", '{"message":"exceeded maximum size"}'),
            "u",
            "f.csv",
        )
        assert "size limit" in message

    def test_mime_restriction_names_the_type_that_was_sent(self):
        message = _explain_http(
            _http_error(400, "Bad Request", '{"message":"mime type not supported"}'),
            "u",
            "f.xlsx",
        )
        assert "spreadsheetml" in message

    def test_the_body_is_always_included(self):
        message = _explain_http(_http_error(400, "Bad Request", "kaboom"), "u", "f")
        assert "kaboom" in message

    def test_an_unreadable_body_does_not_mask_the_status(self):
        error = _http_error(500, "Server Error")
        error.read = lambda: (_ for _ in ()).throw(OSError("gone"))  # type: ignore
        assert "500" in _explain_http(error, "u", "f")


class TestKeyCheck:
    """The anon key authenticates perfectly and cannot write."""

    def _jwt(self, role: str) -> str:
        import base64
        import json

        payload = base64.urlsafe_b64encode(
            json.dumps({"role": role}).encode()
        ).decode().rstrip("=")
        return f"header.{payload}.signature"

    def test_anon_key_is_caught(self):
        assert "anon key" in check_storage._check_key(self._jwt("anon"))

    def test_service_role_passes(self):
        assert check_storage._check_key(self._jwt("service_role")) == ""

    def test_another_role_is_named(self):
        assert "authenticated" in check_storage._check_key(self._jwt("authenticated"))

    def test_publishable_key_is_caught(self):
        assert "publishable" in check_storage._check_key("sb_publishable_abc123")

    def test_secret_key_passes(self):
        assert check_storage._check_key("sb_secret_abc123") == ""

    def test_undecodable_key_is_not_guessed_at(self):
        # Better to say nothing than to accuse a valid key of being wrong.
        assert check_storage._check_key("not.a.jwt") == ""
        assert check_storage._check_key("") == ""


class TestUrlCheck:
    def test_project_url_passes(self):
        assert check_storage._check_url("https://abc.supabase.co") == ""

    def test_storage_path_is_caught(self):
        assert "no path" in check_storage._check_url(
            "https://abc.supabase.co/storage/v1"
        )

    def test_trailing_storage_segment_is_caught(self):
        assert "no path" in check_storage._check_url("https://abc.supabase.co/storage")

    def test_missing_scheme_is_caught(self):
        assert "https" in check_storage._check_url("abc.supabase.co")


class _FakeStore:
    """A stand-in bucket, with each step independently breakable."""

    def __init__(self, fail=None, corrupt=False, undeletable=False):
        self.fail = fail
        self.corrupt = corrupt
        self.undeletable = undeletable
        self.objects: dict[str, bytes] = {}

    def put(self, key, data):
        if self.fail == "put":
            raise StorageError("no bucket")
        self.objects[key] = data
        return key

    def get(self, key):
        if self.fail == "get":
            raise StorageError("cannot read")
        return b"different" if self.corrupt else self.objects[key]

    def delete(self, key):
        if not self.undeletable:
            self.objects.pop(key, None)

    def exists(self, key):
        return key in self.objects


class TestMain:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.setattr(check_storage, "_load_env", lambda path=".env": None)
        monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb_secret_x")
        monkeypatch.setenv("SUPABASE_BUCKET", "uploads")
        # Stubbed, or these reach out to a real host and the suite becomes
        # slow and dependent on the network being up.
        monkeypatch.setattr(check_storage, "_list_buckets", lambda u, k: ["uploads"])

    def _run(self, monkeypatch, store):
        monkeypatch.setattr(
            "api.storage.SupabaseFileStore", lambda **kwargs: store
        )
        return check_storage.main()

    def test_a_clean_round_trip_succeeds(self, monkeypatch, capsys):
        store = _FakeStore()
        assert self._run(monkeypatch, store) == 0
        out = capsys.readouterr().out
        assert "Storage works" in out
        # The probe must not be left behind in the bucket.
        assert store.objects == {}

    def test_a_failed_upload_is_reported(self, monkeypatch, capsys):
        assert self._run(monkeypatch, _FakeStore(fail="put")) == 1
        assert "Upload failed" in capsys.readouterr().out

    def test_a_failed_download_is_reported(self, monkeypatch, capsys):
        assert self._run(monkeypatch, _FakeStore(fail="get")) == 1
        assert "Download failed" in capsys.readouterr().out

    def test_corrupted_bytes_are_caught(self, monkeypatch, capsys):
        # A round trip that returns different bytes would corrupt every
        # upload, and no status code would report it.
        assert self._run(monkeypatch, _FakeStore(corrupt=True)) == 1
        assert "came back different" in capsys.readouterr().out

    def test_the_probe_is_removed_even_when_a_step_fails(self, monkeypatch, capsys):
        store = _FakeStore(fail="get")
        self._run(monkeypatch, store)
        capsys.readouterr()
        assert store.objects == {}

    def test_a_broken_delete_warns_without_failing(self, monkeypatch, capsys):
        # Uploads work; the bucket just accumulates. That is not a deploy
        # blocker, and reporting it as one would be wrong.
        code = self._run(monkeypatch, _FakeStore(undeletable=True))
        out = capsys.readouterr().out
        assert code == 0
        assert "build up" in out and "Storage works" in out

    def test_it_uses_a_spreadsheet_type_not_plain_text(self):
        from api.storage import content_type_for

        # A MIME-restricted bucket accepts text/plain and rejects the real
        # thing, so a probe that is not a spreadsheet proves nothing.
        assert content_type_for(check_storage.SAMPLE_KEY) == "text/csv"


class TestStoreWiring:
    def _capture(self, monkeypatch) -> list:
        """Intercept the outgoing request instead of sending it."""
        sent = []

        def fake_urlopen(request, timeout=None):
            sent.append(request)

            class _Response:
                def read(self):
                    return b""

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

            return _Response()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        return sent

    def test_an_upload_declares_the_real_spreadsheet_type(self, monkeypatch):
        # An octet-stream upload works until the bucket restricts MIME types,
        # and then every upload is rejected for being the wrong kind of file.
        sent = self._capture(monkeypatch)
        store = SupabaseFileStore(url="https://x.supabase.co", key="k", bucket="b")
        store.put("a.xlsx", b"data")
        assert sent[0].get_header("Content-type").endswith("spreadsheetml.sheet")

    def test_an_upload_sends_the_service_key_both_ways(self, monkeypatch):
        sent = self._capture(monkeypatch)
        store = SupabaseFileStore(url="https://x.supabase.co", key="secret", bucket="b")
        store.put("a.csv", b"data")
        assert sent[0].get_header("Authorization") == "Bearer secret"
        assert sent[0].get_header("Apikey") == "secret"

    def test_a_download_sends_no_content_type(self, monkeypatch):
        sent = self._capture(monkeypatch)
        store = SupabaseFileStore(url="https://x.supabase.co", key="k", bucket="b")
        store.get("a.xlsx")
        assert sent[0].get_header("Content-type") is None

    def test_the_key_is_escaped_into_the_url(self, monkeypatch):
        sent = self._capture(monkeypatch)
        store = SupabaseFileStore(url="https://x.supabase.co", key="k", bucket="b")
        store.get("a file & co.csv")
        assert " " not in sent[0].full_url
        assert "/storage/v1/object/b/" in sent[0].full_url

    def test_unreachable_url_advice_names_the_variable(self):
        store = SupabaseFileStore(url="http://127.0.0.1:1", key="k", bucket="b")
        with pytest.raises(StorageError) as caught:
            store.put("a.csv", b"x")
        assert "SUPABASE_URL" in str(caught.value)


class TestBucketListing:
    """"Bucket not found" is least useful exactly when you know you made one."""

    def _run(self, monkeypatch, capsys, names, bucket="uploads"):
        monkeypatch.setattr(check_storage, "_load_env", lambda path=".env": None)
        monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb_secret_x")
        monkeypatch.setenv("SUPABASE_BUCKET", bucket)
        monkeypatch.setattr(check_storage, "_list_buckets", lambda u, k: names)
        monkeypatch.setattr("api.storage.SupabaseFileStore", lambda **kw: _FakeStore())
        return check_storage.main(), capsys.readouterr().out

    def test_the_real_names_are_shown(self, monkeypatch, capsys):
        code, out = self._run(monkeypatch, capsys, ["Uploads", "avatars"])
        assert code == 1
        # The near-miss is the whole point: it is almost always the name.
        assert "Uploads" in out and "avatars" in out
        # Intact, not split across a line break: hyphenated terms are the
        # searchable part of the advice, which is why _fill does not break
        # on hyphens.
        assert "case-sensitive" in out

    def test_an_empty_project_points_at_the_wrong_project(self, monkeypatch, capsys):
        code, out = self._run(monkeypatch, capsys, [])
        assert code == 1
        assert "different Supabase project" in out

    def test_a_matching_bucket_proceeds(self, monkeypatch, capsys):
        code, out = self._run(monkeypatch, capsys, ["uploads"])
        assert code == 0
        assert "Storage works" in out

    def test_an_unlistable_project_is_not_called_empty(self, monkeypatch, capsys):
        # None means "could not ask". Reporting that as "you have no buckets"
        # would send someone off to recreate what already exists.
        code, out = self._run(monkeypatch, capsys, None)
        assert code == 0
        assert "no buckets at all" not in out

    def test_listing_failure_returns_none_not_empty(self, monkeypatch):
        import urllib.error

        def boom(request, timeout=None):
            raise urllib.error.URLError("no route")

        monkeypatch.setattr("urllib.request.urlopen", boom)
        assert check_storage._list_buckets("https://x", "k") is None

    def test_a_listing_is_parsed_into_names(self, monkeypatch):
        import io
        import json

        class _Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        payload = json.dumps([{"name": "uploads"}, {"name": "avatars"}]).encode()
        monkeypatch.setattr(
            "urllib.request.urlopen", lambda r, timeout=None: _Response(payload)
        )
        assert check_storage._list_buckets("https://x", "k") == ["uploads", "avatars"]

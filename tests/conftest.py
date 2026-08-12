"""Test-wide isolation from the outside world.

Several test modules already called ``monkeypatch.delenv("GROQ_API_KEY")`` to
mean "no model here". That stopped working the moment a real key existed,
because ``from_env`` reads .env and a deleted name is one the file is free to
supply again. Every one of those tests then reached the live API: the suite made
hundreds of network calls, took fourteen hours instead of fifteen minutes, spent
real quota, and one test failed for asserting the narration provider was absent
when it was not.

Switching the provider off here rather than unsetting the key is deliberate.
``from_env`` checks this first and returns the null provider whatever key is
present, so it cannot be defeated by a file, and the tests that deliberately
delete the key are unaffected by it.

A test that genuinely wants a provider still sets one explicitly. Nothing here
prevents that; it only stops the default being "whatever is on this machine".
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_outside_world(monkeypatch: pytest.MonkeyPatch) -> None:
    """No language model and no mail server, whatever the developer has set up.

    Both default to a local no-op, which is a supported configuration rather
    than a broken one: the engine's own wording is used, and a digest is written
    to the log. So the suite tests the code and not the machine it runs on.
    """
    monkeypatch.setenv("BUSYLAB_LLM_PROVIDER", "none")
    monkeypatch.setenv("BUSYLAB_MAILER", "log")

    # Every key, not just the one that existed when this was written. Adding
    # OpenRouter broke two tests that empty GROQ_API_KEY and then delete
    # BUSYLAB_LLM_PROVIDER, because the second key was still being read from
    # .env and happily supplied a provider. Any new backend needs its key
    # added here, or the suite starts depending on the machine again.
    for key in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "BUSYLAB_LLM_ENDPOINT"):
        monkeypatch.setenv(key, "")

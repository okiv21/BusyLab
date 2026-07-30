"""Talking to a language model, optionally.

The engine computes everything and the model only writes sentences and routes
questions (spec 2, 8). That division means the model is genuinely optional, and
this module is built so BusyLab runs completely without one: no API key, no
network, no degraded correctness, only plainer prose.

Deliberately built on ``urllib`` from the standard library rather than
``requests`` or a vendor SDK. The dependency footprint of the engine is a
deployment constraint (spec 9: memory, not CPU, is what bites), and an
OpenAI-compatible endpoint is a POST with a JSON body.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)

#: Groq's free tier is the default target: fastest of the free options and
#: OpenAI-compatible, so swapping providers is configuration rather than code.
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

#: Prose quality matters here and the calls are cached, so volume stays low.
DEFAULT_NARRATION_MODEL = "llama-3.3-70b-versatile"
#: Routing is classification and sits in an interactive path, so latency wins.
DEFAULT_ROUTING_MODEL = "llama-3.1-8b-instant"


class ProviderError(RuntimeError):
    """The model could not be reached or refused to answer usefully."""


@runtime_checkable
class Provider(Protocol):
    """Anything that can turn a prompt into text."""

    name: str

    def available(self) -> bool:
        """False when the provider cannot be used, e.g. no API key."""
        ...

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 200,
        temperature: float = 0.2,
    ) -> str:
        ...


@dataclass
class NullProvider:
    """No model at all. Every caller falls back to deterministic text.

    This is the default, and it is a supported configuration rather than a
    broken one: the numbers and the findings are identical either way.
    """

    name: str = "none"

    def available(self) -> bool:
        return False

    def complete(self, system: str, user: str, **kwargs) -> str:
        raise ProviderError("No language model is configured.")


@dataclass
class GroqProvider:
    """Groq's free tier, via its OpenAI-compatible endpoint.

    The free tier's binding constraint is tokens per minute rather than
    requests per minute, so callers should send one small payload at a time
    and cache the result rather than batching everything into one large call.
    """

    api_key: str = field(default_factory=lambda: os.environ.get("GROQ_API_KEY", ""))
    model: str = DEFAULT_NARRATION_MODEL
    endpoint: str = GROQ_ENDPOINT
    timeout: float = 20.0
    #: Free tiers rate-limit rather than fail outright, so one polite retry.
    max_retries: int = 2
    name: str = "groq"

    def available(self) -> bool:
        return bool(self.api_key)

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 200,
        temperature: float = 0.2,
    ) -> str:
        if not self.available():
            raise ProviderError("GROQ_API_KEY is not set.")

        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        ).encode("utf-8")

        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                return body["choices"][0]["message"]["content"].strip()
            except urllib.error.HTTPError as exc:
                last_error = exc
                # 429 is the free tier working as intended, not a failure.
                if exc.code == 429 and attempt < self.max_retries:
                    wait = float(exc.headers.get("retry-after", 2 * (attempt + 1)))
                    log.info("rate limited, waiting %.1fs", wait)
                    time.sleep(min(wait, 10.0))
                    continue
                break
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
                last_error = exc
                break

        raise ProviderError(f"Groq request failed: {last_error}")


def load_dotenv(start: str | os.PathLike[str] | None = None) -> bool:
    """Read a ``.env`` file into the environment, if one exists.

    Written by hand rather than pulled in as a dependency: it is a dozen lines,
    and the engine's dependency footprint is a deployment constraint (spec 9).

    Real environment variables always win, so a value exported in the shell or
    set by the host is never silently overridden by a stale file. Searches the
    working directory and its parents so it works from anywhere in the repo.
    """
    from pathlib import Path

    here = Path(start or os.getcwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / ".env"
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value
        return True
    return False


def from_env(*, model: str | None = None) -> Provider:
    """Build a provider from the environment.

    Returns :class:`NullProvider` when nothing is configured, so importing this
    module never makes the engine depend on a network call.
    """
    load_dotenv()
    choice = os.environ.get("BUSYLAB_LLM_PROVIDER", "auto").lower()
    key = os.environ.get("GROQ_API_KEY", "")
    chosen_model = model or os.environ.get("BUSYLAB_LLM_MODEL") or DEFAULT_NARRATION_MODEL

    if choice in {"none", "off", "null"}:
        return NullProvider()
    if choice in {"groq", "auto"} and key:
        return GroqProvider(api_key=key, model=chosen_model)
    return NullProvider()

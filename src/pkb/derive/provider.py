"""Replaceable, OpenAI-compatible derivation provider contract."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import OpenerDirector, Request, build_opener


class ProviderError(RuntimeError):
    """Base class for sanitized provider failures."""


class ProviderAuthError(ProviderError):
    """The provider rejected authentication or authorization."""


class ProviderRateLimitError(ProviderError):
    """The provider rate-limited the request."""


class ProviderInvalidRequestError(ProviderError):
    """The request or structured provider response was invalid."""


class ProviderTemporaryError(ProviderError):
    """A transport or server failure may succeed when retried."""


@runtime_checkable
class DerivationProvider(Protocol):
    provider_name: str
    model_name: str

    def complete(self, *, system: str, user: str) -> Mapping[str, object]: ...


class FakeDerivationProvider:
    """Deterministic offline provider used by contract and pipeline tests."""

    provider_name = "fake"
    model_name = "fake-model"

    def __init__(self, responses: Sequence[Mapping[str, object] | Exception]):
        self.responses = list(responses)
        self.requests: list[tuple[str, str]] = []

    def complete(self, *, system: str, user: str) -> Mapping[str, object]:
        self.requests.append((system, user))
        if not self.responses:
            raise ProviderInvalidRequestError("fake provider has no response")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class OpenAICompatibleProvider:
    """Minimal chat-completions client with a single socket timeout.

    ``urllib.request`` exposes one timeout for the underlying blocking socket,
    so ``timeout_seconds`` applies to both connection establishment and response
    reads rather than pretending they can be configured independently.
    """

    provider_name = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60,
        opener: OpenerDirector | Any | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model_name = model
        self.timeout_seconds = timeout_seconds
        self._opener = opener or build_opener()

    def complete(self, *, system: str, user: str) -> Mapping[str, object]:
        body = json.dumps(
            {
                "model": self.model_name,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
            }
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                envelope = json.load(response)
        except HTTPError as exc:
            raise _classified_http_error(exc.code) from None
        except json.JSONDecodeError:
            raise ProviderInvalidRequestError(
                "provider returned an invalid structured response"
            ) from None
        except (URLError, TimeoutError, OSError):
            raise ProviderTemporaryError("provider request failed temporarily") from None

        try:
            content = envelope["choices"][0]["message"]["content"]
            result = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            raise ProviderInvalidRequestError(
                "provider returned an invalid structured response"
            ) from None
        if not isinstance(result, dict):
            raise ProviderInvalidRequestError(
                "provider returned an invalid structured response"
            )
        return result


def _classified_http_error(status: int) -> ProviderError:
    if status in {401, 403}:
        return ProviderAuthError(f"provider authentication failed (HTTP {status})")
    if status == 429:
        return ProviderRateLimitError("provider rate limit exceeded (HTTP 429)")
    if status == 408:
        return ProviderTemporaryError("provider request timed out (HTTP 408)")
    if 400 <= status < 500:
        return ProviderInvalidRequestError(f"provider rejected request (HTTP {status})")
    return ProviderTemporaryError(f"provider request failed temporarily (HTTP {status})")

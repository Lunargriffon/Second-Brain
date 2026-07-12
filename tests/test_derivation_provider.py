from __future__ import annotations

import json
from email.message import Message
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from pkb.derive.prompts import ARTICLE_PROMPT_VERSION, ARTICLE_SYSTEM_PROMPT
from pkb.derive.provider import (
    DerivationProvider,
    FakeDerivationProvider,
    OpenAICompatibleProvider,
    ProviderAuthError,
    ProviderInvalidRequestError,
    ProviderRateLimitError,
    ProviderTemporaryError,
)


class _Response:
    def __init__(self, payload: object):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return BytesIO(self._body)

    def __exit__(self, *args):
        return None


class _Opener:
    def __init__(self, response: object):
        self.response = response
        self.calls: list[tuple[object, float]] = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return _Response(self.response)


class _RawOpener:
    def open(self, request, timeout):
        return _ResponseBytes(b"not-json")


class _ResponseBytes:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return BytesIO(self.body)

    def __exit__(self, *args):
        return None


def _http_error(status: int, body: str = "provider detail") -> HTTPError:
    return HTTPError(
        "https://llm.example/v1/chat/completions",
        status,
        "failure",
        Message(),
        BytesIO(body.encode("utf-8")),
    )


def test_fake_provider_records_request_without_network():
    provider = FakeDerivationProvider([{"summary": "s"}])

    result = provider.complete(system="rules", user="content")

    assert result == {"summary": "s"}
    assert provider.requests == [("rules", "content")]
    assert isinstance(provider, DerivationProvider)


def test_openai_provider_requests_structured_json_with_explicit_timeout():
    opener = _Opener(
        {"choices": [{"message": {"content": '{"summary":"摘要"}'}}]}
    )
    provider = OpenAICompatibleProvider(
        base_url="https://llm.example/v1/",
        api_key="secret-token",
        model="model-a",
        timeout_seconds=17,
        opener=opener,
    )

    result = provider.complete(system="rules", user="content")

    assert result == {"summary": "摘要"}
    request, timeout = opener.calls[0]
    assert timeout == 17
    assert request.full_url == "https://llm.example/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer secret-token"
    body = json.loads(request.data)
    assert body == {
        "model": "model-a",
        "messages": [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "content"},
        ],
        "response_format": {"type": "json_object"},
    }


@pytest.mark.parametrize(
    ("failure", "error_type"),
    [
        (_http_error(401), ProviderAuthError),
        (_http_error(403), ProviderAuthError),
        (_http_error(429), ProviderRateLimitError),
        (_http_error(400), ProviderInvalidRequestError),
        (_http_error(422), ProviderInvalidRequestError),
        (_http_error(408), ProviderTemporaryError),
        (_http_error(500), ProviderTemporaryError),
        (URLError("timed out"), ProviderTemporaryError),
    ],
)
def test_provider_classifies_errors_without_leaking_api_key(failure, error_type):
    provider = OpenAICompatibleProvider(
        base_url="https://llm.example/v1",
        api_key="super-secret-key",
        model="model-a",
        opener=_Opener(failure),
    )

    with pytest.raises(error_type) as caught:
        provider.complete(system="rules", user="content")

    assert "super-secret-key" not in str(caught.value)
    assert "provider detail" not in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": "not json"}}]},
        {"choices": [{"message": {"content": "[]"}}]},
    ],
)
def test_provider_rejects_invalid_structured_responses(payload):
    provider = OpenAICompatibleProvider(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="model-a",
        opener=_Opener(payload),
    )

    with pytest.raises(ProviderInvalidRequestError):
        provider.complete(system="rules", user="content")


def test_provider_classifies_non_json_response_as_invalid_structure():
    provider = OpenAICompatibleProvider(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="model-a",
        opener=_RawOpener(),
    )

    with pytest.raises(ProviderInvalidRequestError):
        provider.complete(system="rules", user="content")


def test_article_prompt_is_versioned_and_grounded():
    assert ARTICLE_PROMPT_VERSION == "article-v1"
    assert "JSON" in ARTICLE_SYSTEM_PROMPT
    assert "中文" in ARTICLE_SYSTEM_PROMPT
    assert "原文" in ARTICLE_SYSTEM_PROMPT
    assert "外部事实" in ARTICLE_SYSTEM_PROMPT

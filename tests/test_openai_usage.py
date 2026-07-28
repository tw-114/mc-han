from __future__ import annotations

import io
import json
import urllib.error

import pytest

from mc_han.translator.base import TranslationSegment
from mc_han.translator.openai_provider import OpenAICompatibleTranslator
from mc_han.translator.usage import ProviderAttemptError
from mc_han.usage.models import UsageOutcome


class FakeResponse:
    def __init__(self, payload: dict[str, object]):
        self.payload = payload
        self.headers = {"x-request-id": "private-provider-request-id"}

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def test_openai_compatible_success_returns_normalized_usage(monkeypatch):
    payload = {
        "id": "response-private-id",
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "translations": [
                                {"id": "one", "translation": "译文 one"}
                            ]
                        }
                    )
                }
            }
        ],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 4,
            "total_tokens": 16,
        },
    }
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: FakeResponse(payload),
    )
    translator = make_translator()

    result = translator.translate_batch_with_usage(
        [TranslationSegment(id="one", text="Original")]
    )

    assert result.translations == ("译文 one",)
    assert result.usage.tokens.input_tokens == 12
    assert result.usage.tokens.output_tokens == 4
    assert result.provider_request_id.startswith("sha256:")
    assert "private-provider-request-id" not in result.provider_request_id


def test_openai_compatible_missing_usage_does_not_fail_translation(
    monkeypatch,
):
    payload = {
        "choices": [
            {
                "message": {
                    "content": '{"translations":[{"id":"one","translation":"译文"}]}'
                }
            }
        ]
    }
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: FakeResponse(payload),
    )

    result = make_translator().translate_batch_with_usage(
        [TranslationSegment(id="one", text="Original")]
    )

    assert result.translations == ("译文",)
    assert result.usage.tokens.input_tokens is None
    assert result.usage.diagnostics == ("usage_missing",)


def test_http_429_failure_preserves_usage_without_raw_body(monkeypatch):
    body = json.dumps(
        {
            "error": {"message": "PRIVATE API ERROR BODY"},
            "usage": {
                "prompt_tokens": 22,
                "completion_tokens": 2,
            },
        }
    ).encode()
    error = urllib.error.HTTPError(
        "https://example.invalid",
        429,
        "rate limited",
        {"x-request-id": "private-failed-request-id"},
        io.BytesIO(body),
    )

    def raise_error(*_args: object, **_kwargs: object) -> object:
        raise error

    monkeypatch.setattr("urllib.request.urlopen", raise_error)

    with pytest.raises(ProviderAttemptError) as caught:
        make_translator().translate_batch_with_usage(
            [TranslationSegment(id="one", text="Original")]
        )

    assert caught.value.outcome is UsageOutcome.RATE_LIMITED
    assert caught.value.stable_error_code == "http_rate_limited"
    assert caught.value.usage.tokens.input_tokens == 22
    assert str(caught.value) == "http_rate_limited"
    assert "PRIVATE API ERROR BODY" not in str(caught.value)
    assert caught.value.provider_request_id.startswith("sha256:")


def test_invalid_response_with_usage_is_classified(monkeypatch):
    payload = {
        "choices": [{"message": {"content": "not json"}}],
        "usage": {"prompt_tokens": 9, "completion_tokens": 3},
    }
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: FakeResponse(payload),
    )

    with pytest.raises(ProviderAttemptError) as caught:
        make_translator().translate_batch_with_usage(
            [TranslationSegment(id="one", text="Original")]
        )

    assert caught.value.outcome is UsageOutcome.INVALID_RESPONSE
    assert caught.value.usage.tokens.input_tokens == 9
    assert caught.value.usage.tokens.output_tokens == 3


def test_timeout_is_classified_without_network_details(monkeypatch):
    def raise_timeout(*_args: object, **_kwargs: object) -> object:
        raise TimeoutError("PRIVATE NETWORK PATH")

    monkeypatch.setattr("urllib.request.urlopen", raise_timeout)

    with pytest.raises(ProviderAttemptError) as caught:
        make_translator().translate_batch_with_usage(
            [TranslationSegment(id="one", text="Original")]
        )

    assert caught.value.outcome is UsageOutcome.TIMEOUT
    assert caught.value.stable_error_code == "request_timeout"
    assert "PRIVATE NETWORK PATH" not in str(caught.value)


def make_translator() -> OpenAICompatibleTranslator:
    return OpenAICompatibleTranslator(
        provider_name="fake",
        model="fake-model",
        api_key="sk-test-secret-never-persist",
        base_url="https://example.invalid/v1",
    )

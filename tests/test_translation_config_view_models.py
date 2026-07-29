from __future__ import annotations

import urllib.request
from pathlib import Path

import pytest

from mc_han.qt.translation_config_view_models import (
    TranslationProvider,
    TranslationSessionConfig,
    recommended_translation_config,
    validate_translation_config,
)


@pytest.mark.parametrize(
    ("provider", "base_url", "model"),
    [
        (
            TranslationProvider.DEEPSEEK,
            "https://api.deepseek.com",
            "deepseek-chat",
        ),
        (
            TranslationProvider.OPENAI,
            "https://api.openai.com/v1",
            "gpt-4o-mini",
        ),
        (
            TranslationProvider.OPENAI_COMPATIBLE,
            "https://example.test/v1",
            "local-model",
        ),
    ],
)
def test_supported_provider_configurations_create_existing_client_without_network(
    monkeypatch: pytest.MonkeyPatch,
    provider: TranslationProvider,
    base_url: str,
    model: str,
):
    def reject_network(*args, **kwargs):
        raise AssertionError("configuration validation must not use the network")

    monkeypatch.setattr(urllib.request, "urlopen", reject_network)
    config = TranslationSessionConfig(
        provider=provider,
        base_url=base_url,
        model=model,
        api_key="sk-test-only",
    )

    result = validate_translation_config(config)

    assert result.valid
    assert "不会发送网络请求" in result.message


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("base_url", ""),
        ("base_url", "not-a-url"),
        ("model", ""),
        ("api_key", ""),
    ],
)
def test_required_configuration_fields_are_validated(
    field_name: str,
    value: str,
):
    values = {
        "provider": TranslationProvider.DEEPSEEK,
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_key": "sk-test-only",
    }
    values[field_name] = value

    result = validate_translation_config(TranslationSessionConfig(**values))

    assert not result.valid
    assert field_name in {name for name, _message in result.field_errors}


def test_api_key_is_excluded_from_repr_serializable_view_logs_and_files(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    secret = "sk-private-test-value"
    config = TranslationSessionConfig(
        provider=TranslationProvider.DEEPSEEK,
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        api_key=secret,
    )

    result = validate_translation_config(config)

    assert result.valid
    assert secret not in repr(config)
    assert secret not in repr(config.without_secret())
    assert secret not in caplog.text
    assert list(tmp_path.iterdir()) == []


def test_recommended_config_uses_existing_provider_presets():
    deepseek = recommended_translation_config(TranslationProvider.DEEPSEEK)
    openai = recommended_translation_config(TranslationProvider.OPENAI)
    custom = recommended_translation_config(
        TranslationProvider.OPENAI_COMPATIBLE
    )

    assert deepseek.base_url == "https://api.deepseek.com"
    assert deepseek.model == "deepseek-v4-flash"
    assert deepseek.high_quality_model == "deepseek-v4-pro"
    assert openai.base_url == "https://api.openai.com/v1"
    assert custom.base_url == ""
    assert custom.model == ""

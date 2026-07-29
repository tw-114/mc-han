from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import Enum
from urllib.parse import urlsplit

from mc_han.translator.batching import SPEED_MODE_CONFIGS
from mc_han.translator.openai_provider import (
    OpenAICompatibleTranslator,
    PROVIDER_PRESETS,
)


class TranslationProvider(str, Enum):
    DEEPSEEK = "deepseek"
    OPENAI = "openai"
    OPENAI_COMPATIBLE = "custom"


PROVIDER_DISPLAY_NAMES = {
    TranslationProvider.DEEPSEEK: "DeepSeek",
    TranslationProvider.OPENAI: "OpenAI",
    TranslationProvider.OPENAI_COMPATIBLE: "OpenAI-compatible",
}

RECOMMENDED_MODELS = {
    TranslationProvider.DEEPSEEK: "deepseek-v4-flash",
    TranslationProvider.OPENAI: "gpt-4o-mini",
    TranslationProvider.OPENAI_COMPATIBLE: "",
}
RECOMMENDED_HIGH_QUALITY_MODELS = {
    TranslationProvider.DEEPSEEK: "deepseek-v4-pro",
    TranslationProvider.OPENAI: "gpt-4.1",
    TranslationProvider.OPENAI_COMPATIBLE: "",
}

DEFAULT_CONCURRENCY = 1
DEFAULT_BATCH_SIZE = SPEED_MODE_CONFIGS["balanced"].max_items
DEFAULT_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class TranslationSessionConfig:
    provider: TranslationProvider
    base_url: str
    model: str
    api_key: str = field(repr=False)
    high_quality_model: str = ""
    concurrency: int = DEFAULT_CONCURRENCY
    batch_size: int = DEFAULT_BATCH_SIZE
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not isinstance(self.provider, TranslationProvider):
            raise TypeError("provider must be a TranslationProvider")
        for field_name in (
            "base_url",
            "model",
            "api_key",
            "high_quality_model",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string")
            object.__setattr__(self, field_name, value.strip())
        _validate_bounded_int("concurrency", self.concurrency, 1, 3)
        _validate_bounded_int("batch_size", self.batch_size, 1, 150)
        _validate_bounded_int("timeout_seconds", self.timeout_seconds, 1, 600)

    def without_secret(self) -> dict[str, object]:
        return {
            "provider": self.provider.value,
            "base_url": self.base_url,
            "model": self.model,
            "high_quality_model": self.high_quality_model,
            "concurrency": self.concurrency,
            "batch_size": self.batch_size,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class TranslationConfigPageViewModel:
    config: TranslationSessionConfig
    selected_record_count: int
    selected_category_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.config, TranslationSessionConfig):
            raise TypeError("config must be a TranslationSessionConfig")
        for field_name in ("selected_record_count", "selected_category_count"):
            value = getattr(self, field_name)
            if type(value) is not int:
                raise TypeError(f"{field_name} must be an integer")
            if value < 0:
                raise ValueError(f"{field_name} must not be negative")

    @property
    def selection_summary(self) -> str:
        return (
            f"已选择 {self.selected_record_count:,} 条内容，"
            f"共 {self.selected_category_count:,} 个分类"
        )


@dataclass(frozen=True)
class TranslationConfigValidation:
    valid: bool
    message: str
    field_errors: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if type(self.valid) is not bool:
            raise TypeError("valid must be a bool")
        object.__setattr__(self, "field_errors", tuple(self.field_errors))


TranslatorFactory = Callable[..., OpenAICompatibleTranslator]


def recommended_translation_config(
    provider: TranslationProvider = TranslationProvider.DEEPSEEK,
    *,
    api_key: str = "",
) -> TranslationSessionConfig:
    if not isinstance(provider, TranslationProvider):
        raise TypeError("provider must be a TranslationProvider")
    preset = PROVIDER_PRESETS.get(provider.value)
    return TranslationSessionConfig(
        provider=provider,
        base_url=preset.base_url if preset is not None else "",
        model=RECOMMENDED_MODELS[provider],
        api_key=api_key,
        high_quality_model=RECOMMENDED_HIGH_QUALITY_MODELS[provider],
    )


def validate_translation_config(
    config: TranslationSessionConfig,
    *,
    translator_factory: TranslatorFactory = OpenAICompatibleTranslator,
) -> TranslationConfigValidation:
    errors: list[tuple[str, str]] = []
    if not config.base_url:
        errors.append(("base_url", "请输入 Base URL。"))
    elif not _is_http_url(config.base_url):
        errors.append(("base_url", "Base URL 必须是有效的 HTTP 或 HTTPS 地址。"))
    if not config.model:
        errors.append(("model", "请输入模型名称。"))
    if not config.api_key:
        errors.append(("api_key", "请输入 API Key。"))
    if errors:
        return TranslationConfigValidation(
            valid=False,
            message="请检查标出的必填配置。",
            field_errors=tuple(errors),
        )

    try:
        create_translator(config, translator_factory=translator_factory)
    except (TypeError, ValueError):
        return TranslationConfigValidation(
            valid=False,
            message="无法使用当前字段创建翻译客户端，请检查配置。",
        )
    return TranslationConfigValidation(
        valid=True,
        message="配置有效。当前验证不会发送网络请求，也不会产生费用。",
    )


def create_translator(
    config: TranslationSessionConfig,
    *,
    translator_factory: TranslatorFactory = OpenAICompatibleTranslator,
) -> OpenAICompatibleTranslator:
    provider_name = (
        config.provider.value
        if config.provider is not TranslationProvider.OPENAI_COMPATIBLE
        else f"custom:{config.base_url.rstrip('/')}"
    )
    return translator_factory(
        provider_name=provider_name,
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        timeout_seconds=config.timeout_seconds,
    )


def with_recommended_values(
    config: TranslationSessionConfig,
    provider: TranslationProvider | None = None,
) -> TranslationSessionConfig:
    return replace(
        recommended_translation_config(
            provider or config.provider,
            api_key=config.api_key,
        )
    )


def _is_http_url(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme.lower() in {"http", "https"}
        and bool(parsed.netloc)
        and not parsed.username
        and not parsed.password
    )


def _validate_bounded_int(
    field_name: str,
    value: object,
    minimum: int,
    maximum: int,
) -> None:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(
            f"{field_name} must be between {minimum} and {maximum}"
        )

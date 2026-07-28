from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from concurrent.futures import CancelledError
from dataclasses import dataclass

from mc_han.usage.models import UsageOutcome

from .base import TranslationSegment
from .glossary import build_glossary_prompt, build_style_prompt
from .protection import ProtectedText, protect_text
from .usage import (
    ProviderAttemptError,
    ProviderAttemptResult,
    normalize_usage,
    response_request_id,
)


@dataclass(frozen=True)
class ProviderPreset:
    name: str
    base_url: str
    api_key_env: str


PROVIDER_PRESETS = {
    "openai": ProviderPreset("openai", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    "deepseek": ProviderPreset("deepseek", "https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    "siliconflow": ProviderPreset("siliconflow", "https://api.siliconflow.cn/v1", "SILICONFLOW_API_KEY"),
    "openrouter": ProviderPreset("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "dashscope": ProviderPreset("dashscope", "https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY"),
    "qwen": ProviderPreset("qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY"),
}


class OpenAICompatibleTranslator:
    is_network_provider = True
    endpoint_type = "chat_completions"
    thinking_mode = ""

    def __init__(
        self,
        *,
        provider_name: str,
        model: str,
        api_key: str,
        base_url: str,
        timeout_seconds: int = 120,
    ):
        self.provider_name = provider_name
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def translate_batch(self, segments: list[TranslationSegment]) -> list[str]:
        return list(self.translate_batch_with_usage(segments).translations)

    def translate_batch_with_usage(
        self,
        segments: list[TranslationSegment],
    ) -> ProviderAttemptResult:
        protected_segments: list[tuple[TranslationSegment, ProtectedText]] = [
            (segment, protect_text(segment.text)) for segment in segments
        ]
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": build_system_prompt()},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "glossary": build_glossary_prompt(),
                            "segments": [
                                {
                                    "id": segment.id,
                                    "source_type": segment.source_type,
                                    "file_path": segment.file_path,
                                    "key_path": segment.key_path,
                                    "text": protected.text,
                                }
                                for segment, protected in protected_segments
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
        }
        response, headers = self._post_json("/chat/completions", payload)
        usage = normalize_usage(response)
        request_id = response_request_id(response, headers)
        try:
            content = response["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("response content must be a string")
            parsed = parse_translation_response(content)
            translations = parsed["translations"]
            translations_by_id = {
                item["id"]: item["translation"]
                for item in translations
                if isinstance(item, dict)
            }

            results: list[str] = []
            for segment, protected in protected_segments:
                translated = translations_by_id.get(segment.id)
                if not isinstance(translated, str):
                    raise ValueError("provider response omitted a segment")
                results.append(protected.restore(translated))
        except (
            IndexError,
            KeyError,
            TypeError,
            ValueError,
            RuntimeError,
            json.JSONDecodeError,
        ) as error:
            raise ProviderAttemptError(
                outcome=UsageOutcome.INVALID_RESPONSE,
                stable_error_code="invalid_translation_response",
                retryable=True,
                usage=usage,
                provider_request_id=request_id,
            ) from error
        return ProviderAttemptResult(
            translations=tuple(results),
            usage=usage,
            provider_request_id=request_id,
        )

    def _post_json(
        self,
        path: str,
        payload: dict[str, object],
    ) -> tuple[dict[str, object], object]:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
                headers = response.headers
            parsed = json.loads(body)
            if not isinstance(parsed, dict):
                raise TypeError("response must be an object")
            return parsed, headers
        except urllib.error.HTTPError as error:
            response_data = _safe_json_object(
                error.read().decode("utf-8", errors="replace")
            )
            status = int(error.code)
            outcome = (
                UsageOutcome.RATE_LIMITED
                if status == 429
                else UsageOutcome.PROVIDER_ERROR
            )
            raise ProviderAttemptError(
                outcome=outcome,
                stable_error_code=(
                    "http_rate_limited"
                    if status == 429
                    else f"http_{status}"
                ),
                retryable=status == 429 or status >= 500,
                usage=normalize_usage(response_data),
                provider_request_id=response_request_id(
                    response_data,
                    error.headers,
                ),
            ) from error
        except (TimeoutError, socket.timeout) as error:
            raise ProviderAttemptError(
                outcome=UsageOutcome.TIMEOUT,
                stable_error_code="request_timeout",
                retryable=True,
            ) from error
        except CancelledError as error:
            raise ProviderAttemptError(
                outcome=UsageOutcome.CANCELLED,
                stable_error_code="request_cancelled",
                retryable=False,
            ) from error
        except urllib.error.URLError as error:
            timed_out = isinstance(error.reason, (TimeoutError, socket.timeout))
            raise ProviderAttemptError(
                outcome=(
                    UsageOutcome.TIMEOUT
                    if timed_out
                    else UsageOutcome.PROVIDER_ERROR
                ),
                stable_error_code=(
                    "request_timeout"
                    if timed_out
                    else "network_error"
                ),
                retryable=True,
            ) from error
        except OSError as error:
            raise ProviderAttemptError(
                outcome=UsageOutcome.PROVIDER_ERROR,
                stable_error_code="network_error",
                retryable=True,
            ) from error
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
            raise ProviderAttemptError(
                outcome=UsageOutcome.INVALID_RESPONSE,
                stable_error_code="invalid_json_response",
                retryable=True,
            ) from error


def build_system_prompt() -> str:
    return (
        "You translate Minecraft modpack guide, quest, and book text into Simplified Chinese. "
        "Use natural terminology common in the Minecraft Chinese community. "
        f"Follow this fixed style guide: {build_style_prompt()} "
        "Preserve every protected token exactly, including __MC_HAN_PROTECTED_N__ markers. "
        "Do not translate JSON keys, resource IDs, file paths, item IDs, tags, placeholders, or code-like syntax. "
        "If a segment has source_type lang_name, it is a Minecraft item/block/entity/fluid/effect display name. "
        "Translate it into natural Simplified Chinese, but keep the English original at the end with this exact format: "
        "Chinese Name (English Original). Do not translate resource IDs. If the original is already Chinese, do not add a duplicate English suffix. "
        "Return strict JSON: {\"translations\":[{\"id\":\"...\",\"translation\":\"...\"}]}."
    )


def parse_translation_response(content: str) -> dict[str, object]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(content[start : end + 1])
    if not isinstance(parsed, dict) or not isinstance(parsed.get("translations"), list):
        raise RuntimeError("Provider did not return a translations array")
    return parsed


def _safe_json_object(content: str) -> dict[str, object]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}

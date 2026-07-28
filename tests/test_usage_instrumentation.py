from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import dataclass
from decimal import Decimal
import io
from pathlib import Path
import sqlite3
import urllib.error

import pytest

from mc_han.csv_store import read_extracted_csv, write_extracted_csv
from mc_han.models import ExtractedText
from mc_han.translator.base import TranslationSegment
from mc_han.translator.cache import TranslationCache
from mc_han.translator.engine import TranslationTaskDiagnostic, translate_csv
from mc_han.translator.openai_provider import OpenAICompatibleTranslator
from mc_han.translator.usage import (
    ProviderAttemptError,
    ProviderAttemptResult,
    UsageNormalizationResult,
)
from mc_han.usage.ledger import UsageLedger
from mc_han.usage.models import (
    PricingProfile,
    ReasoningBillingMode,
    TokenUsage,
    UsageOutcome,
)
from mc_han.usage.service import UsageQueryService


class FakeNetworkTranslator:
    is_network_provider = True
    provider_name = "fake"
    model = "fake-model"
    endpoint_type = "chat_completions"
    thinking_mode = "off"

    def __init__(self, outcomes: list[object]):
        self.outcomes = list(outcomes)
        self.calls = 0

    def translate_batch_with_usage(
        self,
        segments: list[TranslationSegment],
    ) -> ProviderAttemptResult:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome == "invalid":
            translations = ("",) * len(segments)
        else:
            translations = tuple(f"译文 {segment.id}" for segment in segments)
        return ProviderAttemptResult(
            translations=translations,
            usage=outcome if isinstance(outcome, UsageNormalizationResult) else usage(),
            provider_request_id="provider-request-private-value",
        )


class LocalPreflightFailure:
    is_network_provider = True
    provider_name = "fake"
    model = "fake-model"
    endpoint_type = "chat_completions"
    thinking_mode = "off"

    def __init__(self, error: Exception):
        self.error = error
        self.preparation_calls = 0
        self.network_calls = 0

    def translate_batch_with_usage(
        self,
        _segments: list[TranslationSegment],
    ) -> ProviderAttemptResult:
        self.preparation_calls += 1
        raise self.error


def test_successful_network_attempt_is_recorded_with_category_and_usage(
    tmp_path: Path,
):
    paths = prepare_csv(tmp_path, [record("1", source_type="jar_patchouli")])
    ledger_path = tmp_path / ".mc-han" / "usage.sqlite3"

    translate_csv(
        input_csv=paths[0],
        output_csv=paths[1],
        translator=FakeNetworkTranslator([usage()]),
        cache_path=tmp_path / "cache.jsonl",
        usage_ledger_path=ledger_path,
        usage_task_id="task-success",
        retry_delay_seconds=0,
    )

    with UsageLedger(ledger_path) as ledger:
        rows = ledger.attempt_rows(task_id="task-success")
        categories = ledger.category_rows()
        summary = UsageQueryService(ledger).task_summary("task-success")

    assert len(rows) == 1
    assert rows[0]["outcome"] == "success"
    assert rows[0]["input_tokens"] == 120
    assert rows[0]["provider_request_id"].startswith("sha256:")
    assert categories[0]["category_id"] == "patchouli"
    assert summary.translated_items == 1
    assert summary.remaining_items == 0


def test_failed_attempt_then_retry_success_records_both_attempts(
    tmp_path: Path,
):
    paths = prepare_csv(tmp_path, [record("1")])
    ledger_path = tmp_path / "usage.sqlite3"
    provider = FakeNetworkTranslator(
        [
            provider_error(UsageOutcome.RATE_LIMITED, "http_rate_limited"),
            usage(),
        ]
    )

    translate_csv(
        input_csv=paths[0],
        output_csv=paths[1],
        translator=provider,
        cache_path=tmp_path / "cache.jsonl",
        usage_ledger_path=ledger_path,
        usage_task_id="task-retry",
        max_retries=1,
        retry_delay_seconds=0,
    )

    with UsageLedger(ledger_path) as ledger:
        rows = ledger.attempt_rows(task_id="task-retry")
        summary = UsageQueryService(ledger).task_summary("task-retry")

    assert provider.calls == 2
    assert [row["attempt_number"] for row in rows] == [1, 2]
    assert [row["outcome"] for row in rows] == ["rate_limited", "success"]
    assert rows[0]["input_tokens"] == 120
    assert summary.retry_count == 1


def test_continuous_rate_limit_records_every_attempt(tmp_path: Path):
    paths = prepare_csv(tmp_path, [record("1")])
    ledger_path = tmp_path / "usage.sqlite3"
    provider = FakeNetworkTranslator(
        [
            provider_error(UsageOutcome.RATE_LIMITED, "http_rate_limited"),
            provider_error(UsageOutcome.RATE_LIMITED, "http_rate_limited"),
            provider_error(UsageOutcome.RATE_LIMITED, "http_rate_limited"),
        ]
    )

    translate_csv(
        input_csv=paths[0],
        output_csv=paths[1],
        translator=provider,
        cache_path=tmp_path / "cache.jsonl",
        usage_ledger_path=ledger_path,
        usage_task_id="task-429",
        max_retries=2,
        retry_delay_seconds=0,
        continue_on_error=True,
    )

    with UsageLedger(ledger_path) as ledger:
        rows = ledger.attempt_rows(task_id="task-429")
        summary = UsageQueryService(ledger).task_summary("task-429")

    assert len(rows) == 3
    assert all(row["outcome"] == "rate_limited" for row in rows)
    assert summary.remaining_items == 1


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        (UsageOutcome.TIMEOUT, "request_timeout"),
        (UsageOutcome.CANCELLED, "request_cancelled"),
    ],
)
def test_timeout_and_cancelled_attempts_have_stable_outcomes(
    tmp_path: Path,
    outcome: UsageOutcome,
    code: str,
):
    paths = prepare_csv(tmp_path, [record("1")])
    ledger_path = tmp_path / f"{outcome.value}.sqlite3"

    translate_csv(
        input_csv=paths[0],
        output_csv=paths[1],
        translator=FakeNetworkTranslator([provider_error(outcome, code)]),
        cache_path=tmp_path / f"{outcome.value}.jsonl",
        usage_ledger_path=ledger_path,
        usage_task_id=f"task-{outcome.value}",
        max_retries=0,
        retry_delay_seconds=0,
        continue_on_error=True,
    )

    with UsageLedger(ledger_path) as ledger:
        row = ledger.attempt_rows()[0]

    assert row["outcome"] == outcome.value
    assert row["stable_error_code"] == code


def test_cache_reuse_updates_task_stats_without_api_attempt(tmp_path: Path):
    source, output = prepare_csv(tmp_path, [record("1")])
    cache_path = tmp_path / "cache.jsonl"
    cache = TranslationCache(cache_path)
    cache.set(
        provider="fake",
        model="fake-model",
        original="Original 1",
        translation="缓存译文",
    )
    ledger_path = tmp_path / "usage.sqlite3"
    provider = FakeNetworkTranslator([])

    translate_csv(
        input_csv=source,
        output_csv=output,
        translator=provider,
        cache_path=cache_path,
        usage_ledger_path=ledger_path,
        usage_task_id="task-cache",
    )

    with UsageLedger(ledger_path) as ledger:
        summary = UsageQueryService(ledger).task_summary("task-cache")

    assert provider.calls == 0
    assert summary.api_attempts == 0
    assert summary.reused_items == 1
    assert summary.avoided_api_items == 1


def test_preflight_failure_is_not_retried_or_recorded_as_api_attempt(
    tmp_path: Path,
):
    source, output = prepare_csv(tmp_path, [record("1")])
    ledger_path = tmp_path / "usage.sqlite3"
    provider = LocalPreflightFailure(ValueError("local preparation failed"))

    translate_csv(
        input_csv=source,
        output_csv=output,
        translator=provider,
        cache_path=tmp_path / "cache.jsonl",
        usage_ledger_path=ledger_path,
        max_retries=2,
        retry_delay_seconds=0,
        continue_on_error=True,
    )

    with UsageLedger(ledger_path) as ledger:
        attempts = ledger.attempt_rows()

    assert provider.preparation_calls == 1
    assert provider.network_calls == 0
    assert attempts == []


def test_preflight_cancel_is_not_an_api_attempt_or_retry(tmp_path: Path):
    source, output = prepare_csv(tmp_path, [record("1")])
    ledger_path = tmp_path / "usage.sqlite3"
    provider = LocalPreflightFailure(CancelledError())

    translate_csv(
        input_csv=source,
        output_csv=output,
        translator=provider,
        cache_path=tmp_path / "cache.jsonl",
        usage_ledger_path=ledger_path,
        max_retries=2,
        retry_delay_seconds=0,
        continue_on_error=True,
    )

    with UsageLedger(ledger_path) as ledger:
        assert ledger.attempt_rows() == []
    assert provider.preparation_calls == 1


@pytest.mark.parametrize(
    "stage",
    ["protect", "prompt", "serialize", "request"],
)
def test_openai_local_preflight_errors_never_start_or_record_network_attempt(
    tmp_path: Path,
    monkeypatch,
    stage: str,
):
    source, output = prepare_csv(tmp_path, [record("1")])
    ledger_path = tmp_path / "usage.sqlite3"
    network_calls = 0

    def unexpected_urlopen(*_args: object, **_kwargs: object) -> object:
        nonlocal network_calls
        network_calls += 1
        raise AssertionError("urlopen must not be called")

    monkeypatch.setattr("urllib.request.urlopen", unexpected_urlopen)
    if stage == "protect":
        monkeypatch.setattr(
            "mc_han.translator.openai_provider.protect_text",
            lambda _value: (_ for _ in ()).throw(ValueError("protect failed")),
        )
    elif stage == "prompt":
        monkeypatch.setattr(
            "mc_han.translator.openai_provider.build_system_prompt",
            lambda: (_ for _ in ()).throw(ValueError("prompt failed")),
        )
    elif stage == "serialize":
        monkeypatch.setattr(
            "mc_han.translator.openai_provider.json.dumps",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                TypeError("serialize failed")
            ),
        )
    else:
        monkeypatch.setattr(
            "mc_han.translator.openai_provider.urllib.request.Request",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ValueError("request construction failed")
            ),
        )
    provider = OpenAICompatibleTranslator(
        provider_name="openai",
        model="test-model",
        api_key="test-key",
        base_url="https://example.invalid/v1",
    )

    translate_csv(
        input_csv=source,
        output_csv=output,
        translator=provider,
        cache_path=tmp_path / "cache.jsonl",
        usage_ledger_path=ledger_path,
        max_retries=2,
        retry_delay_seconds=0,
        continue_on_error=True,
    )

    with UsageLedger(ledger_path) as ledger:
        assert ledger.attempt_rows() == []
    assert network_calls == 0


def test_openai_timeout_after_urlopen_is_one_network_attempt(
    tmp_path: Path,
    monkeypatch,
):
    source, output = prepare_csv(tmp_path, [record("1")])
    ledger_path = tmp_path / "usage.sqlite3"
    network_calls = 0

    def timeout(*_args: object, **_kwargs: object) -> object:
        nonlocal network_calls
        network_calls += 1
        raise TimeoutError("private timeout details")

    monkeypatch.setattr("urllib.request.urlopen", timeout)
    provider = OpenAICompatibleTranslator(
        provider_name="openai",
        model="test-model",
        api_key="test-key",
        base_url="https://example.invalid/v1",
    )

    translate_csv(
        input_csv=source,
        output_csv=output,
        translator=provider,
        cache_path=tmp_path / "cache.jsonl",
        usage_ledger_path=ledger_path,
        max_retries=0,
        retry_delay_seconds=0,
        continue_on_error=True,
    )

    with UsageLedger(ledger_path) as ledger:
        rows = ledger.attempt_rows()
    assert network_calls == 1
    assert [row["outcome"] for row in rows] == ["timeout"]


def test_openai_rate_limit_after_urlopen_is_one_network_attempt(
    tmp_path: Path,
    monkeypatch,
):
    source, output = prepare_csv(tmp_path, [record("1")])
    ledger_path = tmp_path / "usage.sqlite3"
    network_calls = 0

    def rate_limit(request: object, **_kwargs: object) -> object:
        nonlocal network_calls
        network_calls += 1
        raise urllib.error.HTTPError(
            getattr(request, "full_url", "https://example.invalid"),
            429,
            "rate limited",
            {},
            io.BytesIO(b'{"error":{"message":"private"}}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", rate_limit)
    provider = OpenAICompatibleTranslator(
        provider_name="openai",
        model="test-model",
        api_key="test-key",
        base_url="https://example.invalid/v1",
    )

    translate_csv(
        input_csv=source,
        output_csv=output,
        translator=provider,
        cache_path=tmp_path / "cache.jsonl",
        usage_ledger_path=ledger_path,
        max_retries=0,
        retry_delay_seconds=0,
        continue_on_error=True,
    )

    with UsageLedger(ledger_path) as ledger:
        rows = ledger.attempt_rows()
    assert network_calls == 1
    assert [row["outcome"] for row in rows] == ["rate_limited"]


def test_openai_cancel_after_urlopen_is_not_retried(
    tmp_path: Path,
    monkeypatch,
):
    source, output = prepare_csv(tmp_path, [record("1")])
    ledger_path = tmp_path / "usage.sqlite3"
    network_calls = 0

    def cancel(*_args: object, **_kwargs: object) -> object:
        nonlocal network_calls
        network_calls += 1
        raise CancelledError()

    monkeypatch.setattr("urllib.request.urlopen", cancel)
    provider = OpenAICompatibleTranslator(
        provider_name="openai",
        model="test-model",
        api_key="test-key",
        base_url="https://example.invalid/v1",
    )

    translate_csv(
        input_csv=source,
        output_csv=output,
        translator=provider,
        cache_path=tmp_path / "cache.jsonl",
        usage_ledger_path=ledger_path,
        max_retries=2,
        retry_delay_seconds=0,
        continue_on_error=True,
    )

    with UsageLedger(ledger_path) as ledger:
        rows = ledger.attempt_rows()
    assert network_calls == 1
    assert [row["outcome"] for row in rows] == ["cancelled"]
    assert rows[0]["stable_error_code"] == "request_cancelled"


def test_local_validation_failure_records_api_attempt_not_fake_tokens(
    tmp_path: Path,
):
    source, output = prepare_csv(tmp_path, [record("1")])
    ledger_path = tmp_path / "usage.sqlite3"

    translate_csv(
        input_csv=source,
        output_csv=output,
        translator=FakeNetworkTranslator(["invalid"]),
        cache_path=tmp_path / "cache.jsonl",
        usage_ledger_path=ledger_path,
        usage_task_id="task-validation",
        max_retries=0,
        continue_on_error=True,
    )

    with UsageLedger(ledger_path) as ledger:
        row = ledger.attempt_rows()[0]
        summary = UsageQueryService(ledger).project_summary()

    assert row["outcome"] == "local_validation_failed"
    assert row["input_tokens"] == 120
    assert summary.local_validation_failed_items == 1


def test_success_without_provider_usage_keeps_tokens_unknown(tmp_path: Path):
    source, output = prepare_csv(tmp_path, [record("1")])
    ledger_path = tmp_path / "usage.sqlite3"
    missing_usage = UsageNormalizationResult(
        TokenUsage(),
        diagnostics=("usage_missing",),
    )

    translate_csv(
        input_csv=source,
        output_csv=output,
        translator=FakeNetworkTranslator([missing_usage]),
        cache_path=tmp_path / "cache.jsonl",
        usage_ledger_path=ledger_path,
        usage_task_id="task-missing-usage",
    )

    with UsageLedger(ledger_path) as ledger:
        row = ledger.attempt_rows()[0]
        summary = UsageQueryService(ledger).project_summary()

    assert row["outcome"] == "success"
    assert row["input_tokens"] is None
    assert row["output_tokens"] is None
    assert row["usage_diagnostics_json"] == '["usage_missing"]'
    assert summary.input_tokens is None
    assert summary.incomplete_usage_count == 1


def test_resume_does_not_repeat_completed_api_request(tmp_path: Path):
    source, output = prepare_csv(tmp_path, [record("1")])
    ledger_path = tmp_path / "usage.sqlite3"
    first = FakeNetworkTranslator([usage()])
    translate_csv(
        input_csv=source,
        output_csv=output,
        translator=first,
        cache_path=tmp_path / "cache.jsonl",
        usage_ledger_path=ledger_path,
        usage_task_id="task-resume",
    )
    second = FakeNetworkTranslator([])

    translate_csv(
        input_csv=output,
        output_csv=output,
        translator=second,
        cache_path=tmp_path / "cache.jsonl",
        usage_ledger_path=ledger_path,
        usage_task_id="task-resume",
    )

    with UsageLedger(ledger_path) as ledger:
        rows = ledger.attempt_rows(task_id="task-resume")

    assert first.calls == 1
    assert second.calls == 0
    assert len(rows) == 1


def test_concurrent_workers_share_one_locked_usage_ledger(tmp_path: Path):
    records = [record(str(index)) for index in range(9)]
    source, output = prepare_csv(tmp_path, records)
    ledger_path = tmp_path / "usage.sqlite3"
    provider = FakeNetworkTranslator([usage() for _record in records])

    translate_csv(
        input_csv=source,
        output_csv=output,
        translator=provider,
        cache_path=tmp_path / "cache.jsonl",
        usage_ledger_path=ledger_path,
        usage_task_id="task-concurrent",
        worker_count=3,
        max_batch_items=1,
        retry_delay_seconds=0,
    )

    with UsageLedger(ledger_path) as ledger:
        rows = ledger.attempt_rows(task_id="task-concurrent")

    assert len(rows) == 9
    assert {row["outcome"] for row in rows} == {"success"}


def test_attempt_is_durable_before_later_batch_failure(tmp_path: Path):
    records = [record("1"), record("2")]
    source, output = prepare_csv(tmp_path, records)
    ledger_path = tmp_path / "usage.sqlite3"
    provider = FakeNetworkTranslator(
        [usage(), provider_error(UsageOutcome.PROVIDER_ERROR, "network_error")]
    )

    with pytest.raises(RuntimeError):
        translate_csv(
            input_csv=source,
            output_csv=output,
            translator=provider,
            cache_path=tmp_path / "cache.jsonl",
            usage_ledger_path=ledger_path,
            usage_task_id="task-crash",
            max_batch_items=1,
            max_retries=0,
            continue_on_error=False,
        )

    with UsageLedger(ledger_path) as ledger:
        rows = ledger.attempt_rows(task_id="task-crash")

    assert len(rows) == 2
    assert rows[0]["outcome"] == "success"


def test_local_csv_write_failure_does_not_retry_successful_api_request(
    tmp_path: Path,
    monkeypatch,
):
    source, output = prepare_csv(tmp_path, [record("1")])
    ledger_path = tmp_path / "usage.sqlite3"
    provider = FakeNetworkTranslator([usage()])

    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated local save failure")

    monkeypatch.setattr(
        "mc_han.translator.engine.write_extracted_csv",
        fail_write,
    )

    with pytest.raises(OSError, match="simulated local save failure"):
        translate_csv(
            input_csv=source,
            output_csv=output,
            translator=provider,
            cache_path=tmp_path / "cache.jsonl",
            usage_ledger_path=ledger_path,
            usage_task_id="task-local-save",
            max_retries=2,
        )

    with UsageLedger(ledger_path) as ledger:
        rows = ledger.attempt_rows(task_id="task-local-save")

    assert provider.calls == 1
    assert len(rows) == 1
    assert rows[0]["outcome"] == "success"


@pytest.mark.parametrize(
    "ledger_error",
    [
        sqlite3.OperationalError("database is locked"),
        sqlite3.OperationalError("database is busy"),
    ],
)
def test_successful_result_survives_ledger_failure_and_resume_uses_cache(
    tmp_path: Path,
    monkeypatch,
    ledger_error: sqlite3.OperationalError,
):
    source, output = prepare_csv(tmp_path, [record("1")])
    ledger_path = tmp_path / "usage.sqlite3"
    cache_path = tmp_path / "cache.jsonl"
    first = FakeNetworkTranslator([usage()])
    events: list[object] = []

    def fail_record_attempt(
        _ledger: UsageLedger,
        _event: object,
    ) -> bool:
        raise ledger_error

    monkeypatch.setattr(UsageLedger, "record_attempt", fail_record_attempt)
    translate_csv(
        input_csv=source,
        output_csv=output,
        translator=first,
        cache_path=cache_path,
        usage_ledger_path=ledger_path,
        usage_task_id="task-ledger-failure",
        max_retries=2,
        retry_delay_seconds=0,
        event_callback=events.append,
    )

    assert first.calls == 1
    assert read_extracted_csv(output)[0].translation
    assert TranslationCache(cache_path).get(
        provider="fake",
        model="fake-model",
        original="Original 1",
    )
    assert any(
        isinstance(event, TranslationTaskDiagnostic)
        and event.code == "usage_ledger_write_failed"
        for event in events
    )

    second = FakeNetworkTranslator([])
    translate_csv(
        input_csv=output,
        output_csv=output,
        translator=second,
        cache_path=cache_path,
        usage_ledger_path=ledger_path,
        usage_task_id="task-ledger-resume",
    )

    assert second.calls == 0


def test_category_insert_failure_preserves_result_and_cache(
    tmp_path: Path,
):
    source, output = prepare_csv(tmp_path, [record("1")])
    cache_path = tmp_path / "cache.jsonl"
    ledger = UsageLedger(tmp_path / "usage.sqlite3")
    ledger._connection.execute(  # noqa: SLF001 - controlled fault injection.
        """
        CREATE TRIGGER reject_usage_category
        BEFORE INSERT ON attempt_categories
        BEGIN
            SELECT RAISE(ABORT, 'simulated category failure');
        END
        """
    )
    ledger._connection.commit()  # noqa: SLF001
    provider = FakeNetworkTranslator([usage()])

    translate_csv(
        input_csv=source,
        output_csv=output,
        translator=provider,
        cache_path=cache_path,
        usage_ledger=ledger,
        usage_task_id="task-category-failure",
    )

    assert provider.calls == 1
    assert read_extracted_csv(output)[0].translation
    assert ledger.attempt_rows() == []
    resumed = FakeNetworkTranslator([])
    translate_csv(
        input_csv=output,
        output_csv=output,
        translator=resumed,
        cache_path=cache_path,
        usage_ledger=ledger,
        usage_task_id="task-category-resume",
    )
    assert resumed.calls == 0
    ledger.close()


def test_task_stats_failure_does_not_block_attempt_or_translation(
    tmp_path: Path,
):
    source, output = prepare_csv(tmp_path, [record("1")])
    ledger = UsageLedger(tmp_path / "usage.sqlite3")
    ledger._connection.execute(  # noqa: SLF001 - controlled fault injection.
        """
        CREATE TRIGGER reject_task_stats
        BEFORE INSERT ON task_stats
        BEGIN
            SELECT RAISE(ABORT, 'simulated task stats failure');
        END
        """
    )
    ledger._connection.commit()  # noqa: SLF001
    provider = FakeNetworkTranslator([usage()])

    translate_csv(
        input_csv=source,
        output_csv=output,
        translator=provider,
        cache_path=tmp_path / "cache.jsonl",
        usage_ledger=ledger,
        usage_task_id="task-stats-failure",
    )

    assert provider.calls == 1
    assert read_extracted_csv(output)[0].translation
    assert len(ledger.attempt_rows()) == 1
    ledger.close()


def test_pricing_profile_estimate_and_reported_cost_stay_separate(
    tmp_path: Path,
):
    source, output = prepare_csv(tmp_path, [record("1")])
    ledger_path = tmp_path / "usage.sqlite3"
    profile = PricingProfile(
        profile_id="test-profile",
        provider="fake",
        model_pattern="fake-*",
        effective_from="2026-01-01T00:00:00+00:00",
        currency="USD",
        input_per_million=Decimal("1"),
        cached_input_per_million=Decimal("0.5"),
        output_per_million=Decimal("2"),
        reasoning_billing_mode=ReasoningBillingMode.INCLUDED_IN_OUTPUT,
        source_reference="test-only",
    )

    translate_csv(
        input_csv=source,
        output_csv=output,
        translator=FakeNetworkTranslator([usage(reported_cost=Decimal("0.9"))]),
        cache_path=tmp_path / "cache.jsonl",
        usage_ledger_path=ledger_path,
        usage_task_id="task-cost",
        pricing_profiles=(profile,),
    )

    with UsageLedger(ledger_path) as ledger:
        row = ledger.attempt_rows()[0]

    assert row["provider_reported_cost"] == "0.9"
    assert Decimal(row["estimated_cost"]) != Decimal(row["provider_reported_cost"])
    assert row["pricing_profile_id"] == "test-profile"


def test_reported_cost_is_kept_without_inventing_an_estimate(tmp_path: Path):
    source, output = prepare_csv(tmp_path, [record("1")])
    ledger_path = tmp_path / "usage.sqlite3"

    translate_csv(
        input_csv=source,
        output_csv=output,
        translator=FakeNetworkTranslator(
            [usage(reported_cost=Decimal("0.75"))]
        ),
        cache_path=tmp_path / "cache.jsonl",
        usage_ledger_path=ledger_path,
        usage_task_id="task-reported-only",
    )

    with UsageLedger(ledger_path) as ledger:
        row = ledger.attempt_rows()[0]

    assert row["provider_reported_cost"] == "0.75"
    assert row["estimated_cost"] is None
    assert row["pricing_profile_id"] == ""


def test_private_provider_url_paths_and_request_ids_are_not_persisted(
    tmp_path: Path,
):
    source, output = prepare_csv(
        tmp_path,
        [
            ExtractedText(
                id="1",
                source_type="C:\\Users\\PrivatePerson\\source",
                container="mods/demo.jar",
                file_path="C:\\Users\\PrivatePerson\\secret.md",
                key_path="secret",
                original="PRIVATE ORIGINAL TEXT",
            )
        ],
    )
    provider = FakeNetworkTranslator([usage()])
    provider.provider_name = "custom:https://private.example/v1?account=secret"
    provider.api_key = "sk-test-super-secret"
    ledger_path = tmp_path / "usage.sqlite3"

    translate_csv(
        input_csv=source,
        output_csv=output,
        translator=provider,
        cache_path=tmp_path / "cache.jsonl",
        usage_ledger_path=ledger_path,
        usage_task_id="task-privacy",
    )

    content = ledger_path.read_bytes()
    for forbidden in (
        b"PrivatePerson",
        b"PRIVATE ORIGINAL TEXT",
        b"private.example",
        b"account=secret",
        b"provider-request-private-value",
        b"sk-test-super-secret",
    ):
        assert forbidden not in content


def usage(
    *,
    reported_cost: Decimal | None = None,
) -> UsageNormalizationResult:
    return UsageNormalizationResult(
        TokenUsage(
            input_tokens=120,
            output_tokens=40,
            cached_input_tokens=20,
            uncached_input_tokens=100,
            reasoning_tokens=5,
            total_tokens=160,
            reasoning_included_in_output=True,
        ),
        provider_reported_cost=reported_cost,
        currency="USD" if reported_cost is not None else "",
    )


def provider_error(
    outcome: UsageOutcome,
    code: str,
) -> ProviderAttemptError:
    return ProviderAttemptError(
        outcome=outcome,
        stable_error_code=code,
        retryable=True,
        usage=usage(),
        provider_request_id="failed-request-private-value",
    )


def record(identifier: str, *, source_type: str = "jar_lang") -> ExtractedText:
    return ExtractedText(
        id=identifier,
        source_type=source_type,
        container="mods/demo.jar",
        file_path="assets/demo/lang/en_us.json",
        key_path=f"message.{identifier}",
        original=f"Original {identifier}",
    )


def prepare_csv(
    tmp_path: Path,
    records: list[ExtractedText],
) -> tuple[Path, Path]:
    source = tmp_path / "extracted_texts.csv"
    output = tmp_path / "translated.csv"
    write_extracted_csv(records, source)
    return source, output

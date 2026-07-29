from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from mc_han.core.project import project_paths
from mc_han.csv_store import read_extracted_csv, write_extracted_csv
from mc_han.models import ExtractedText
from mc_han.qt.translation_config_view_models import (
    TranslationProvider,
    TranslationSessionConfig,
)
from mc_han.scanner import ScanRecords
from mc_han.services.scan_service import classify_scan_records
from mc_han.services.trial_translation import (
    run_trial_translation,
    select_trial_samples,
)
from mc_han.services.translation_rules import TranslationRuleStore
from mc_han.translator.base import TranslationSegment
from mc_han.translator.usage import (
    ProviderAttemptError,
    ProviderAttemptResult,
    UsageNormalizationResult,
)
from mc_han.usage.ledger import UsageLedger
from mc_han.usage.models import TokenUsage, UsageOutcome
from mc_han.usage.service import UsageQueryService
from mc_han.workflow.scan_models import ScanSelectionState
from mc_han.workflow.trial_models import TrialSampleStatus
from mc_han.workflow.translation_rules import (
    TranslationRuleScope,
    TranslationRuleType,
)


class FakeTrialProvider:
    is_network_provider = True
    provider_name = "deepseek"
    model = "deepseek-chat"
    endpoint_type = "chat_completions"
    thinking_mode = ""

    def __init__(self, failed_ids: set[str] | None = None):
        self.failed_ids = failed_ids or set()
        self.calls: list[tuple[str, ...]] = []

    def translate_batch_with_usage(
        self,
        segments: list[TranslationSegment],
    ) -> ProviderAttemptResult:
        ids = tuple(segment.id for segment in segments)
        self.calls.append(ids)
        if any(text_id in self.failed_ids for text_id in ids):
            raise ProviderAttemptError(
                outcome=UsageOutcome.CANCELLED,
                stable_error_code="fake_failed",
                retryable=False,
                usage=_usage(),
            )
        return ProviderAttemptResult(
            translations=tuple(f"译文 {segment.id}" for segment in segments),
            usage=_usage(),
        )


def _usage() -> UsageNormalizationResult:
    return UsageNormalizationResult(
        tokens=TokenUsage(
            input_tokens=20,
            output_tokens=10,
            total_tokens=30,
        ),
        provider_reported_cost=Decimal("0.001"),
        currency="USD",
    )


def _record(
    index: int,
    *,
    source_type: str = "jar_lang",
) -> ExtractedText:
    return ExtractedText(
        id=f"record-{index:02d}",
        source_type=source_type,
        container=f"mods/mod-{index % 4}.jar",
        file_path=f"assets/demo/page-{index}.json",
        key_path=f"entry.{index}",
        original=f"Original text {index}",
    )


def _selection(records: list[ExtractedText]) -> ScanSelectionState:
    result = classify_scan_records(
        ScanRecords(
            records,
            inventory={
                "jar_safety_diagnostics": [],
                "resourcepack_lang_zh_cn_files_found": 0,
            },
        ),
        scan_duration=0.1,
    )
    return ScanSelectionState.from_result(result).select_all()


def _config(*, batch_size: int = 10) -> TranslationSessionConfig:
    return TranslationSessionConfig(
        provider=TranslationProvider.DEEPSEEK,
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        api_key="sk-fake-test",
        batch_size=batch_size,
    )


def test_sample_selection_is_stable_and_prioritizes_categories():
    source_types = (
        "jar_lang",
        "ftbquests_lang",
        "jar_patchouli",
        "jar_modonomicon",
        "jar_ae2guide",
        "kubejs_lang",
    )
    records = [
        _record(index, source_type=source_types[index % len(source_types)])
        for index in range(15)
    ]
    selection = _selection(records)

    first = select_trial_samples(records, selection)
    second = select_trial_samples(reversed(records), selection)

    assert len(first) == 8
    assert [item.text_id for item in first] == [
        item.text_id for item in second
    ]
    assert len({item.category_id for item in first[:6]}) == 6


def test_fake_provider_success_persists_usage_csv_and_cache(tmp_path: Path):
    records = [_record(index) for index in range(8)]
    paths = project_paths(tmp_path)
    write_extracted_csv(records, paths.extracted_csv)
    samples = select_trial_samples(records, _selection(records), sample_count=8)
    provider = FakeTrialProvider()

    result = run_trial_translation(
        tmp_path,
        _config(),
        samples,
        task_id="trial-success",
        translator_factory=lambda _config: provider,
    )

    assert result.successful_count == 8
    assert result.failed_count == 0
    assert result.usage.api_attempts == 1
    assert result.usage.total_tokens == 30
    assert result.display_cost == (Decimal("0.001"), "USD", "服务商报告")
    assert all(record.translation for record in read_extracted_csv(paths.extracted_csv))
    assert paths.translation_cache_jsonl.is_file()
    assert paths.translations_sqlite.is_file()
    with UsageLedger(paths.usage_sqlite) as ledger:
        summary = UsageQueryService(ledger).task_summary("trial-success")
    assert summary.api_attempts == 1
    assert summary.translated_items == 8


def test_partial_failure_and_retry_target_only_failed_samples(tmp_path: Path):
    records = [_record(index) for index in range(8)]
    paths = project_paths(tmp_path)
    write_extracted_csv(records, paths.extracted_csv)
    samples = select_trial_samples(records, _selection(records), sample_count=8)
    failed_id = samples[3].text_id
    first_provider = FakeTrialProvider({failed_id})

    first = run_trial_translation(
        tmp_path,
        _config(batch_size=1),
        samples,
        task_id="trial-partial",
        translator_factory=lambda _config: first_provider,
    )

    assert first.failed_ids == {failed_id}
    assert first.successful_count == 7
    retry_provider = FakeTrialProvider()
    retried = run_trial_translation(
        tmp_path,
        _config(batch_size=1),
        first.samples,
        task_id=first.task_id,
        target_ids=first.failed_ids,
        translator_factory=lambda _config: retry_provider,
    )

    assert retry_provider.calls == [(failed_id,)]
    assert retried.failed_count == 0
    assert retried.successful_count == 8
    assert retried.usage.api_attempts == 9


def test_trial_cache_prevents_later_duplicate_provider_request(tmp_path: Path):
    records = [_record(index) for index in range(8)]
    paths = project_paths(tmp_path)
    write_extracted_csv(records, paths.extracted_csv)
    samples = select_trial_samples(records, _selection(records), sample_count=8)
    first_provider = FakeTrialProvider()
    first = run_trial_translation(
        tmp_path,
        _config(),
        samples,
        task_id="trial-cache-first",
        translator_factory=lambda _config: first_provider,
    )
    cleared = [
        replace(record, translation="")
        for record in read_extracted_csv(paths.extracted_csv)
    ]
    write_extracted_csv(cleared, paths.extracted_csv)
    later_provider = FakeTrialProvider()

    reused = run_trial_translation(
        tmp_path,
        _config(),
        first.samples,
        task_id="full-translation-later",
        target_ids=frozenset(item.text_id for item in first.samples),
        translator_factory=lambda _config: later_provider,
    )

    assert later_provider.calls == []
    assert reused.successful_count == 8
    assert all(item.from_cache for item in reused.samples)
    assert reused.usage.api_attempts == 0


def test_retry_of_rejected_sample_applies_saved_rule_and_bypasses_old_cache(
    tmp_path: Path,
):
    records = [_record(index) for index in range(8)]
    paths = project_paths(tmp_path)
    rejected = replace(
        records[0],
        review_status="needs_retranslate",
        note="needs_retranslate",
    )
    records[0] = rejected
    write_extracted_csv(records, paths.extracted_csv)
    TranslationRuleStore(paths.translation_rules_json).add_feedback_rule(
        rejected,
        rule_type=TranslationRuleType.EXACT,
        scope=TranslationRuleScope.RECORD,
        instruction="使用玩家常用说法",
        source="trial_feedback:wording",
    )
    provider = FakeTrialProvider()
    captured: list[tuple[str, ...]] = []
    original_method = provider.translate_batch_with_usage

    def capture(segments):
        captured.extend(segment.instructions for segment in segments)
        return original_method(segments)

    provider.translate_batch_with_usage = capture
    samples = select_trial_samples(
        records,
        _selection(records),
        sample_count=8,
    )

    result = run_trial_translation(
        tmp_path,
        _config(),
        samples,
        task_id="trial-rule-retry",
        target_ids=frozenset({rejected.id}),
        translator_factory=lambda _config: provider,
    )

    assert captured == [("使用玩家常用说法",)]
    saved = {
        item.id: item for item in read_extracted_csv(paths.extracted_csv)
    }
    assert saved[rejected.id].translation
    assert saved[rejected.id].review_status == ""
    assert result.failed_count == 0

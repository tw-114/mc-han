from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from mc_han.models import ExtractedText
from mc_han.scanner import ScanRecords
from mc_han.services.scan_service import classify_scan_records
from mc_han.services.translation_planning import (
    build_translation_plan_comparison,
)
from mc_han.translator.sqlite_cache import SQLiteTranslationCache
from mc_han.workflow.scan_models import ScanSelectionState
from mc_han.workflow.translation_plan import TranslationPlanMode


def _record(
    record_id: str,
    source_type: str,
    *,
    translation: str = "",
    skip_status: str = "",
) -> ExtractedText:
    return ExtractedText(
        id=record_id,
        source_type=source_type,
        container=f"mods/{record_id}.jar",
        file_path=f"assets/{record_id}/lang/en_us.json",
        key_path=f"{record_id}.text",
        original=f"Text for {record_id}",
        translation=translation,
        skip_status=skip_status,
    )


def _selection(records: list[ExtractedText]) -> ScanSelectionState:
    result = classify_scan_records(
        ScanRecords(records, inventory={}),
        scan_duration=0.1,
    )
    return ScanSelectionState.from_result(result).select_all()


def test_plan_modes_are_stable_and_route_only_explicit_categories():
    records = [
        _record("lang", "jar_lang"),
        _record("quest", "ftbquests_lang"),
        _record("guide", "jar_ae2guide"),
    ]

    comparison = build_translation_plan_comparison(
        records,
        _selection(records),
        provider="deepseek",
        base_model="deepseek-v4-flash",
        high_quality_model="deepseek-v4-pro",
        concurrency=2,
    )

    assert tuple(plan.mode for plan in comparison.plans) == tuple(
        TranslationPlanMode
    )
    economy = comparison.for_mode(TranslationPlanMode.ECONOMY)
    balanced = comparison.for_mode(TranslationPlanMode.BALANCED)
    high = comparison.for_mode(TranslationPlanMode.HIGH_QUALITY)
    assert {route.model for route in economy.routes} == {
        "deepseek-v4-flash"
    }
    assert {
        route.category_id.value: route.model for route in balanced.routes
    } == {
        "ftb_quests": "deepseek-v4-pro",
        "guideme": "deepseek-v4-pro",
        "mod_language": "deepseek-v4-flash",
    }
    assert {route.model for route in high.routes} == {"deepseek-v4-pro"}
    assert economy.estimated_request_count <= balanced.estimated_request_count
    assert balanced.estimated_request_count <= high.estimated_request_count


def test_plan_counts_history_cache_skip_and_ai_without_network(tmp_path: Path):
    historical = _record(
        "historical",
        "jar_lang",
        translation="历史译文",
    )
    cached = _record("cached", "jar_lang")
    pending = _record("pending", "jar_patchouli")
    skipped = _record("skipped", "jar_lang", skip_status="skip")
    records = [historical, cached, pending, skipped]
    cache_path = tmp_path / "translations.sqlite"
    cache = SQLiteTranslationCache(cache_path)
    cache.set(
        cached,
        translation="缓存译文",
        provider="deepseek",
        model="deepseek-v4-flash",
    )
    cache.close()

    comparison = build_translation_plan_comparison(
        records,
        _selection(records),
        provider="deepseek",
        base_model="deepseek-v4-flash",
        high_quality_model="deepseek-v4-pro",
        sqlite_cache_path=cache_path,
    )
    plan = comparison.for_mode(TranslationPlanMode.BALANCED)

    assert plan.selected_record_count == 4
    assert plan.historical_translation_count == 1
    assert plan.cache_reuse_count == 1
    assert plan.skipped_count == 1
    assert plan.ai_translation_count == 1
    assert plan.estimated_input_tokens > 0
    assert plan.estimated_output_tokens > 0
    assert plan.estimated_cost_low is not None


def test_unknown_custom_model_keeps_token_estimate_but_not_fake_zero_cost():
    records = [_record("custom", "jar_lang")]

    plan = build_translation_plan_comparison(
        records,
        _selection(records),
        provider="custom:https://localhost/v1",
        base_model="local-model",
    ).for_mode(TranslationPlanMode.BALANCED)

    assert plan.estimated_input_tokens > 0
    assert plan.estimated_cost_low is None
    assert plan.estimated_cost_high is None
    assert "没有本地单价" in plan.pricing_note


def test_budget_warning_and_limit_use_high_estimate():
    records = [_record("budget", "jar_lang")]
    plan = build_translation_plan_comparison(
        records,
        _selection(records),
        provider="openai",
        base_model="gpt-4.1",
    ).for_mode(TranslationPlanMode.BALANCED)
    assert plan.estimated_cost_high is not None

    assert plan.reaches_budget_warning(plan.estimated_cost_high)
    assert plan.exceeds_budget(plan.estimated_cost_high / Decimal("2"))
    assert not plan.exceeds_budget(plan.estimated_cost_high * Decimal("2"))


def test_sqlite_bulk_lookup_preserves_context(tmp_path: Path):
    first = _record("first", "jar_lang")
    second = replace(first, id="second", key_path="other.context")
    cache = SQLiteTranslationCache(tmp_path / "cache.sqlite")
    cache.set(
        first,
        translation="第一条",
        provider="deepseek",
        model="deepseek-v4-flash",
    )

    found = cache.get_many((first, second))
    cache.close()

    assert found == {"first": "第一条"}

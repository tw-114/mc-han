from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from mc_han.models import ExtractedText
from mc_han.services.provenance import TranslationProvenanceStore
from mc_han.translator.batching import (
    build_token_batches,
    make_pending_group,
    resolve_speed_mode,
)
from mc_han.translator.cache import (
    TranslationCache,
    make_reuse_key,
)
from mc_han.translator.sqlite_cache import SQLiteTranslationCache
from mc_han.workflow.scan_models import (
    CATEGORY_DEFINITIONS,
    ScanCategoryId,
    ScanSelectionState,
    category_for_record,
)
from mc_han.workflow.translation_plan import (
    TranslationPlan,
    TranslationPlanComparison,
    TranslationPlanMode,
    TranslationRoute,
)
from mc_han.workflow.provenance import TranslationSource


@dataclass(frozen=True)
class ModelPrice:
    input_per_million: Decimal
    cached_input_per_million: Decimal
    output_per_million: Decimal
    currency: str = "USD"


# Official list prices checked 2026-07-29. Actual provider billing wins.
MODEL_PRICES: dict[tuple[str, str], ModelPrice] = {
    ("deepseek", "deepseek-v4-flash"): ModelPrice(
        Decimal("0.14"),
        Decimal("0.0028"),
        Decimal("0.28"),
    ),
    ("deepseek", "deepseek-v4-pro"): ModelPrice(
        Decimal("0.435"),
        Decimal("0.003625"),
        Decimal("0.87"),
    ),
    ("openai", "gpt-4o-mini"): ModelPrice(
        Decimal("0.15"),
        Decimal("0.075"),
        Decimal("0.60"),
    ),
    ("openai", "gpt-4.1"): ModelPrice(
        Decimal("2.00"),
        Decimal("0.50"),
        Decimal("8.00"),
    ),
}

HIGH_QUALITY_CATEGORIES = frozenset(
    {
        ScanCategoryId.FTB_QUESTS,
        ScanCategoryId.PATCHOULI,
        ScanCategoryId.MODONOMICON,
        ScanCategoryId.GUIDEME,
    }
)
MODE_SPEED = {
    TranslationPlanMode.ECONOMY: "fast",
    TranslationPlanMode.BALANCED: "balanced",
    TranslationPlanMode.HIGH_QUALITY: "safe",
}
MODE_TIME_PER_REQUEST = {
    TranslationPlanMode.ECONOMY: (2, 7),
    TranslationPlanMode.BALANCED: (4, 14),
    TranslationPlanMode.HIGH_QUALITY: (8, 30),
}
REQUEST_INPUT_OVERHEAD = 300


def build_translation_plan_comparison(
    records: tuple[ExtractedText, ...] | list[ExtractedText],
    selection: ScanSelectionState,
    *,
    provider: str,
    base_model: str,
    high_quality_model: str = "",
    concurrency: int = 1,
    sqlite_cache_path: Path | None = None,
    jsonl_cache_path: Path | None = None,
    provenance_path: Path | None = None,
) -> TranslationPlanComparison:
    if type(concurrency) is not int or concurrency <= 0:
        raise ValueError("concurrency must be a positive integer")
    selected = selection.selected_records(records)
    skipped = tuple(record for record in selected if _is_skipped(record))
    eligible = tuple(record for record in selected if not _is_skipped(record))
    historical = tuple(
        record for record in eligible if record.translation.strip()
    )
    existing_chinese_ids = _existing_chinese_record_ids(
        historical,
        provenance_path=provenance_path,
    )
    missing = tuple(
        record for record in eligible if not record.translation.strip()
    )
    cached_ids = _cached_record_ids(
        missing,
        provider=provider,
        model=base_model,
        sqlite_cache_path=sqlite_cache_path,
        jsonl_cache_path=jsonl_cache_path,
    )
    pending = tuple(record for record in missing if record.id not in cached_ids)
    plans = tuple(
        _build_plan(
            mode,
            selected=selected,
            historical=historical,
            existing_chinese_ids=existing_chinese_ids,
            cached_ids=cached_ids,
            pending=pending,
            skipped=skipped,
            provider=provider,
            base_model=base_model,
            high_quality_model=high_quality_model,
            concurrency=concurrency,
        )
        for mode in TranslationPlanMode
    )
    return TranslationPlanComparison(plans=plans)


def recommended_high_quality_model(provider: str) -> str:
    return {
        "deepseek": "deepseek-v4-pro",
        "openai": "gpt-4.1",
    }.get(provider.casefold(), "")


def _build_plan(
    mode: TranslationPlanMode,
    *,
    selected: tuple[ExtractedText, ...],
    historical: tuple[ExtractedText, ...],
    existing_chinese_ids: frozenset[str],
    cached_ids: frozenset[str],
    pending: tuple[ExtractedText, ...],
    skipped: tuple[ExtractedText, ...],
    provider: str,
    base_model: str,
    high_quality_model: str,
    concurrency: int,
) -> TranslationPlan:
    routed: dict[tuple[ScanCategoryId, str], list[ExtractedText]] = defaultdict(list)
    for record in pending:
        category = category_for_record(record)
        model = _model_for_category(
            mode,
            category,
            base_model=base_model,
            high_quality_model=high_quality_model,
        )
        routed[(category, model)].append(record)

    request_count = 0
    input_tokens = 0
    output_tokens = 0
    cost = Decimal("0")
    cost_known = True
    currency = "USD"
    routes: list[TranslationRoute] = []
    speed = resolve_speed_mode(MODE_SPEED[mode])
    for (category, model), route_records in sorted(
        routed.items(),
        key=lambda item: (
            item[0][0].value,
            item[0][1].casefold(),
        ),
    ):
        grouped: dict[str, list[tuple[int, ExtractedText]]] = defaultdict(list)
        for index, record in enumerate(route_records):
            grouped[
                make_reuse_key(
                    provider=provider,
                    model=model,
                    original=record.original,
                )
            ].append((index, record))
        pending_groups = [
            make_pending_group(key, rows)
            for key, rows in grouped.items()
        ]
        batches = build_token_batches(pending_groups, speed)
        route_input = sum(group.input_tokens for group in pending_groups)
        route_output = sum(group.output_tokens for group in pending_groups)
        route_input += len(batches) * REQUEST_INPUT_OVERHEAD
        request_count += len(batches)
        input_tokens += route_input
        output_tokens += route_output
        routes.append(
            TranslationRoute(
                category_id=category,
                category_name=CATEGORY_DEFINITIONS[category].title,
                model=model,
                record_count=len(route_records),
            )
        )
        price = MODEL_PRICES.get(
            (provider.casefold(), model.casefold())
        )
        if price is None:
            cost_known = False
        else:
            currency = price.currency
            cost += (
                Decimal(route_input) * price.input_per_million
                + Decimal(route_output) * price.output_per_million
            ) / Decimal(1_000_000)

    seconds_per_request = MODE_TIME_PER_REQUEST[mode]
    waves = math.ceil(request_count / concurrency) if request_count else 0
    low_cost = cost * Decimal("0.85") if cost_known else None
    high_cost = cost * Decimal("1.25") if cost_known else None
    labels = {
        TranslationPlanMode.ECONOMY: (
            "经济模式",
            "尽量合并短文本，全部使用基础模型。",
        ),
        TranslationPlanMode.BALANCED: (
            "平衡模式",
            "长指南和任务说明可使用明确列出的高质量模型。",
        ),
        TranslationPlanMode.HIGH_QUALITY: (
            "高质量模式",
            "所有新译文使用高质量模型并采用更稳妥批次。",
        ),
    }
    title, description = labels[mode]
    return TranslationPlan(
        mode=mode,
        title=title,
        description=description,
        recommended=mode is TranslationPlanMode.BALANCED,
        selected_record_count=len(selected),
        existing_chinese_reuse_count=len(existing_chinese_ids),
        historical_translation_count=(
            len(historical) - len(existing_chinese_ids)
        ),
        cache_reuse_count=len(cached_ids),
        ai_translation_count=len(pending),
        skipped_count=len(skipped),
        estimated_request_count=request_count,
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=output_tokens,
        estimated_cost_low=low_cost,
        estimated_cost_high=high_cost,
        currency=currency if cost_known else "",
        estimated_seconds_low=waves * seconds_per_request[0],
        estimated_seconds_high=waves * seconds_per_request[1],
        routes=tuple(routes),
        pricing_note=(
            "按 2026-07-29 官方公开单价估算，实际账单以服务商为准。"
            if cost_known
            else "当前模型没有本地单价，Token 和请求数仍可估算。"
        ),
    )


def _model_for_category(
    mode: TranslationPlanMode,
    category: ScanCategoryId,
    *,
    base_model: str,
    high_quality_model: str,
) -> str:
    if not high_quality_model:
        return base_model
    if mode is TranslationPlanMode.HIGH_QUALITY:
        return high_quality_model
    if (
        mode is TranslationPlanMode.BALANCED
        and category in HIGH_QUALITY_CATEGORIES
    ):
        return high_quality_model
    return base_model


def _cached_record_ids(
    records: tuple[ExtractedText, ...],
    *,
    provider: str,
    model: str,
    sqlite_cache_path: Path | None,
    jsonl_cache_path: Path | None,
) -> frozenset[str]:
    cached: set[str] = set()
    if sqlite_cache_path is not None and Path(sqlite_cache_path).is_file():
        sqlite_cache = SQLiteTranslationCache(sqlite_cache_path)
        try:
            cached.update(sqlite_cache.get_many(records))
        finally:
            sqlite_cache.close()
    if jsonl_cache_path is not None and Path(jsonl_cache_path).is_file():
        cache = TranslationCache(jsonl_cache_path)
        for record in records:
            if record.id in cached:
                continue
            if cache.get(
                provider=provider,
                model=model,
                original=record.original,
            ):
                cached.add(record.id)
    return frozenset(cached)


def _existing_chinese_record_ids(
    records: tuple[ExtractedText, ...],
    *,
    provenance_path: Path | None,
) -> frozenset[str]:
    if provenance_path is None or not Path(provenance_path).is_file():
        return frozenset()
    sources = frozenset(
        {
            TranslationSource.OFFICIAL_ZH_CN,
            TranslationSource.MODPACK_AUTHOR,
            TranslationSource.TRUSTED_RESOURCE_PACK,
        }
    )
    with TranslationProvenanceStore(provenance_path) as store:
        return frozenset(
            record.id
            for record in records
            if (
                (provenance := store.get(record.id)) is not None
                and provenance.current_source in sources
            )
        )


def _is_skipped(record: ExtractedText) -> bool:
    if record.skip_status.strip():
        return True
    note = record.note.strip().casefold()
    return note in {"skip", "skipped", "user_skipped"} or note.startswith(
        ("skip:", "skip=")
    )

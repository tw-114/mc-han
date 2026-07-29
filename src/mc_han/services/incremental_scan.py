from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from mc_han.models import ExtractedText
from mc_han.services.provenance import TranslationProvenanceStore
from mc_han.utils.atomic_json import write_json_atomic
from mc_han.workflow.incremental import (
    IncrementalScanSummary,
    RecordChange,
    ScanChangeStatus,
)
from mc_han.workflow.provenance import TranslationProvenance, TranslationSource
from mc_han.workflow.translation_rules import (
    TranslationRule,
    TranslationRuleScope,
    target_for_scope,
)


@dataclass(frozen=True)
class TranslationMigration:
    previous: ExtractedText
    current: ExtractedText
    previous_provenance: TranslationProvenance | None


@dataclass(frozen=True)
class IncrementalScanReconciliation:
    records: tuple[ExtractedText, ...]
    summary: IncrementalScanSummary
    migrations: tuple[TranslationMigration, ...]


def reconcile_incremental_scan(
    current_records: list[ExtractedText],
    previous_records: list[ExtractedText],
    *,
    provenance_store: TranslationProvenanceStore | None = None,
    rules: tuple[TranslationRule, ...] = (),
) -> IncrementalScanReconciliation:
    previous_by_context = {
        _context_key(record): record for record in previous_records
    }
    used_previous_ids: set[str] = set()
    changes: list[RecordChange] = []
    updated: list[ExtractedText] = []
    migrations: list[TranslationMigration] = []

    unmatched_current: list[ExtractedText] = []
    for current in current_records:
        previous = previous_by_context.get(_context_key(current))
        if previous is None:
            unmatched_current.append(current)
            continue
        used_previous_ids.add(previous.id)
        if previous.original == current.original:
            changes.append(
                _change(
                    ScanChangeStatus.UNCHANGED,
                    previous,
                    current,
                    rules=rules,
                )
            )
            updated.append(current)
            continue
        migrated, migration = _preserve_manual_work(
            current,
            previous,
            provenance_store=provenance_store,
        )
        updated.append(migrated)
        if migration is not None:
            migrations.append(migration)
        changes.append(
            _change(
                ScanChangeStatus.SOURCE_CHANGED,
                previous,
                migrated,
                rules=rules,
            )
        )

    available_previous: dict[
        tuple[str, str, str, str],
        list[ExtractedText],
    ] = {}
    for previous in previous_records:
        if previous.id in used_previous_ids:
            continue
        available_previous.setdefault(
            _move_key(previous),
            [],
        ).append(previous)
    for values in available_previous.values():
        values.sort(key=_record_sort_key)

    for current in unmatched_current:
        candidates = available_previous.get(_move_key(current), [])
        previous = candidates.pop(0) if candidates else None
        if previous is None:
            updated.append(current)
            changes.append(
                _change(
                    ScanChangeStatus.ADDED,
                    None,
                    current,
                    rules=rules,
                )
            )
            continue
        used_previous_ids.add(previous.id)
        migrated, migration = _preserve_manual_work(
            current,
            previous,
            provenance_store=provenance_store,
        )
        updated.append(migrated)
        if migration is not None:
            migrations.append(migration)
        changes.append(
            _change(
                ScanChangeStatus.CONTEXT_CHANGED,
                previous,
                migrated,
                rules=rules,
            )
        )

    for previous in previous_records:
        if previous.id in used_previous_ids:
            continue
        changes.append(
            _change(
                ScanChangeStatus.REMOVED,
                previous,
                None,
                rules=rules,
            )
        )

    return IncrementalScanReconciliation(
        records=tuple(updated),
        summary=IncrementalScanSummary(
            changes=tuple(changes),
            migrated_manual_count=len(migrations),
        ),
        migrations=tuple(migrations),
    )


def write_incremental_scan_summary(
    path: Path,
    summary: IncrementalScanSummary,
) -> None:
    write_json_atomic(
        Path(path),
        {
            "version": 1,
            **summary.to_dict(),
        },
    )


def _preserve_manual_work(
    current: ExtractedText,
    previous: ExtractedText,
    *,
    provenance_store: TranslationProvenanceStore | None,
) -> tuple[ExtractedText, TranslationMigration | None]:
    if not previous.translation.strip():
        return current, None
    provenance = (
        provenance_store.get(previous.id)
        if provenance_store is not None
        else None
    )
    is_manual = (
        (provenance is not None and (
            provenance.current_source is TranslationSource.MANUAL
            or bool(provenance.manual_confirmed_at)
        ))
        or previous.note.strip().casefold() == "edited"
    )
    if not is_manual:
        return current, None
    migrated = replace(
        current,
        translation=previous.translation,
        note="source_changed: manual translation requires review",
        review_status="needs_retranslate",
        skip_status=previous.skip_status,
    )
    return migrated, TranslationMigration(previous, migrated, provenance)


def _change(
    status: ScanChangeStatus,
    previous: ExtractedText | None,
    current: ExtractedText | None,
    *,
    rules: tuple[TranslationRule, ...],
) -> RecordChange:
    display = current or previous
    if display is None:
        raise ValueError("change requires a record")
    return RecordChange(
        status=status,
        previous_id=previous.id if previous is not None else "",
        current_id=current.id if current is not None else "",
        source_type=display.source_type,
        container=display.container,
        file_path=display.file_path,
        key_path=display.key_path,
        affected_rule_ids=_affected_rule_ids(previous, current, rules),
    )


def _affected_rule_ids(
    previous: ExtractedText | None,
    current: ExtractedText | None,
    rules: tuple[TranslationRule, ...],
) -> tuple[str, ...]:
    if previous is None:
        return ()
    affected: list[str] = []
    for rule in rules:
        if not rule.enabled:
            continue
        if rule.scope in {
            TranslationRuleScope.PROJECT,
            TranslationRuleScope.GLOBAL,
        }:
            continue
        old_target = target_for_scope(previous, rule.scope)
        new_target = (
            target_for_scope(current, rule.scope)
            if current is not None
            else ""
        )
        if rule.target == old_target and rule.target != new_target:
            affected.append(rule.rule_id)
    return tuple(sorted(set(affected)))


def _context_key(record: ExtractedText) -> tuple[str, str, str, str]:
    return (
        record.source_type,
        record.container,
        record.file_path,
        record.key_path,
    )


def _move_key(record: ExtractedText) -> tuple[str, str, str, str]:
    from mc_han.workflow.provenance import record_mod_id

    return (
        record.source_type,
        record_mod_id(record),
        record.key_path,
        record.original,
    )


def _record_sort_key(record: ExtractedText) -> tuple[str, ...]:
    return (
        record.container.casefold(),
        record.file_path.casefold(),
        record.key_path.casefold(),
        record.id,
    )

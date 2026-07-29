from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from mc_han.models import ExtractedText
from mc_han.workflow.provenance import (
    ExistingTranslationCandidate,
    SOURCE_PRIORITY,
    TranslationProvenance,
    TranslationSource,
    original_text_hash,
    record_artifact_version,
    record_mod_id,
    translated_text_hash,
)


class TranslationProvenanceStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            self.path,
            timeout=30,
            check_same_thread=False,
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=30000")
        self._ensure_schema()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> TranslationProvenanceStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def get(self, record_id: str) -> TranslationProvenance | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT record_id, initial_source, current_source, provider, model,
                       rule_version, first_generated_at, last_modified_at,
                       manual_confirmed_at, original_hash, artifact_version,
                       mod_id, changed_after_update, translation_hash
                FROM translation_provenance
                WHERE record_id = ?
                """,
                (record_id,),
            ).fetchone()
        return _provenance_from_row(row) if row is not None else None

    def list_current(self) -> tuple[TranslationProvenance, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT record_id, initial_source, current_source, provider, model,
                       rule_version, first_generated_at, last_modified_at,
                       manual_confirmed_at, original_hash, artifact_version,
                       mod_id, changed_after_update, translation_hash
                FROM translation_provenance
                ORDER BY record_id
                """
            ).fetchall()
        return tuple(_provenance_from_row(row) for row in rows)

    def record_translation(
        self,
        record: ExtractedText,
        translation: str,
        *,
        source: TranslationSource,
        provider: str = "",
        model: str = "",
        rule_version: str = "",
        artifact_version: str | None = None,
        mod_id: str | None = None,
        manual_confirmed: bool = False,
        changed_after_update: bool | None = None,
    ) -> TranslationProvenance:
        if not translation.strip():
            raise ValueError("translation must not be empty")
        if not isinstance(source, TranslationSource):
            raise TypeError("source must be TranslationSource")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        resolved_artifact = artifact_version or record_artifact_version(record)
        resolved_mod_id = mod_id or record_mod_id(record)
        original_hash = original_text_hash(record.original)
        translation_hash = translated_text_hash(translation)
        with self._lock:
            existing = self.get(record.id)
            if (
                existing is not None
                and existing.current_source is source
                and existing.provider == provider
                and existing.model == model
                and existing.rule_version == rule_version
                and existing.original_hash == original_hash
                and existing.artifact_version == resolved_artifact
                and existing.mod_id == resolved_mod_id
                and existing.translation_hash == translation_hash
                and not manual_confirmed
                and changed_after_update is None
            ):
                return existing
            initial_source = (
                existing.initial_source if existing is not None else source
            )
            first_generated_at = (
                existing.first_generated_at if existing is not None else now
            )
            confirmed_at = (
                now
                if manual_confirmed
                else (
                    existing.manual_confirmed_at
                    if existing is not None
                    else ""
                )
            )
            changed = (
                bool(changed_after_update)
                if changed_after_update is not None
                else (
                    existing is not None
                    and (
                        existing.original_hash != original_hash
                        or existing.artifact_version != resolved_artifact
                    )
                )
            )
            self._connection.execute(
                """
                INSERT INTO translation_provenance (
                    record_id, initial_source, current_source, provider, model,
                    rule_version, first_generated_at, last_modified_at,
                    manual_confirmed_at, original_hash, artifact_version,
                    mod_id, changed_after_update, translation_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(record_id) DO UPDATE SET
                    current_source = excluded.current_source,
                    provider = excluded.provider,
                    model = excluded.model,
                    rule_version = excluded.rule_version,
                    last_modified_at = excluded.last_modified_at,
                    manual_confirmed_at = excluded.manual_confirmed_at,
                    original_hash = excluded.original_hash,
                    artifact_version = excluded.artifact_version,
                    mod_id = excluded.mod_id,
                    changed_after_update = excluded.changed_after_update,
                    translation_hash = excluded.translation_hash
                """,
                (
                    record.id,
                    initial_source.value,
                    source.value,
                    provider,
                    model,
                    rule_version,
                    first_generated_at,
                    now,
                    confirmed_at,
                    original_hash,
                    resolved_artifact,
                    resolved_mod_id,
                    int(changed),
                    translation_hash,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO translation_provenance_history (
                    record_id, source, provider, model, rule_version, event_at,
                    original_hash, artifact_version, mod_id, translation_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    source.value,
                    provider,
                    model,
                    rule_version,
                    now,
                    original_hash,
                    resolved_artifact,
                    resolved_mod_id,
                    translation_hash,
                ),
            )
            self._connection.commit()
        result = self.get(record.id)
        if result is None:
            raise sqlite3.DatabaseError("provenance record was not saved")
        return result

    def confirm_manual(self, record: ExtractedText) -> TranslationProvenance:
        if not record.translation.strip():
            raise ValueError("translation must not be empty")
        existing = self.get(record.id)
        source = (
            existing.current_source
            if existing is not None
            else TranslationSource.PROJECT_HISTORY
        )
        return self.record_translation(
            record,
            record.translation,
            source=source,
            provider=existing.provider if existing else "",
            model=existing.model if existing else "",
            rule_version=existing.rule_version if existing else "",
            manual_confirmed=True,
        )

    def count_sources(
        self,
        record_ids: set[str] | frozenset[str],
        sources: frozenset[TranslationSource],
    ) -> int:
        if not record_ids:
            return 0
        values = tuple(sorted(record_ids))
        source_values = tuple(sorted(source.value for source in sources))
        id_placeholders = ",".join("?" for _ in values)
        source_placeholders = ",".join("?" for _ in source_values)
        with self._lock:
            row = self._connection.execute(
                (
                    "SELECT COUNT(*) FROM translation_provenance "
                    f"WHERE record_id IN ({id_placeholders}) "
                    f"AND current_source IN ({source_placeholders})"
                ),
                values + source_values,
            ).fetchone()
        return int(row[0]) if row else 0

    def _ensure_schema(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS translation_provenance (
                    record_id TEXT PRIMARY KEY,
                    initial_source TEXT NOT NULL,
                    current_source TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    rule_version TEXT NOT NULL DEFAULT '',
                    first_generated_at TEXT NOT NULL,
                    last_modified_at TEXT NOT NULL,
                    manual_confirmed_at TEXT NOT NULL DEFAULT '',
                    original_hash TEXT NOT NULL,
                    artifact_version TEXT NOT NULL,
                    mod_id TEXT NOT NULL,
                    changed_after_update INTEGER NOT NULL DEFAULT 0,
                    translation_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS translation_provenance_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    rule_version TEXT NOT NULL DEFAULT '',
                    event_at TEXT NOT NULL,
                    original_hash TEXT NOT NULL,
                    artifact_version TEXT NOT NULL,
                    mod_id TEXT NOT NULL,
                    translation_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_provenance_source
                    ON translation_provenance(current_source);
                CREATE INDEX IF NOT EXISTS idx_provenance_history_record
                    ON translation_provenance_history(record_id, id);
                """
            )
            self._connection.commit()


def choose_existing_candidate(
    record: ExtractedText,
    candidates: tuple[ExistingTranslationCandidate, ...],
    *,
    artifact_version: str,
    mod_id: str,
) -> ExistingTranslationCandidate | None:
    matching = tuple(
        candidate
        for candidate in candidates
        if candidate.matches(
            record,
            mod_id=mod_id,
            artifact_version=artifact_version,
        )
    )
    if not matching:
        return None
    return max(
        matching,
        key=lambda item: (
            SOURCE_PRIORITY[item.source],
            item.source_location.casefold(),
            item.source_location,
        ),
    )


def provenance_rule_version(path: Path) -> str:
    path = Path(path)
    if not path.is_file():
        return ""
    from hashlib import sha256

    digest = sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def reconcile_scan_provenance(
    store: TranslationProvenanceStore,
    records: list[ExtractedText],
    previous_records: list[ExtractedText],
    candidates: tuple[ExistingTranslationCandidate, ...],
) -> None:
    previous_by_key = {
        _record_identity(record): record
        for record in previous_records
        if record.translation.strip()
    }
    candidates_by_id: dict[str, list[ExistingTranslationCandidate]] = {}
    for candidate in candidates:
        candidates_by_id.setdefault(candidate.record_id, []).append(candidate)

    for record in records:
        if not record.translation.strip():
            continue
        previous = previous_by_key.get(_record_identity(record))
        existing = store.get(record.id)
        if previous is not None and previous.translation == record.translation:
            if existing is not None:
                store.record_translation(
                    record,
                    record.translation,
                    source=existing.current_source,
                    provider=existing.provider,
                    model=existing.model,
                    rule_version=existing.rule_version,
                    artifact_version=existing.artifact_version,
                    mod_id=existing.mod_id,
                )
            else:
                store.record_translation(
                    record,
                    record.translation,
                    source=TranslationSource.PROJECT_HISTORY,
                )
            continue

        candidate = next(
            (
                item
                for item in sorted(
                    candidates_by_id.get(record.id, ()),
                    key=lambda value: (
                        -SOURCE_PRIORITY[value.source],
                        value.source_location.casefold(),
                        value.source_location,
                    ),
                )
                if item.translation == record.translation
                and item.key_path == record.key_path
                and item.mod_id == record_mod_id(record)
                and item.original_hash == original_text_hash(record.original)
            ),
            None,
        )
        if candidate is not None:
            store.record_translation(
                record,
                record.translation,
                source=candidate.source,
                artifact_version=candidate.artifact_version,
                mod_id=candidate.mod_id,
            )
        elif existing is None:
            store.record_translation(
                record,
                record.translation,
                source=TranslationSource.PROJECT_HISTORY,
            )


def _record_identity(record: ExtractedText) -> tuple[str, str, str, str, str]:
    return (
        record.source_type,
        record.container,
        record.file_path,
        record.key_path,
        record.original,
    )


def _provenance_from_row(row: tuple[object, ...]) -> TranslationProvenance:
    return TranslationProvenance(
        record_id=str(row[0]),
        initial_source=TranslationSource(str(row[1])),
        current_source=TranslationSource(str(row[2])),
        provider=str(row[3]),
        model=str(row[4]),
        rule_version=str(row[5]),
        first_generated_at=str(row[6]),
        last_modified_at=str(row[7]),
        manual_confirmed_at=str(row[8]),
        original_hash=str(row[9]),
        artifact_version=str(row[10]),
        mod_id=str(row[11]),
        changed_after_update=bool(row[12]),
        translation_hash=str(row[13]),
    )

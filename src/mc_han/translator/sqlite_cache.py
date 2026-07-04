from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import RLock

from mc_han.models import ExtractedText

from .cache import normalize_original

DEFAULT_PROMPT_VERSION = "mc-han-prompt-v1"
DEFAULT_GLOSSARY_VERSION = "mc-han-glossary-v1"


@dataclass(frozen=True)
class SQLiteCacheEntry:
    key: str
    translation: str
    status: str


class SQLiteTranslationCache:
    def __init__(
        self,
        path: Path,
        *,
        prompt_version: str = DEFAULT_PROMPT_VERSION,
        glossary_version: str = DEFAULT_GLOSSARY_VERSION,
    ):
        self.path = Path(path)
        self.prompt_version = prompt_version
        self.glossary_version = glossary_version
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self.connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self._ensure_schema()

    def get(self, record: ExtractedText) -> str | None:
        key = make_sqlite_cache_key(
            original=record.original,
            context_hash=context_hash(record),
            prompt_version=self.prompt_version,
            glossary_version=self.glossary_version,
        )
        with self._lock:
            row = self.connection.execute(
                "SELECT translation FROM translations WHERE cache_key = ? AND status = 'translated'",
                (key,),
            ).fetchone()
        return str(row[0]) if row else None

    def set(
        self,
        record: ExtractedText,
        *,
        translation: str,
        provider: str,
        model: str,
        status: str = "translated",
        error: str = "",
    ) -> None:
        original = normalize_original(record.original)
        ctx_hash = context_hash(record)
        key = make_sqlite_cache_key(
            original=original,
            context_hash=ctx_hash,
            prompt_version=self.prompt_version,
            glossary_version=self.glossary_version,
        )
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO translations (
                    cache_key, original, context_hash, prompt_version, glossary_version,
                    provider, model, source_type, file_path, key_path, translation,
                    status, error, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    provider = excluded.provider,
                    model = excluded.model,
                    source_type = excluded.source_type,
                    file_path = excluded.file_path,
                    key_path = excluded.key_path,
                    translation = excluded.translation,
                    status = excluded.status,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    original,
                    ctx_hash,
                    self.prompt_version,
                    self.glossary_version,
                    provider,
                    model,
                    record.source_type,
                    record.file_path,
                    record.key_path,
                    translation,
                    status,
                    error,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            self.connection.commit()

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def _ensure_schema(self) -> None:
        with self._lock:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS translations (
                    cache_key TEXT PRIMARY KEY,
                    original TEXT NOT NULL,
                    context_hash TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    glossary_version TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    key_path TEXT NOT NULL,
                    translation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )
                """
            )
            self.connection.execute("CREATE INDEX IF NOT EXISTS idx_translations_status ON translations(status)")
            self.connection.execute("CREATE INDEX IF NOT EXISTS idx_translations_original ON translations(original)")
            self.connection.commit()


def context_hash(record: ExtractedText) -> str:
    stable = "\0".join((record.source_type, record.container, record.file_path, record.key_path))
    return sha256(stable.encode("utf-8")).hexdigest()


def make_sqlite_cache_key(
    *,
    original: str,
    context_hash: str,
    prompt_version: str,
    glossary_version: str,
) -> str:
    stable = "\0".join((normalize_original(original), context_hash, prompt_version, glossary_version))
    return sha256(stable.encode("utf-8")).hexdigest()

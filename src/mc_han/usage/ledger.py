from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock

from .models import ApiAttemptUsage

SCHEMA_VERSION = 1


class UsageSchemaError(sqlite3.DatabaseError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class UsageLedger:
    """Thread-safe, immediately durable storage for API-attempt metadata."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            self.path,
            timeout=30,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout=30000")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._ensure_schema()

    def __enter__(self) -> "UsageLedger":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def record_attempt(self, event: ApiAttemptUsage) -> bool:
        if not isinstance(event, ApiAttemptUsage):
            raise TypeError("event must be ApiAttemptUsage")
        values = event.to_dict()
        with self._lock:
            try:
                self._connection.execute(
                    """
                    INSERT INTO api_attempts (
                        event_id, task_id, batch_id, attempt_number, provider,
                        model, endpoint_type, thinking_mode, source_types_json,
                        usage_diagnostics_json,
                        item_count, input_tokens, output_tokens,
                        cached_input_tokens, uncached_input_tokens,
                        reasoning_tokens, total_tokens,
                        reasoning_included_in_output, request_started_at,
                        latency_ms, outcome, retryable, stable_error_code,
                        provider_request_id, provider_reported_cost,
                        estimated_cost, currency, pricing_profile_id
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        event.event_id,
                        event.task_id,
                        event.batch_id,
                        event.attempt_number,
                        event.provider,
                        event.model,
                        event.endpoint_type,
                        event.thinking_mode,
                        json.dumps(values["source_types"], separators=(",", ":")),
                        json.dumps(
                            values["usage_diagnostics"],
                            separators=(",", ":"),
                        ),
                        event.item_count,
                        event.tokens.input_tokens,
                        event.tokens.output_tokens,
                        event.tokens.cached_input_tokens,
                        event.tokens.uncached_input_tokens,
                        event.tokens.reasoning_tokens,
                        event.tokens.total_tokens,
                        (
                            None
                            if event.tokens.reasoning_included_in_output is None
                            else int(event.tokens.reasoning_included_in_output)
                        ),
                        event.request_started_at,
                        event.latency_ms,
                        event.outcome.value,
                        int(event.retryable),
                        event.stable_error_code,
                        event.provider_request_id,
                        (
                            str(event.provider_reported_cost)
                            if event.provider_reported_cost is not None
                            else None
                        ),
                        (
                            str(event.estimated_cost)
                            if event.estimated_cost is not None
                            else None
                        ),
                        event.currency,
                        event.pricing_profile_id,
                    ),
                )
                self._connection.executemany(
                    """
                    INSERT INTO attempt_categories (
                        event_id, category_id, item_count
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        (
                            event.event_id,
                            category.category_id.value,
                            category.item_count,
                        )
                        for category in event.category_items
                    ),
                )
                self._connection.commit()
                return True
            except sqlite3.IntegrityError:
                self._connection.rollback()
                duplicate = self._connection.execute(
                    "SELECT 1 FROM api_attempts WHERE event_id = ?",
                    (event.event_id,),
                ).fetchone()
                if duplicate is not None:
                    return False
                raise
            except sqlite3.Error:
                self._connection.rollback()
                raise

    def update_task_stats(
        self,
        *,
        task_id: str,
        total_items: int,
        reused_items: int,
        avoided_api_items: int,
        remaining_items: int,
        updated_at: str,
    ) -> None:
        """Upsert non-API task state that cannot be rebuilt from attempt events."""
        counts = {
            "total_items": total_items,
            "reused_items": reused_items,
            "avoided_api_items": avoided_api_items,
            "remaining_items": remaining_items,
        }
        for field_name, value in counts.items():
            if type(value) is not int:
                raise TypeError(f"{field_name} must be an integer")
            if value < 0:
                raise ValueError(f"{field_name} must not be negative")
        if (
            not isinstance(task_id, str)
            or not task_id
            or any(character in task_id for character in ("/", "\\", "\0"))
            or any(ord(character) < 32 for character in task_id)
        ):
            raise ValueError("task_id must be a safe non-empty identifier")
        try:
            datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("updated_at must be an ISO-8601 timestamp") from error
        with self._lock:
            try:
                self._connection.execute(
                    """
                    INSERT INTO task_stats (
                        task_id, total_items, reused_items, avoided_api_items,
                        remaining_items, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        total_items = excluded.total_items,
                        reused_items = excluded.reused_items,
                        avoided_api_items = excluded.avoided_api_items,
                        remaining_items = excluded.remaining_items,
                        updated_at = excluded.updated_at
                    """,
                    (
                        task_id,
                        total_items,
                        reused_items,
                        avoided_api_items,
                        remaining_items,
                        updated_at,
                    ),
                )
                self._connection.commit()
            except sqlite3.Error:
                self._connection.rollback()
                raise

    def attempt_rows(
        self,
        *,
        task_id: str | None = None,
    ) -> list[sqlite3.Row]:
        with self._lock:
            if task_id is None:
                rows = self._connection.execute(
                    "SELECT * FROM api_attempts ORDER BY request_started_at, event_id"
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT * FROM api_attempts
                    WHERE task_id = ?
                    ORDER BY request_started_at, event_id
                    """,
                    (task_id,),
                ).fetchall()
        return list(rows)

    def category_rows(self) -> list[sqlite3.Row]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT event_id, category_id, item_count
                FROM attempt_categories
                ORDER BY event_id, category_id
                """
            ).fetchall()
        return list(rows)

    def task_stats_rows(
        self,
        *,
        task_id: str | None = None,
    ) -> list[sqlite3.Row]:
        with self._lock:
            if task_id is None:
                rows = self._connection.execute(
                    "SELECT * FROM task_stats ORDER BY task_id"
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM task_stats WHERE task_id = ?",
                    (task_id,),
                ).fetchall()
        return list(rows)

    def schema_version(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT version FROM schema_version LIMIT 1"
            ).fetchone()
        return int(row["version"])

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _ensure_schema(self) -> None:
        with self._lock:
            try:
                self._connection.execute("BEGIN")
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_version (
                        version INTEGER NOT NULL
                    )
                    """
                )
                row = self._connection.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                if row is None:
                    self._connection.execute(
                        "INSERT INTO schema_version(version) VALUES (?)",
                        (SCHEMA_VERSION,),
                    )
                else:
                    try:
                        version = int(row["version"])
                    except (TypeError, ValueError) as error:
                        raise UsageSchemaError(
                            "usage_schema_invalid"
                        ) from error
                    if version > SCHEMA_VERSION:
                        raise UsageSchemaError("usage_schema_newer")
                    if version < SCHEMA_VERSION:
                        raise UsageSchemaError("usage_schema_older")
                self._create_schema_v1()
                self._connection.commit()
            except sqlite3.Error:
                self._connection.rollback()
                self._connection.close()
                raise

    def _create_schema_v1(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS api_attempts (
                event_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL CHECK(attempt_number > 0),
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                endpoint_type TEXT NOT NULL,
                thinking_mode TEXT NOT NULL,
                source_types_json TEXT NOT NULL,
                usage_diagnostics_json TEXT NOT NULL,
                item_count INTEGER NOT NULL CHECK(item_count >= 0),
                input_tokens INTEGER,
                output_tokens INTEGER,
                cached_input_tokens INTEGER,
                uncached_input_tokens INTEGER,
                reasoning_tokens INTEGER,
                total_tokens INTEGER,
                reasoning_included_in_output INTEGER,
                request_started_at TEXT NOT NULL,
                latency_ms INTEGER NOT NULL CHECK(latency_ms >= 0),
                outcome TEXT NOT NULL,
                retryable INTEGER NOT NULL,
                stable_error_code TEXT NOT NULL,
                provider_request_id TEXT NOT NULL,
                provider_reported_cost TEXT,
                estimated_cost TEXT,
                currency TEXT NOT NULL,
                pricing_profile_id TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS attempt_categories (
                event_id TEXT NOT NULL REFERENCES api_attempts(event_id)
                    ON DELETE CASCADE,
                category_id TEXT NOT NULL,
                item_count INTEGER NOT NULL CHECK(item_count > 0),
                PRIMARY KEY(event_id, category_id)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS task_stats (
                task_id TEXT PRIMARY KEY,
                total_items INTEGER NOT NULL CHECK(total_items >= 0),
                reused_items INTEGER NOT NULL CHECK(reused_items >= 0),
                avoided_api_items INTEGER NOT NULL CHECK(avoided_api_items >= 0),
                remaining_items INTEGER NOT NULL CHECK(remaining_items >= 0),
                updated_at TEXT NOT NULL
            )
            """
        )
        for sql in (
            "CREATE INDEX IF NOT EXISTS idx_usage_task ON api_attempts(task_id)",
            """
            CREATE INDEX IF NOT EXISTS idx_usage_provider_model
            ON api_attempts(provider, model)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_usage_started
            ON api_attempts(request_started_at)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_usage_outcome
            ON api_attempts(outcome)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_usage_category
            ON attempt_categories(category_id)
            """,
        ):
            self._connection.execute(sql)

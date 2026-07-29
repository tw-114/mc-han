from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ScanChangeStatus(str, Enum):
    UNCHANGED = "unchanged"
    ADDED = "added"
    SOURCE_CHANGED = "source_changed"
    REMOVED = "removed"
    CONTEXT_CHANGED = "context_changed"


CHANGE_STATUS_ORDER = (
    ScanChangeStatus.UNCHANGED,
    ScanChangeStatus.ADDED,
    ScanChangeStatus.SOURCE_CHANGED,
    ScanChangeStatus.REMOVED,
    ScanChangeStatus.CONTEXT_CHANGED,
)


@dataclass(frozen=True)
class RecordChange:
    status: ScanChangeStatus
    previous_id: str
    current_id: str
    source_type: str
    container: str
    file_path: str
    key_path: str
    affected_rule_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, ScanChangeStatus):
            raise TypeError("status must be ScanChangeStatus")
        for field_name in (
            "previous_id",
            "current_id",
            "source_type",
            "container",
            "file_path",
            "key_path",
        ):
            if not isinstance(getattr(self, field_name), str):
                raise TypeError(f"{field_name} must be a string")
        if self.status is ScanChangeStatus.ADDED and not self.current_id:
            raise ValueError("added changes require current_id")
        if self.status is ScanChangeStatus.REMOVED and not self.previous_id:
            raise ValueError("removed changes require previous_id")
        if self.status not in {
            ScanChangeStatus.ADDED,
            ScanChangeStatus.REMOVED,
        } and (not self.previous_id or not self.current_id):
            raise ValueError("paired changes require both record IDs")
        rules = tuple(self.affected_rule_ids)
        if not all(isinstance(item, str) and item for item in rules):
            raise ValueError("affected_rule_ids must contain non-empty strings")
        object.__setattr__(
            self,
            "affected_rule_ids",
            tuple(sorted(set(rules))),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "previous_id": self.previous_id,
            "current_id": self.current_id,
            "source_type": self.source_type,
            "container": self.container,
            "file_path": self.file_path,
            "key_path": self.key_path,
            "affected_rule_ids": list(self.affected_rule_ids),
        }


@dataclass(frozen=True)
class IncrementalScanSummary:
    changes: tuple[RecordChange, ...] = ()
    migrated_manual_count: int = 0

    def __post_init__(self) -> None:
        changes = tuple(self.changes)
        if not all(isinstance(item, RecordChange) for item in changes):
            raise TypeError("changes must contain RecordChange values")
        if type(self.migrated_manual_count) is not int:
            raise TypeError("migrated_manual_count must be an integer")
        if self.migrated_manual_count < 0:
            raise ValueError("migrated_manual_count must not be negative")
        status_order = {
            status: index for index, status in enumerate(CHANGE_STATUS_ORDER)
        }
        object.__setattr__(
            self,
            "changes",
            tuple(
                sorted(
                    changes,
                    key=lambda item: (
                        status_order[item.status],
                        item.container.casefold(),
                        item.file_path.casefold(),
                        item.key_path.casefold(),
                        item.previous_id,
                        item.current_id,
                    ),
                )
            ),
        )

    def count(self, status: ScanChangeStatus) -> int:
        return sum(item.status is status for item in self.changes)

    @property
    def unchanged_count(self) -> int:
        return self.count(ScanChangeStatus.UNCHANGED)

    @property
    def added_count(self) -> int:
        return self.count(ScanChangeStatus.ADDED)

    @property
    def source_changed_count(self) -> int:
        return self.count(ScanChangeStatus.SOURCE_CHANGED)

    @property
    def removed_count(self) -> int:
        return self.count(ScanChangeStatus.REMOVED)

    @property
    def context_changed_count(self) -> int:
        return self.count(ScanChangeStatus.CONTEXT_CHANGED)

    @property
    def affected_rule_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    rule_id
                    for change in self.changes
                    for rule_id in change.affected_rule_ids
                }
            )
        )

    @property
    def needs_review_count(self) -> int:
        return self.source_changed_count + self.context_changed_count

    def to_dict(self) -> dict[str, object]:
        return {
            "counts": {
                status.value: self.count(status)
                for status in CHANGE_STATUS_ORDER
            },
            "migrated_manual_count": self.migrated_manual_count,
            "affected_rule_ids": list(self.affected_rule_ids),
            "changes": [item.to_dict() for item in self.changes],
        }

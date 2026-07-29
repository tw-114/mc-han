from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from mc_han.models import ExtractedText


class TranslationRuleType(str, Enum):
    EXACT = "exact"
    TERMINOLOGY = "terminology"
    STYLE = "style"
    FORBIDDEN = "forbidden"
    FORMAT = "format"


class TranslationRuleScope(str, Enum):
    RECORD = "record"
    FILE = "file"
    MOD = "mod"
    PROJECT = "project"
    GLOBAL = "global"


SCOPE_PRIORITY = {
    TranslationRuleScope.RECORD: 5,
    TranslationRuleScope.FILE: 4,
    TranslationRuleScope.MOD: 3,
    TranslationRuleScope.PROJECT: 2,
    TranslationRuleScope.GLOBAL: 1,
}


@dataclass(frozen=True)
class TranslationRule:
    rule_id: str
    rule_type: TranslationRuleType
    scope: TranslationRuleScope
    target: str
    instruction: str
    source: str
    created_at: str
    enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.rule_type, TranslationRuleType):
            raise TypeError("rule_type must be TranslationRuleType")
        if not isinstance(self.scope, TranslationRuleScope):
            raise TypeError("scope must be TranslationRuleScope")
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be bool")
        for field_name in (
            "rule_id",
            "target",
            "instruction",
            "source",
            "created_at",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string")
            if not value.strip():
                raise ValueError(f"{field_name} must not be empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "rule_type": self.rule_type.value,
            "scope": self.scope.value,
            "target": self.target,
            "instruction": self.instruction,
            "source": self.source,
            "created_at": self.created_at,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class TranslationRuleResolution:
    applied: tuple[TranslationRule, ...]
    conflicts: tuple[TranslationRule, ...]

    @property
    def prompt_instructions(self) -> tuple[str, ...]:
        return tuple(rule.instruction for rule in self.applied)


def target_for_scope(
    record: ExtractedText,
    scope: TranslationRuleScope,
) -> str:
    if not isinstance(record, ExtractedText):
        raise TypeError("record must be ExtractedText")
    if scope is TranslationRuleScope.RECORD:
        return record.id
    if scope is TranslationRuleScope.FILE:
        return f"{record.container}\0{record.file_path}"
    if scope is TranslationRuleScope.MOD:
        return record.container
    if scope in {TranslationRuleScope.PROJECT, TranslationRuleScope.GLOBAL}:
        return "*"
    raise TypeError("scope must be TranslationRuleScope")


def rule_matches(rule: TranslationRule, record: ExtractedText) -> bool:
    return rule.target == target_for_scope(record, rule.scope)


def resolve_translation_rules(
    rules: tuple[TranslationRule, ...],
    record: ExtractedText,
) -> TranslationRuleResolution:
    matching = tuple(
        sorted(
            (
                rule
                for rule in rules
                if rule.enabled and rule_matches(rule, record)
            ),
            key=lambda rule: (
                SCOPE_PRIORITY[rule.scope],
                rule.created_at,
                rule.rule_id,
            ),
            reverse=True,
        )
    )
    winners: dict[TranslationRuleType, TranslationRule] = {}
    conflicts: list[TranslationRule] = []
    for rule in matching:
        winner = winners.get(rule.rule_type)
        if winner is None:
            winners[rule.rule_type] = rule
        elif rule.instruction != winner.instruction:
            conflicts.append(rule)
    return TranslationRuleResolution(
        applied=tuple(
            winners[rule_type]
            for rule_type in TranslationRuleType
            if rule_type in winners
        ),
        conflicts=tuple(conflicts),
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from mc_han.models import ExtractedText
from mc_han.utils.atomic_json import write_json_atomic
from mc_han.workflow.translation_rules import (
    TranslationRule,
    TranslationRuleResolution,
    TranslationRuleScope,
    TranslationRuleType,
    resolve_translation_rules,
    target_for_scope,
    utc_now,
)


class TranslationRuleStore:
    def __init__(
        self,
        project_path: Path,
        *,
        global_path: Path | None = None,
    ) -> None:
        self.project_path = Path(project_path)
        self.global_path = Path(global_path) if global_path else None

    def load(self) -> tuple[TranslationRule, ...]:
        rules = list(_read_rules(self.project_path))
        if self.global_path is not None:
            rules.extend(
                rule
                for rule in _read_rules(self.global_path)
                if rule.scope is TranslationRuleScope.GLOBAL
            )
        unique = {rule.rule_id: rule for rule in rules}
        return tuple(
            sorted(
                unique.values(),
                key=lambda rule: (rule.created_at, rule.rule_id),
            )
        )

    def add_feedback_rule(
        self,
        record: ExtractedText,
        *,
        rule_type: TranslationRuleType,
        scope: TranslationRuleScope,
        instruction: str,
        source: str,
    ) -> TranslationRule:
        rule = TranslationRule(
            rule_id=f"rule-{uuid4().hex}",
            rule_type=rule_type,
            scope=scope,
            target=target_for_scope(record, scope),
            instruction=instruction.strip(),
            source=source.strip(),
            created_at=utc_now(),
        )
        target_path = (
            self.global_path
            if scope is TranslationRuleScope.GLOBAL
            and self.global_path is not None
            else self.project_path
        )
        rules = list(_read_rules(target_path))
        rules.append(rule)
        _write_rules(target_path, tuple(rules))
        return rule

    def set_enabled(self, rule_id: str, enabled: bool) -> TranslationRule:
        if type(enabled) is not bool:
            raise TypeError("enabled must be bool")
        for path in self._paths():
            rules = list(_read_rules(path))
            for index, rule in enumerate(rules):
                if rule.rule_id != rule_id:
                    continue
                updated = replace(rule, enabled=enabled)
                rules[index] = updated
                _write_rules(path, tuple(rules))
                return updated
        raise ValueError("translation rule was not found")

    def resolve(self, record: ExtractedText) -> TranslationRuleResolution:
        return resolve_translation_rules(self.load(), record)

    def _paths(self) -> tuple[Path, ...]:
        if self.global_path is None:
            return (self.project_path,)
        return (self.project_path, self.global_path)


def _read_rules(path: Path) -> tuple[TranslationRule, ...]:
    path = Path(path)
    if not path.is_file():
        return ()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ()
    items = raw.get("rules", ()) if isinstance(raw, dict) else ()
    if not isinstance(items, list):
        return ()
    rules: list[TranslationRule] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            rules.append(
                TranslationRule(
                    rule_id=item["rule_id"],
                    rule_type=TranslationRuleType(item["rule_type"]),
                    scope=TranslationRuleScope(item["scope"]),
                    target=item["target"],
                    instruction=item["instruction"],
                    source=item["source"],
                    created_at=item["created_at"],
                    enabled=item.get("enabled", True),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(rules)


def _write_rules(path: Path, rules: tuple[TranslationRule, ...]) -> None:
    write_json_atomic(
        Path(path),
        {
            "version": 1,
            "rules": [rule.to_dict() for rule in rules],
        },
    )

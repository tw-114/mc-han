from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from mc_han.models import ExtractedText
from mc_han.services.translation_rules import TranslationRuleStore
from mc_han.workflow.translation_rules import (
    TranslationRuleScope,
    TranslationRuleType,
)


def _record(record_id: str = "demo") -> ExtractedText:
    return ExtractedText(
        id=record_id,
        source_type="jar_lang",
        container="mods/demo.jar",
        file_path="assets/demo/lang/en_us.json",
        key_path=f"demo.{record_id}",
        original="Original",
    )


def test_rule_store_round_trip_priority_conflict_disable_and_undo(
    tmp_path: Path,
):
    store = TranslationRuleStore(tmp_path / "rules.json")
    record = _record()
    global_rule = store.add_feedback_rule(
        record,
        rule_type=TranslationRuleType.STYLE,
        scope=TranslationRuleScope.GLOBAL,
        instruction="使用简洁语气",
        source="trial_feedback:tone",
    )
    record_rule = store.add_feedback_rule(
        record,
        rule_type=TranslationRuleType.STYLE,
        scope=TranslationRuleScope.RECORD,
        instruction="使用教程式语气",
        source="trial_feedback:tone",
    )

    resolution = store.resolve(record)
    assert resolution.applied == (record_rule,)
    assert resolution.conflicts == (global_rule,)
    assert store.set_enabled(record_rule.rule_id, False).enabled is False
    assert store.resolve(record).applied == (global_rule,)
    assert store.set_enabled(record_rule.rule_id, True).enabled is True
    assert TranslationRuleStore(tmp_path / "rules.json").load() == store.load()


def test_scope_targets_do_not_leak_to_unrelated_context(tmp_path: Path):
    store = TranslationRuleStore(tmp_path / "rules.json")
    record = _record("first")
    store.add_feedback_rule(
        record,
        rule_type=TranslationRuleType.EXACT,
        scope=TranslationRuleScope.FILE,
        instruction="固定翻译",
        source="trial_feedback:wording",
    )

    same_file = replace(record, id="second", key_path="demo.second")
    other_file = replace(
        record,
        id="third",
        file_path="assets/demo/other.json",
    )
    assert store.resolve(same_file).prompt_instructions == ("固定翻译",)
    assert store.resolve(other_file).prompt_instructions == ()


def test_global_rules_are_stored_separately_and_loaded_for_project(
    tmp_path: Path,
):
    project_path = tmp_path / "project.json"
    global_path = tmp_path / "global.json"
    store = TranslationRuleStore(project_path, global_path=global_path)
    record = _record()

    store.add_feedback_rule(
        record,
        rule_type=TranslationRuleType.TERMINOLOGY,
        scope=TranslationRuleScope.GLOBAL,
        instruction="ME 保持英文",
        source="trial_feedback:terminology",
    )

    assert not project_path.exists()
    assert global_path.is_file()
    assert store.resolve(record).prompt_instructions == ("ME 保持英文",)

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from mc_han.cli import main, run_usage
from mc_han.core.project import project_paths
from mc_han.usage.ledger import UsageLedger
from mc_han.usage.models import (
    ApiAttemptUsage,
    TokenUsage,
    UsageCategoryCount,
    UsageOutcome,
)
from mc_han.workflow.scan_models import ScanCategoryId


def test_usage_cli_text_output(tmp_path: Path, capsys):
    modpack = tmp_path / "pack"
    modpack.mkdir()
    with UsageLedger(project_paths(modpack).usage_sqlite) as ledger:
        ledger.record_attempt(make_event(event_id="event-text"))

    code = main(["usage", str(modpack)])
    output = capsys.readouterr().out

    assert code == 0
    assert "翻译用量报告" in output
    assert "API 请求：1" in output
    assert "输入 Token：100" in output
    assert "模组语言文件" in output


def test_usage_cli_json_stdout_is_one_document(tmp_path: Path, capsys):
    modpack = tmp_path / "pack"
    modpack.mkdir()
    with UsageLedger(project_paths(modpack).usage_sqlite) as ledger:
        ledger.record_attempt(make_event(event_id="event-json"))

    code = main(["usage", str(modpack), "--json"])
    output = capsys.readouterr()
    parsed = json.loads(output.out)

    assert code == 0
    assert output.err == ""
    assert parsed["status"] == "ok"
    assert parsed["summary"]["api_attempts"] == 1


def test_usage_cli_missing_ledger_is_friendly(tmp_path: Path, capsys):
    modpack = tmp_path / "pack"
    modpack.mkdir()

    code = main(["usage", str(modpack), "--json"])
    parsed = json.loads(capsys.readouterr().out)

    assert code == 1
    assert parsed == {
        "status": "not_found",
        "message": "尚无翻译 API 用量记录。",
    }


def test_usage_cli_text_errors_are_written_to_stderr(tmp_path: Path, capsys):
    modpack = tmp_path / "pack"
    modpack.mkdir()

    code = main(["usage", str(modpack)])
    output = capsys.readouterr()

    assert code == 1
    assert output.out == ""
    assert "尚无翻译 API 用量记录" in output.err


def test_usage_cli_reports_partial_provider_cost_as_subtotal(
    tmp_path: Path,
    capsys,
):
    modpack = tmp_path / "pack"
    modpack.mkdir()
    with UsageLedger(project_paths(modpack).usage_sqlite) as ledger:
        ledger.record_attempt(
            replace(
                make_event(event_id="reported"),
                provider_reported_cost=Decimal("0.01"),
                currency="CNY",
            )
        )
        ledger.record_attempt(make_event(event_id="missing"))

    code = main(["usage", str(modpack)])
    output = capsys.readouterr()

    assert code == 0
    assert output.err == ""
    assert "服务商已报告费用小计：0.01 CNY" in output.out
    assert "另有 1 个请求未返回费用" in output.out


def test_usage_cli_corrupt_database_has_stable_error_without_path(
    tmp_path: Path,
    capsys,
):
    private_fragment = "PrivatePerson"
    modpack = tmp_path / private_fragment / "pack"
    ledger_path = project_paths(modpack).usage_sqlite
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_bytes(b"not a sqlite database")

    code = run_usage(modpack, json_output=True)
    output = capsys.readouterr()
    parsed = json.loads(output.out)

    assert code == 2
    assert output.err == ""
    assert parsed["code"] == "usage_database_unreadable"
    assert private_fragment not in output.out


def test_usage_cli_rejects_untrusted_values_in_structurally_valid_database(
    tmp_path: Path,
    capsys,
):
    private_fragment = "PrivatePerson"
    modpack = tmp_path / "pack"
    modpack.mkdir()
    ledger_path = project_paths(modpack).usage_sqlite
    with UsageLedger(ledger_path) as ledger:
        ledger.record_attempt(make_event(event_id="event-tampered"))
    connection = sqlite3.connect(ledger_path)
    connection.execute(
        "UPDATE attempt_categories SET category_id = ?",
        (f"C:\\Users\\{private_fragment}\\secret",),
    )
    connection.commit()
    connection.close()

    code = main(["usage", str(modpack), "--json"])
    output = capsys.readouterr().out
    parsed = json.loads(output)

    assert code == 2
    assert parsed["code"] == "usage_database_unreadable"
    assert private_fragment not in output


def make_event(*, event_id: str) -> ApiAttemptUsage:
    return ApiAttemptUsage(
        event_id=event_id,
        task_id="task-cli",
        batch_id="batch-cli",
        attempt_number=1,
        provider="fake",
        model="fake-model",
        endpoint_type="chat_completions",
        thinking_mode="",
        category_items=(
            UsageCategoryCount(ScanCategoryId.MOD_LANGUAGE, 1),
        ),
        source_types=("jar_lang",),
        item_count=1,
        tokens=TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cached_input_tokens=20,
            uncached_input_tokens=80,
        ),
        request_started_at="2026-01-01T00:00:00+00:00",
        latency_ms=20,
        outcome=UsageOutcome.SUCCESS,
        retryable=False,
        stable_error_code="",
        provider_request_id="sha256:1234567890abcdef",
        provider_reported_cost=None,
        estimated_cost=None,
        currency="",
        pricing_profile_id="",
    )

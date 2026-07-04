from __future__ import annotations

from argparse import Namespace

from mc_han.checkpoints import create_checkpoint, csv_status, rollback_checkpoint
from mc_han.cli import run_rollback, run_status, run_translate
from mc_han.csv_store import read_extracted_csv, write_extracted_csv
from mc_han.models import ExtractedText


def write_sample_csv(path, translation: str = ""):
    write_extracted_csv(
        [
            ExtractedText(
                id="1",
                source_type="jar_lang",
                container="mods/demo.jar",
                file_path="assets/demo/lang/en_us.json",
                key_path="message.demo",
                original="Hello world",
                translation=translation,
            )
        ],
        path,
    )


def test_checkpoint_and_rollback_restore_csv(tmp_path):
    csv_path = tmp_path / "extracted_texts.csv"
    write_sample_csv(csv_path)
    checkpoint = create_checkpoint(csv_path, label="trial")
    write_sample_csv(csv_path, translation="你好，世界")

    restored = rollback_checkpoint(csv_path)
    records = read_extracted_csv(csv_path)

    assert restored.path == checkpoint.path
    assert records[0].translation == ""


def test_status_counts_translation_and_checkpoints(tmp_path):
    csv_path = tmp_path / "extracted_texts.csv"
    write_sample_csv(csv_path, translation="你好，世界")
    create_checkpoint(csv_path, label="status")

    status = csv_status(csv_path)

    assert status.total_rows == 1
    assert status.translated_rows == 1
    assert status.missing_rows == 0
    assert status.checkpoint_count == 1


def test_run_translate_creates_checkpoint_before_overwriting(tmp_path):
    csv_path = tmp_path / "extracted_texts.csv"
    write_sample_csv(csv_path)

    code = run_translate(
        Namespace(
            modpack_dir=tmp_path,
            input=csv_path,
            output=csv_path,
            provider="mock",
            model=None,
            api_key=None,
            api_key_env=None,
            base_url=None,
            cache=tmp_path / "cache.jsonl",
            batch_size=8,
            limit=None,
            confirm_cost=False,
            no_checkpoint=False,
            force=False,
        )
    )
    status = csv_status(csv_path)

    assert code == 0
    assert status.translated_rows == 1
    assert status.checkpoint_count == 1


def test_run_status_and_rollback_commands(tmp_path, capsys):
    csv_path = tmp_path / "extracted_texts.csv"
    write_sample_csv(csv_path)
    create_checkpoint(csv_path, label="command")
    write_sample_csv(csv_path, translation="你好，世界")

    assert run_status(csv_path) == 0
    assert "Translated 1" in capsys.readouterr().out
    assert run_rollback(csv_path, "latest") == 0
    assert read_extracted_csv(csv_path)[0].translation == ""

from __future__ import annotations

from argparse import Namespace

from mc_han.cli import real_provider_cost_allowed, run_preview, run_translate
from mc_han.csv_store import write_extracted_csv
from mc_han.models import ExtractedText


def test_real_provider_requires_limit_or_confirm_cost():
    assert real_provider_cost_allowed("mock", None, False)
    assert real_provider_cost_allowed("openai", 5, False)
    assert real_provider_cost_allowed("openai", None, True)
    assert not real_provider_cost_allowed("openai", None, False)


def test_run_translate_blocks_real_provider_before_api_key_lookup(tmp_path):
    csv_path = tmp_path / "extracted_texts.csv"
    write_extracted_csv(
        [
            ExtractedText(
                id="1",
                source_type="jar_lang",
                container="mods/demo.jar",
                file_path="assets/demo/lang/en_us.json",
                key_path="message.demo",
                original="Hello world",
            )
        ],
        csv_path,
    )

    code = run_translate(
        Namespace(
            modpack_dir=tmp_path,
            input=csv_path,
            output=csv_path,
            provider="openai",
            model="example-model",
            api_key=None,
            api_key_env=None,
            base_url=None,
            cache=tmp_path / "cache.jsonl",
            batch_size=8,
            limit=None,
            confirm_cost=False,
            force=False,
        )
    )

    assert code == 2
    assert not (tmp_path / "cache.jsonl").exists()


def test_preview_command_outputs_counts_and_samples(tmp_path, capsys):
    csv_path = tmp_path / "extracted_texts.csv"
    write_extracted_csv(
        [
            ExtractedText(
                id="1",
                source_type="jar_lang",
                container="mods/demo.jar",
                file_path="assets/demo/lang/en_us.json",
                key_path="message.demo",
                original="Hello world",
                translation="你好，世界",
            ),
            ExtractedText(
                id="2",
                source_type="jar_patchouli",
                container="mods/demo.jar",
                file_path="assets/demo/patchouli_books/book/en_us/entries/intro.json",
                key_path="name",
                original="Intro",
            ),
        ],
        csv_path,
    )

    code = run_preview(csv_path, limit=1, translated_only=True, untranslated_only=False)
    output = capsys.readouterr().out

    assert code == 0
    assert "rows: 2" in output
    assert "translated_rows: 1" in output
    assert "Hello world" in output
    assert "Intro" not in output

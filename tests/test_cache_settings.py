from __future__ import annotations

from pathlib import Path

from mc_han.cli import create_translator, resolve_translation_settings
from mc_han.csv_store import read_extracted_csv, write_extracted_csv
from mc_han.models import ExtractedText
from mc_han.settings import UserSettings, load_settings, masked_api_key, save_settings
from mc_han.translator.base import TranslationSegment, Translator
from mc_han.translator.cache import TranslationCache
from mc_han.translator.engine import TranslationProgress, translate_csv
from mc_han.translator.sqlite_cache import SQLiteTranslationCache


class CountingTranslator(Translator):
    provider_name = "deepseek"
    model = "deepseek-v4"

    def __init__(self) -> None:
        self.segment_count = 0

    def translate_batch(self, segments: list[TranslationSegment]) -> list[str]:
        self.segment_count += len(segments)
        return [f"译文：{segment.text.strip()}" for segment in segments]


def test_cache_hits_same_provider_different_model_and_normalized_text(tmp_path: Path):
    cache_path = tmp_path / "translation_cache.jsonl"
    cache = TranslationCache(cache_path)
    cache.set(
        provider="deepseek",
        model="deepseek-v3",
        original="Hello world\n",
        translation="你好，世界",
    )

    assert (
        cache.get(
            provider="deepseek",
            model="deepseek-v4",
            original="Hello world   ",
        )
        == "你好，世界"
    )


def test_translate_csv_reuses_duplicate_text_within_one_run(tmp_path: Path):
    csv_path = tmp_path / "extracted_texts.csv"
    output_csv = tmp_path / "translated.csv"
    write_extracted_csv(
        [
            ExtractedText(id="1", source_type="jar_lang", container="a.jar", file_path="a", key_path="a", original="Repeat me"),
            ExtractedText(id="2", source_type="jar_lang", container="b.jar", file_path="b", key_path="b", original="Repeat me "),
            ExtractedText(id="3", source_type="jar_lang", container="c.jar", file_path="c", key_path="c", original="Unique"),
        ],
        csv_path,
    )
    translator = CountingTranslator()
    progress: list[TranslationProgress] = []

    records, translated_count, cache_hits = translate_csv(
        input_csv=csv_path,
        output_csv=output_csv,
        translator=translator,
        cache_path=tmp_path / "cache.jsonl",
        progress_callback=progress.append,
    )

    assert translator.segment_count == 2
    assert translated_count == 3
    assert cache_hits == 1
    assert len(records) == 3
    assert all(record.translation for record in read_extracted_csv(output_csv))
    assert progress[-1].completed_rows == 3


def test_translate_csv_uses_sqlite_cache_for_resume(tmp_path: Path):
    csv_path = tmp_path / "extracted_texts.csv"
    first_output = tmp_path / "first.csv"
    second_output = tmp_path / "second.csv"
    sqlite_path = tmp_path / ".mc-han" / "translations.sqlite"
    records = [
        ExtractedText(
            id="1",
            source_type="jar_lang",
            container="mods/demo.jar",
            file_path="assets/demo/lang/en_us.json",
            key_path="message.demo",
            original="Resume me",
        )
    ]
    write_extracted_csv(records, csv_path)

    first_translator = CountingTranslator()
    translate_csv(
        input_csv=csv_path,
        output_csv=first_output,
        translator=first_translator,
        cache_path=tmp_path / "cache.jsonl",
        sqlite_cache_path=sqlite_path,
    )
    write_extracted_csv(records, csv_path)
    second_translator = CountingTranslator()
    resumed, translated_count, cache_hits = translate_csv(
        input_csv=csv_path,
        output_csv=second_output,
        translator=second_translator,
        cache_path=tmp_path / "empty-cache.jsonl",
        sqlite_cache_path=sqlite_path,
    )

    assert sqlite_path.exists()
    assert first_translator.segment_count == 1
    assert second_translator.segment_count == 0
    assert translated_count == 0
    assert cache_hits == 1
    assert resumed[0].translation == "译文：Resume me"


def test_sqlite_cache_key_includes_context_hash(tmp_path: Path):
    sqlite_cache = SQLiteTranslationCache(tmp_path / "translations.sqlite")
    first = ExtractedText(
        id="1",
        source_type="jar_lang",
        container="mods/demo.jar",
        file_path="assets/demo/lang/en_us.json",
        key_path="message.one",
        original="Same text",
    )
    second = ExtractedText(
        id="2",
        source_type="jar_lang",
        container="mods/demo.jar",
        file_path="assets/demo/lang/en_us.json",
        key_path="message.two",
        original="Same text",
    )

    sqlite_cache.set(first, translation="第一处译文", provider="mock", model="mock")
    try:
        assert sqlite_cache.get(first) == "第一处译文"
        assert sqlite_cache.get(second) is None
    finally:
        sqlite_cache.close()


def test_settings_save_load_omits_plaintext_api_key(tmp_path: Path):
    config_path = tmp_path / "config.json"

    saved_path = save_settings(
        UserSettings(
            provider="deepseek",
            model="deepseek-chat",
            api_key="sk-test-123456",
            base_url="https://api.deepseek.com",
            limit=20,
        ),
        path=config_path,
    )
    loaded = load_settings(path=config_path)

    assert saved_path == config_path
    assert loaded.provider == "deepseek"
    assert loaded.model == "deepseek-chat"
    assert loaded.api_key is None
    assert "sk-test-123456" not in config_path.read_text(encoding="utf-8")
    assert masked_api_key("sk-test-123456") == "sk-t...3456"


def test_resolve_translation_settings_uses_saved_config(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "config.json"
    save_settings(UserSettings(provider="deepseek", model="deepseek-chat", api_key="saved-key", limit=20), path=config_path)
    monkeypatch.setenv("MC_HAN_CONFIG", str(config_path))

    class Args:
        use_config = True
        provider = None
        model = None
        api_key = None
        api_key_env = None
        base_url = None
        limit = None

    resolved = resolve_translation_settings(Args())

    assert resolved.provider == "deepseek"
    assert resolved.model == "deepseek-chat"
    assert resolved.api_key is None
    assert resolved.limit == 20


def test_create_custom_translator_requires_base_url_and_accepts_api_key():
    assert create_translator("custom", "model", "key", None, None) is None

    translator = create_translator("custom", "model", "key", None, "https://example.test/v1")

    assert translator is not None
    assert translator.provider_name == "custom:https://example.test/v1"

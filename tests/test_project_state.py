from __future__ import annotations

import json

from mc_han.core.project import ensure_project_dirs, project_paths, read_extracted_jsonl, write_extracted_jsonl
from mc_han.models import ExtractedText


def test_project_paths_use_mc_han_state_directory(tmp_path):
    modpack = tmp_path / "pack"
    paths = project_paths(modpack)
    ensure_project_dirs(paths)

    assert paths.state_dir == modpack.resolve() / ".mc-han"
    assert paths.extracted_jsonl.name == "extracted_texts.jsonl"
    assert paths.translations_sqlite.name == "translations.sqlite"
    assert paths.usage_sqlite.name == "usage.sqlite3"
    assert paths.output_dir == paths.state_dir / "output"
    assert paths.state_dir.exists()
    assert paths.logs_dir.exists()


def test_extracted_jsonl_round_trip_has_gui_mvp_fields(tmp_path):
    path = tmp_path / ".mc-han" / "extracted_texts.jsonl"
    records = [
        ExtractedText(
            id="abc",
            source_type="jar_ae2guide",
            container="mods/demo.jar",
            file_path="assets/ae2/ae2guide/index.md",
            key_path="p[0]",
            original="Use the ME Terminal.",
            translation="使用 ME 终端。",
        )
    ]

    write_extracted_jsonl(records, path)
    raw = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    loaded = read_extracted_jsonl(path)

    assert set(raw) == {
        "text_id",
        "source_type",
        "container",
        "file_path",
        "key_path",
        "original",
        "translation",
        "status",
        "note",
    }
    assert raw["status"] == "translated"
    assert loaded == records

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from mc_han.models import ExtractedText


@dataclass(frozen=True)
class ProjectPaths:
    modpack_dir: Path
    state_dir: Path
    extracted_csv: Path
    extracted_jsonl: Path
    translations_sqlite: Path
    provenance_sqlite: Path
    usage_sqlite: Path
    translation_cache_jsonl: Path
    translation_rules_json: Path
    output_dir: Path
    logs_dir: Path
    scan_report: Path
    quality_report_txt: Path
    quality_report_json: Path
    scan_options_json: Path


def project_paths(modpack_dir: Path) -> ProjectPaths:
    modpack_dir = Path(modpack_dir).expanduser().resolve()
    state_dir = modpack_dir / ".mc-han"
    output_dir = state_dir / "output"
    logs_dir = state_dir / "logs"
    return ProjectPaths(
        modpack_dir=modpack_dir,
        state_dir=state_dir,
        extracted_csv=state_dir / "extracted_texts.csv",
        extracted_jsonl=state_dir / "extracted_texts.jsonl",
        translations_sqlite=state_dir / "translations.sqlite",
        provenance_sqlite=state_dir / "provenance.sqlite3",
        usage_sqlite=state_dir / "usage.sqlite3",
        translation_cache_jsonl=state_dir / "translation_cache.jsonl",
        translation_rules_json=state_dir / "translation_rules.json",
        output_dir=output_dir,
        logs_dir=logs_dir,
        scan_report=logs_dir / "scan_report.txt",
        quality_report_txt=state_dir / "汉化检查报告.txt",
        quality_report_json=state_dir / "quality_report.json",
        scan_options_json=state_dir / "scan_options.json",
    )


def ensure_project_dirs(paths: ProjectPaths) -> None:
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)


def write_extracted_jsonl(records: list[ExtractedText], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for record in records:
            file.write(json.dumps(to_jsonl_record(record), ensure_ascii=False) + "\n")


def read_extracted_jsonl(path: Path) -> list[ExtractedText]:
    records: list[ExtractedText] = []
    path = Path(path)
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            records.append(
                ExtractedText(
                    id=str(raw.get("text_id", raw.get("id", ""))),
                    source_type=str(raw.get("source_type", "")),
                    container=str(raw.get("container", "")),
                    file_path=str(raw.get("file_path", "")),
                    key_path=str(raw.get("key_path", "")),
                    original=str(raw.get("original", "")),
                    translation=str(raw.get("translation", "")),
                    note=str(raw.get("note", "")),
                )
            )
    return records


def to_jsonl_record(record: ExtractedText) -> dict[str, str]:
    return {
        "text_id": record.id,
        "source_type": record.source_type,
        "container": record.container,
        "file_path": record.file_path,
        "key_path": record.key_path,
        "original": record.original,
        "translation": record.translation,
        "status": record_status(record),
        "note": record.note,
    }


def record_status(record: ExtractedText) -> str:
    if record.translation.strip():
        return "translated"
    if record.note.lower().startswith("failed"):
        return "failed"
    return "pending"


def write_scan_options(path: Path, *, translate_names: bool) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"translate_names": translate_names}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_scan_options(path: Path) -> dict[str, object]:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}

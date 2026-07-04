from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .csv_store import read_extracted_csv
from .quality.checks import check_csv


@dataclass(frozen=True)
class CsvStatus:
    total_rows: int
    translated_rows: int
    missing_rows: int
    errors: int
    warnings: int
    checkpoint_count: int


@dataclass(frozen=True)
class CheckpointInfo:
    path: Path
    created_at: str
    label: str
    source_csv: str


def csv_status(csv_path: Path) -> CsvStatus:
    records = read_extracted_csv(csv_path)
    issues = check_csv(csv_path)
    translated_rows = sum(1 for record in records if record.translation.strip())
    return CsvStatus(
        total_rows=len(records),
        translated_rows=translated_rows,
        missing_rows=len(records) - translated_rows,
        errors=sum(1 for issue in issues if issue.severity == "error"),
        warnings=sum(1 for issue in issues if issue.severity == "warning"),
        checkpoint_count=len(list_checkpoints(csv_path)),
    )


def create_checkpoint(csv_path: Path, *, label: str = "before-translate") -> CheckpointInfo:
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)
    created_at = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_label = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in label).strip("-")
    checkpoint_dir = default_checkpoint_dir(csv_path)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoint_dir / f"{created_at}-{safe_label or 'checkpoint'}.csv"
    shutil.copy2(csv_path, checkpoint_path)
    info = CheckpointInfo(
        path=checkpoint_path,
        created_at=created_at,
        label=safe_label or "checkpoint",
        source_csv=str(csv_path),
    )
    write_checkpoint_metadata(info)
    return info


def list_checkpoints(csv_path: Path) -> list[CheckpointInfo]:
    checkpoint_dir = default_checkpoint_dir(csv_path)
    if not checkpoint_dir.exists():
        return []
    checkpoints: list[CheckpointInfo] = []
    for path in sorted(checkpoint_dir.glob("*.csv")):
        metadata = read_checkpoint_metadata(path)
        if metadata is not None:
            checkpoints.append(metadata)
        else:
            checkpoints.append(
                CheckpointInfo(
                    path=path,
                    created_at=path.stem.split("-", 2)[0],
                    label="checkpoint",
                    source_csv=str(csv_path),
                )
            )
    return checkpoints


def rollback_checkpoint(csv_path: Path, *, checkpoint: str = "latest") -> CheckpointInfo:
    checkpoints = list_checkpoints(csv_path)
    if not checkpoints:
        raise FileNotFoundError(f"No checkpoints found for {csv_path}")
    selected = checkpoints[-1] if checkpoint == "latest" else find_checkpoint(checkpoints, checkpoint)
    Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(selected.path, csv_path)
    return selected


def find_checkpoint(checkpoints: list[CheckpointInfo], checkpoint: str) -> CheckpointInfo:
    for info in checkpoints:
        if info.path.name == checkpoint or info.path.stem == checkpoint:
            return info
    raise FileNotFoundError(f"Checkpoint not found: {checkpoint}")


def default_checkpoint_dir(csv_path: Path) -> Path:
    return Path(csv_path).parent / ".mc-han-checkpoints"


def metadata_path(checkpoint_path: Path) -> Path:
    return checkpoint_path.with_suffix(".json")


def write_checkpoint_metadata(info: CheckpointInfo) -> None:
    metadata_path(info.path).write_text(
        json.dumps(
            {
                "path": str(info.path),
                "created_at": info.created_at,
                "label": info.label,
                "source_csv": info.source_csv,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def read_checkpoint_metadata(checkpoint_path: Path) -> CheckpointInfo | None:
    path = metadata_path(checkpoint_path)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return CheckpointInfo(
            path=checkpoint_path,
            created_at=str(raw.get("created_at", "")),
            label=str(raw.get("label", "")),
            source_csv=str(raw.get("source_csv", "")),
        )
    except (json.JSONDecodeError, OSError):
        return None

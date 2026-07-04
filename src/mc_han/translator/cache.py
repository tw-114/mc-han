from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import RLock


@dataclass(frozen=True)
class CacheEntry:
    key: str
    provider: str
    model: str
    original: str
    translation: str
    created_at: str


class TranslationCache:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = RLock()
        self._provider_model_index: dict[tuple[str, str, str], CacheEntry] = {}
        self._provider_index: dict[tuple[str, str], CacheEntry] = {}
        self._entries = self._load()

    def get(self, *, provider: str, model: str, original: str, allow_provider_fallback: bool = True) -> str | None:
        with self._lock:
            entry = self._entries.get(make_cache_key(provider=provider, model=model, original=original))
            if entry is None:
                entry = self._provider_model_index.get(
                    (canonical_provider(provider), canonical_model(model), normalize_original(original))
                )
            if entry is None and allow_provider_fallback:
                entry = self._provider_index.get((canonical_provider(provider), normalize_original(original)))
        return entry.translation if entry else None

    def set(self, *, provider: str, model: str, original: str, translation: str) -> None:
        key = make_cache_key(provider=provider, model=model, original=original)
        with self._lock:
            if key in self._entries and self._entries[key].translation == translation:
                return
            entry = CacheEntry(
                key=key,
                provider=provider,
                model=model,
                original=original,
                translation=translation,
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(entry.__dict__, ensure_ascii=False) + "\n")
            self._entries[key] = entry
            self._index_entry(entry)

    def _load(self) -> dict[str, CacheEntry]:
        if not self.path.exists():
            return {}
        entries: dict[str, CacheEntry] = {}
        with self.path.open(encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    entry = CacheEntry(
                        key=raw["key"],
                        provider=raw["provider"],
                        model=raw["model"],
                        original=raw["original"],
                        translation=raw["translation"],
                        created_at=raw.get("created_at", ""),
                    )
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
                entries[entry.key] = entry
                self._index_entry(entry)
        return entries

    def _index_entry(self, entry: CacheEntry) -> None:
        provider = canonical_provider(entry.provider)
        model = canonical_model(entry.model)
        original = normalize_original(entry.original)
        self._provider_model_index[(provider, model, original)] = entry
        self._provider_index[(provider, original)] = entry


def make_cache_key(*, provider: str, model: str, original: str) -> str:
    stable = "\0".join((canonical_provider(provider), canonical_model(model), normalize_original(original)))
    return sha256(stable.encode("utf-8")).hexdigest()


def make_reuse_key(*, provider: str, model: str, original: str) -> str:
    return "\0".join((canonical_provider(provider), canonical_model(model), normalize_original(original)))


def normalize_original(original: str) -> str:
    normalized = original.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in normalized.split("\n")).strip()


def canonical_provider(provider: str) -> str:
    return provider.strip().lower()


def canonical_model(model: str) -> str:
    return model.strip().lower()

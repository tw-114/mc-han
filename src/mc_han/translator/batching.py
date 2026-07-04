from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Iterable

from mc_han.models import ExtractedText


@dataclass(frozen=True)
class SpeedModeConfig:
    name: str
    min_items: int
    max_items: int
    max_input_tokens: int
    max_output_tokens: int
    safety_margin: float
    long_text_tokens: int
    retry_split_on_failure: bool

    @property
    def safe_input_tokens(self) -> int:
        return max(1, int(self.max_input_tokens * (1 - self.safety_margin)))

    @property
    def safe_output_tokens(self) -> int:
        return max(1, int(self.max_output_tokens * (1 - self.safety_margin)))


@dataclass(frozen=True)
class PendingGroup:
    reuse_key: str
    rows: tuple[tuple[int, ExtractedText], ...]
    input_tokens: int
    output_tokens: int

    @property
    def representative(self) -> ExtractedText:
        return self.rows[0][1]

    @property
    def row_count(self) -> int:
        return len(self.rows)


SPEED_MODE_CONFIGS = {
    "safe": SpeedModeConfig(
        name="safe",
        min_items=10,
        max_items=20,
        max_input_tokens=2400,
        max_output_tokens=3200,
        safety_margin=0.25,
        long_text_tokens=900,
        retry_split_on_failure=True,
    ),
    "balanced": SpeedModeConfig(
        name="balanced",
        min_items=30,
        max_items=60,
        max_input_tokens=6200,
        max_output_tokens=8200,
        safety_margin=0.22,
        long_text_tokens=1800,
        retry_split_on_failure=True,
    ),
    "fast": SpeedModeConfig(
        name="fast",
        min_items=80,
        max_items=150,
        max_input_tokens=12000,
        max_output_tokens=16000,
        safety_margin=0.25,
        long_text_tokens=2600,
        retry_split_on_failure=True,
    ),
}


WORD_RE = re.compile(r"[A-Za-z0-9_]+(?:['.-][A-Za-z0-9_]+)*")
CJK_RE = re.compile(r"[\u3400-\u9fff]")
TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s+[^<>]*?)?>|\$\([^)]+\)|[%$][A-Za-z0-9_{}:.%-]+")


def resolve_speed_mode(
    mode: str | None,
    *,
    max_items: int | None = None,
    max_input_tokens: int | None = None,
    max_output_tokens: int | None = None,
) -> SpeedModeConfig:
    key = (mode or "balanced").strip().lower()
    if key not in SPEED_MODE_CONFIGS:
        key = "balanced"
    config = SPEED_MODE_CONFIGS[key]
    if max_items is None and max_input_tokens is None and max_output_tokens is None:
        return config
    return replace(
        config,
        max_items=max_items if max_items and max_items > 0 else config.max_items,
        max_input_tokens=max_input_tokens if max_input_tokens and max_input_tokens > 0 else config.max_input_tokens,
        max_output_tokens=max_output_tokens if max_output_tokens and max_output_tokens > 0 else config.max_output_tokens,
    )


def make_pending_group(reuse_key: str, rows: Iterable[tuple[int, ExtractedText]]) -> PendingGroup:
    row_tuple = tuple(rows)
    text = row_tuple[0][1].original if row_tuple else ""
    input_tokens = estimate_input_tokens(text)
    return PendingGroup(
        reuse_key=reuse_key,
        rows=row_tuple,
        input_tokens=input_tokens,
        output_tokens=estimate_output_tokens(text, input_tokens=input_tokens),
    )


def build_token_batches(groups: list[PendingGroup], config: SpeedModeConfig) -> list[list[PendingGroup]]:
    batches: list[list[PendingGroup]] = []
    current: list[PendingGroup] = []
    input_total = 0
    output_total = 0

    for group in groups:
        if is_long_group(group, config):
            if current:
                batches.append(current)
                current = []
                input_total = 0
                output_total = 0
            batches.append([group])
            continue

        would_exceed_tokens = (
            input_total + group.input_tokens > config.safe_input_tokens
            or output_total + group.output_tokens > config.safe_output_tokens
        )
        would_exceed_items = len(current) >= config.max_items
        if current and (would_exceed_tokens or would_exceed_items):
            batches.append(current)
            current = []
            input_total = 0
            output_total = 0

        current.append(group)
        input_total += group.input_tokens
        output_total += group.output_tokens

    if current:
        batches.append(current)
    return batches


def is_long_group(group: PendingGroup, config: SpeedModeConfig) -> bool:
    return (
        group.input_tokens >= config.long_text_tokens
        or group.input_tokens >= config.safe_input_tokens // 2
        or group.output_tokens >= config.safe_output_tokens // 2
    )


def estimate_input_tokens(text: str) -> int:
    if not text:
        return 1
    words = WORD_RE.findall(text)
    cjk_chars = CJK_RE.findall(text)
    tags = TAG_RE.findall(text)
    punctuation = sum(1 for char in text if not char.isalnum() and not char.isspace())
    line_cost = max(0, len(text.splitlines()) - 1) * 2
    residual_chars = max(0, len(text) - sum(len(word) for word in words) - len(cjk_chars))
    estimate = (
        len(words) * 1.35
        + len(cjk_chars) * 1.0
        + len(tags) * 1.5
        + punctuation * 0.35
        + residual_chars / 5
        + line_cost
        + 8
    )
    return max(1, math.ceil(estimate))


def estimate_output_tokens(text: str, *, input_tokens: int | None = None) -> int:
    source_tokens = input_tokens if input_tokens is not None else estimate_input_tokens(text)
    return max(8, math.ceil(source_tokens * 1.45 + 12))

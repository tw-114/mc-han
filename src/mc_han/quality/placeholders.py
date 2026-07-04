from __future__ import annotations

import re
from collections import Counter

PLACEHOLDER_RE = re.compile(
    r"§[0-9A-FK-ORa-fk-or]"
    r"|&[0-9A-FK-ORa-fk-or]"
    r"|%(?:\d+\$)?[sdif]"
    r"|\{\d+\}"
    r"|\$\{[A-Za-z_][A-Za-z0-9_.-]*\}"
    r"|\$\([^)]+\)"
    r"|</?[A-Za-z][A-Za-z0-9]*(?:\s+[^<>]*?)?>",
)


def extract_placeholders(text: str) -> tuple[str, ...]:
    return tuple(match.group(0) for match in PLACEHOLDER_RE.finditer(text))


def placeholder_counts(text: str) -> Counter[str]:
    return Counter(extract_placeholders(text))


def placeholders_match(source: str, translated: str) -> bool:
    return placeholder_counts(source) == placeholder_counts(translated)

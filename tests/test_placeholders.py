from __future__ import annotations

from mc_han.quality.placeholders import extract_placeholders, placeholders_match


def test_extract_placeholders_covers_minecraft_and_mod_syntax():
    text = "Press §a%s &b{0} ${name} $(item) <ItemLink id=\"ae2:controller\">"

    placeholders = extract_placeholders(text)

    assert "§a" in placeholders
    assert "&b" in placeholders
    assert "%s" in placeholders
    assert "{0}" in placeholders
    assert "${name}" in placeholders
    assert "$(item)" in placeholders
    assert '<ItemLink id="ae2:controller">' in placeholders


def test_placeholders_match_uses_counts():
    source = "Use %s and %s with {0}."
    translated = "使用 %s 和 %s，并保留 {0}。"
    broken = "使用 %s，并保留 {0}。"

    assert placeholders_match(source, translated)
    assert not placeholders_match(source, broken)

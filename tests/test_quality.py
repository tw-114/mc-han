from __future__ import annotations

from mc_han.quality.garbled import has_garbled_text
from mc_han.quality.markdown import fenced_code_blocks_closed


def test_garbled_marker_detection():
    assert has_garbled_text("锟斤拷")
    assert has_garbled_text("FranÃ§ais")
    assert not has_garbled_text("正常的中文文本")


def test_fenced_code_blocks_closed():
    assert fenced_code_blocks_closed("Text\n```json\n{}\n```\nMore text")
    assert not fenced_code_blocks_closed("Text\n```json\n{}")

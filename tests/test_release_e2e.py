from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from mc_han.qt.release_smoke import run_packaged_e2e_smoke_test


def test_release_e2e_smoke_uses_fake_provider_and_real_workflow():
    assert run_packaged_e2e_smoke_test() == 0

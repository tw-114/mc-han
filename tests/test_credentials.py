from __future__ import annotations

import os
from pathlib import Path

import pytest

from mc_han.services.credentials import (
    CredentialStore,
    MemoryCredentialStore,
    WindowsDpapiCipher,
)
from mc_han.settings import UserSettings, load_settings, save_settings


class FakeCipher:
    def protect(self, value: bytes) -> bytes:
        return bytes(byte ^ 0xA5 for byte in value)

    def unprotect(self, value: bytes) -> bytes:
        return bytes(byte ^ 0xA5 for byte in value)


class FailingCipher:
    def protect(self, _value: bytes) -> bytes:
        raise OSError("simulated secure storage failure")

    def unprotect(self, _value: bytes) -> bytes:
        raise OSError("simulated secure storage failure")


def test_encrypted_store_round_trip_contains_no_plaintext(tmp_path: Path):
    path = tmp_path / "credentials.json"
    secret = "sk-private-value"
    store = CredentialStore(path, cipher=FakeCipher())

    result = store.save("deepseek", "https://api.deepseek.com", secret)
    reopened = CredentialStore(path, cipher=FakeCipher())

    assert result.persisted
    assert reopened.load("deepseek", "https://api.deepseek.com") == secret
    assert secret not in path.read_text(encoding="utf-8")
    assert reopened.contains_persisted(
        "deepseek",
        "https://api.deepseek.com",
    )
    assert len(reopened.descriptors()) == 1


def test_secure_storage_failure_falls_back_to_memory(tmp_path: Path):
    store = CredentialStore(
        tmp_path / "credentials.json",
        cipher=FailingCipher(),
    )

    result = store.save(
        "openai",
        "https://api.openai.com/v1",
        "session-secret",
    )

    assert not result.persisted
    assert "本次会话" in result.message
    assert store.load("openai", "https://api.openai.com/v1") == (
        "session-secret"
    )
    assert not store.path.exists()


def test_memory_store_delete_removes_session_secret():
    store = MemoryCredentialStore()
    store.save("custom", "https://example.test/v1", "temporary")

    assert store.has_session_credentials
    assert store.delete_all()
    assert not store.has_session_credentials
    assert store.load("custom", "https://example.test/v1") is None


def test_legacy_plaintext_key_is_ignored_and_removed_on_save(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(
        '{"provider":"deepseek","api_key":"legacy-private"}\n',
        encoding="utf-8",
    )

    loaded = load_settings(path)
    save_settings(
        UserSettings(provider=loaded.provider, api_key="still-private"),
        path,
    )

    assert loaded.api_key is None
    assert "private" not in path.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_windows_dpapi_round_trip():
    cipher = WindowsDpapiCipher()
    secret = b"mc-han-dpapi-test"

    encrypted = cipher.protect(secret)

    assert encrypted != secret
    assert cipher.unprotect(encrypted) == secret

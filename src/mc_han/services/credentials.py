from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mc_han.settings import config_path
from mc_han.utils.atomic_json import write_json_atomic


CREDENTIALS_SCHEMA_VERSION = 1
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class SecretCipher(Protocol):
    def protect(self, value: bytes) -> bytes: ...

    def unprotect(self, value: bytes) -> bytes: ...


class _DataBlob(ctypes.Structure):
    _fields_ = (
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    )


class WindowsDpapiCipher:
    """Encrypt secrets for the current Windows user via DPAPI."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows DPAPI is unavailable")
        self._crypt32 = ctypes.windll.crypt32
        self._kernel32 = ctypes.windll.kernel32

    def protect(self, value: bytes) -> bytes:
        return self._transform(value, protect=True)

    def unprotect(self, value: bytes) -> bytes:
        return self._transform(value, protect=False)

    def _transform(self, value: bytes, *, protect: bool) -> bytes:
        if not isinstance(value, bytes):
            raise TypeError("DPAPI input must be bytes")
        buffer = ctypes.create_string_buffer(value)
        input_blob = _DataBlob(
            len(value),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
        )
        output_blob = _DataBlob()
        if protect:
            succeeded = self._crypt32.CryptProtectData(
                ctypes.byref(input_blob),
                "mc-han credential",
                None,
                None,
                None,
                CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(output_blob),
            )
        else:
            succeeded = self._crypt32.CryptUnprotectData(
                ctypes.byref(input_blob),
                None,
                None,
                None,
                None,
                CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(output_blob),
            )
        if not succeeded:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(
                output_blob.pbData,
                output_blob.cbData,
            )
        finally:
            self._kernel32.LocalFree(output_blob.pbData)


@dataclass(frozen=True)
class CredentialDescriptor:
    provider: str
    base_url: str


@dataclass(frozen=True)
class CredentialSaveResult:
    persisted: bool
    message: str


class CredentialStore:
    def __init__(
        self,
        path: Path | None = None,
        *,
        cipher: SecretCipher | None = None,
        enable_platform_cipher: bool = True,
    ) -> None:
        self.path = Path(path) if path is not None else credentials_path()
        self._memory: dict[str, str] = {}
        self.last_warning = ""
        if cipher is not None:
            self._cipher = cipher
        elif enable_platform_cipher:
            try:
                self._cipher = WindowsDpapiCipher()
            except OSError:
                self._cipher = None
        else:
            self._cipher = None

    @property
    def persistent_available(self) -> bool:
        return self._cipher is not None

    @property
    def has_session_credentials(self) -> bool:
        return bool(self._memory)

    def save(
        self,
        provider: str,
        base_url: str,
        api_key: str,
    ) -> CredentialSaveResult:
        provider, base_url, api_key = _validate_credential(
            provider,
            base_url,
            api_key,
        )
        identity = _credential_identity(provider, base_url)
        self._memory[identity] = api_key
        if self._cipher is None:
            self.last_warning = (
                "当前系统无法使用安全凭据存储，API Key 仅保留在本次会话。"
            )
            return CredentialSaveResult(False, self.last_warning)
        try:
            encrypted = self._cipher.protect(api_key.encode("utf-8"))
            payload = self._read_payload()
            credentials = payload.setdefault("credentials", {})
            if not isinstance(credentials, dict):
                credentials = {}
                payload["credentials"] = credentials
            credentials[identity] = {
                "provider": provider,
                "base_url": base_url,
                "encrypted": base64.b64encode(encrypted).decode("ascii"),
            }
            write_json_atomic(self.path, payload)
        except (OSError, ValueError, TypeError, UnicodeError):
            self.last_warning = (
                "API Key 安全保存失败，已退回仅保留在本次会话。"
            )
            return CredentialSaveResult(False, self.last_warning)
        self.last_warning = ""
        return CredentialSaveResult(True, "API Key 已使用 Windows DPAPI 安全保存。")

    def load(self, provider: str, base_url: str) -> str | None:
        provider = _required_text(provider, "provider")
        base_url = _required_text(base_url, "base_url")
        identity = _credential_identity(provider, base_url)
        if identity in self._memory:
            return self._memory[identity]
        if self._cipher is None:
            return None
        entry = self._credential_entries().get(identity)
        if not isinstance(entry, dict):
            return None
        encrypted = entry.get("encrypted")
        if not isinstance(encrypted, str):
            return None
        try:
            protected = base64.b64decode(encrypted, validate=True)
            api_key = self._cipher.unprotect(protected).decode("utf-8")
        except (OSError, ValueError, TypeError, UnicodeError):
            self.last_warning = (
                "已保存的 API Key 无法解密，请删除后重新输入。"
            )
            return None
        self._memory[identity] = api_key
        self.last_warning = ""
        return api_key

    def descriptors(self) -> tuple[CredentialDescriptor, ...]:
        descriptors: list[CredentialDescriptor] = []
        for entry in self._credential_entries().values():
            if not isinstance(entry, dict):
                continue
            provider = entry.get("provider")
            base_url = entry.get("base_url")
            if isinstance(provider, str) and isinstance(base_url, str):
                descriptors.append(CredentialDescriptor(provider, base_url))
        return tuple(
            sorted(
                set(descriptors),
                key=lambda item: (item.provider.casefold(), item.base_url),
            )
        )

    def contains_persisted(self, provider: str, base_url: str) -> bool:
        identity = _credential_identity(
            _required_text(provider, "provider"),
            _required_text(base_url, "base_url"),
        )
        return identity in self._credential_entries()

    def delete_all(self) -> bool:
        had_credentials = bool(self._memory) or bool(self._credential_entries())
        self._memory.clear()
        if self.path.exists():
            try:
                self.path.unlink()
            except OSError:
                self.last_warning = "无法删除安全凭据，请稍后重试。"
                return False
        self.last_warning = ""
        return had_credentials

    def _credential_entries(self) -> dict[str, object]:
        payload = self._read_payload()
        credentials = payload.get("credentials")
        return credentials if isinstance(credentials, dict) else {}

    def _read_payload(self) -> dict[str, object]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = {}
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        return {
            "schema_version": CREDENTIALS_SCHEMA_VERSION,
            "credentials": (
                raw.get("credentials")
                if isinstance(raw.get("credentials"), dict)
                else {}
            ),
        }


class MemoryCredentialStore(CredentialStore):
    def __init__(self) -> None:
        super().__init__(
            path=Path("__mc_han_memory_credentials__"),
            enable_platform_cipher=False,
        )

    def descriptors(self) -> tuple[CredentialDescriptor, ...]:
        return ()

    def contains_persisted(self, provider: str, base_url: str) -> bool:
        _required_text(provider, "provider")
        _required_text(base_url, "base_url")
        return False

    def delete_all(self) -> bool:
        had_credentials = bool(self._memory)
        self._memory.clear()
        self.last_warning = ""
        return had_credentials


def credentials_path() -> Path:
    return config_path().parent / "credentials.json"


def _validate_credential(
    provider: str,
    base_url: str,
    api_key: str,
) -> tuple[str, str, str]:
    return (
        _required_text(provider, "provider"),
        _required_text(base_url, "base_url"),
        _required_text(api_key, "api_key"),
    )


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be empty")
    if "\x00" in stripped or any(
        character in stripped for character in ("\r", "\n", "\t")
    ):
        raise ValueError(f"{field_name} contains control characters")
    return stripped


def _credential_identity(provider: str, base_url: str) -> str:
    material = f"{provider.casefold()}\0{base_url.rstrip('/').casefold()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()

from __future__ import annotations

import lzma
import math
import zipfile
import zlib
from dataclasses import dataclass
from typing import Callable, Generic, Iterator, TypeVar


@dataclass(frozen=True)
class ZipSafetyLimits:
    max_entries: int = 100_000
    max_entry_uncompressed: int = 16 * 1024 * 1024
    max_candidate_uncompressed_total: int = 256 * 1024 * 1024
    max_actual_read_total: int = 256 * 1024 * 1024
    max_compression_ratio: float = 200.0
    chunk_size: int = 64 * 1024

    def __post_init__(self) -> None:
        integer_fields = (
            "max_entries",
            "max_entry_uncompressed",
            "max_candidate_uncompressed_total",
            "max_actual_read_total",
            "chunk_size",
        )
        for field_name in integer_fields:
            value = getattr(self, field_name)
            if type(value) is not int:
                raise TypeError(f"{field_name} must be an integer")
            if value <= 0:
                raise ValueError(f"{field_name} must be greater than zero")

        ratio = self.max_compression_ratio
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
            raise TypeError("max_compression_ratio must be an int or float")
        if isinstance(ratio, float) and not math.isfinite(ratio):
            raise ValueError("max_compression_ratio must be finite")
        if ratio <= 0:
            raise ValueError("max_compression_ratio must be greater than zero")


DEFAULT_ZIP_LIMITS = ZipSafetyLimits()

ENTRY_COUNT_LIMIT = "entry_count_limit"
ENTRY_SIZE_LIMIT = "entry_size_limit"
JAR_TOTAL_SIZE_LIMIT = "jar_total_size_limit"
ACTUAL_READ_LIMIT = "actual_read_limit"
COMPRESSION_RATIO_LIMIT = "compression_ratio_limit"
ENCRYPTED_ENTRY = "encrypted_entry"
BAD_ZIP = "bad_zip"
READ_ERROR = "read_error"


@dataclass(frozen=True)
class ZipDiagnostic:
    code: str
    entry: str | None
    reason: str
    stops_jar: bool = False


class ZipSafetyError(RuntimeError):
    def __init__(self, diagnostic: ZipDiagnostic):
        super().__init__(diagnostic.reason)
        self.diagnostic = diagnostic

    @property
    def code(self) -> str:
        return self.diagnostic.code


CandidateTag = TypeVar("CandidateTag")


class SafeZipReader(Generic[CandidateTag]):
    def __init__(
        self,
        archive: zipfile.ZipFile,
        *,
        limits: ZipSafetyLimits = DEFAULT_ZIP_LIMITS,
    ) -> None:
        self.archive = archive
        self.limits = limits
        self.diagnostics: list[ZipDiagnostic] = []
        self.candidate_uncompressed = 0
        self.actual_read_bytes = 0
        self.stopped = False

    def prepare_candidates(
        self,
        selector: Callable[[str], CandidateTag | None],
    ) -> list[tuple[zipfile.ZipInfo, CandidateTag]]:
        return list(self.iter_candidates(selector))

    def iter_candidates(
        self,
        selector: Callable[[str], CandidateTag | None],
    ) -> Iterator[tuple[zipfile.ZipInfo, CandidateTag]]:
        infos = self.archive.infolist()
        if len(infos) > self.limits.max_entries:
            self._record(
                ZipDiagnostic(
                    code=ENTRY_COUNT_LIMIT,
                    entry=None,
                    reason=(
                        f"archive has {len(infos)} entries; "
                        f"limit is {self.limits.max_entries}"
                    ),
                    stops_jar=True,
                )
            )
            return

        for info in sorted(infos, key=lambda item: item.filename):
            if self.stopped:
                break
            if info.is_dir():
                continue
            tag = selector(info.filename)
            if tag is None:
                continue
            diagnostic = self._validate_candidate_metadata(info)
            if diagnostic is not None:
                self._record(diagnostic)
                if diagnostic.stops_jar:
                    break
                continue
            proposed_total = self.candidate_uncompressed + info.file_size
            if proposed_total > self.limits.max_candidate_uncompressed_total:
                self._record(
                    ZipDiagnostic(
                        code=JAR_TOTAL_SIZE_LIMIT,
                        entry=info.filename,
                        reason=(
                            f"candidate text total would be {proposed_total} bytes; "
                            f"limit is {self.limits.max_candidate_uncompressed_total}"
                        ),
                        stops_jar=True,
                    )
                )
                break
            self.candidate_uncompressed = proposed_total
            yield info, tag

    def read_entry(self, info: zipfile.ZipInfo) -> bytes:
        if self.stopped:
            diagnostic = self.diagnostics[-1]
            raise ZipSafetyError(diagnostic)
        chunks: list[bytes] = []
        entry_bytes = 0
        try:
            with self.archive.open(info, "r") as stream:
                while True:
                    chunk = stream.read(self.limits.chunk_size)
                    if not chunk:
                        break
                    entry_bytes += len(chunk)
                    self.actual_read_bytes += len(chunk)
                    if entry_bytes > self.limits.max_entry_uncompressed:
                        self._raise(
                            ZipDiagnostic(
                                code=ACTUAL_READ_LIMIT,
                                entry=info.filename,
                                reason=(
                                    f"actual entry data exceeded "
                                    f"{self.limits.max_entry_uncompressed} bytes"
                                ),
                                stops_jar=True,
                            )
                        )
                    if self.actual_read_bytes > self.limits.max_actual_read_total:
                        self._raise(
                            ZipDiagnostic(
                                code=ACTUAL_READ_LIMIT,
                                entry=info.filename,
                                reason=(
                                    f"actual archive reads exceeded "
                                    f"{self.limits.max_actual_read_total} bytes"
                                ),
                                stops_jar=True,
                            )
                        )
                    chunks.append(chunk)
        except ZipSafetyError:
            raise
        except (
            EOFError,
            KeyError,
            lzma.LZMAError,
            NotImplementedError,
            OSError,
            RuntimeError,
            ValueError,
            zipfile.BadZipFile,
            zlib.error,
        ) as error:
            self._raise(
                ZipDiagnostic(
                    code=READ_ERROR,
                    entry=info.filename,
                    reason=f"stream read failed: {type(error).__name__}",
                )
            )
        return b"".join(chunks)

    def _validate_candidate_metadata(self, info: zipfile.ZipInfo) -> ZipDiagnostic | None:
        if info.flag_bits & 0x1:
            return ZipDiagnostic(
                code=ENCRYPTED_ENTRY,
                entry=info.filename,
                reason="encrypted ZIP entries are not supported",
            )
        if info.file_size < 0 or info.compress_size < 0:
            return ZipDiagnostic(
                code=READ_ERROR,
                entry=info.filename,
                reason="ZIP entry reports a negative size",
            )
        if info.file_size > self.limits.max_entry_uncompressed:
            return ZipDiagnostic(
                code=ENTRY_SIZE_LIMIT,
                entry=info.filename,
                reason=(
                    f"entry reports {info.file_size} uncompressed bytes; "
                    f"limit is {self.limits.max_entry_uncompressed}"
                ),
            )
        if info.file_size > 0 and info.compress_size == 0:
            return ZipDiagnostic(
                code=COMPRESSION_RATIO_LIMIT,
                entry=info.filename,
                reason="non-empty entry reports zero compressed bytes",
            )
        ratio = (
            float(info.file_size) / float(info.compress_size)
            if info.compress_size > 0
            else 0.0
        )
        if ratio > self.limits.max_compression_ratio:
            return ZipDiagnostic(
                code=COMPRESSION_RATIO_LIMIT,
                entry=info.filename,
                reason=(
                    f"compression ratio is {ratio:.2f}; "
                    f"limit is {self.limits.max_compression_ratio:.2f}"
                ),
            )
        return None

    def _record(self, diagnostic: ZipDiagnostic) -> None:
        self.diagnostics.append(diagnostic)
        if diagnostic.stops_jar:
            self.stopped = True

    def _raise(self, diagnostic: ZipDiagnostic) -> None:
        self._record(diagnostic)
        raise ZipSafetyError(diagnostic)


def bad_zip_diagnostic(error: BaseException) -> ZipDiagnostic:
    return ZipDiagnostic(
        code=BAD_ZIP,
        entry=None,
        reason=f"cannot open ZIP archive: {type(error).__name__}",
        stops_jar=True,
    )

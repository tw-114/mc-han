from __future__ import annotations

import io
import zipfile

import pytest

import mc_han.builder.resourcepack as resourcepack_module
from mc_han.builder.resourcepack import read_container_file
from mc_han.utils.safe_zip import (
    ACTUAL_READ_LIMIT,
    COMPRESSION_RATIO_LIMIT,
    ENCRYPTED_ENTRY,
    ENTRY_COUNT_LIMIT,
    ENTRY_SIZE_LIMIT,
    JAR_TOTAL_SIZE_LIMIT,
    READ_ERROR,
    SafeZipReader,
    ZipSafetyError,
    ZipSafetyLimits,
)


@pytest.mark.parametrize(
    ("field_name", "value", "error_type"),
    [
        ("max_entries", True, TypeError),
        ("max_entries", 1.5, TypeError),
        ("max_entries", "100", TypeError),
        ("max_entries", 0, ValueError),
        ("max_entries", -1, ValueError),
        ("max_entry_uncompressed", float("nan"), TypeError),
        ("max_compression_ratio", float("nan"), ValueError),
        ("max_compression_ratio", float("inf"), ValueError),
        ("max_compression_ratio", float("-inf"), ValueError),
        ("max_compression_ratio", True, TypeError),
    ],
)
def test_zip_safety_limits_reject_invalid_values(field_name, value, error_type):
    with pytest.raises(error_type, match=field_name):
        ZipSafetyLimits(**{field_name: value})


@pytest.mark.parametrize("ratio", [200, 200.5])
def test_zip_safety_limits_accept_integer_and_float_compression_ratios(ratio):
    assert ZipSafetyLimits(max_compression_ratio=ratio).max_compression_ratio == ratio


def test_safe_zip_reads_normal_small_archive():
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("assets/demo/ae2guide/page.md", "Normal guide text.")
    archive_bytes.seek(0)

    with zipfile.ZipFile(archive_bytes) as archive:
        reader = SafeZipReader(archive)
        candidates = reader.prepare_candidates(markdown_selector)
        assert len(candidates) == 1
        assert reader.read_entry(candidates[0][0]) == b"Normal guide text."
        assert reader.diagnostics == []


def test_safe_zip_accepts_normal_larger_guide():
    data = b"Guide paragraph.\n" * 16_000
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("assets/demo/ae2guide/large.md", data)
    archive_bytes.seek(0)

    with zipfile.ZipFile(archive_bytes) as archive:
        reader = SafeZipReader(
            archive,
            limits=small_limits(
                max_entry_uncompressed=len(data) + 1,
                max_candidate_uncompressed_total=len(data) + 1,
                max_actual_read_total=len(data) + 1,
                max_compression_ratio=500.0,
            ),
        )
        candidates = reader.prepare_candidates(markdown_selector)
        assert reader.read_entry(candidates[0][0]) == data


def test_safe_zip_rejects_single_oversized_candidate_from_metadata():
    info = fake_info("assets/demo/ae2guide/large.md", file_size=101, compress_size=50)
    reader = SafeZipReader(FakeArchive([info]), limits=small_limits(max_entry_uncompressed=100))

    assert reader.prepare_candidates(markdown_selector) == []
    assert [item.code for item in reader.diagnostics] == [ENTRY_SIZE_LIMIT]


def test_safe_zip_stops_when_candidate_metadata_total_exceeds_limit():
    infos = [
        fake_info(f"assets/demo/ae2guide/{index}.md", file_size=60, compress_size=30)
        for index in range(3)
    ]
    reader = SafeZipReader(
        FakeArchive(infos),
        limits=small_limits(max_candidate_uncompressed_total=100),
    )

    candidates = reader.prepare_candidates(markdown_selector)

    assert len(candidates) == 1
    assert reader.stopped
    assert reader.diagnostics[-1].code == JAR_TOTAL_SIZE_LIMIT


def test_safe_zip_rejects_high_compression_ratio_without_large_fixture():
    info = fake_info(
        "assets/demo/ae2guide/compressed.md",
        file_size=1_000_000,
        compress_size=1_000,
    )
    reader = SafeZipReader(
        FakeArchive([info]),
        limits=small_limits(
            max_entry_uncompressed=2_000_000,
            max_candidate_uncompressed_total=2_000_000,
            max_compression_ratio=200.0,
        ),
    )

    assert reader.prepare_candidates(markdown_selector) == []
    assert reader.diagnostics[0].code == COMPRESSION_RATIO_LIMIT


def test_safe_zip_allows_empty_entry_with_zero_compressed_size():
    info = fake_info("assets/demo/ae2guide/empty.md", file_size=0, compress_size=0)
    archive = FakeArchive([info], streams={info.filename: FakeStream(b"")})
    reader = SafeZipReader(archive, limits=small_limits())

    candidates = reader.prepare_candidates(markdown_selector)

    assert len(candidates) == 1
    assert reader.read_entry(candidates[0][0]) == b""
    assert reader.diagnostics == []


def test_safe_zip_allows_compression_ratio_exactly_at_limit():
    info = fake_info("assets/demo/ae2guide/exact.md", file_size=1_000, compress_size=5)
    reader = SafeZipReader(FakeArchive([info]), limits=small_limits(max_compression_ratio=200.0))

    assert len(reader.prepare_candidates(markdown_selector)) == 1
    assert reader.diagnostics == []


def test_safe_zip_rejects_compression_ratio_just_above_limit():
    info = fake_info("assets/demo/ae2guide/above.md", file_size=1_001, compress_size=5)
    reader = SafeZipReader(FakeArchive([info]), limits=small_limits(max_compression_ratio=200.0))

    assert reader.prepare_candidates(markdown_selector) == []
    assert reader.diagnostics[0].code == COMPRESSION_RATIO_LIMIT


@pytest.mark.parametrize(
    ("file_size", "compress_size"),
    [
        (-1, 1),
        (1, -1),
    ],
)
def test_safe_zip_rejects_negative_zipinfo_sizes(file_size, compress_size):
    info = fake_info(
        "assets/demo/ae2guide/negative.md",
        file_size=file_size,
        compress_size=compress_size,
    )
    reader = SafeZipReader(FakeArchive([info]), limits=small_limits())

    assert reader.prepare_candidates(markdown_selector) == []
    assert reader.diagnostics[0].code == READ_ERROR


def test_safe_zip_rejects_nonempty_entry_with_zero_compressed_size():
    info = fake_info("assets/demo/ae2guide/zero.md", file_size=10, compress_size=0)
    reader = SafeZipReader(FakeArchive([info]), limits=small_limits())

    assert reader.prepare_candidates(markdown_selector) == []
    assert reader.diagnostics[0].code == COMPRESSION_RATIO_LIMIT


def test_safe_zip_directory_does_not_trigger_zero_compressed_size_error():
    directory = fake_info("assets/demo/ae2guide/", file_size=10, compress_size=0)
    reader = SafeZipReader(FakeArchive([directory]), limits=small_limits())

    assert reader.prepare_candidates(markdown_selector) == []
    assert reader.diagnostics == []


def test_safe_zip_entry_count_includes_non_candidates_and_directories():
    infos = [
        fake_info("assets/demo/ae2guide/page.md", file_size=10, compress_size=10),
        fake_info("assets/demo/image.png", file_size=10, compress_size=10),
        fake_info("assets/demo/", file_size=0, compress_size=0),
    ]
    reader = SafeZipReader(FakeArchive(infos), limits=small_limits(max_entries=2))

    assert reader.prepare_candidates(markdown_selector) == []
    assert reader.diagnostics[0].code == ENTRY_COUNT_LIMIT


def test_safe_zip_rejects_encrypted_candidate_metadata():
    info = fake_info("assets/demo/ae2guide/secret.md", file_size=10, compress_size=10)
    info.flag_bits |= 0x1
    reader = SafeZipReader(FakeArchive([info]), limits=small_limits())

    assert reader.prepare_candidates(markdown_selector) == []
    assert reader.diagnostics[0].code == ENCRYPTED_ENTRY


def test_safe_zip_actual_stream_limit_catches_forged_small_metadata():
    info = fake_info("assets/demo/ae2guide/forged.md", file_size=4, compress_size=4)
    archive = FakeArchive([info], streams={info.filename: FakeStream(b"x" * 32)})
    reader = SafeZipReader(
        archive,
        limits=small_limits(max_entry_uncompressed=16, chunk_size=8),
    )
    candidate = reader.prepare_candidates(markdown_selector)[0][0]

    with pytest.raises(ZipSafetyError) as raised:
        reader.read_entry(candidate)

    assert raised.value.code == ACTUAL_READ_LIMIT
    assert reader.diagnostics[-1].code == ACTUAL_READ_LIMIT
    assert reader.stopped


def test_safe_zip_actual_archive_read_budget_stops_archive():
    info = fake_info("assets/demo/ae2guide/forged.md", file_size=4, compress_size=4)
    archive = FakeArchive([info], streams={info.filename: FakeStream(b"x" * 24)})
    reader = SafeZipReader(
        archive,
        limits=small_limits(
            max_entry_uncompressed=64,
            max_actual_read_total=12,
            chunk_size=8,
        ),
    )
    candidate = reader.prepare_candidates(markdown_selector)[0][0]

    with pytest.raises(ZipSafetyError) as raised:
        reader.read_entry(candidate)

    assert raised.value.code == ACTUAL_READ_LIMIT
    assert raised.value.diagnostic.stops_jar
    assert reader.stopped


def test_candidate_total_stop_preserves_completed_entry_and_opens_no_later_entries():
    infos = [
        fake_info("assets/demo/ae2guide/a.md", file_size=6, compress_size=6),
        fake_info("assets/demo/ae2guide/b.md", file_size=6, compress_size=6),
        fake_info("assets/demo/ae2guide/c.md", file_size=1, compress_size=1),
    ]
    archive = FakeArchive(
        infos,
        streams={info.filename: FakeStream(info.filename.encode("ascii")) for info in infos},
    )
    reader = SafeZipReader(
        archive,
        limits=small_limits(max_candidate_uncompressed_total=10),
    )
    completed = []

    for info, _tag in reader.iter_candidates(markdown_selector):
        completed.append(reader.read_entry(info))

    assert completed == [b"assets/demo/ae2guide/a.md"]
    assert archive.opened == ["assets/demo/ae2guide/a.md"]
    assert reader.diagnostics[-1].code == JAR_TOTAL_SIZE_LIMIT


def test_actual_total_stop_does_not_open_entry_after_failed_entry():
    infos = [
        fake_info("assets/demo/ae2guide/a.md", file_size=6, compress_size=6),
        fake_info("assets/demo/ae2guide/b.md", file_size=6, compress_size=6),
        fake_info("assets/demo/ae2guide/c.md", file_size=1, compress_size=1),
    ]
    archive = FakeArchive(
        infos,
        streams={info.filename: FakeStream(b"x" * info.file_size) for info in infos},
    )
    reader = SafeZipReader(
        archive,
        limits=small_limits(
            max_entry_uncompressed=20,
            max_actual_read_total=10,
            chunk_size=2,
        ),
    )
    completed = []

    for info, _tag in reader.iter_candidates(markdown_selector):
        try:
            completed.append(reader.read_entry(info))
        except ZipSafetyError as error:
            assert error.code == ACTUAL_READ_LIMIT
            break

    assert completed == [b"x" * 6]
    assert archive.opened == [
        "assets/demo/ae2guide/a.md",
        "assets/demo/ae2guide/b.md",
    ]
    assert "assets/demo/ae2guide/c.md" not in archive.opened


def test_safe_zip_records_mid_stream_read_error_and_returns_no_partial_data():
    info = fake_info("assets/demo/ae2guide/broken.md", file_size=16, compress_size=8)
    archive = FakeArchive(
        [info],
        streams={info.filename: FakeStream(b"partial-data", fail_after=4)},
    )
    reader = SafeZipReader(archive, limits=small_limits(chunk_size=4))
    candidate = reader.prepare_candidates(markdown_selector)[0][0]

    with pytest.raises(ZipSafetyError) as raised:
        reader.read_entry(candidate)

    assert raised.value.code == READ_ERROR
    assert reader.diagnostics[-1].code == READ_ERROR


def test_non_candidate_binary_entries_do_not_consume_candidate_or_read_budget():
    image = fake_info("assets/demo/textures/huge.png", file_size=1_000_000, compress_size=1)
    class_file = fake_info("demo/Huge.class", file_size=1_000_000, compress_size=1)
    guide = fake_info("assets/demo/ae2guide/page.md", file_size=4, compress_size=4)
    archive = FakeArchive(
        [image, class_file, guide],
        streams={guide.filename: FakeStream(b"text")},
    )
    reader = SafeZipReader(
        archive,
        limits=small_limits(
            max_candidate_uncompressed_total=10,
            max_actual_read_total=10,
            max_compression_ratio=2.0,
        ),
    )

    candidates = reader.prepare_candidates(markdown_selector)
    assert len(candidates) == 1
    assert reader.candidate_uncompressed == 4
    assert reader.read_entry(candidates[0][0]) == b"text"
    assert archive.opened == [guide.filename]


def test_build_source_read_fails_clearly_for_dangerous_entry(tmp_path):
    modpack = tmp_path / "pack"
    jar_path = modpack / "mods" / "dangerous.jar"
    jar_path.parent.mkdir(parents=True)
    entry_name = "assets/demo/ae2guide/page.md"
    with zipfile.ZipFile(jar_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(entry_name, b"x" * 20_000)

    with pytest.raises(RuntimeError, match=r"\[compression_ratio_limit\]"):
        read_container_file(
            modpack,
            "mods/dangerous.jar",
            entry_name,
            zip_limits=small_limits(
                max_entry_uncompressed=30_000,
                max_candidate_uncompressed_total=30_000,
                max_actual_read_total=30_000,
                max_compression_ratio=10.0,
            ),
        )


def test_build_source_read_stops_on_jar_candidate_total_before_reading_target(tmp_path):
    modpack = tmp_path / "pack"
    jar_path = modpack / "mods" / "total-limit.jar"
    jar_path.parent.mkdir(parents=True)
    target = "assets/demo/ae2guide/a-page.md"
    with zipfile.ZipFile(jar_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(target, b"a" * 10)
        archive.writestr("assets/demo/ae2guide/z-page.md", b"z" * 10)

    with pytest.raises(RuntimeError, match=r"\[jar_total_size_limit\]"):
        read_container_file(
            modpack,
            "mods/total-limit.jar",
            target,
            zip_limits=small_limits(
                max_entry_uncompressed=20,
                max_candidate_uncompressed_total=15,
                max_actual_read_total=30,
            ),
        )


def test_build_source_open_missing_jar_uses_stable_read_error(tmp_path):
    modpack = tmp_path / "pack"
    modpack.mkdir()

    with pytest.raises(RuntimeError, match=r"\[read_error\].*FileNotFoundError"):
        read_container_file(
            modpack,
            "mods/missing.jar",
            "assets/demo/ae2guide/page.md",
        )


def test_build_source_open_oserror_uses_stable_read_error(tmp_path, monkeypatch):
    modpack = tmp_path / "pack"
    jar_path = modpack / "mods" / "blocked.jar"
    jar_path.parent.mkdir(parents=True)
    jar_path.write_bytes(b"placeholder")

    def raise_oserror(_path):
        raise OSError("simulated access failure")

    monkeypatch.setattr(resourcepack_module.zipfile, "ZipFile", raise_oserror)

    with pytest.raises(RuntimeError, match=r"\[read_error\].*OSError"):
        read_container_file(
            modpack,
            "mods/blocked.jar",
            "assets/demo/ae2guide/page.md",
        )


def small_limits(**overrides) -> ZipSafetyLimits:
    values = {
        "max_entries": 20,
        "max_entry_uncompressed": 1_024,
        "max_candidate_uncompressed_total": 4_096,
        "max_actual_read_total": 4_096,
        "max_compression_ratio": 100.0,
        "chunk_size": 16,
    }
    values.update(overrides)
    return ZipSafetyLimits(**values)


def markdown_selector(name: str) -> str | None:
    return "markdown" if name.endswith(".md") else None


def fake_info(name: str, *, file_size: int, compress_size: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.file_size = file_size
    info.compress_size = compress_size
    return info


class FakeArchive:
    def __init__(self, infos, *, streams=None):
        self.infos = infos
        self.streams = streams or {}
        self.opened = []

    def infolist(self):
        return self.infos

    def open(self, info, mode="r"):
        assert mode == "r"
        self.opened.append(info.filename)
        return self.streams.get(info.filename, FakeStream(b"x" * info.file_size))


class FakeStream:
    def __init__(self, data: bytes, *, fail_after: int | None = None):
        self.data = data
        self.fail_after = fail_after
        self.offset = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.closed = True

    def read(self, size: int) -> bytes:
        if self.fail_after is not None and self.offset >= self.fail_after:
            raise OSError("simulated stream failure")
        if self.offset >= len(self.data):
            return b""
        chunk = self.data[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

"""Unit: rendered slides are written so they survive the Docker VM stopping right afterwards."""

from __future__ import annotations

from pathlib import Path

import pytest

from media_os_api.carousel.render import write_durably

pytestmark = pytest.mark.unit


def test_write_durably_writes_the_bytes_and_leaves_no_temp_file(tmp_path: Path) -> None:
    target = tmp_path / "slide-01.png"
    write_durably(target, b"\x89PNG data")
    assert target.read_bytes() == b"\x89PNG data"
    assert [p.name for p in tmp_path.iterdir()] == ["slide-01.png"]
    write_durably(target, b"newer")  # replaces in place
    assert target.read_bytes() == b"newer"


def test_write_durably_refuses_empty_data(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        write_durably(tmp_path / "slide-01.png", b"")
    assert not (tmp_path / "slide-01.png").exists()


def test_write_durably_flushes_the_file_and_its_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_os_api.carousel import render

    file_flushes: list[int] = []
    folder_flushes: list[Path] = []
    import os

    real_fsync = os.fsync

    def spy_fsync(fd: int) -> None:
        file_flushes.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    monkeypatch.setattr(render, "fsync_dir", folder_flushes.append)
    write_durably(tmp_path / "slide-01.png", b"png")
    assert len(file_flushes) == 1  # the file's data
    assert folder_flushes == [tmp_path]  # the rename into place

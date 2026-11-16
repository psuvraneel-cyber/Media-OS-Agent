"""Tiny synthetic MP4 files: just enough box structure for the upload inspector (no real video)."""

from __future__ import annotations

import struct


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def make_mp4(
    seconds: float = 12.0,
    width: int = 1080,
    height: int = 1920,
    *,
    video: bool = True,
    moov_last: bool = False,
    mdat_bytes: int = 64,
) -> bytes:
    timescale = 1000
    mvhd = (
        b"\x00\x00\x00\x00"  # version 0 + flags
        + struct.pack(">IIII", 0, 0, timescale, int(seconds * timescale))
        + b"\x00" * 80
    )
    tkhd = b"\x00\x00\x00\x07" + b"\x00" * 72 + struct.pack(">II", width << 16, height << 16)
    hdlr = b"\x00" * 8 + (b"vide" if video else b"soun") + b"\x00" * 12 + b"Handler\x00"
    trak = _box(b"trak", _box(b"tkhd", tkhd) + _box(b"mdia", _box(b"hdlr", hdlr)))
    moov = _box(b"moov", _box(b"mvhd", mvhd) + trak)
    ftyp = _box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2avc1mp41")
    mdat = _box(b"mdat", b"\x00" * mdat_bytes)
    return ftyp + (mdat + moov if moov_last else moov + mdat)

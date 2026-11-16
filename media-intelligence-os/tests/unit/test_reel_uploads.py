"""Unit: reel upload checks, the MP4 inspector and the streamed GitHub blob body."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from tests.mp4_fixtures import make_mp4

from media_os_api.content import uploads as up
from media_os_api.publish import media_host
from media_os_api.publish.media_host import GitHubPagesHost


def _write(tmp_path: Path, data: bytes, name: str = "r.mp4") -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_inspector_reads_duration_size_and_video_track(tmp_path: Path) -> None:
    info = up.inspect_mp4(_write(tmp_path, make_mp4(12.5, 1080, 1920)))
    assert info.problems == [] and info.has_video
    assert info.duration == pytest.approx(12.5)
    assert (info.width, info.height) == (1080, 1920)
    # Index at the end of the file (common from phones) works too.
    late = up.inspect_mp4(_write(tmp_path, make_mp4(4, 720, 1280, moov_last=True), "late.mp4"))
    assert late.duration == pytest.approx(4) and late.has_video


def test_inspector_rejects_non_mp4_and_audio_only(tmp_path: Path) -> None:
    assert up.inspect_mp4(_write(tmp_path, b"\x89PNG\r\n\x1a\n" + b"0" * 40)).problems
    audio = up.inspect_mp4(_write(tmp_path, make_mp4(video=False), "a.mp4"))
    assert "No video track found." in audio.problems


def test_reel_checks_pass_warn_and_fail(tmp_path: Path) -> None:
    good = up.reel_checks(up.inspect_mp4(_write(tmp_path, make_mp4(20))), 30 * 1024 * 1024)
    assert good["passed"] and all(c["status"] == "PASS" for c in good["checks"])

    square = up.reel_checks(up.inspect_mp4(_write(tmp_path, make_mp4(20, 1080, 1080))), 1000)
    assert square["passed"]
    assert any(c["name"] == "aspect_ratio" and c["status"] == "WARN" for c in square["checks"])

    short = up.reel_checks(up.inspect_mp4(_write(tmp_path, make_mp4(1.5))), 1000)
    assert not short["passed"]
    big = up.reel_checks(up.inspect_mp4(_write(tmp_path, make_mp4(20))), up.MAX_REEL_BYTES + 1)
    assert not big["passed"] and "95 MB" in str(big["checks"])


def test_save_stream_writes_and_enforces_the_limit(tmp_path: Path) -> None:
    async def chunks(*parts: bytes) -> AsyncIterator[bytes]:
        for p in parts:
            yield p

    dest = tmp_path / "x" / "file.mp4"
    size, digest, head = asyncio.run(up.save_stream(chunks(b"abc", b"", b"def"), dest, limit=10))
    assert (size, dest.read_bytes(), head) == (6, b"abcdef", b"abcdef") and len(digest) == 64

    over = tmp_path / "over.mp4"
    with pytest.raises(up.UploadError) as exc:
        asyncio.run(up.save_stream(chunks(b"123456", b"789012"), over, limit=10))
    assert exc.value.status == 413
    assert not over.exists() and not list(tmp_path.glob(".over.mp4.part"))
    with pytest.raises(up.UploadError):
        asyncio.run(up.save_stream(chunks(), tmp_path / "empty.mp4", limit=10))


def test_resolve_asset_path_stays_inside_its_volume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEDIA_OS_MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("MEDIA_OS_UPLOADS_ROOT", str(tmp_path / "uploads"))
    assert (
        up.resolve_asset_path("uploads/b/reels/a.mp4")
        == (tmp_path / "uploads" / "b" / "reels" / "a.mp4").resolve()
    )
    assert (
        up.resolve_asset_path("carousels/p/v1/slide-01.png")
        == (tmp_path / "media" / "carousels" / "p" / "v1" / "slide-01.png").resolve()
    )
    assert up.resolve_asset_path("uploads/../../etc/passwd") is None
    assert up.resolve_asset_path("../secret") is None


def test_image_sniffing_and_text_helpers() -> None:
    assert up.sniff_image(b"\x89PNG\r\n\x1a\n....") == "image/png"
    assert up.sniff_image(b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert up.sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert up.sniff_image(b"GIF89a") is None
    assert up.title_from_filename("my_wedding-reel  01.mp4") == "my wedding reel 01"
    assert up._clean_hashtags(["wedding, #biye", "#biye", "card"]) == ["#wedding", "#biye", "#card"]


def test_streamed_blob_body_is_valid_base64_json(tmp_path: Path) -> None:
    data = bytes(range(256)) * 50_000  # 12.8 MB, spans several read chunks
    path = _write(tmp_path, data, "big.bin")
    body = b"".join(media_host._blob_body(path))
    assert len(body) == media_host._blob_body_length(path)
    parsed = json.loads(body)
    assert parsed["encoding"] == "base64" and base64.b64decode(parsed["content"]) == data


def test_publish_streams_path_files_and_live_check_accepts_video(tmp_path: Path) -> None:
    video = _write(tmp_path, make_mp4(10), "reel.mp4")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "head"}})
        if url.endswith("/git/commits/head"):
            return httpx.Response(200, json={"tree": {"sha": "t0"}})
        if url.endswith("/git/blobs"):
            seen["blob"] = json.loads(request.read())
            return httpx.Response(201, json={"sha": "b1"})
        if url.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "t1"})
        if url.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "c1"})
        if "/git/refs/heads/main" in url:
            return httpx.Response(200, json={})
        if url.startswith("https://o.github.io/"):
            return httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"x")
        return httpx.Response(404)

    host = GitHubPagesHost(
        token="t",
        repo="o/m",
        base_url="https://o.github.io/m",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )
    try:
        hosted = host.publish([("b/2026-11/p/v1/reel.mp4", video)], message="Reel")
        host.wait_until_live(hosted, timeout_s=1)
    finally:
        host.close()
    blob = seen["blob"]
    assert isinstance(blob, dict) and base64.b64decode(blob["content"]) == video.read_bytes()
    assert hosted[0].url == "https://o.github.io/m/b/2026-11/p/v1/reel.mp4"


def test_image_post_checks() -> None:
    one = up.image_post_checks([(1080, 1350)])
    assert one["passed"] and one["version"].startswith(up.OWNER_QA_PREFIX)
    mixed = up.image_post_checks([(1080, 1080), (1080, 1350)])
    assert mixed["passed"] and any(c["name"] == "same_shape" for c in mixed["checks"])
    tall = up.image_post_checks([(600, 1600)])
    assert tall["passed"] and any(c["name"] == "aspect_ratio" for c in tall["checks"])
    assert not up.image_post_checks([(None, None)])["passed"]
    assert not up.image_post_checks([(1080, 1080)] * 11)["passed"]


def test_to_jpeg_applies_phone_rotation_and_caps_size() -> None:
    import io

    from PIL import Image

    from media_os_api.publish.service import MAX_IMAGE_EDGE, to_jpeg

    img = Image.new("RGB", (400, 200), (255, 0, 0))
    exif = img.getexif()
    exif[0x0112] = 6  # "rotate 90° clockwise to display"
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    with Image.open(io.BytesIO(to_jpeg(buf.getvalue()))) as out:
        assert out.size == (200, 400)

    big = io.BytesIO()
    Image.new("RGB", (5000, 2500)).save(big, format="PNG")
    with Image.open(io.BytesIO(to_jpeg(big.getvalue()))) as out:
        assert max(out.size) == MAX_IMAGE_EDGE and out.format == "JPEG"

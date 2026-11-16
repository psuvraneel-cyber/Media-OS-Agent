"""Owner uploads: their own reels, reel cover frames and the brand logo.

Files live on their own volume (`MEDIA_OS_UPLOADS_ROOT`, default `/data/uploads`), writable by the
API only; the worker reads it to host a reel before publishing. Rendered carousel slides stay on
the media volume. An Asset row's `local_path` starting with `uploads/` points at this volume.

Uploads arrive as a raw request body and are streamed to disk in chunks, so a large video never
sits in memory or in the API's memory-backed /tmp.
"""

from __future__ import annotations

import hashlib
import os
import struct
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_os_api.db.models import Asset, BrandProfile, ContentPackage
from media_os_api.services.audit_service import record_audit_event
from media_os_api.workflow.state_machine import ContentLifecycleStatus, transition_content_status

UPLOADS_PREFIX = "uploads/"
# GitHub Pages hosts the reel for Instagram to download; GitHub refuses files over 100 MB.
MAX_REEL_BYTES = 95 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MIN_REEL_SECONDS = 3
MAX_REEL_SECONDS = 15 * 60  # Instagram's API limit for reels
LONG_REEL_SECONDS = 180
CHUNK = 1024 * 1024
REEL_VIDEO = "REEL_VIDEO"
REEL_POSTER = "REEL_POSTER"
BRAND_LOGO = "BRAND_LOGO"
CAPTION_MAX = 2200
HASHTAGS_MAX = 30
MAX_POST_IMAGE_BYTES = 8 * 1024 * 1024  # Instagram's limit for a photo
MAX_POST_IMAGES = 10
POST_IMAGE = "UPLOAD_IMAGE"  # staged until it's part of a post
SLIDE = "CAROUSEL_SLIDE"
STAGED = "STAGED"
STAGED_MAX_AGE_HOURS = 24
# qa_report.version marks owner uploads, so the generated-carousel review queue skips them.
OWNER_QA_PREFIX = "owner-"
OWNER_REEL_QA = "owner-reel-1"
OWNER_IMAGE_QA = "owner-image-1"
OWNER_FORMATS = ("REEL", "POST", "CAROUSEL")


class UploadError(ValueError):
    def __init__(self, message: str, *, code: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def media_root() -> Path:
    return Path(os.environ.get("MEDIA_OS_MEDIA_ROOT", "/data/media"))


def uploads_root() -> Path:
    return Path(os.environ.get("MEDIA_OS_UPLOADS_ROOT", "/data/uploads"))


def resolve_asset_path(local_path: str) -> Path | None:
    """Absolute path of an asset's file, or None if it would escape its volume."""
    if local_path.startswith(UPLOADS_PREFIX):
        root, rel = uploads_root().resolve(), local_path[len(UPLOADS_PREFIX) :]
    else:
        root, rel = media_root().resolve(), local_path
    path = (root / rel).resolve()
    return path if path.is_relative_to(root) and path != root else None


# --------------------------------------------------------------------------- sniffing


IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def sniff_image(head: bytes) -> str | None:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_size(data: bytes) -> tuple[int | None, int | None]:
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            return im.width, im.height
    except Exception:
        return None, None


@dataclass
class VideoInfo:
    brand: str = ""
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    has_video: bool = False
    problems: list[str] = field(default_factory=list)


_CONTAINERS = {b"moov", b"trak", b"mdia"}


def _boxes(f: BinaryIO, start: int, end: int) -> list[tuple[bytes, int, int]]:
    """(type, payload_start, payload_end) of the boxes between start and end."""
    out = []
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        header = f.read(8)
        if len(header) < 8:
            break
        size, kind = struct.unpack(">I4s", header)
        payload = pos + 8
        if size == 1:
            big = f.read(8)
            if len(big) < 8:
                break
            size = struct.unpack(">Q", big)[0]
            payload = pos + 16
        elif size == 0:
            size = end - pos
        if size < payload - pos or pos + size > end:
            break
        out.append((kind, payload, pos + size))
        pos += size
    return out


def inspect_mp4(path: Path) -> VideoInfo:
    """Read duration, frame size and whether there's a video track, without decoding."""
    info = VideoInfo()
    size = path.stat().st_size
    with path.open("rb") as f:
        top = _boxes(f, 0, size)
        if not top or top[0][0] != b"ftyp":
            info.problems.append("This isn't an MP4 video.")
            return info
        f.seek(top[0][1])
        info.brand = f.read(4).decode("latin-1", "replace").strip()
        moov = next((b for b in top if b[0] == b"moov"), None)
        if moov is None:
            info.problems.append("The video is incomplete (no index). Export it again.")
            return info
        for kind, start, end in _boxes(f, moov[1], moov[2]):
            if kind == b"mvhd":
                f.seek(start)
                body = f.read(min(end - start, 32))
                if body[:1] == b"\x01" and len(body) >= 32:
                    timescale, duration = struct.unpack(">IQ", body[20:32])
                elif len(body) >= 20:
                    timescale, duration = struct.unpack(">II", body[12:20])
                else:
                    continue
                if timescale:
                    info.duration = duration / timescale
            elif kind == b"trak":
                _track(f, start, end, info)
    if not info.has_video:
        info.problems.append("No video track found.")
    return info


def _track(f: BinaryIO, start: int, end: int, info: VideoInfo) -> None:
    width = height = None
    handler = b""
    for kind, s, e in _boxes(f, start, end):
        if kind == b"tkhd" and e - s >= 84:
            f.seek(e - 8)
            w, h = struct.unpack(">II", f.read(8))
            width, height = w >> 16, h >> 16
        elif kind == b"mdia":
            for k2, s2, _e2 in _boxes(f, s, e):
                if k2 == b"hdlr":
                    f.seek(s2 + 8)
                    handler = f.read(4)
    if handler == b"vide" and not info.has_video:
        info.has_video = True
        info.width, info.height = width or None, height or None


def reel_checks(info: VideoInfo, size: int) -> dict[str, Any]:
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, detail: str = "") -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    add("format", "FAIL" if info.problems else "PASS", " ".join(info.problems))
    mb = size / (1024 * 1024)
    if size > MAX_REEL_BYTES:
        add("file_size", "FAIL", f"{mb:.0f} MB; the limit is 95 MB. Export at a lower bitrate.")
    else:
        add("file_size", "PASS", f"{mb:.1f} MB")
    d = info.duration
    if d is None:
        add("duration", "WARN", "Couldn't read the length.")
    elif d < MIN_REEL_SECONDS:
        add("duration", "FAIL", f"{d:.1f} s; Instagram needs at least 3 s.")
    elif d > MAX_REEL_SECONDS:
        add("duration", "FAIL", f"{d / 60:.1f} min; Instagram allows up to 15 min.")
    elif d > LONG_REEL_SECONDS:
        add("duration", "WARN", f"{d / 60:.1f} min; reels over 3 min may not be shown as reels.")
    else:
        add("duration", "PASS", f"{d:.1f} s")
    if info.width and info.height:
        ratio = info.width / info.height
        if abs(ratio - 9 / 16) <= 0.02:
            add("aspect_ratio", "PASS", f"{info.width}×{info.height} (9:16)")
        else:
            add(
                "aspect_ratio",
                "WARN",
                f"{info.width}×{info.height}; reels look best in 9:16 (e.g. 1080×1920).",
            )
    passed = not any(c["status"] == "FAIL" for c in checks)
    return {"version": OWNER_REEL_QA, "passed": passed, "checks": checks}


def image_post_checks(sizes: list[tuple[int | None, int | None]]) -> dict[str, Any]:
    """Checks for an owner photo post (1 image) or carousel (2-10 images)."""
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, detail: str = "") -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    n = len(sizes)
    if not 1 <= n <= MAX_POST_IMAGES:
        add("image_count", "FAIL", f"{n} images; a post takes 1 image, a carousel 2–10.")
    else:
        add("image_count", "PASS", "1 image" if n == 1 else f"{n} images")
    unreadable = [i for i, (w, h) in enumerate(sizes, 1) if not w or not h]
    if unreadable:
        add("readable", "FAIL", f"Image {', '.join(map(str, unreadable))} couldn't be opened.")
    ratios = [(i, w / h) for i, (w, h) in enumerate(sizes, 1) if w and h]
    odd = [i for i, r in ratios if not 0.79 <= r <= 1.92]
    if odd:
        add(
            "aspect_ratio",
            "WARN",
            f"Image {', '.join(map(str, odd))} is taller than 4:5 or wider than 1.91:1; "
            "Instagram will crop it.",
        )
    elif ratios:
        add("aspect_ratio", "PASS")
    small = [i for i, (w, _h) in enumerate(sizes, 1) if w and w < 600]
    if small:
        add("resolution", "WARN", f"Image {', '.join(map(str, small))} is under 600 px wide.")
    if n > 1 and ratios and max(r for _, r in ratios) - min(r for _, r in ratios) > 0.02:
        add("same_shape", "WARN", "Instagram crops every slide to the first slide's shape.")
    passed = not any(c["status"] == "FAIL" for c in checks)
    return {"version": OWNER_IMAGE_QA, "passed": passed, "checks": checks}


# --------------------------------------------------------------------------- saving


async def save_stream(
    chunks: AsyncIterator[bytes], dest: Path, *, limit: int
) -> tuple[int, str, bytes]:
    """Write chunks to dest; returns (size, sha256, first 64 bytes). Refuses past the limit."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.part")
    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        with tmp.open("wb") as out:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > limit:
                    raise UploadError(
                        f"File is larger than {limit // (1024 * 1024)} MB.",
                        code="TOO_LARGE",
                        status=413,
                    )
                if len(head) < 64:
                    head += chunk[: 64 - len(head)]
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise UploadError("The file is empty.", code="EMPTY")
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return size, digest.hexdigest(), head


def _clean_hashtags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for raw in tags:
        for part in str(raw).replace(",", " ").split():
            tag = part.strip()
            if not tag:
                continue
            tag = tag if tag.startswith("#") else f"#{tag}"
            if len(tag) > 1 and tag not in out:
                out.append(tag[:100])
    return out


def title_from_filename(filename: str) -> str:
    stem = Path(filename or "Reel").stem.replace("_", " ").replace("-", " ").strip()
    return (" ".join(stem.split()) or "Reel")[:200]


def create_reel(
    session: Session,
    *,
    brand: BrandProfile,
    path: Path,
    relative: str,
    size: int,
    checksum: str,
    title: str,
    actor_id: str,
) -> ContentPackage:
    """Record an uploaded video as a reel awaiting the owner's approval."""
    info = inspect_mp4(path)
    qa = reel_checks(info, size)
    pkg = ContentPackage(
        brand_profile_id=brand.id,
        research_packet_id=None,
        platform="INSTAGRAM",
        format="REEL",
        status=ContentLifecycleStatus.DRAFTED.value,
        approval_status="PENDING",
        title=title[:512],
        hook=None,
        caption="",
        hashtags=[],
        slide_data=[],
        source_manifest=[{"kind": "owner_upload", "file": title[:200]}],
        qa_report=qa,
    )
    session.add(pkg)
    session.flush()
    session.add(
        Asset(
            brand_profile_id=brand.id,
            content_package_id=pkg.id,
            asset_type=REEL_VIDEO,
            local_path=relative,
            mime_type="video/mp4",
            file_size=size,
            checksum=f"sha256:{checksum}",
            width=info.width,
            height=info.height,
            duration_seconds=Decimal(f"{info.duration:.2f}") if info.duration else None,
            status="ACTIVE",
        )
    )
    transition_content_status(
        session,
        content_package_id=pkg.id,
        to_status=ContentLifecycleStatus.RENDERED,
        actor_id=actor_id,
        reason="Owner uploaded the video",
        expected_brand_id=brand.id,
    )
    if qa["passed"]:
        transition_content_status(
            session,
            content_package_id=pkg.id,
            to_status=ContentLifecycleStatus.QA_PASSED,
            actor_id=actor_id,
            reason="Upload checks passed",
            expected_brand_id=brand.id,
        )
    session.refresh(pkg)
    record_audit_event(
        session,
        actor_id=actor_id,
        action="REEL_UPLOADED",
        target_type="content_package",
        target_id=str(pkg.id),
        outcome="SUCCESS",
        metadata={"bytes": size, "qa_passed": qa["passed"]},
    )
    return pkg


def stage_image(
    session: Session,
    *,
    brand: BrandProfile,
    data: bytes,
    mime: str,
) -> Asset:
    """Save one uploaded image, not yet part of any post (`create_image_post` attaches it)."""
    relative, dest = new_upload_path(brand, "staged", IMAGE_TYPES[mime])
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    width, height = image_size(data)
    asset = Asset(
        brand_profile_id=brand.id,
        content_package_id=None,
        asset_type=POST_IMAGE,
        local_path=relative,
        mime_type=mime,
        file_size=len(data),
        checksum=f"sha256:{hashlib.sha256(data).hexdigest()}",
        width=width,
        height=height,
        status=STAGED,
    )
    session.add(asset)
    session.flush()
    return asset


def create_image_post(
    session: Session,
    *,
    brand: BrandProfile,
    asset_ids: list[uuid.UUID],
    title: str,
    actor_id: str,
    now: datetime | None = None,
) -> ContentPackage:
    """One image becomes a photo post, 2-10 become a carousel, in the order given."""
    now = now or datetime.now(UTC)
    if not 1 <= len(asset_ids) <= MAX_POST_IMAGES:
        raise UploadError("A post takes 1 image and a carousel 2–10.", code="IMAGE_COUNT")
    if len(set(asset_ids)) != len(asset_ids):
        raise UploadError("The same image is in the list twice.", code="DUPLICATE_IMAGE")
    found = {
        a.id: a
        for a in session.execute(
            select(Asset).where(Asset.id.in_(asset_ids)).with_for_update()
        ).scalars()
    }
    assets: list[Asset] = []
    for aid in asset_ids:
        a = found.get(aid)
        if (
            a is None
            or a.brand_profile_id != brand.id
            or a.asset_type != POST_IMAGE
            or a.status != STAGED
            or (now - a.created_at).total_seconds() > STAGED_MAX_AGE_HOURS * 3600
        ):
            raise UploadError(
                "One of the images is missing or already used. Upload it again.",
                code="IMAGE_NOT_AVAILABLE",
                status=409,
            )
        assets.append(a)

    qa = image_post_checks([(a.width, a.height) for a in assets])
    fmt = "POST" if len(assets) == 1 else "CAROUSEL"
    pkg = ContentPackage(
        brand_profile_id=brand.id,
        research_packet_id=None,
        platform="INSTAGRAM",
        format=fmt,
        status=ContentLifecycleStatus.DRAFTED.value,
        approval_status="PENDING",
        title=title[:512] or ("Photo post" if fmt == "POST" else "Carousel"),
        caption="",
        hashtags=[],
        slide_data=[{"index": i, "source": "owner_upload"} for i in range(1, len(assets) + 1)],
        source_manifest=[{"kind": "owner_upload", "images": len(assets)}],
        qa_report=qa,
    )
    session.add(pkg)
    session.flush()
    # Slides are ordered by path, so each file moves to slide-01, slide-02, ... in the chosen order.
    for i, a in enumerate(assets, 1):
        ext = IMAGE_TYPES.get(a.mime_type, "jpg")
        rel = f"{brand.slug}/posts/{pkg.id}/slide-{i:02d}.{ext}"
        old = resolve_asset_path(a.local_path)
        new = uploads_root() / rel
        new.parent.mkdir(parents=True, exist_ok=True)
        if old is None or not old.is_file():
            raise UploadError(
                "One of the images is missing. Upload it again.", code="IMAGE_NOT_AVAILABLE"
            )
        old.replace(new)
        a.local_path, a.asset_type, a.status = f"{UPLOADS_PREFIX}{rel}", SLIDE, "ACTIVE"
        a.content_package_id = pkg.id
    session.flush()
    transition_content_status(
        session,
        content_package_id=pkg.id,
        to_status=ContentLifecycleStatus.RENDERED,
        actor_id=actor_id,
        reason="Owner uploaded the images",
        expected_brand_id=brand.id,
    )
    if qa["passed"]:
        transition_content_status(
            session,
            content_package_id=pkg.id,
            to_status=ContentLifecycleStatus.QA_PASSED,
            actor_id=actor_id,
            reason="Upload checks passed",
            expected_brand_id=brand.id,
        )
    session.refresh(pkg)
    record_audit_event(
        session,
        actor_id=actor_id,
        action="POST_UPLOADED",
        target_type="content_package",
        target_id=str(pkg.id),
        outcome="SUCCESS",
        metadata={"format": fmt, "images": len(assets), "qa_passed": qa["passed"]},
    )
    return pkg


def is_owner_upload(pkg: ContentPackage) -> bool:
    return str((pkg.qa_report or {}).get("version", "")).startswith(OWNER_QA_PREFIX)


def update_reel_text(
    pkg: ContentPackage, *, title: str | None, caption: str | None, hashtags: list[str] | None
) -> None:
    if title is not None:
        pkg.title = title.strip()[:512] or pkg.title
    if caption is not None:
        if len(caption) > CAPTION_MAX:
            raise UploadError("Captions can be up to 2,200 characters.", code="CAPTION_TOO_LONG")
        pkg.caption = caption.strip()
    if hashtags is not None:
        tags = _clean_hashtags(hashtags)
        if len(tags) > HASHTAGS_MAX:
            raise UploadError("Instagram allows up to 30 hashtags.", code="TOO_MANY_HASHTAGS")
        pkg.hashtags = tags
    from media_os_api.content.caption import full_caption

    if len(full_caption(pkg.caption, pkg.hashtags or [])) > CAPTION_MAX:
        raise UploadError(
            "Caption plus hashtags is longer than Instagram's 2,200 characters.",
            code="CAPTION_TOO_LONG",
        )


def active_assets(session: Session, pkg: ContentPackage, asset_type: str) -> list[Asset]:
    return list(
        session.execute(
            select(Asset)
            .where(
                Asset.content_package_id == pkg.id,
                Asset.asset_type == asset_type,
                Asset.status == "ACTIVE",
            )
            .order_by(Asset.created_at)
        ).scalars()
    )


def retire_asset(asset: Asset) -> None:
    """Mark an upload deleted and remove its file (rendered slides are never touched)."""
    asset.status = "DELETED"
    if asset.local_path.startswith(UPLOADS_PREFIX):
        path = resolve_asset_path(asset.local_path)
        if path is not None:
            path.unlink(missing_ok=True)


def new_upload_path(brand: BrandProfile, kind: str, ext: str) -> tuple[str, Path]:
    stamp = datetime.now(UTC).strftime("%Y-%m")
    rel = f"{brand.slug}/{kind}/{stamp}/{uuid.uuid4().hex}.{ext}"
    return f"{UPLOADS_PREFIX}{rel}", uploads_root() / rel

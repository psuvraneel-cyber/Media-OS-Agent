"""N3 publishing: GitHub Pages uploader, Postiz client, JPEG conversion, caps (no network)."""

from __future__ import annotations

import base64
import io
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from media_os_api.publish.media_host import GitHubPagesHost, HostedFile, MediaHostError
from media_os_api.publish.postiz import Integration, PostizClient, PostizError
from media_os_api.publish.service import group_id, local_day_bounds, to_jpeg

pytestmark = pytest.mark.unit

TOKEN = "github_pat_test_only"
KEY = "postiz-test-key"


# ----------------------------------------------------------------- GitHub Pages host


class FakeGitHub:
    def __init__(self, *, same_tree: bool = False, live_after: int = 0) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.same_tree = same_tree
        self.live_after = live_after
        self.public_hits = 0
        self.listing: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        if request.url.host == "api.github.com":
            assert request.headers["authorization"] == f"Bearer {TOKEN}"
            path = request.url.path.removeprefix("/repos/owner/media")
            if path == "/git/ref/heads/main":
                return httpx.Response(200, json={"object": {"sha": "head1"}})
            if path == "/git/commits/head1":
                return httpx.Response(200, json={"tree": {"sha": "tree0"}})
            if path == "/git/blobs":
                return httpx.Response(201, json={"sha": f"blob{len(self.calls)}"})
            if path == "/git/trees":
                return httpx.Response(201, json={"sha": "tree0" if self.same_tree else "tree1"})
            if path == "/git/trees/tree0":
                return httpx.Response(200, json={"truncated": False, "tree": self.listing})
            if path == "/git/commits":
                return httpx.Response(201, json={"sha": "commit1"})
            if path == "/git/refs/heads/main":
                return httpx.Response(200, json={})
            return httpx.Response(404)
        # public GitHub Pages URL
        assert "authorization" not in request.headers  # token never sent to Pages
        self.public_hits += 1
        if self.public_hits <= self.live_after:
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"jpg")


def host(fake: FakeGitHub) -> GitHubPagesHost:
    return GitHubPagesHost(
        token=TOKEN,
        repo="owner/media",
        base_url="https://owner.github.io/media",
        transport=httpx.MockTransport(fake),
        sleep=lambda _s: None,
    )


def test_publish_makes_one_commit_with_all_files() -> None:
    fake = FakeGitHub()
    files = host(fake).publish(
        [("brand/a/slide-01.jpg", b"one"), ("brand/a/slide-02.jpg", b"two")], "m"
    )
    assert [f.url for f in files] == [
        "https://owner.github.io/media/brand/a/slide-01.jpg",
        "https://owner.github.io/media/brand/a/slide-02.jpg",
    ]
    blobs = [c for c in fake.calls if c[1].endswith("/git/blobs")]
    assert [base64.b64decode(c[2]["content"]) for c in blobs] == [b"one", b"two"]
    tree = next(c for c in fake.calls if c[1].endswith("/git/trees"))[2]
    assert tree["base_tree"] == "tree0" and len(tree["tree"]) == 2
    assert [c[0] for c in fake.calls if c[1].endswith("/git/commits")] == ["POST"]
    assert any(c[0] == "PATCH" for c in fake.calls)


def test_remove_matching_deletes_only_matching_files_in_one_commit() -> None:
    fake = FakeGitHub()
    fake.listing = [
        {"path": "brand", "type": "tree"},
        {"path": "brand/2026-11/p1/v1/slide-01.jpg", "type": "blob"},
        {"path": "brand/2026-11/p1/v1/slide-02.jpg", "type": "blob"},
        {"path": "brand/2026-11/p2/v1/reel.mp4", "type": "blob"},
        {"path": "index.html", "type": "blob"},
    ]
    gone = host(fake).remove_matching(lambda p: "/p1/" in p, "clean")
    assert gone == ["brand/2026-11/p1/v1/slide-01.jpg", "brand/2026-11/p1/v1/slide-02.jpg"]
    tree = next(c for c in fake.calls if c[0] == "POST" and c[1].endswith("/git/trees"))[2]
    assert tree["base_tree"] == "tree0"
    assert [e["sha"] for e in tree["tree"]] == [None, None]  # null sha deletes the file
    assert any(c[0] == "PATCH" for c in fake.calls)

    nothing = FakeGitHub()
    assert host(nothing).remove_matching(lambda p: False, "clean") == []
    assert not any(c[0] in ("POST", "PATCH") for c in nothing.calls)


def test_identical_content_skips_the_commit() -> None:
    fake = FakeGitHub(same_tree=True)
    host(fake).publish([("brand/a/slide-01.jpg", b"one")], "m")
    assert not any(c[0] == "PATCH" for c in fake.calls)


def test_wait_until_live_polls_then_succeeds_and_times_out() -> None:
    fake = FakeGitHub(live_after=2)
    h = host(fake)
    h.wait_until_live([HostedFile("p", "https://owner.github.io/media/p.jpg")])
    assert fake.public_hits == 3
    never = FakeGitHub(live_after=10_000)
    with pytest.raises(MediaHostError) as exc:
        host(never).wait_until_live(
            [HostedFile("p", "https://owner.github.io/media/p.jpg")], timeout_s=0, every_s=0
        )
    assert exc.value.code == "PAGES_NOT_LIVE" and exc.value.retryable


def test_bad_token_is_permanent_and_paths_are_validated() -> None:
    def deny(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    h = GitHubPagesHost(
        token=TOKEN,
        repo="owner/media",
        base_url="https://x.github.io/m",
        transport=httpx.MockTransport(deny),
    )
    with pytest.raises(MediaHostError) as exc:
        h.publish([("a/b.jpg", b"x")], "m")
    assert exc.value.code == "GITHUB_HTTP_401" and not exc.value.retryable
    with pytest.raises(ValueError):
        h.publish([("../escape.jpg", b"x")], "m")
    with pytest.raises(ValueError):
        GitHubPagesHost(token=TOKEN, repo="owner/media", base_url="http://insecure")
    with pytest.raises(ValueError):
        GitHubPagesHost(token="", repo="owner/media", base_url="https://x.github.io/m")


# ----------------------------------------------------------------- Postiz client


def test_postiz_payload_shape_and_auth() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json=[
                {"postId": f"p-{p['integration']['id']}", "integration": p["integration"]["id"]}
                for p in body["posts"]
            ],
        )

    client = PostizClient(
        base_url="http://host.docker.internal:4007",
        api_key=KEY,
        transport=httpx.MockTransport(handler),
    )
    when = datetime(2026, 11, 18, 13, 30, tzinfo=UTC)
    created = client.schedule_carousel(
        when=when,
        caption="বিয়ের কার্ড #Nimontron",
        image_urls=["https://o.github.io/m/1.jpg", "https://o.github.io/m/2.jpg"],
        targets=[
            Integration("ig1", "instagram", "N", False),
            Integration("fb1", "facebook", "N", False),
        ],
        image_prefix="g1",
    )
    assert created == {"ig1": "p-ig1", "fb1": "p-fb1"}
    req = seen[0]
    assert req.url.path == "/api/public/v1/posts"
    assert req.headers["authorization"] == KEY  # raw key, no Bearer prefix
    body = json.loads(req.content)
    assert body["type"] == "schedule" and body["shortLink"] is False
    assert body["date"] == when.isoformat()
    ig, fb = body["posts"]
    assert ig["settings"] == {"__type": "instagram", "post_type": "post", "collaborators": []}
    assert fb["settings"]["__type"] == "facebook"
    assert ig["value"][0]["image"][0] == {"id": "g1-1", "path": "https://o.github.io/m/1.jpg"}


def test_postiz_errors_and_base_url_guard() -> None:
    def status(code: int):  # type: ignore[no-untyped-def]
        return httpx.MockTransport(lambda _r: httpx.Response(code, text="nope"))

    for code, retryable in ((401, False), (400, False), (429, True), (503, True)):
        c = PostizClient(base_url="http://localhost:4007", api_key=KEY, transport=status(code))
        with pytest.raises(PostizError) as exc:
            c.integrations()
        assert exc.value.retryable is retryable
    with pytest.raises(ValueError):
        PostizClient(base_url="https://evil.example.com", api_key=KEY)
    with pytest.raises(ValueError):
        PostizClient(base_url="http://localhost:4007", api_key=" ")
    ok = PostizClient(base_url="http://localhost:4007", api_key=KEY, transport=status(200))
    with pytest.raises(ValueError):
        ok.schedule_carousel(
            when=datetime(2026, 1, 1), caption="x", image_urls=[], targets=[], image_prefix="g"
        )


def test_integrations_parsing() -> None:
    data = [
        {"id": "a", "identifier": "instagram", "name": "Nimontron", "disabled": False},
        {"id": "b", "identifier": "facebook", "name": "Nimontron", "disabled": True},
    ]
    c = PostizClient(
        base_url="http://localhost:4007",
        api_key=KEY,
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=data)),
    )
    assert c.integrations() == [
        Integration("a", "instagram", "Nimontron", False),
        Integration("b", "facebook", "Nimontron", True),
    ]


# ----------------------------------------------------------------- helpers


def test_to_jpeg_converts_png() -> None:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (40, 50), (163, 22, 31, 255)).save(buf, format="PNG")
    out = to_jpeg(buf.getvalue())
    assert out[:3] == b"\xff\xd8\xff"
    with Image.open(io.BytesIO(out)) as im:
        assert im.format == "JPEG" and im.size == (40, 50) and im.mode == "RGB"


def test_local_day_bounds_and_group_id() -> None:
    when = datetime(2026, 11, 18, 20, 0, tzinfo=UTC)  # 01:30 next day in Kolkata
    start, end = local_day_bounds(when, "Asia/Kolkata")
    assert start == datetime(2026, 11, 18, 18, 30, tzinfo=UTC)
    assert end == datetime(2026, 11, 19, 18, 30, tzinfo=UTC)
    import uuid

    pid = uuid.uuid4()
    assert group_id(pid, when) == group_id(pid, when) != group_id(pid, end)

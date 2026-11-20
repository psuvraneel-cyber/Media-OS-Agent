"""Public image hosting on GitHub Pages (owner-chosen, free, no card).

Instagram's Graph API downloads every slide from a public HTTPS URL, so approved carousels are
committed to the owner's public `nimontron-media` repo (served by GitHub Pages). All slides of one
carousel go into a single commit via the Git Data API, so each post triggers one Pages build.

The token is a fine-grained PAT limited to that one repo (Contents: read/write). It is read from
the environment, never logged, and only ever sent to api.github.com.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

GITHUB_API = "https://api.github.com"
_REPO_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_./")


class MediaHostError(RuntimeError):
    def __init__(self, message: str, *, code: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class HostedFile:
    path: str  # repo path, e.g. nimontron/2026-11/<id>/slide-01.jpg
    url: str  # public URL


_BLOB_HEAD = b'{"encoding":"base64","content":"'
_BLOB_TAIL = b'"}'
_READ = 3 * 1024 * 1024  # a multiple of 3, so each chunk encodes without padding


def _blob_body_length(path: Path) -> int:
    return len(_BLOB_HEAD) + 4 * ((path.stat().st_size + 2) // 3) + len(_BLOB_TAIL)


def _blob_body(path: Path) -> Iterator[bytes]:
    yield _BLOB_HEAD
    with path.open("rb") as f:
        while chunk := f.read(_READ):
            yield base64.b64encode(chunk)
    yield _BLOB_TAIL


def _check_repo(repo: str) -> str:
    owner, _, name = repo.partition("/")
    if not owner or not name or "/" in name or not set(repo) <= _REPO_CHARS or ".." in repo:
        raise ValueError("NIMONTRON_MEDIA_GITHUB_REPO must look like 'owner/repo'.")
    return repo


def _check_path(path: str) -> str:
    if path.startswith("/") or ".." in path.split("/") or not set(path) <= _REPO_CHARS:
        raise ValueError(f"Unsafe repo path: {path!r}")
    return path


class GitHubPagesHost:
    def __init__(
        self,
        *,
        token: str,
        repo: str,
        base_url: str,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        branch: str = "main",
    ) -> None:
        if not token.strip():
            raise ValueError("NIMONTRON_MEDIA_GITHUB_TOKEN is not set.")
        if not base_url.startswith("https://"):
            raise ValueError("NIMONTRON_MEDIA_BASE_URL must be an https:// URL.")
        self._repo = _check_repo(repo.strip())
        self._base_url = base_url.rstrip("/")
        self._branch = branch
        self._sleep = sleep
        self._api = httpx.Client(
            base_url=GITHUB_API,
            headers={
                "Authorization": f"Bearer {token.strip()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "media-intelligence-os",
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=False,
            transport=transport,
            trust_env=False,
        )
        self._public = httpx.Client(
            timeout=httpx.Timeout(15.0, connect=10.0),
            follow_redirects=True,
            transport=transport,
            trust_env=False,
            headers={"User-Agent": "media-intelligence-os"},
        )

    def close(self) -> None:
        self._api.close()
        self._public.close()

    def url_for(self, path: str) -> str:
        return f"{self._base_url}/{_check_path(path)}"

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = self._api.request(method, f"/repos/{self._repo}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise MediaHostError(
                "GitHub unreachable.", code="GITHUB_NETWORK", retryable=True
            ) from exc
        if resp.status_code in (401, 403, 404):
            raise MediaHostError(
                f"GitHub refused the request (HTTP {resp.status_code}); check the token's repo "
                "access and Contents permission.",
                code=f"GITHUB_HTTP_{resp.status_code}",
                retryable=resp.status_code == 403 and "rate limit" in resp.text.lower(),
            )
        if resp.status_code >= 400:
            raise MediaHostError(
                f"GitHub error HTTP {resp.status_code}.",
                code=f"GITHUB_HTTP_{resp.status_code}",
                retryable=resp.status_code >= 500 or resp.status_code == 409,
            )
        return resp.json() if resp.content else None

    def publish(self, files: Sequence[tuple[str, bytes | Path]], message: str) -> list[HostedFile]:
        """Commit all files in one commit on the Pages branch. Idempotent for identical content."""
        if not files:
            raise ValueError("Nothing to publish.")
        paths = [_check_path(p) for p, _ in files]
        ref = self._call("GET", f"/git/ref/heads/{self._branch}")
        head_sha = ref["object"]["sha"]
        head = self._call("GET", f"/git/commits/{head_sha}")
        tree = []
        for path, data in files:
            if isinstance(data, Path):
                # Large files (reels) are streamed as base64 JSON so they never sit in memory.
                blob = self._call(
                    "POST",
                    "/git/blobs",
                    content=_blob_body(data),
                    headers={
                        "Content-Type": "application/json",
                        "Content-Length": str(_blob_body_length(data)),
                    },
                    timeout=httpx.Timeout(600.0, connect=10.0),
                )
            else:
                blob = self._call(
                    "POST",
                    "/git/blobs",
                    json={
                        "content": base64.b64encode(data).decode("ascii"),
                        "encoding": "base64",
                    },
                )
            tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        new_tree = self._call(
            "POST", "/git/trees", json={"base_tree": head["tree"]["sha"], "tree": tree}
        )
        if new_tree["sha"] != head["tree"]["sha"]:  # identical content: nothing to commit
            commit = self._call(
                "POST",
                "/git/commits",
                json={"message": message, "tree": new_tree["sha"], "parents": [head_sha]},
            )
            self._call(
                "PATCH",
                f"/git/refs/heads/{self._branch}",
                json={"sha": commit["sha"], "force": False},
            )
        return [HostedFile(p, self.url_for(p)) for p in paths]

    def remove_matching(self, wanted: Callable[[str], bool], message: str) -> list[str]:
        """Delete every file whose repo path `wanted` accepts, in one commit. Returns the paths.

        Lists the branch's whole tree once (recursive), so one call covers many posts."""
        ref = self._call("GET", f"/git/ref/heads/{self._branch}")
        head_sha = ref["object"]["sha"]
        head = self._call("GET", f"/git/commits/{head_sha}")
        listing = self._call("GET", f"/git/trees/{head['tree']['sha']}?recursive=1")
        if listing.get("truncated"):
            raise MediaHostError(
                "The media repo is too large to list in one call.",
                code="GITHUB_TREE_TRUNCATED",
                retryable=False,
            )
        paths = sorted(
            str(item["path"])
            for item in listing.get("tree", [])
            if item.get("type") == "blob" and wanted(str(item.get("path", "")))
        )
        if not paths:
            return []
        tree = [
            {"path": _check_path(p), "mode": "100644", "type": "blob", "sha": None} for p in paths
        ]
        new_tree = self._call(
            "POST", "/git/trees", json={"base_tree": head["tree"]["sha"], "tree": tree}
        )
        commit = self._call(
            "POST",
            "/git/commits",
            json={"message": message, "tree": new_tree["sha"], "parents": [head_sha]},
        )
        self._call(
            "PATCH", f"/git/refs/heads/{self._branch}", json={"sha": commit["sha"], "force": False}
        )
        return paths

    def wait_until_live(
        self, files: list[HostedFile], *, timeout_s: float = 420.0, every_s: float = 15.0
    ) -> None:
        """Poll the public URLs until every one serves its image or video (Pages builds take
        1-2 min). Only the headers are read, so a large video isn't downloaded each time."""
        deadline = time.monotonic() + timeout_s
        pending = list(files)
        while True:
            still: list[HostedFile] = []
            for f in pending:
                try:
                    with self._public.stream("GET", f.url) as resp:
                        ok = resp.status_code == 200 and resp.headers.get(
                            "content-type", ""
                        ).startswith(("image/", "video/"))
                except httpx.HTTPError:
                    ok = False
                if not ok:
                    still.append(f)
            if not still:
                return
            if time.monotonic() >= deadline:
                raise MediaHostError(
                    f"{len(still)} image(s) not yet live on GitHub Pages.",
                    code="PAGES_NOT_LIVE",
                    retryable=True,
                )
            pending = still
            self._sleep(every_s)

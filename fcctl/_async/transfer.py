"""Async core of cross-server restore: stream backup files from a source Pilot into a target Pilot's chunked upload.

Recipe verified live (against the live API during development). Constant memory: one chunk at a time.
Source chunks are fetched with HTTP Range (a server that ignores Range is handled by skipping).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import httpx

from ..request import FcError, raise_for_pilot
from . import _io
from .pilot import PilotClient

PART_SUFFIX = (("private", "-private-files.tar"), ("public", "-files.tar"), ("database", "-database.sql.gz"))


class TransferError(FcError): ...


def classify(filename: str) -> str | None:
    for part, suffix in PART_SUFFIX:  # private before public: "-private-files.tar" also ends "-files.tar"
        if filename.endswith(suffix):
            return part
    return None


async def _auth(p: PilotClient) -> tuple[dict, str]:
    s = await p.session()
    return {"Authorization": f"Bearer {s['token']}"}, s["gateway"]


async def _call(p: PilotClient, method: str, path: str, *, retries: int = 4, backoff: float = 1.0,
          timeout: float = 120.0, headers: dict | None = None, **kw) -> httpx.Response:
    """One Pilot request with retry on 5xx/network errors and a single re-mint on 401."""
    reminted, attempt = False, 0
    while True:
        h, gw = await _auth(p)
        h.update(headers or {})
        try:
            r = await p.http.request(method, f"{gw}/api/v1{path}", headers=h, timeout=timeout, **kw)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            if attempt >= retries:
                raise TransferError(f"{method} {path}: {e}") from e
            attempt += 1
            await _io.sleep(backoff * 2 ** (attempt - 1))
            continue
        if r.status_code == 401 and not reminted:
            reminted = True
            p.invalidate()  # next _auth() re-mints via the Central client (and its reauth hook)
            continue
        if r.status_code >= 500 and attempt < retries:
            attempt += 1
            await _io.sleep(backoff * 2 ** (attempt - 1))
            continue
        return r


async def _read_chunk(src: PilotClient, path: str, offset: int, length: int, **kw) -> bytes:
    r = await _call(src, "GET", path, headers={"Range": f"bytes={offset}-{offset + length - 1}"}, **kw)
    if r.status_code == 206:
        return r.content
    raise_for_pilot(r)
    # server ignored Range: whole body came back; slice it (only for small files)
    return r.content[offset:offset + length]


def _load(state: dict | str | Path | None) -> tuple[dict, Callable[[], None]]:
    if isinstance(state, dict):
        return state, lambda: None
    if state is None:
        d: dict = {}
        return d, lambda: None
    path = Path(state)
    d = json.loads(path.read_text()) if path.exists() else {}
    return d, lambda: path.write_text(json.dumps(d))


async def restore_from_site_backup(src_pilot: PilotClient, src_site: str, backup_timestamp: str,
                             dst_pilot: PilotClient, dst_site: str,
                             parts=("database", "public", "private"), chunk_size: int = 8 * 1024 * 1024,
                             progress: Callable[[int, int, str], None] | None = None, *,
                             state: dict | str | Path | None = None, retries: int = 4,
                             backoff: float = 1.0, chunk_timeout: float = 120.0,
                             skip_failing_patches: bool = False, upload_only: bool = False) -> Any:
    """Stream backup `parts` src -> dst upload, then POST dst actions/restore. Returns the task.

    `state` (dict or JSON file path) records upload_id and per-part uploaded offsets so a rerun
    resumes. progress(bytes_done_total, bytes_total, filename) is called after each chunk.
    """
    kw = dict(retries=retries, backoff=backoff, timeout=chunk_timeout)
    meta = raise_for_pilot(await _call(src_pilot, "GET", f"/sites/{src_site}/backups/{backup_timestamp}", **kw))
    files = (meta.get("files") if isinstance(meta, dict) else None) or []
    chosen: dict[str, dict] = {}
    for f in files:
        part = classify(f.get("filename", ""))
        if part in parts:
            chosen[part] = f
    missing = [p for p in parts if p not in chosen]
    if missing:
        raise TransferError(f"backup {backup_timestamp} has no file for parts {missing}")

    st, save = _load(state)
    if st.get("dst_site") != dst_site or st.get("timestamp") != backup_timestamp or "upload_id" not in st:
        body = {"files": {p: {"filename": chosen[p]["filename"], "size": int(chosen[p]["size"])} for p in parts}}
        up = raise_for_pilot(await _call(dst_pilot, "POST", f"/sites/{dst_site}/uploads", json=body, **kw))
        st.clear()
        st.update(upload_id=up["upload_id"], dst_site=dst_site, timestamp=backup_timestamp, done={})
        save()
    uid, done = st["upload_id"], st.setdefault("done", {})

    total = sum(int(chosen[p]["size"]) for p in parts)
    sent = sum(min(int(done.get(p, 0)), int(chosen[p]["size"])) for p in parts)
    for part in parts:
        f = chosen[part]
        size, fid = int(f["size"]), f["filename"]
        src_path = f"/sites/{src_site}/backups/{backup_timestamp}/files/{fid}/content"
        off = int(done.get(part, 0))
        while off < size:
            n = min(chunk_size, size - off)
            data = await _read_chunk(src_pilot, src_path, off, n, **kw)
            if len(data) != n:
                raise TransferError(f"{fid}: short read at {off}: got {len(data)} of {n}")
            r = await _call(dst_pilot, "PUT", f"/sites/{dst_site}/uploads/{uid}/files/{part}",
                      params={"offset": off}, content=data,
                      headers={"Content-Type": "application/octet-stream"}, **kw)
            raise_for_pilot(r)
            off += n
            sent += n
            done[part] = off
            save()
            if progress:
                progress(sent, total, fid)
    if upload_only:
        # Uploading does not touch the target site; the overwriting restore is left to the caller
        # (e.g. the guarded backups.restore) so approval rules apply.
        return {"upload_id": uid, "parts": list(parts)}
    body = {"parts": list(parts), "upload_id": uid, "skip_failing_patches": skip_failing_patches}
    return raise_for_pilot(await _call(dst_pilot, "POST", f"/sites/{dst_site}/actions/restore",
                                 json=body, headers={"Idempotency-Key": f"xs-{uid}"}, **kw))


async def restore_via_download_links(src_pilot: PilotClient, src_site: str, backup_timestamp: str,
                               dst_pilot: PilotClient, dst_site: str, parts=("database", "public", "private"),
                               skip_failing_patches: bool = False) -> Any:
    """S3 path (needs offsite backup + bench s3 config; not verified live): fetch presigned links and
    ask the target to restore from them. Falls back to the streaming path when no links exist."""
    links = raise_for_pilot(await _call(src_pilot, "GET",
                                  f"/sites/{src_site}/backups/{backup_timestamp}/download-links"))
    urls = links.get("links", links) if isinstance(links, dict) else {}
    if not all(p in urls for p in parts):
        raise TransferError("download-links missing parts; use restore_from_site_backup")
    body = {"parts": list(parts), "backup_urls": {p: urls[p] for p in parts},
            "skip_failing_patches": skip_failing_patches}
    return raise_for_pilot(await _call(dst_pilot, "POST", f"/sites/{dst_site}/actions/restore", json=body))

"""Secure evidence (photo/video) validation and storage for disputes."""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

ROOT_DIR = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = ROOT_DIR / "uploads" / "evidence"

# Hard limits — production-safe for a demo/web service
MAX_FILES = 5
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_VIDEO_BYTES = 25 * 1024 * 1024  # 25 MB

ALLOWED: dict[str, dict[str, Any]] = {
    "image/jpeg": {"kind": "image", "ext": ".jpg", "max": MAX_IMAGE_BYTES, "magic": (b"\xff\xd8\xff",)},
    "image/png": {"kind": "image", "ext": ".png", "max": MAX_IMAGE_BYTES, "magic": (b"\x89PNG\r\n\x1a\n",)},
    "image/webp": {"kind": "image", "ext": ".webp", "max": MAX_IMAGE_BYTES, "magic": (b"RIFF",)},  # RIFF....WEBP
    "video/mp4": {"kind": "video", "ext": ".mp4", "max": MAX_VIDEO_BYTES, "magic": (b"\x00\x00\x00",)},  # ftyp nearby
    "video/webm": {"kind": "video", "ext": ".webm", "max": MAX_VIDEO_BYTES, "magic": (b"\x1a\x45\xdf\xa3",)},
}

# Map client content-type / extension → canonical MIME
_CONTENT_ALIASES = {
    "image/jpg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "image/x-png": "image/png",
}

_SAFE_ID = re.compile(r"^[a-f0-9]{8,32}\.(jpg|png|webp|mp4|webm)$")


def is_valid_evidence_id(file_id: str) -> bool:
    return bool(_SAFE_ID.match(file_id or ""))


def ensure_evidence_dir() -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


def _canonical_mime(content_type: str | None) -> str | None:
    if not content_type:
        return None
    mime = content_type.split(";")[0].strip().lower()
    return _CONTENT_ALIASES.get(mime, mime)


def _magic_ok(mime: str, head: bytes) -> bool:
    meta = ALLOWED[mime]
    if mime == "image/webp":
        return head.startswith(b"RIFF") and b"WEBP" in head[:16]
    if mime == "video/mp4":
        # ISO BMFF: size(4) + 'ftyp' at offset 4
        return len(head) >= 8 and head[4:8] == b"ftyp"
    for sig in meta["magic"]:
        if head.startswith(sig):
            return True
    return False


async def save_evidence_files(files: list[UploadFile] | None) -> list[dict[str, Any]]:
    """Validate and persist uploaded evidence. Returns metadata list (no absolute paths)."""
    if not files:
        return []

    # Filter empty file inputs browsers send
    uploads = [f for f in files if f and f.filename]
    if not uploads:
        return []
    if len(uploads) > MAX_FILES:
        raise HTTPException(400, f"At most {MAX_FILES} evidence files allowed")

    ensure_evidence_dir()
    saved: list[dict[str, Any]] = []

    for upload in uploads:
        mime = _canonical_mime(upload.content_type)
        if not mime or mime not in ALLOWED:
            raise HTTPException(
                400,
                "Unsupported file type. Allowed: JPEG, PNG, WebP, MP4, WebM",
            )
        meta = ALLOWED[mime]
        raw = await upload.read(meta["max"] + 1)
        if len(raw) == 0:
            continue
        if len(raw) > meta["max"]:
            kind = meta["kind"]
            limit_mb = meta["max"] // (1024 * 1024)
            raise HTTPException(400, f"{kind.title()} too large (max {limit_mb} MB)")
        if not _magic_ok(mime, raw[:32]):
            raise HTTPException(400, f"File content does not match declared type ({mime})")

        file_id = f"{secrets.token_hex(12)}{meta['ext']}"
        dest = EVIDENCE_DIR / file_id
        # Write with restrictive permissions
        dest.write_bytes(raw)
        try:
            os.chmod(dest, 0o644)
        except OSError:
            pass

        saved.append(
            {
                "id": file_id,
                "kind": meta["kind"],
                "content_type": mime,
                "size_bytes": len(raw),
                "original_name": (upload.filename or "evidence")[:120],
                "url": f"/evidence/{file_id}",
            }
        )

    return saved


def resolve_evidence_path(file_id: str) -> Path:
    """Resolve a stored evidence id to a path; reject traversal / unknown names."""
    if not is_valid_evidence_id(file_id):
        raise HTTPException(404, "Evidence not found")
    path = (EVIDENCE_DIR / file_id).resolve()
    if not str(path).startswith(str(EVIDENCE_DIR.resolve())):
        raise HTTPException(404, "Evidence not found")
    if not path.is_file():
        raise HTTPException(404, "Evidence not found")
    return path

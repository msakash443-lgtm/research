"""Object storage for PDFs and full text (plan M0.10.2).

Callers store and fetch bytes by *key* — a relative, slash-separated name such as
`projects/<project_id>/sources/<source_id>/fulltext.pdf` — never by filesystem path. The key is what
goes in `Source.fulltext_path`. Only the local-disk backend exists today; an S3-compatible backend can
implement the same `ObjectStore` protocol later without callers changing.

Stored objects are write-once: putting identical bytes again is a no-op, putting different bytes
under an existing key raises `ObjectConflict`. Quotes are verified against stored text (M3.4), so the
text must not change underneath them; a corrected file goes under a new key.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.config import Settings

# One segment: starts with a letter/digit, then letters, digits, '.', '_' or '-'. No '..' anywhere.
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
MAX_KEY_LENGTH = 512
MAX_SEGMENTS = 16


class ObjectStorageError(Exception):
    """Base class for object storage failures."""


class InvalidObjectKey(ObjectStorageError, ValueError):
    """The key is not a safe relative storage key."""


class ObjectNotFound(ObjectStorageError, KeyError):
    """No object is stored under the key."""


class ObjectConflict(ObjectStorageError):
    """Different bytes are already stored under the key (objects are write-once)."""


class ObjectTooLarge(ObjectStorageError):
    """The object is bigger than the configured cap."""


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    sha256: str


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> StoredObject: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def stat(self, key: str) -> StoredObject: ...


def validate_key(key: str) -> str:
    """Return `key` unchanged if it is a safe relative key, else raise `InvalidObjectKey`."""
    if not isinstance(key, str) or not key:
        raise InvalidObjectKey("storage key must be a non-empty string")
    if len(key) > MAX_KEY_LENGTH:
        raise InvalidObjectKey(f"storage key is longer than {MAX_KEY_LENGTH} characters")
    segments = key.split("/")
    if len(segments) > MAX_SEGMENTS:
        raise InvalidObjectKey(f"storage key has more than {MAX_SEGMENTS} segments")
    for segment in segments:
        if not _SEGMENT.fullmatch(segment) or ".." in segment:
            raise InvalidObjectKey(f"invalid storage key segment: {segment!r}")
    return key


def fulltext_key(project_id: uuid.UUID, source_id: uuid.UUID, extension: str) -> str:
    """The key for a source's full text, e.g. `projects/<p>/sources/<s>/fulltext.pdf`."""
    extension = extension.lower().lstrip(".")
    if extension not in {"pdf", "txt", "html", "xml", "json"}:
        raise InvalidObjectKey(f"unsupported full-text extension: {extension!r}")
    return validate_key(f"projects/{uuid.UUID(str(project_id))}/sources/{uuid.UUID(str(source_id))}/fulltext.{extension}")


def _real(path: Path) -> Path:
    """`path.resolve()` without Windows' `\\\\?\\` long-path prefix, which `resolve()` sometimes adds
    while a directory on the path is being created concurrently; left in, it breaks containment checks."""
    text = str(path.resolve())
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[len("\\\\?\\UNC\\") :]
    elif text.startswith("\\\\?\\"):
        text = text[len("\\\\?\\") :]
    return Path(text)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LocalDiskStore:
    """Objects as files under `root`. Writes are atomic (temp file + rename) and write-once."""

    def __init__(self, root: str | os.PathLike[str], max_bytes: int) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        Path(root).mkdir(parents=True, exist_ok=True)
        self.root = _real(Path(root))
        self.max_bytes = max_bytes

    def _path(self, key: str) -> Path:
        path = _real(self.root / validate_key(key))
        # Defence in depth: validate_key already rules out escapes, symlinks could still point out.
        if not path.is_relative_to(self.root):
            raise InvalidObjectKey(f"storage key resolves outside the store: {key!r}")
        return path

    def put(self, key: str, data: bytes) -> StoredObject:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("object data must be bytes")
        if len(data) > self.max_bytes:
            raise ObjectTooLarge(f"object is {len(data)} bytes; the limit is {self.max_bytes}")
        data = bytes(data)
        path = self._path(key)
        stored = StoredObject(key=key, size=len(data), sha256=_digest(data))
        if path.exists():
            return self._same_or_conflict(key, path, stored)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                # os.link fails if the target exists, so a concurrent writer can't be overwritten.
                os.link(tmp_name, path)
            except FileExistsError:
                return self._same_or_conflict(key, path, stored)
        finally:
            Path(tmp_name).unlink(missing_ok=True)
        return stored

    def _same_or_conflict(self, key: str, path: Path, stored: StoredObject) -> StoredObject:
        if _digest(path.read_bytes()) != stored.sha256:
            raise ObjectConflict(f"different content is already stored under {key!r}")
        return stored

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFound(key)
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def stat(self, key: str) -> StoredObject:
        data = self.get(key)
        return StoredObject(key=key, size=len(data), sha256=_digest(data))


def get_object_store(settings: Settings) -> ObjectStore:
    """The configured store. An unknown or not-yet-built backend fails loudly."""
    backend = settings.object_storage_backend
    if backend == "local":
        return LocalDiskStore(settings.object_storage_root, settings.object_storage_max_bytes)
    raise ObjectStorageError(f"object storage backend {backend!r} is not available (only 'local' is implemented)")

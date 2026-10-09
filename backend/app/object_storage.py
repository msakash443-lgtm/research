"""Object storage for PDFs and full text (plan M0.10.2/M0.10.3).

Callers store and fetch bytes by *key* — a relative, slash-separated name such as
`projects/<project_id>/sources/<source_id>/fulltext.pdf` — never by filesystem path. The key is what
goes in `Source.fulltext_path`. Two backends implement the same `ObjectStore` protocol: `LocalDiskStore`
(disk) and `S3Store` (any S3-compatible REST API — AWS S3, MinIO, Cloudflare R2, …).

Stored objects are write-once: putting identical bytes again is a no-op, putting different bytes
under an existing key raises `ObjectConflict`. Quotes are verified against stored text (M3.4), so the
text must not change underneath them; a corrected file goes under a new key.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from app.config import Settings

# One segment: starts with a letter/digit, then lower-case letters, digits, '.', '_' or '-'. No
# uppercase (so two keys differing only in case can never alias on a case-insensitive filesystem,
# e.g. NTFS) and no '..' anywhere. `fulltext_key` only ever produces lower-case UUID segments.
_SEGMENT = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
# Windows device names are reserved regardless of extension or case (`CON`, `con.txt`, `Con.tar.gz`
# all resolve to the same device): https://learn.microsoft.com/windows/win32/fileio/naming-a-file
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{d}" for d in "0123456789"),
    *(f"lpt{d}" for d in "0123456789"),
}
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
        if segment.endswith("."):
            raise InvalidObjectKey(f"storage key segment aliases on Windows (trailing dot): {segment!r}")
        stem = segment.split(".", 1)[0]
        if stem in _WINDOWS_RESERVED:
            raise InvalidObjectKey(f"storage key segment is a reserved Windows device name: {segment!r}")
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


_UNSUPPORTED_CONDITIONAL_STATUSES = frozenset({400, 405, 501})


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _sign_v4(*, method: str, url: str, region: str, access_key: str, secret_key: str, payload_hash: str) -> dict[str, str]:
    """AWS Signature Version 4 headers for one S3 request.

    https://docs.aws.amazon.com/general/latest/gr/sigv4-signing-examples.html — implemented from the
    spec with stdlib `hmac`/`hashlib` only (no AWS SDK dependency). Covers exactly what `S3Store` sends:
    GET/HEAD/PUT with no query string, one path, at most one extra (unsigned) header.
    """
    parts = urlsplit(url)
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    canonical_uri = quote(parts.path, safe="/-_.~") or "/"
    host = parts.netloc
    signed_header_names = "host;x-amz-content-sha256;x-amz-date"
    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
    canonical_request = f"{method}\n{canonical_uri}\n\n{canonical_headers}\n{signed_header_names}\n{payload_hash}"
    credential_scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = (
        f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    )
    signing_key = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret_key}".encode(), date_stamp), region), "s3"), "aws4_request")
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_header_names}, Signature={signature}"
    )
    return {
        "host": host,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
        "Authorization": authorization,
    }


def _safe_body(response: httpx.Response) -> str:
    """A short, header-free excerpt of an error response — never the request we sent (no credentials)."""
    try:
        return response.text[:300]
    except Exception:
        return ""


class S3Store:
    """Objects as keys in an S3-compatible bucket. Write-once like `LocalDiskStore`: same bytes again is
    a no-op, different bytes under an existing key raises `ObjectConflict`.

    Write-once is enforced with a conditional PUT (`If-None-Match: *`), the atomic mechanism AWS S3 and
    most S3-compatible services support. If the backend rejects the header (400/405/501, or
    `conditional_put=False`), falls back to check-then-put, which has the same race window `LocalDiskStore`
    had before M0.10.2's `os.link` fix: a true concurrent write can lose the race and overwrite — acceptable
    for a fallback path, not the default.

    Credentials (`access_key_id`/`secret_access_key`) are held in memory only, signed into each request's
    `Authorization` header (SigV4), and never logged or included in an exception message.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        max_bytes: int,
        path_style: bool = True,
        conditional_put: bool = True,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not endpoint_url or not bucket or not access_key_id or not secret_access_key:
            raise ObjectStorageError(
                "S3 object storage requires endpoint_url, bucket, access_key_id and secret_access_key"
            )
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._endpoint = endpoint_url.rstrip("/")
        self._bucket = bucket
        self._region = region
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self.max_bytes = max_bytes
        self._path_style = path_style
        self._conditional_put = conditional_put
        self._transport = transport

    def _object_url(self, key: str) -> str:
        safe_key = validate_key(key)
        if self._path_style:
            return f"{self._endpoint}/{self._bucket}/{safe_key}"
        parts = urlsplit(self._endpoint)
        host = f"{self._bucket}.{parts.netloc}"
        return urlunsplit((parts.scheme, host, f"/{safe_key}", "", ""))

    def _send(self, method: str, key: str, *, content: bytes | None = None, extra_headers: dict[str, str] | None = None) -> httpx.Response:
        url = self._object_url(key)
        payload_hash = _digest(content or b"")
        headers = _sign_v4(
            method=method, url=url, region=self._region,
            access_key=self._access_key_id, secret_key=self._secret_access_key, payload_hash=payload_hash,
        )
        if extra_headers:
            headers.update(extra_headers)
        with httpx.Client(timeout=30.0, transport=self._transport) as client:
            return client.request(method, url, content=content, headers=headers)

    def _existing_or_conflict(self, key: str, stored: StoredObject) -> StoredObject:
        existing = self.get(key)
        if _digest(existing) != stored.sha256:
            raise ObjectConflict(f"different content is already stored under {key!r}")
        return stored

    def put(self, key: str, data: bytes) -> StoredObject:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("object data must be bytes")
        if len(data) > self.max_bytes:
            raise ObjectTooLarge(f"object is {len(data)} bytes; the limit is {self.max_bytes}")
        data = bytes(data)
        stored = StoredObject(key=key, size=len(data), sha256=_digest(data))
        if self._conditional_put:
            response = self._send("PUT", key, content=data, extra_headers={"If-None-Match": "*"})
            if response.status_code in (200, 201):
                return stored
            if response.status_code == 412:
                return self._existing_or_conflict(key, stored)
            if response.status_code not in _UNSUPPORTED_CONDITIONAL_STATUSES:
                raise ObjectStorageError(f"S3 PUT {key!r} failed: {response.status_code} {_safe_body(response)}")
            # Backend doesn't support conditional PUT; fall through to check-then-put below.
        if self.exists(key):
            return self._existing_or_conflict(key, stored)
        response = self._send("PUT", key, content=data)
        if response.status_code == 412:
            return self._existing_or_conflict(key, stored)
        if response.status_code not in (200, 201):
            raise ObjectStorageError(f"S3 PUT {key!r} failed: {response.status_code} {_safe_body(response)}")
        return stored

    def get(self, key: str) -> bytes:
        response = self._send("GET", key)
        if response.status_code == 404:
            raise ObjectNotFound(key)
        if response.status_code != 200:
            raise ObjectStorageError(f"S3 GET {key!r} failed: {response.status_code} {_safe_body(response)}")
        return response.content

    def exists(self, key: str) -> bool:
        response = self._send("HEAD", key)
        if response.status_code == 404:
            return False
        if response.status_code != 200:
            raise ObjectStorageError(f"S3 HEAD {key!r} failed: {response.status_code}")
        return True

    def stat(self, key: str) -> StoredObject:
        data = self.get(key)
        return StoredObject(key=key, size=len(data), sha256=_digest(data))


def get_object_store(settings: Settings) -> ObjectStore:
    """The configured store. An unknown or not-yet-built backend fails loudly."""
    backend = settings.object_storage_backend
    if backend == "local":
        return LocalDiskStore(settings.object_storage_root, settings.object_storage_max_bytes)
    if backend == "s3":
        secret = settings.object_storage_s3_secret_access_key
        return S3Store(
            endpoint_url=settings.object_storage_s3_endpoint_url or "",
            bucket=settings.object_storage_s3_bucket or "",
            region=settings.object_storage_s3_region,
            access_key_id=settings.object_storage_s3_access_key_id or "",
            secret_access_key=secret.get_secret_value() if secret else "",
            max_bytes=settings.object_storage_max_bytes,
            path_style=settings.object_storage_s3_path_style,
            conditional_put=settings.object_storage_s3_conditional_put,
        )
    raise ObjectStorageError(f"object storage backend {backend!r} is not available (only 'local', 's3' are implemented)")

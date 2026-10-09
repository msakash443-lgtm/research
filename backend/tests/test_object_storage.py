"""Object storage abstraction (plan M0.10.2/M0.10.3): safe keys, write-once writes (local disk and
S3-compatible), atomic local-disk writes."""

import hashlib
import os
import threading
import uuid

import httpx
import pytest

from app.config import Settings
from app.object_storage import (
    InvalidObjectKey,
    LocalDiskStore,
    ObjectConflict,
    ObjectNotFound,
    ObjectStorageError,
    ObjectTooLarge,
    S3Store,
    fulltext_key,
    get_object_store,
    validate_key,
)


@pytest.fixture
def store(tmp_path):
    return LocalDiskStore(tmp_path / "objects", max_bytes=1024)


def test_put_then_get_round_trips_bytes_and_reports_hash(store):
    data = b"%PDF-1.7 fake body"
    stored = store.put("projects/p/sources/s/fulltext.pdf", data)

    assert stored.size == len(data)
    assert stored.sha256 == hashlib.sha256(data).hexdigest()
    assert store.get("projects/p/sources/s/fulltext.pdf") == data
    assert store.exists("projects/p/sources/s/fulltext.pdf")
    assert store.stat("projects/p/sources/s/fulltext.pdf") == stored


def test_missing_object_raises_not_found(store):
    assert not store.exists("nope.txt")
    with pytest.raises(ObjectNotFound):
        store.get("nope.txt")


def test_same_bytes_again_is_a_no_op(store):
    first = store.put("a.txt", b"same")
    assert store.put("a.txt", b"same") == first


def test_different_bytes_under_an_existing_key_are_refused_and_original_kept(store):
    store.put("a.txt", b"original")
    with pytest.raises(ObjectConflict):
        store.put("a.txt", b"changed")
    assert store.get("a.txt") == b"original"


def test_object_over_the_cap_is_refused_and_nothing_written(store, tmp_path):
    with pytest.raises(ObjectTooLarge):
        store.put("big.bin", b"x" * 1025)
    assert not store.exists("big.bin")
    store.put("ok.bin", b"x" * 1024)  # exactly the cap is fine


def test_no_temp_files_left_behind(store):
    store.put("dir/a.txt", b"one")
    with pytest.raises(ObjectConflict):
        store.put("dir/a.txt", b"two")
    leftovers = [name for name in os.listdir(store.root / "dir") if name != "a.txt"]
    assert leftovers == []


def test_non_bytes_data_is_rejected(store):
    with pytest.raises(TypeError):
        store.put("a.txt", "text, not bytes")


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/etc/passwd",
        "../outside.txt",
        "a/../../outside.txt",
        "a/./b.txt",
        "a//b.txt",
        "a/b/",
        "a\\b.txt",
        "C:/windows/x.txt",
        "..",
        "a/..b",
        ".hidden",
        "a/b c.txt",
        "a/\u00e9.txt",
        "a" * 600,
        "/".join(["a"] * 17),
        "A/b.txt",
        "a/B.txt",
        "a.",
        "a/b.",
        "con",
        "CON",
        "con.txt",
        "a/con.tar.gz",
        "nul",
        "prn.pdf",
        "aux",
        "com1",
        "COM9.txt",
        "lpt1",
    ],
)
def test_unsafe_keys_are_rejected(store, key):
    with pytest.raises(InvalidObjectKey):
        validate_key(key)
    with pytest.raises(InvalidObjectKey):
        store.put(key, b"x")
    with pytest.raises(InvalidObjectKey):
        store.get(key)


def test_symlink_escaping_the_root_is_rejected(store, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    store.root.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(outside, store.root / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    with pytest.raises(InvalidObjectKey):
        store.put("link/x.txt", b"x")
    assert list(outside.iterdir()) == []


def test_fulltext_key_shape_and_extension_allow_list():
    project_id, source_id = uuid.uuid4(), uuid.uuid4()
    assert fulltext_key(project_id, source_id, ".PDF") == f"projects/{project_id}/sources/{source_id}/fulltext.pdf"
    with pytest.raises(InvalidObjectKey):
        fulltext_key(project_id, source_id, "exe")
    with pytest.raises(ValueError):
        fulltext_key("../x", source_id, "pdf")


@pytest.mark.parametrize("round_", range(5))
def test_concurrent_writers_of_the_same_key_one_content_wins(store, round_):
    # A nested key, so the writers also race to create its directories: on Windows that once made
    # `resolve()` return a `\\?\` long-path and the key was wrongly refused as escaping the root.
    key = fulltext_key(uuid.uuid4(), uuid.uuid4(), "txt")
    results, errors = [], []

    def write(payload):
        try:
            results.append(store.put(key, payload))
        except ObjectConflict as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(b"A" if i % 2 else b"B",)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winner = store.get(key)
    assert winner in (b"A", b"B")
    assert all(result.sha256 == hashlib.sha256(winner).hexdigest() for result in results)
    assert len(results) + len(errors) == 8 and errors


def test_get_object_store_builds_local_store_from_settings(tmp_path):
    settings = Settings(object_storage_root=str(tmp_path / "s"), object_storage_max_bytes=10)
    store = get_object_store(settings)
    assert isinstance(store, LocalDiskStore)
    with pytest.raises(ObjectTooLarge):
        store.put("a.txt", b"x" * 11)


def test_unknown_backend_fails_loudly(tmp_path):
    settings = Settings(object_storage_backend="azure", object_storage_root=str(tmp_path))
    with pytest.raises(ObjectStorageError, match="not available"):
        get_object_store(settings)


def test_max_bytes_must_be_positive(tmp_path):
    with pytest.raises(ValueError):
        LocalDiskStore(tmp_path, max_bytes=0)
    with pytest.raises(ValueError):
        Settings(object_storage_max_bytes=0)


class _FakeS3Transport(httpx.BaseTransport):
    """A minimal in-memory stand-in for an S3-compatible bucket (path-style): honours `If-None-Match:
    *` on PUT like real S3/MinIO/R2 do, or refuses the header with 501 when `supports_conditional` is
    False, so both of `S3Store.put`'s code paths can be exercised without a network call."""

    def __init__(self, *, supports_conditional: bool = True) -> None:
        self.objects: dict[str, bytes] = {}
        self.supports_conditional = supports_conditional
        self.requests: list[httpx.Request] = []

    def _key(self, request: httpx.Request) -> str:
        _, _, key = request.url.path.lstrip("/").partition("/")
        return key

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers.get("Authorization", "").startswith("AWS4-HMAC-SHA256 Credential=")
        key = self._key(request)
        if request.method == "PUT":
            if request.headers.get("if-none-match") == "*":
                if not self.supports_conditional:
                    return httpx.Response(501, text="NotImplemented")
                if key in self.objects:
                    return httpx.Response(412, text="PreconditionFailed")
            self.objects[key] = request.content
            return httpx.Response(200)
        if request.method == "GET":
            if key not in self.objects:
                return httpx.Response(404, text="NoSuchKey")
            return httpx.Response(200, content=self.objects[key])
        if request.method == "HEAD":
            return httpx.Response(200 if key in self.objects else 404)
        raise AssertionError(f"unexpected method {request.method}")


class _AlwaysFailTransport(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")


def _s3_store(transport, **overrides):
    kwargs = dict(
        endpoint_url="https://s3.example.com",
        bucket="test-bucket",
        region="us-east-1",
        access_key_id="AKIAEXAMPLE",
        secret_access_key="super-secret-value",
        max_bytes=1024,
        transport=transport,
    )
    kwargs.update(overrides)
    return S3Store(**kwargs)


def test_s3_put_then_get_round_trips_bytes_and_reports_hash():
    store = _s3_store(_FakeS3Transport())
    data = b"%PDF-1.7 fake body"
    stored = store.put("projects/p/sources/s/fulltext.pdf", data)

    assert stored.size == len(data)
    assert stored.sha256 == hashlib.sha256(data).hexdigest()
    assert store.get("projects/p/sources/s/fulltext.pdf") == data
    assert store.exists("projects/p/sources/s/fulltext.pdf")
    assert store.stat("projects/p/sources/s/fulltext.pdf") == stored


def test_s3_missing_object_raises_not_found():
    store = _s3_store(_FakeS3Transport())
    assert not store.exists("nope.txt")
    with pytest.raises(ObjectNotFound):
        store.get("nope.txt")


def test_s3_same_bytes_again_is_a_no_op():
    store = _s3_store(_FakeS3Transport())
    first = store.put("a.txt", b"same")
    assert store.put("a.txt", b"same") == first


def test_s3_different_bytes_under_an_existing_key_are_refused_and_original_kept():
    store = _s3_store(_FakeS3Transport())
    store.put("a.txt", b"original")
    with pytest.raises(ObjectConflict):
        store.put("a.txt", b"different")
    assert store.get("a.txt") == b"original"


def test_s3_object_over_the_cap_is_refused_and_nothing_written():
    transport = _FakeS3Transport()
    store = _s3_store(transport, max_bytes=4)
    with pytest.raises(ObjectTooLarge):
        store.put("big.bin", b"too big")
    assert transport.requests == []  # refused before any request was sent
    assert not store.exists("big.bin")


def test_s3_non_bytes_data_is_rejected():
    store = _s3_store(_FakeS3Transport())
    with pytest.raises(TypeError):
        store.put("a.txt", "text, not bytes")


def test_s3_falls_back_to_check_then_put_when_conditional_put_is_unsupported():
    store = _s3_store(_FakeS3Transport(supports_conditional=False))
    first = store.put("a.txt", b"same")
    assert store.put("a.txt", b"same") == first
    with pytest.raises(ObjectConflict):
        store.put("a.txt", b"different")


def test_s3_conditional_put_disabled_skips_the_conditional_attempt_entirely():
    transport = _FakeS3Transport(supports_conditional=True)
    store = _s3_store(transport, conditional_put=False)
    store.put("a.txt", b"x")
    assert all("if-none-match" not in request.headers for request in transport.requests)


def test_s3_error_response_never_leaks_the_secret_key():
    store = _s3_store(_AlwaysFailTransport())
    with pytest.raises(ObjectStorageError) as exc_info:
        store.get("a.txt")
    assert "super-secret-value" not in str(exc_info.value)


def test_s3_construction_requires_every_credential_field():
    for missing in ("endpoint_url", "bucket", "access_key_id", "secret_access_key"):
        kwargs = dict(
            endpoint_url="https://s3.example.com", bucket="b", region="us-east-1",
            access_key_id="a", secret_access_key="s", max_bytes=10,
        )
        kwargs[missing] = ""
        with pytest.raises(ObjectStorageError):
            S3Store(**kwargs)


def test_s3_max_bytes_must_be_positive():
    with pytest.raises(ValueError):
        _s3_store(_FakeS3Transport(), max_bytes=0)


def test_get_object_store_builds_s3_store_from_settings():
    settings = Settings(
        object_storage_backend="s3",
        object_storage_s3_endpoint_url="https://s3.example.com",
        object_storage_s3_bucket="test-bucket",
        object_storage_s3_access_key_id="AKIAEXAMPLE",
        object_storage_s3_secret_access_key="super-secret-value",
    )
    store = get_object_store(settings)
    assert isinstance(store, S3Store)


def test_s3_backend_requires_credentials_at_settings_construction():
    with pytest.raises(ValueError, match="OBJECT_STORAGE_BACKEND=s3 requires"):
        Settings(_env_file=None, object_storage_backend="s3")
    with pytest.raises(ValueError, match="OBJECT_STORAGE_S3_SECRET_ACCESS_KEY"):
        Settings(
            _env_file=None, object_storage_backend="s3",
            object_storage_s3_endpoint_url="https://s3.example.com",
            object_storage_s3_bucket="test-bucket", object_storage_s3_access_key_id="AKIAEXAMPLE",
        )

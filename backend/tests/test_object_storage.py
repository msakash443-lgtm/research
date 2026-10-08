"""Object storage abstraction (plan M0.10.2): safe keys, write-once, atomic local-disk writes."""

import hashlib
import os
import threading
import uuid

import pytest

from app.config import Settings
from app.object_storage import (
    InvalidObjectKey,
    LocalDiskStore,
    ObjectConflict,
    ObjectNotFound,
    ObjectStorageError,
    ObjectTooLarge,
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
    settings = Settings(object_storage_backend="s3", object_storage_root=str(tmp_path))
    with pytest.raises(ObjectStorageError, match="not available"):
        get_object_store(settings)


def test_max_bytes_must_be_positive(tmp_path):
    with pytest.raises(ValueError):
        LocalDiskStore(tmp_path, max_bytes=0)
    with pytest.raises(ValueError):
        Settings(object_storage_max_bytes=0)

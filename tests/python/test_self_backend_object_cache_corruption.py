"""Damaged optional object-cache metadata must fall back to compilation."""

from pathlib import Path

import pytest

from pcc.backend import self_backend_cache_identity as identity_module
from pcc.frontends.python import pipeline_self_backend_cache as cache
from pcc.tools import compiler_cache_retention as retention


@pytest.fixture
def cached_objects(tmp_path, monkeypatch):
    monkeypatch.setenv(cache.OBJECT_CACHE_ENV, "1")
    monkeypatch.setenv(cache.OBJECT_CACHE_IDENTITY_ENV, "corruption-test-compiler")
    monkeypatch.setenv(cache.OBJECT_CACHE_DIR_ENV, str(tmp_path / "cache"))
    monkeypatch.delenv("PCC_CODE_PROFILE", raising=False)
    # Cache identity/configuration are not the boundary under test. The live
    # planner, file copying, checksum validation, publication and leases are.
    monkeypatch.setattr(
        identity_module, "self_backend_emitter_source_identity", lambda: "source"
    )
    monkeypatch.setattr(cache, "_effective_backend_configuration", lambda: "config")
    target = "self-aarch64-darwin-v0"
    items = []
    payloads = []
    for index in range(2):
        stem = tmp_path / ("module_" + str(index))
        ir = stem.with_suffix(".ll")
        ir.write_text(
            "define i64 @answer_" + str(index) + "() {\n"
            "entry:\n ret i64 " + str(index) + "\n}\n",
            encoding="utf-8",
        )
        items.append(
            (str(stem.with_suffix(".result")), str(stem.with_suffix(".pco")), str(ir))
        )
        payloads.append(b"owned-object-" + str(index).encode("ascii"))

    def plan():
        return cache.plan(
            items, target, "pcc-native-pco-v1", str(tmp_path),
            host_python_command=None, plan_host_code="", small_int_decimal=None,
        )

    def publish(planned):
        return cache.publish(
            items, planned, str(tmp_path),
            host_python_command=None, publish_host_code="",
        )

    planned = plan()
    assert [status for _path, status in planned] == ["miss", "miss"]
    for item, payload in zip(items, payloads):
        Path(item[1]).write_bytes(payload)
    assert publish(planned)
    for item in items:
        Path(item[1]).unlink()

    acquired = []
    released = []
    acquire = retention.acquire_entry_lease
    release = retention.release_entry_lease

    def acquire_lease(path):
        lease = acquire(path)
        assert lease and Path(lease).is_file()
        acquired.append(lease)
        return lease

    def release_lease(lease):
        released.append(lease)
        release(lease)

    monkeypatch.setattr(retention, "acquire_entry_lease", acquire_lease)
    monkeypatch.setattr(retention, "release_entry_lease", release_lease)
    return items, payloads, planned, target, plan, publish, acquired, released


@pytest.mark.parametrize("corrupt_index", [0, 1])
@pytest.mark.parametrize(
    "checksum",
    [b"\xff\n", b"not-a-digest\n", b"0" * 64 + b"\n", b""],
    ids=["non-ascii", "malformed", "wrong-digest", "empty"],
)
def test_corrupt_checksum_misses_without_losing_valid_neighbors(
    cached_objects, corrupt_index, checksum,
):
    items, payloads, planned, target, plan, publish, acquired, released = cached_objects
    sidecar = Path(planned[corrupt_index][0] + ".sha256")
    sidecar.write_bytes(checksum)

    observed = plan()

    assert [status for _path, status in observed] == [
        "miss" if index == corrupt_index else "hit" for index in range(2)
    ]
    assert [path for path, _status in observed] == [path for path, _status in planned]
    assert len(acquired) == 2 and acquired == released
    assert all(not Path(lease).exists() for lease in acquired)
    assert sidecar.read_bytes() == checksum
    assert not Path(items[corrupt_index][0]).exists()
    assert not Path(items[corrupt_index][1]).exists()
    valid_index = 1 - corrupt_index
    assert Path(items[valid_index][1]).read_bytes() == payloads[valid_index]
    assert Path(items[valid_index][0]).read_text().splitlines() == [
        target, items[valid_index][1], "hit",
    ]

    # A normal miss publication replaces the bad sidecar; no manual eviction
    # is needed, and the next lookup validates both entries as ordinary hits.
    replacement = b"replacement-owned-object"
    Path(items[corrupt_index][1]).write_bytes(replacement)
    assert publish(observed)
    for item in items:
        Path(item[1]).unlink()
        Path(item[0]).unlink(missing_ok=True)
    assert [status for _path, status in plan()] == ["hit", "hit"]
    assert Path(items[corrupt_index][1]).read_bytes() == replacement
    assert Path(items[valid_index][1]).read_bytes() == payloads[valid_index]
    assert len(acquired) == 4 and acquired == released
    assert all(not Path(lease).exists() for lease in acquired)


@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
def test_unexpected_copy_errors_propagate_and_release_the_reader_lease(
    cached_objects, monkeypatch, error_type,
):
    _items, _payloads, _planned, _target, plan, _publish, acquired, released = cached_objects

    def fail_copy(_source, _destination):
        raise error_type("unexpected copy failure")

    monkeypatch.setattr(cache, "_copy_digest", fail_copy)
    with pytest.raises(error_type, match="unexpected copy failure"):
        plan()
    assert len(acquired) == 1 and acquired == released
    assert not Path(acquired[0]).exists()

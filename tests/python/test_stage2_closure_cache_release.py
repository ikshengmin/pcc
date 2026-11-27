"""The selfhost-only last-use release preserves direct scanner contracts."""
from pathlib import Path

import pytest
from pcc.frontends.python import pipeline, pipeline_import_scan as scan

NAMES = (
    "_DISCOVERY_TEXT_CACHE",
    "_WITHOUT_TYPE_CHECKING_CACHE",
    "_WITHOUT_IMPLEMENTATION_CACHE",
)
SOURCE = (
    "from typing import TYPE_CHECKING\n"
    "if TYPE_CHECKING:\n"
    "    import only_typing\n"
    "import sys\n"
    "if sys.implementation.name == 'cpython':\n"
    "    import only_cpython\n"
    "import actual_runtime\n"
    "text = 'import hidden' # irrelevant\n"
)


@pytest.fixture(autouse=True)
def preserve_source_cache_state():
    objects = [getattr(scan, name) for name in NAMES]
    previous = [dict(obj) for obj in objects]
    for obj in objects:
        obj.clear()
    yield
    for obj, contents in zip(objects, previous):
        obj.clear()
        obj.update(contents)


def populate(source=SOURCE):
    return (
        scan._source_import_discovery_text(source),
        scan._without_type_checking_imports(source),
        scan._without_inactive_implementation_imports(source),
    )


def test_release_preserves_identity_and_recomputes_exact_content():
    expected = populate()
    objects = [getattr(scan, name) for name in NAMES]
    entries = [dict(obj) for obj in objects]
    assert all(entries)
    assert "only_typing" not in expected[1]
    assert "only_cpython" not in expected[2]
    assert scan.release_owned_coordinator_lexical_caches() is True
    assert all(getattr(scan,name) is obj and not obj
               for name,obj in zip(NAMES,objects))
    assert populate() == expected
    assert [dict(obj) for obj in objects] == entries
    assert populate(SOURCE + "import new_runtime\n") != expected
    # Calling a direct reader still uses its ordinary memoization.
    assert scan._source_import_discovery_text(SOURCE) is objects[0][SOURCE]


@pytest.mark.parametrize("name", NAMES)
def test_release_leaves_all_objects_unchanged_if_any_was_replaced(monkeypatch, name):
    populate()
    objects = [getattr(scan, item) for item in NAMES]
    expected = [dict(obj) for obj in objects]
    sentinel = {"foreign": "owner"}
    monkeypatch.setattr(scan, name, sentinel)
    assert scan.release_owned_coordinator_lexical_caches() is False
    assert sentinel == {"foreign": "owner"}
    assert [dict(obj) for obj in objects] == expected


def test_only_owned_selfhost_route_can_release(monkeypatch, tmp_path):
    owned = tmp_path / "pcc"
    owned.mkdir()
    real = owned / "__main__.py"
    real.write_text("")
    other = tmp_path / "__main__.py"
    other.write_text("")
    monkeypatch.setattr(pipeline, "_PCC_DIR", str(owned))
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "1")
    calls = []
    monkeypatch.setattr(scan, "release_owned_coordinator_lexical_caches",
                        lambda: not calls.append("release"), raising=False)
    release = pipeline._release_selfhost_coordinator_lexical_caches
    assert release([str(real)], ["pcc.__main__"], "pcc.__main__", "on", "self", False, None)
    assert not release([str(other)], ["pcc.__main__"], "pcc.__main__", "on", "self", False, None)
    assert not release([str(real)], ["pcc.__main__"], "pcc.__main__", "off", "self", False, None)
    assert not release([str(real)], ["pcc.__main__"], "pcc.__main__", "on", "llvm", False, None)
    assert not release([str(real)], ["pcc.__main__"], "pcc.__main__", "on", "self", True, None)
    assert not release([str(real)], ["pcc.__main__"], "ordinary", "on", "self", False, None)
    assert not release([str(real)], ["other"], "pcc.__main__", "on", "self", False, None)
    assert not release([], ["pcc.__main__"], "pcc.__main__", "on", "self", False, None)
    assert not release([str(real)], ["pcc.__main__"], "pcc.__main__", "on", "self", False, "x86_64-unknown-linux-gnu")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    assert not release([str(real)], ["pcc.__main__"], "pcc.__main__", "on", "self", False, None)
    assert calls == ["release"]

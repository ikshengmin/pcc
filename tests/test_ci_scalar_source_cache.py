"""Contracts for read-only scalar source templates and isolated executions."""

import ast
import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def scalar():
    # Load a fresh helper/cache per test without importing the PCC package.
    path = ROOT / "tests/python/test_scalar_arena_projections.py"
    spec = importlib.util.spec_from_file_location("scalar_source_cache_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_cache_reuses_text_and_observes_same_metadata_edits(scalar, tmp_path, monkeypatch):
    monkeypatch.setattr(scalar, "ROOT", tmp_path)
    path = tmp_path / "source.py"
    path.write_text("value = 1\n")
    before = path.stat()
    first = scalar._tree("source.py")
    assert scalar._tree("source.py") is first
    assert scalar._source_tree.cache_info().misses == 1
    assert scalar._source_tree.cache_info().hits == 1
    path.write_text("value = 2\n")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size
    second = scalar._tree("source.py")
    assert second is not first
    assert first.body[0].value.value == 1
    assert second.body[0].value.value == 2
    assert scalar._source_tree.cache_info().misses == 2
    path.unlink()
    with pytest.raises(FileNotFoundError):
        scalar._tree("source.py")


def test_source_cache_is_bounded_and_rejects_invalid_edits(scalar, tmp_path, monkeypatch):
    monkeypatch.setattr(scalar, "ROOT", tmp_path)
    path = tmp_path / "source.py"
    for value in range(20):
        path.write_text(f"value = {value}\n")
        assert scalar._tree("source.py").body[0].value.value == value
    assert scalar._source_tree.cache_info().maxsize == 16
    assert scalar._source_tree.cache_info().currsize == 16
    path.write_text("def broken(\n")
    with pytest.raises(SyntaxError):
        scalar._tree("source.py")


def test_extracted_functions_are_fresh_and_keep_last_definition(scalar, tmp_path, monkeypatch):
    monkeypatch.setattr(scalar, "ROOT", tmp_path)
    path = tmp_path / "source.py"
    path.write_text("class First:\n    def getter(self): return 1\n"
                    "class Last:\n    def getter(self): return 2\n")
    template = ast.dump(scalar._tree("source.py"), include_attributes=True)
    first = scalar._function("source.py", "getter")
    second = scalar._function("source.py", "getter")
    assert first is not second
    assert first.body[0].value.value == second.body[0].value.value == 2
    first.body[0].value.value = 99
    scalar._execute_nodes([first], {})
    assert second.body[0].value.value == 2
    assert ast.dump(scalar._tree("source.py"), include_attributes=True) == template
    assert scalar._function("source.py", "getter").body[0].value.value == 2
    reference = tmp_path / "reference.json"
    reference.write_text(json.dumps({"functions": {"source.py:getter": {
        "source": "def getter(self): return 3\n"}}}))
    monkeypatch.setattr(scalar, "REFERENCE", reference)
    original = scalar._function("source.py", "getter", original=True)
    original.body[0].value.value = 99
    assert scalar._function("source.py", "getter", original=True).body[0].value.value == 3


def test_namespaces_and_probes_do_not_mutate_cached_templates(scalar):
    paths = {scalar.KERNEL, "pcc/backend/self_backend_value_arena.py",
             scalar.SITES[0]["path"]}
    before = {path: ast.dump(scalar._tree(path), include_attributes=True) for path in paths}
    left, right = scalar._namespace(), scalar._namespace()
    for name in ("CompilerInt2", "CompilerInt4", "CompilerIntArena", "IndexedFunctionKernel"):
        assert left[name] is not right[name]
    first = scalar._probe(scalar.SITES[0], left)
    second = scalar._probe(scalar.SITES[0], right)
    assert first is not second
    assert first.__globals__ is left
    assert second.__globals__ is right
    left["IndexedFunctionKernel"].cache_contract_marker = True
    assert not hasattr(right["IndexedFunctionKernel"], "cache_contract_marker")
    assert {path: ast.dump(scalar._tree(path), include_attributes=True) for path in paths} == before

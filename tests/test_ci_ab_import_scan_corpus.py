"""Common, baseline-compatible contracts for the executable scanner corpus.

The reference test executes only CPython. The discovery test delegates to real
closure functions without parsing/codegen workers or compiler execution. Native
program execution, bytes, linkage and timing remain the bounded A/B owner's job.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "benchmarks" / "ci_ab" / "import_scan"
PREFIX = "benchmarks/ci_ab/import_scan/"
SCALES = {"n": 32, "4n": 128, "16n": 512}
NAMES = (
    "negative_n.py", "positive_n.py",
    "positive_4n.py", "negative_4n.py",
    "negative_16n.py", "positive_16n.py",
)
URL_OUTPUT = "a%20b%2Fc\nd%20e%2Ff\n"
FALLBACK_IMPORT = "    from urllib.parse import quote as fallback_quote\n"


def _manifest():
    return json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))


def _expected(name):
    if name == "warmup.py":
        return "6\n" + URL_OUTPUT
    scale = name.removesuffix(".py").split("_", 1)[1]
    count = SCALES[scale]
    return str(count * (count + 1) // 2) + "\n" + URL_OUTPUT


def test_corpus_manifest_has_exact_shared_v1_contract():
    manifest = _manifest()
    assert set(manifest) == {"schema", "warmup", "inputs"}
    assert manifest["schema"] == "pcc.ci-ab-inputs.v1"
    assert manifest["warmup"] == PREFIX + "warmup.py"
    rows = manifest["inputs"]
    assert isinstance(rows, list) and len(rows) == 6
    assert [row["path"] for row in rows] == [PREFIX + name for name in NAMES]
    # The harness alternates arm order by pair-index parity. Neither marker
    # class may be perfectly confounded with one arm running first.
    for kind in ("negative", "positive"):
        assert {index % 2 for index, name in enumerate(NAMES, 1)
                if name.startswith(kind + "_")} == {0, 1}
    hashes = set()
    for row, name in zip(rows, NAMES):
        assert set(row) == {"path", "expected_output"}
        assert row["expected_output"] == _expected(name)
        assert row["expected_output"].endswith("\n")
        assert "\x00" not in row["expected_output"]
        path = ROOT / row["path"]
        assert path.is_file() and not path.is_symlink()
        assert path.parent == CORPUS
        hashes.add(hashlib.sha256(path.read_bytes()).hexdigest())
    assert len(hashes) == 6


@pytest.mark.parametrize("name", NAMES)
def test_scaled_inputs_execute_real_arithmetic_and_public_imports(name):
    source = (CORPUS / name).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert isinstance(tree.body[1], ast.ImportFrom)
    assert tree.body[1].module == "urllib.parse"
    assert [(alias.name, alias.asname) for alias in tree.body[1].names] == [
        ("quote", None),
    ]
    compute = tree.body[2]
    assert isinstance(compute, ast.FunctionDef) and compute.name == "compute"
    assert ast.dump(compute.body[0], include_attributes=False) == ast.dump(
        ast.parse("total = 0").body[0], include_attributes=False,
    )
    count = SCALES[name.removesuffix(".py").split("_", 1)[1]]
    assert len(compute.body) == count + 2
    for value, statement in enumerate(compute.body[1:-1], 1):
        expected = ast.parse("total = total + " + str(value)).body[0]
        assert ast.dump(statement, include_attributes=False) == ast.dump(
            expected, include_attributes=False,
        )
    assert ast.dump(compute.body[-1], include_attributes=False) == ast.dump(
        ast.parse("return total").body[0], include_attributes=False,
    )
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert any(isinstance(call.func, ast.Name) and call.func.id == "compute"
               for call in calls)
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) and
                   (isinstance(node, ast.Import) or node.module != "urllib.parse")
                   for node in ast.walk(tree))
    handlers = [node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)]
    if name.startswith("negative_"):
        assert "except AttributeError" not in source
        assert handlers == []
    else:
        assert source.count("except AttributeError:") == 1
        assert len(handlers) == 1
        assert isinstance(handlers[0].type, ast.Name)
        assert handlers[0].type.id == "AttributeError"
        assert isinstance(handlers[0].body[0], ast.ImportFrom)
        assert FALLBACK_IMPORT in source
        assert any(isinstance(node, ast.Raise) for node in ast.walk(tree))


@pytest.mark.parametrize("name", ("warmup.py",) + NAMES)
def test_corpus_filter_keeps_runtime_source_and_masks_real_handler_import(name):
    from pcc.frontends.python import pipeline_import_scan

    source = (CORPUS / name).read_text(encoding="utf-8")
    expected = source.replace(FALLBACK_IMPORT, "\n")
    filtered = pipeline_import_scan._without_attribute_error_handler_imports(source)
    assert filtered == expected
    # No object-identity assertion: the common gate must pass the unchanged arm.
    assert "from urllib.parse import quote\n" in filtered
    if not name.startswith("negative_"):
        assert FALLBACK_IMPORT in source and FALLBACK_IMPORT not in filtered
        assert "print(fallback_quote(" in filtered


@pytest.mark.parametrize("name", ("negative_n.py", "positive_n.py"))
def test_flattened_entry_reaches_real_top_level_and_owned_package_scans(
    name, tmp_path, monkeypatch,
):
    from pcc.frontends.python import pipeline
    from pcc.frontends.python import pipeline_dependency_closure as closure

    # Reproduce the entry-only freeze, rather than letting adjacent corpus files
    # accidentally supply a missing dependency. The provider must be unchanged
    # repository source, just as both measurement arms use their shared snapshot.
    source = (CORPUS / name).read_text(encoding="utf-8")
    entry = tmp_path / "pair1.py"
    entry.write_text(source, encoding="utf-8")
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PCC_SOURCE_ROOT", str(ROOT))
    monkeypatch.setenv("PCC_REPO_ROOT", str(ROOT))
    monkeypatch.setenv("HOME", str(empty_home))
    monkeypatch.delenv("PCC_PY_STDLIB_ROOT", raising=False)
    monkeypatch.delenv("PCC_PACKAGE_SITE", raising=False)
    original_filter = closure._without_attribute_error_handler_imports
    original_top = closure._top_level_import_targets
    original_package = closure._package_import_targets
    owner = []
    observations = []

    def record_filter(text):
        if owner:
            observations.append((owner[-1], text))
        return original_filter(text)

    def record_top(root_dir, text, **kwargs):
        owner.append(("top", ""))
        try:
            return original_top(root_dir, text, **kwargs)
        finally:
            owner.pop()

    def record_package(src_path, module_name, **kwargs):
        owner.append(("package", str(Path(src_path).resolve())))
        try:
            return original_package(src_path, module_name, **kwargs)
        finally:
            owner.pop()

    # Transparent observers: every original scanner and resolver still runs.
    monkeypatch.setattr(closure, "_without_attribute_error_handler_imports", record_filter)
    monkeypatch.setattr(closure, "_top_level_import_targets", record_top)
    monkeypatch.setattr(closure, "_package_import_targets", record_package)
    sources, modules, recursive = pipeline._prepare_single_source_compile_closure(
        str(entry), "pair1", emit_llvm_only=False, ir_scaffold_mode="on",
        recursive_stdlib=False, python_library=False,
    )
    assert recursive is True
    sources, modules = closure._prepare_multi_source_compile_closure(
        sources, modules, recursive_stdlib=recursive, ir_scaffold_mode="on",
    )
    actual = {name: Path(path).resolve() for path, name in zip(sources, modules)}
    assert actual == {
        "pair1": entry.resolve(),
        "urllib": (ROOT / "pcc/stdlib/urllib/__init__.py").resolve(),
        "urllib.parse": (ROOT / "pcc/stdlib/urllib/parse.py").resolve(),
    }
    assert (("top", ""), source) in observations
    for module_name in ("urllib", "urllib.parse"):
        path = actual[module_name]
        assert (("package", str(path)), path.read_text(encoding="utf-8")) in observations


@pytest.mark.parametrize("name", ("warmup.py",) + NAMES)
def test_corpus_matches_cpython_reference(name):
    """Reference stdout is checked by a real isolated CPython subprocess."""
    assert sys.implementation.name == "cpython"
    result = subprocess.run(
        [sys.executable, "-I", str(CORPUS / name)],
        check=False, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == _expected(name)
    assert result.stderr == ""

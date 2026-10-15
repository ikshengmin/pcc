"""Host-only freshness contracts for relocated compiled module origins."""

import hashlib
from pathlib import Path

import pytest

from pcc.backend import self_backend_cache_identity as backend_identity
from pcc.driver import bootstrap_cache_identity as bootstrap_identity
from pcc.tools import runtime_archive_provenance as provenance


def _source(root, *, frontend="frontend = 1\n"):
    paths = set(backend_identity.MACHO_LINK_SOURCE_PATHS) | {
        "pcc/__init__.py", "pcc/stdlib/__init__.py", "pcc/frontends/python/pipeline.py",
        "pcc/backend/self_backend_dispatch.py",
        "pcc/driver/bootstrap_cache_identity.py",
        "pcc/backend/self_backend_cache_identity.py",
        "pcc/tools/runtime_archive_provenance.py", "pcc/tools/ir_to_obj.py",
        "pcc/backend/owned_object_emit.py", "scripts/bootstrap.py",
    }
    for relative in paths:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# synthetic identity input: " + relative + "\n")
    (root / "pcc/frontends/python/pipeline.py").write_text(frontend)
    return root


def _expected(root):
    emitter = hashlib.sha256()
    emitter.update((root / "pcc/tools/ir_to_obj.py").read_bytes())
    emitter.update((root / "pcc/backend/owned_object_emit.py").read_bytes())
    emitter.update(backend_identity.macho_linker_source_identity(root).encode("ascii"))
    value = bootstrap_identity.bootstrap_source_sha256(root) + ":" + emitter.hexdigest()
    return hashlib.sha256(value.encode("ascii")).hexdigest()


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.delenv("PCC_SOURCE_ROOT", raising=False)
    monkeypatch.delenv("PCC_REPO_ROOT", raising=False)
    monkeypatch.setattr(provenance, "_CODEGEN_CHECKSUM_CACHE", {})
    return _source(tmp_path / "source")


def _origins(monkeypatch, root):
    for module, relative in (
        (provenance, "pcc/tools/runtime_archive_provenance.py"),
        (bootstrap_identity, "pcc/driver/bootstrap_cache_identity.py"),
        (backend_identity, "pcc/backend/self_backend_cache_identity.py"),
    ):
        monkeypatch.setattr(module, "__file__", str(root / relative))


@pytest.mark.parametrize("selection", ["source-repo", "source-package", "repo", "both"])
def test_explicit_root_relocates_all_codegen_hash_inputs(source, tmp_path, monkeypatch, selection):
    expected = _expected(source)
    _origins(monkeypatch, tmp_path / "missing-compiled-origin")
    if selection.startswith("source") or selection == "both":
        monkeypatch.setenv("PCC_SOURCE_ROOT", str(source / "pcc" if selection == "source-package" else source))
    if selection in ("repo", "both"):
        monkeypatch.setenv("PCC_REPO_ROOT", str(source))
    assert provenance.codegen_checksum() == expected
    assert not provenance.manifest_is_stale_for_current_codegen({"members": [{"codegen_checksum": expected}]})
    for invalid in ("unknown", "f" * 64, ""):
        assert provenance.manifest_is_stale_for_current_codegen({"members": [{"codegen_checksum": invalid}]})


def test_no_override_preserves_module_source_identity(source, monkeypatch):
    expected = _expected(source)
    _origins(monkeypatch, source)
    assert provenance.codegen_checksum() == expected


@pytest.mark.parametrize("selection", ["source", "repo", "conflict", "incomplete", "file"])
def test_invalid_explicit_root_cannot_reuse_warm_cache(source, tmp_path, monkeypatch, selection):
    _origins(monkeypatch, source)
    expected = provenance.codegen_checksum()
    assert expected == _expected(source)
    other = tmp_path / "other"
    if selection == "conflict":
        _source(other)
        monkeypatch.setenv("PCC_SOURCE_ROOT", str(source))
        monkeypatch.setenv("PCC_REPO_ROOT", str(other))
    else:
        if selection == "incomplete":
            (other / "pcc").mkdir(parents=True)
            (other / "pcc/__init__.py").write_text("")
        elif selection == "file":
            other.write_text("not a source directory\n")
        monkeypatch.setenv("PCC_REPO_ROOT" if selection == "repo" else "PCC_SOURCE_ROOT", str(other))
    assert provenance.codegen_checksum() == "unknown"
    assert provenance.manifest_is_stale_for_current_codegen({"members": [{"codegen_checksum": expected}]})


def test_cache_is_bound_to_selected_source_root(source, tmp_path, monkeypatch):
    other = _source(tmp_path / "other", frontend="frontend = 2\n")
    _origins(monkeypatch, tmp_path / "missing-compiled-origin")
    monkeypatch.setenv("PCC_SOURCE_ROOT", str(source))
    first = provenance.codegen_checksum()
    monkeypatch.setenv("PCC_SOURCE_ROOT", str(other))
    second = provenance.codegen_checksum()
    assert first == _expected(source)
    assert second == _expected(other)
    assert first != second
    assert provenance.manifest_is_stale_for_current_codegen({"members": [{"codegen_checksum": first}]})


@pytest.mark.parametrize("relative", [
    "pcc/tools/ir_to_obj.py", "pcc/backend/owned_object_emit.py",
    backend_identity.MACHO_LINK_SOURCE_PATHS[0],
])
def test_missing_emitter_input_remains_unknown(source, tmp_path, monkeypatch, relative):
    expected = _expected(source)
    (source / relative).unlink()
    _origins(monkeypatch, tmp_path / "missing-compiled-origin")
    monkeypatch.setenv("PCC_SOURCE_ROOT", str(source))
    assert provenance.codegen_checksum() == "unknown"
    assert provenance.manifest_is_stale_for_current_codegen({"members": [{"codegen_checksum": expected}]})


def test_package_and_repository_overrides_may_name_same_source(source, tmp_path, monkeypatch):
    _origins(monkeypatch, tmp_path / "missing-compiled-origin")
    monkeypatch.setenv("PCC_SOURCE_ROOT", str(source / "pcc"))
    monkeypatch.setenv("PCC_REPO_ROOT", str(source))
    assert provenance.codegen_checksum() == _expected(source)


def test_missing_origin_without_override_remains_unknown(source, tmp_path, monkeypatch):
    _origins(monkeypatch, tmp_path / "missing-compiled-origin")
    assert provenance.codegen_checksum() == "unknown"
    assert provenance.manifest_is_stale_for_current_codegen({"members": [{"codegen_checksum": _expected(source)}]})


def test_identical_source_at_another_root_preserves_digest(source, tmp_path, monkeypatch):
    other = _source(tmp_path / "other")
    monkeypatch.setenv("PCC_SOURCE_ROOT", str(source))
    first = provenance.codegen_checksum()
    monkeypatch.setenv("PCC_SOURCE_ROOT", str(other))
    assert provenance.codegen_checksum() == first == _expected(other)


def test_unknown_identity_does_not_poison_repaired_source(source, monkeypatch):
    monkeypatch.setenv("PCC_SOURCE_ROOT", str(source))
    path = source / "pcc/tools/ir_to_obj.py"
    original = path.read_bytes()
    path.unlink()
    assert provenance.codegen_checksum() == "unknown"
    path.write_bytes(original)
    assert provenance.codegen_checksum() == _expected(source)


@pytest.mark.parametrize("selection", ["source", "source-package", "repo", "repo-package", "both"])
def test_selected_native_path_provider_hashes_relocated_source(source, tmp_path, monkeypatch, selection):
    from pcc.stdlib.pathlib import Path as NativePath

    expected = _expected(source)
    _origins(monkeypatch, tmp_path / "missing-compiled-origin")
    for module in (provenance, bootstrap_identity, backend_identity):
        monkeypatch.setattr(module, "Path", NativePath)
    if selection.startswith("source") or selection == "both":
        monkeypatch.setenv("PCC_SOURCE_ROOT", str(source / "pcc" if selection == "source-package" else source))
    if selection.startswith("repo") or selection == "both":
        monkeypatch.setenv("PCC_REPO_ROOT", str(source / "pcc" if selection == "repo-package" else source))
    assert provenance.codegen_checksum() == expected


@pytest.mark.parametrize("provider", ["host", "pcc"])
@pytest.mark.parametrize("selection", ["source-alias", "package-alias", "repo-alias"])
def test_symlink_aliases_select_one_codegen_root(source, tmp_path, monkeypatch, provider, selection):
    from pcc.stdlib.pathlib import Path as NativePath

    expected = _expected(source)
    alias = tmp_path / "source-alias"
    alias.symlink_to(source, target_is_directory=True)
    _origins(monkeypatch, tmp_path / "missing-compiled-origin")
    if provider == "pcc":
        # Interpret the actual selected Path provider; this is not native
        # execution. Its POSIX resolve() only performs lexical normalization.
        for module in (provenance, bootstrap_identity, backend_identity):
            monkeypatch.setattr(module, "Path", NativePath)
    if selection == "repo-alias":
        monkeypatch.setenv("PCC_SOURCE_ROOT", str(source))
        monkeypatch.setenv("PCC_REPO_ROOT", str(alias))
    else:
        monkeypatch.setenv("PCC_SOURCE_ROOT", str(alias / "pcc" if selection == "package-alias" else alias))
        monkeypatch.setenv("PCC_REPO_ROOT", str(source))
    assert provenance.codegen_checksum() == expected
    assert provenance._CODEGEN_CHECKSUM_CACHE["root"] == str(source)
    assert not provenance.manifest_is_stale_for_current_codegen({"members": [{"codegen_checksum": expected}]})


def test_literal_tilde_matches_runtime_source_path_interpretation(source, tmp_path, monkeypatch):
    literal = _source(tmp_path / "~" / "source")
    _origins(monkeypatch, tmp_path / "missing-compiled-origin")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "different-home"))
    monkeypatch.setenv("PCC_SOURCE_ROOT", "~/source")
    assert provenance.codegen_checksum() == _expected(literal)


def test_lexical_parent_is_normalized_before_path_resolution(source, tmp_path, monkeypatch):
    expected = _expected(source)
    _origins(monkeypatch, tmp_path / "missing-compiled-origin")
    raw = str(tmp_path / "link" / ".." / "source")
    original_path = provenance.Path
    seen = []

    def normalized_path(value):
        seen.append(str(value))
        assert str(value) != raw, "discovery normalizes lexical parents before resolving"
        return original_path(value)

    monkeypatch.setattr(provenance, "Path", normalized_path)
    monkeypatch.setenv("PCC_SOURCE_ROOT", raw)
    assert provenance.codegen_checksum() == expected
    assert str(source) in seen

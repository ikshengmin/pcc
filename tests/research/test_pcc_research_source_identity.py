import json
import sys
from types import SimpleNamespace

import pytest

from scripts.research.pcc_ttf_verified import run_fuzz
from scripts.research.pcc_ttf_verified.pcc_adapter import PCCAdapter
from scripts.research.pcc_ttf_verified.source_identity import (
    git_blob,
    observe_source,
)

check_source = observe_source


@pytest.fixture(autouse=True)
def isolated_pcc_modules():
    """Synthetic roots must not inherit the compiler loaded by conftest.

    Keep observe_source's real sys.modules check intact. Each foreign-import
    test installs its own conflicting module after this isolation, so an
    unrelated ambient import cannot make the negative control pass. Restore
    the original module objects before the next test or fixture runs.
    """
    original = {name: module for name, module in sys.modules.items()
                if name == "pcc" or name.startswith("pcc.")}
    with pytest.MonkeyPatch.context() as patch:
        for name in original:
            patch.delitem(sys.modules, name)
        yield patch
    restored = {name: module for name, module in sys.modules.items()
                if name == "pcc" or name.startswith("pcc.")}
    assert restored.keys() == original.keys()
    assert all(restored[name] is module for name, module in original.items())


def source_fixture(tmp_path):
    package = tmp_path / "pcc"
    package.mkdir()
    source = package / "__init__.py"
    source.write_text("# fixed source\n")
    manifest = {"revision": "a" * 40,
                "files": {"pcc/__init__.py": git_blob(source.read_bytes())}}
    return source, manifest


def test_source_manifest_refuses_changed_or_extra_files(tmp_path):
    source, manifest = source_fixture(tmp_path)
    assert check_source(tmp_path, manifest)["verified_source_files"] == 1
    source.write_text("# changed source\n")
    with pytest.raises(ValueError, match="mismatch"):
        check_source(tmp_path, manifest)


def test_source_manifest_refuses_untracked_file(tmp_path):
    source, manifest = source_fixture(tmp_path)
    (source.parent / "extra.py").write_text("# unexpected\n")
    with pytest.raises(ValueError, match="mismatch"):
        check_source(tmp_path, manifest)


def test_source_manifest_refuses_partial_revision(tmp_path):
    _, manifest = source_fixture(tmp_path)
    manifest["revision"] = "a48e408e"
    with pytest.raises(ValueError, match="full source revision"):
        check_source(tmp_path, manifest)


def test_source_manifest_refuses_path_escape(tmp_path):
    _, manifest = source_fixture(tmp_path)
    manifest["files"] = {"pcc/../../outside.py": "0" * 40}
    with pytest.raises(ValueError, match="invalid PCC"):
        check_source(tmp_path, manifest)


@pytest.mark.parametrize("module_name", ["pcc", "pcc.research_foreign"])
def test_source_manifest_refuses_foreign_import(tmp_path, isolated_pcc_modules, module_name):
    _, manifest = source_fixture(tmp_path)
    isolated_pcc_modules.setitem(
        sys.modules, module_name,
        SimpleNamespace(__file__="/different/pcc/foreign.py"),
    )
    with pytest.raises(ValueError, match="different source root"):
        check_source(tmp_path, manifest)


def test_unidentified_source_never_claims_a_revision(tmp_path):
    package = tmp_path / "pcc"
    package.mkdir()
    (package / "__init__.py").write_text("# source\n")
    assert observe_source(tmp_path)["revision"] is None


def test_fuzz_report_uses_actual_adapter_identity(tmp_path, monkeypatch):
    identity = {"revision": "b" * 40, "root": "/selected/source", "files": {}}
    class InertAdapter:
        def __init__(self, source_root, source_manifest):
            assert source_root == "selected"
            assert source_manifest == {"chosen": True}

        def lower(self, source):
            return {"accepted": True, "timings_ns": {"test_inert": 1}}, "test IR"

        def verify_source_identity(self):
            return identity

    monkeypatch.setattr(run_fuzz, "PCCAdapter", InertAdapter)
    report = run_fuzz.run(tmp_path, count=1, depth=0, source_root="selected",
                          source_manifest={"chosen": True})
    assert report["source_commit"] == identity["revision"]
    assert report["source_identity"] == identity
    assert json.loads((tmp_path / "summary.json").read_text())["source_commit"] == "b" * 40


def test_adapter_rejects_source_drift_without_importing_pcc(tmp_path):
    source, manifest = source_fixture(tmp_path)
    adapter = PCCAdapter.__new__(PCCAdapter)
    adapter.source_root = tmp_path
    adapter.source_manifest = None
    adapter.source_identity = observe_source(tmp_path)
    source.write_text("# drift\n")
    with pytest.raises(ValueError, match="changed during"):
        adapter.verify_source_identity()


@pytest.mark.parametrize("module_name", ["pcc", "pcc.research_foreign"])
def test_adapter_rejects_foreign_import_before_loading_compiler(tmp_path, isolated_pcc_modules, module_name):
    _, manifest = source_fixture(tmp_path)
    isolated_pcc_modules.setitem(
        sys.modules, module_name,
        SimpleNamespace(__file__="/different/pcc/__init__.py"),
    )
    with pytest.raises(ValueError, match="different source root"):
        PCCAdapter(tmp_path, manifest)

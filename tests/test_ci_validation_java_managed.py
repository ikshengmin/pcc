"""Source-native companion policy; no forged workflow provenance substitutes for its hosted run."""
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

from pcc.driver.native_provisioning import NativeProvisioningError
from scripts import ci_validation_java_managed as managed
from scripts import ci_validation_plan as plan
from scripts.ci_validation_inventory import classify

ROOT=Path(__file__).resolve().parents[1]
NATIVE="tests/runtime_shared/test_java_reference_abi.py"
JDK="tests/java/test_source_managed_reference_runtime.py"


def test_java_abi_observer_and_independent_jdk_each_have_one_real_owner():
    registry=json.loads((ROOT/"scripts/data/ci_validation_references.json").read_bytes())["functions"]
    groups=json.loads((ROOT/"scripts/data/ci_validation_shared.json").read_bytes())["groups"]
    assert [key for key, members in groups.items() if NATIVE in members] == ["abi"]
    assert classify(NATIVE, "test_managed_reference_native_abi_restores_real_roots", True, registry) == "integration"
    assert classify(JDK, "test_unchanged_managed_reference_originals_in_selected_jdk", True, registry) == "reference"
    assert managed.needed("shared","integration")
    assert not any(managed.needed(d,l) for d in plan.LANGUAGES for l in plan.LANES)
    assert not any(managed.needed("shared",l) for l in ("ordinary","reference"))
    for path in (JDK,"pcc/frontends/java/features_only.py","pcc/frontends/java/source_native_references.py"):
        requested=plan.select([("M",path)])["domains"]
        assert "java" in requested
        assert ("shared" in requested) == (path in plan.JAVA_MANAGED_ABI_INPUTS)
    for path in ("pcc/frontends/java/source_native_statics.py", "tests/java/StaticArrayObserve.c",
                 "tests/java/test_source_native_static_arrays.py", "tests/java/test_source_constants.py",
                 "tests/java/test_source_class_initialization_model.py", "tests/java/test_source_native_arrays.py",
                 "tests/java/test_source_static_member_classes.py"):
        assert {"java", "shared"} <= set(plan.select([("M", path)])["domains"])


def test_native_reservation_stops_the_new_explicit_producer_before_any_output(tmp_path, monkeypatch):
    monkeypatch.setenv("PCC_TEST_NO_NATIVE_PROVISIONING","1")
    out=tmp_path/"absent"
    with pytest.raises(NativeProvisioningError):
        managed.prepare_object(ROOT,out)
    assert not out.exists()
    with pytest.raises(NativeProvisioningError):
        managed.produce(ROOT,tmp_path/"not-a-bundle",out)
    assert not out.exists()


def test_consumer_does_not_rebuild_absent_or_extra_input_files(tmp_path,monkeypatch):
    monkeypatch.setattr(managed,"prepare_object",lambda *a:pytest.fail("consumer invoked producer"))
    with pytest.raises(ValueError,match="exactly one object"):
        managed.admit(ROOT,tmp_path,{})
    for n in (managed.OBJECT,managed.RECORD,managed.MANIFEST,"extra"):
        (tmp_path/n).write_bytes(b"untrusted")
    with pytest.raises(ValueError,match="exactly one object"):
        managed.admit(ROOT,tmp_path,{})
    assert all(p.read_bytes()==b"untrusted" for p in tmp_path.iterdir())


@pytest.mark.pcc_gate(probe=lambda: os.name=="posix")
def test_artifact_directory_and_member_symlinks_are_never_consumed(tmp_path):
    src=tmp_path/"input";src.mkdir()
    alias=tmp_path/"alias";alias.symlink_to(src, target_is_directory=True)
    with pytest.raises(ValueError,match="regular managed input directory"):
        managed.admit(ROOT,alias,{})
    # bundle.regular_file rejects symlinks even if the material is in-tree.
    original=src/"actual";original.write_bytes(b"not accepted")
    (src/managed.OBJECT).symlink_to(original)
    with pytest.raises(ValueError,match="symlink"):
        managed.checked_file(src,managed.OBJECT,managed.MAX_OBJECT)


def test_consumer_rejects_even_type_coercions_in_a_binding_manifest(tmp_path,monkeypatch):
    # Pure comparison model; no object production/admission or CI credentials are claimed.
    expected={"schema":"contract-unit-test","gc_backend":0,"lease":{"run_attempt":1}}
    monkeypatch.setattr(managed,"binding",lambda *a:expected)
    for name in (managed.OBJECT, managed.RECORD):
        (tmp_path/name).write_bytes(b"unit-model")
    for wrong in ({"gc_backend":False},{"gc_backend":0.0},{"lease":{"run_attempt":True}},{"extra":0}):
        (tmp_path/managed.MANIFEST).write_text(json.dumps(dict(expected,**wrong)))
        with pytest.raises(ValueError,match="differs"):
            managed.admit(ROOT,tmp_path,{})


@pytest.mark.parametrize("directory,native,ambient", [
    (None, {"owner":"unit-test"}, None), ("missing", None, None), ("missing", {"owner":"unit-test"}, "untrusted.o")
])
def test_missing_or_ambient_companion_keeps_observation_mandatory_without_usable_input(tmp_path,directory,native,ambient):
    selected=None if directory is None else tmp_path/directory
    state,child,error=managed.consumer_inputs(ROOT,selected,native,ambient)
    assert state is None and error.startswith("ValueError:")
    assert child == {"PCC_CI_REQUIRE_JAVA_MANAGED_INPUT":"1","PCC_JAVA_MANAGED_OBJECT":""}


@pytest.mark.parametrize("search_dir,module_name", [
    (ROOT/"scripts","ci_validation_java_managed"), (ROOT,"scripts.ci_validation_java_managed")])
def test_unrelated_lane_import_needs_no_installed_compiler(search_dir,module_name,tmp_path):
    # -I -S drops environment PYTHONPATH, site handles, editable installation
    # and the current directory. This is an import-shape control, not a CI lease.
    command=[sys.executable,"-I","-S","-c",
             "import sys,importlib;sys.path.insert(0,sys.argv[1]);"
             "m=importlib.import_module(sys.argv[2]);"
             "assert not m.needed('java','ordinary') and 'pcc' not in sys.modules",
             str(search_dir),module_name]
    run=subprocess.run(command,cwd=tmp_path,capture_output=True,timeout=5)
    assert (run.returncode,run.stdout,run.stderr)==(0,b"",b"")

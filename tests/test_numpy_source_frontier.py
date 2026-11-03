"""Source/receipt contract tests. Compiler attempts belong to the explicit CI runner."""
import hashlib
import json
from pathlib import Path

import pytest

from scripts import numpy_source_frontier as frontier


def manifest():
    return json.loads(frontier.MANIFEST.read_text())


def test_numpy_frontier_pins_full_original_tus_not_scalar_extractions():
    lock = manifest()
    assert lock["version"] == "2.4.4"
    assert lock["translation_units"] == {
        "c": "numpy/_core/src/multiarray/alloc.c",
        "cxx": "numpy/_core/src/npymath/halffloat.cpp",
    }
    assert lock["git_tree"] == "7ecab3aabe64431c219f35f3d65fb2d75edbe286"
    assert lock["upstream_archive_sha256"] is None
    for name in lock["translation_units"].values():
        assert len(lock["sha256"][name]) == 64
    assert "vendored-meson/meson/COPYING" in lock["sha256"]


def test_host_frontier_cannot_promote_production_stages():
    report = frontier.initial_report(manifest())
    assert report["production_qualified"] is False
    assert all(row["status"] == "UNRUN" for row in report["stages"].values())
    assert report["host_pcc_development"] == []
    assert report["limits"] == {"per_process_tree_seconds": 90, "tree_rss_bytes": 1024 ** 3}


@pytest.mark.parametrize("name", ["../outside", "/outside", "a//b", "a/./b", "a\\b", "C:/a", ""])
def test_source_paths_reject_noncanonical_spelling(tmp_path, name):
    with pytest.raises(ValueError, match="canonical"):
        frontier.source_path(tmp_path, name)


def test_compile_command_selects_public_pcc_object_route_without_cc(tmp_path):
    source = tmp_path / "halffloat.cpp"
    output = tmp_path / "halffloat.o"
    command = frontier.compile_command(source, output, [tmp_path / "headers"], interpreter="/owned-host/python")
    assert command == [
        "/owned-host/python", "-m", "pcc", "--backend=self", "--no-cache", "--emit-obj", str(output),
        "--cpp-arg=-I" + str(tmp_path / "headers"), str(source),
    ]
    assert not source.exists()  # Command construction never rewrites upstream input.


def test_native_missing_receipt_never_calls_a_compiler():
    report = frontier.initial_report(manifest())
    frontier.check_native(None, None, report)
    assert report["stages"]["native_pcc1"]["status"] == "BLOCKED"
    assert report["stages"]["numpy_import"]["status"] == "UNRUN"
    assert report["production_qualified"] is False


@pytest.mark.parametrize("receipt", [
    {"schema": "pcc.platform-bootstrap.v1", "source": {"commit": "current"},
     "stages": [{"stage": 1, "sha256": "fake-static-compiler"}]},
    {"schema": "pcc.platform-bootstrap.v1", "source": {"commit": "old-source"},
     "stages": [{"stage": 1, "sha256": "old-compiler"}]},
    {"schema": "pcc.platform-bootstrap.v1", "source": {"commit": "current"},
     "stages": [{"stage": 1, "sha256": "tampered-compiler"}], "qualified": True},
])
def test_fake_stale_or_tampered_receipts_cannot_admit_or_execute_a_compiler(
    tmp_path, monkeypatch, receipt,
):
    compiler = tmp_path / "pcc1"
    compiler.write_bytes(b"\x7fELF-not-a-proven-current-source-pcc1")
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt))
    monkeypatch.setattr(frontier, "digest", lambda *a: pytest.fail("partial hash admission"))
    monkeypatch.setattr(frontier.subprocess, "run", lambda *a, **kw: pytest.fail("unadmitted launch"))
    monkeypatch.setattr(frontier.subprocess, "Popen", lambda *a, **kw: pytest.fail("unadmitted launch"))
    report = frontier.initial_report(manifest())
    frontier.check_native(compiler, receipt_path, report)
    stage = report["stages"]["native_pcc1"]
    assert stage["status"] == "BLOCKED"
    assert stage["compiler_supplied"] and stage["receipt_supplied"]
    assert "not admitted or executed" in stage["reason"]
    assert "command" not in stage and "process" not in stage
    assert report["production_qualified"] is False
    assert report["stages"]["build_scripts_meson"]["status"] == "BLOCKED"


def fake_source(tmp_path, monkeypatch):
    source = tmp_path / "project"
    source.mkdir()
    payload = b"int original(void) { return 7; }\n"
    (source / "original.c").write_bytes(payload)
    sha256 = hashlib.sha256(payload).hexdigest()
    blob = hashlib.sha1(b"blob " + str(len(payload)).encode() + b"\0" + payload).hexdigest()
    entry = b"100644 blob " + blob.encode() + b"\toriginal.c\0"
    def git(command, **kwargs):
        return "pinned-tree\n" if command[1] == "rev-parse" else entry
    monkeypatch.setattr(frontier.subprocess, "check_output", git)
    lock = {"directory": "project", "git_tree": "pinned-tree",
            "sha256": {"original.c": sha256}, "source_scope": "test source",
            "upstream_archive_sha256": None}
    return source, lock


def test_full_tree_verifier_checks_original_bytes(tmp_path, monkeypatch):
    source, lock = fake_source(tmp_path, monkeypatch)
    result = frontier.verify_source(tmp_path, lock)
    assert result["file_count"] == 1
    (source / "original.c").write_text("int replaced(void) { return 0; }\n")
    with pytest.raises(ValueError, match="unchanged source"):
        frontier.verify_source(tmp_path, lock)


def test_unrecorded_generated_header_is_not_an_owned_input(tmp_path, monkeypatch):
    source, lock = fake_source(tmp_path, monkeypatch)
    (source / "_numpyconfig.h").write_text("#define GUESS 1\n")
    with pytest.raises(ValueError, match="unlocked source/generated"):
        frontier.verify_source(tmp_path, lock)


def test_environment_blocks_external_tool_discovery_and_keeps_locale(tmp_path, monkeypatch):
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("PCC_SOURCE_ROOT", "/old/source")
    monkeypatch.setenv("PCC_REPO_ROOT", "/old/repository")
    env = frontier.environment(tmp_path, tmp_path)
    assert env["LC_ALL"] == "C.UTF-8"
    assert Path(env["PATH"]).is_dir()
    assert list(Path(env["PATH"]).iterdir()) == []
    assert env["CC"] == env["CXX"] == env["PCC_HOST_PYTHON"]
    assert not Path(env["CC"]).exists()
    assert env["PCC_SELF_OBJ"] == env["PCC_SELF_LINK"] == "pcc"
    assert env["PCC_DISABLE_COMPILE_CACHE"] == "1"
    assert env["PCC_SOURCE_ROOT"] == env["PCC_REPO_ROOT"] == str(tmp_path)
    assert frontier.os.environ["PCC_SOURCE_ROOT"] == "/old/source"
    assert frontier.os.environ["PCC_REPO_ROOT"] == "/old/repository"

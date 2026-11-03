"""Source/receipt contract tests. Compiler attempts belong to the explicit CI runner."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

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
    def git(root, arguments, **kwargs):
        if arguments == ["rev-parse", "--verify", "HEAD^{commit}"]:
            return "pinned-commit\n"
        if arguments == ["rev-parse", "pinned-commit:project"]:
            return "pinned-tree\n"
        if arguments == ["rev-parse", "--path-format=absolute", "--git-path", "info/attributes"]:
            return str(tmp_path / "absent-info-attributes") + "\n"
        if arguments == ["ls-tree", "-r", "-z", "pinned-tree"]:
            return entry
        if arguments == ["check-attr", "--source=pinned-commit", "-z", "--stdin", "--all"]:
            assert kwargs["input"] == b"project/original.c\0"
            return b""
        pytest.fail("unexpected Git command: " + repr(arguments))
    monkeypatch.setattr(frontier, "git_source", git)
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


def git_fixture(tmp_path, attributes="*.bat text eol=crlf\n"):
    """CI-only real Git checkout inputs; no compiler or imported fixture code."""
    root = tmp_path / "repository"
    root.mkdir()
    env = dict(os.environ, GIT_ATTR_NOSYSTEM="1", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull)

    def git(*arguments):
        return subprocess.check_output(
            ["git", "-c", "core.attributesFile=" + os.devnull, *arguments],
            cwd=root, env=env, timeout=30,
        )

    git("init", "-q")
    git("config", "user.name", "PCC fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "core.autocrlf", "false")
    source = root / "project"
    source.mkdir()
    (source / ".gitattributes").write_text(attributes)
    original = b"@echo off\necho original\n"
    (source / "original.bat").write_bytes(original)
    git("add", "project")
    git("commit", "-qm", "pinned original source")
    tree = git("rev-parse", "HEAD:project").decode().strip()
    lock = {"directory": "project", "git_tree": tree,
            "sha256": {"original.bat": hashlib.sha256(original).hexdigest()},
            "source_scope": "test source", "upstream_archive_sha256": None}
    return root, source, lock, original, git


@pytest.mark.parametrize("attributes", ("*.bat text eol=crlf\n", "*.bat eol=crlf\n"))
def test_git_pinned_text_checkout_preserves_canonical_and_working_byte_identities(tmp_path, attributes):
    root, source, lock, original, git = git_fixture(tmp_path, attributes)
    canonical = frontier.verify_source(root, lock)
    assert canonical["checkout_transformations"] == []
    assert canonical["checkout_content_sha256"] == canonical["content_sha256"]
    path = source / "original.bat"
    path.unlink()
    git("checkout", "HEAD", "--", "project/original.bat")
    checked_out = path.read_bytes()
    assert checked_out == original.replace(b"\n", b"\r\n")
    converted = frontier.verify_source(root, lock)
    assert path.read_bytes() == checked_out  # Verification never rewrites it.
    assert converted["git_tree"] == canonical["git_tree"]
    assert converted["content_sha256"] == canonical["content_sha256"]
    assert converted["checkout_content_sha256"] != canonical["checkout_content_sha256"]
    row, = converted["checkout_transformations"]
    assert row == {
        "path": "original.bat", "kind": "git-text-eol-crlf",
        "git_blob": frontier.blob_identity(original),
        "canonical_sha256": hashlib.sha256(original).hexdigest(),
        "checkout_sha256": hashlib.sha256(checked_out).hexdigest(),
    }
    assert frontier.verify_source(root, lock) == converted


@pytest.mark.parametrize("changed", [
    b"@echo off\r\necho edited\r\n",  # A real edit is not a checkout conversion.
    b"@echo off\r\necho original\n",  # Mixed endings are not the forward image.
    b"@echo off\r\r\necho original\r\n",
    b"@echo off\r\necho original",  # Missing final newline is an edit too.
])
def test_text_checkout_never_normalizes_away_unexpected_bytes(tmp_path, changed):
    root, source, lock, _original, _git = git_fixture(tmp_path)
    path = source / "original.bat"
    path.write_bytes(changed)
    with pytest.raises(ValueError, match="unchanged source"):
        frontier.verify_source(root, lock)
    assert path.read_bytes() == changed


@pytest.mark.parametrize("attributes", ("*.bat -text\n", "*.bat text eol=lf\n",
                                        "*.bat text=auto eol=crlf\n", ""))
def test_crlf_difference_requires_a_supported_explicit_pinned_text_rule(tmp_path, attributes):
    root, source, lock, original, _git = git_fixture(tmp_path, attributes)
    (source / "original.bat").write_bytes(original.replace(b"\n", b"\r\n"))
    with pytest.raises(ValueError, match="unchanged source"):
        frontier.verify_source(root, lock)


@pytest.mark.parametrize("attribute", ("filter=unowned", "filter=unspecified", "filter=unset", "-filter",
                                      "working-tree-encoding=UTF-8", "ident", "-ident", "crlf"))
def test_even_unchanged_files_cannot_authorize_unsupported_checkout_filters(tmp_path, attribute):
    root, _source, lock, _original, git = git_fixture(tmp_path, "*.bat text eol=crlf " + attribute + "\n")
    # A configured filter must never be invoked to obtain canonical bytes.
    git("config", "filter.unowned.smudge", "false")
    git("config", "filter.unowned.clean", "false")
    git("config", "filter.unowned.required", "true")
    with pytest.raises(ValueError, match="unsupported Git"):
        frontier.verify_source(root, lock)


def test_working_attributes_cannot_authorize_a_different_checkout(tmp_path):
    root, source, lock, original, _git = git_fixture(tmp_path, "*.bat text eol=lf\n")
    (source / ".gitattributes").write_text("*.bat text eol=crlf\n")
    (source / "original.bat").write_bytes(original.replace(b"\n", b"\r\n"))
    with pytest.raises(ValueError, match="unchanged source"):
        frontier.verify_source(root, lock)


def test_untracked_info_attributes_are_not_pinned_authority(tmp_path):
    root, _source, lock, _original, _git = git_fixture(tmp_path)
    (root / ".git/info/attributes").write_text("*.bat text eol=crlf\n")
    with pytest.raises(ValueError, match="untracked Git info/attributes"):
        frontier.verify_source(root, lock)


def test_exact_source_tree_is_still_required_before_attribute_admission(tmp_path):
    root, _source, lock, _original, _git = git_fixture(tmp_path)
    lock["git_tree"] = "0" * 40
    with pytest.raises(ValueError, match="source tree drift"):
        frontier.verify_source(root, lock)


def test_git_source_disables_external_attributes_and_object_replacements(tmp_path, monkeypatch):
    def read(command, **kwargs):
        assert command == ["git", "--no-replace-objects", "-c", "core.attributesFile=" + os.devnull,
                           "check-attr", "--source=pinned", "-z", "--stdin", "text"]
        assert kwargs["env"]["GIT_ATTR_NOSYSTEM"] == "1"
        return b""

    monkeypatch.setattr(frontier.subprocess, "check_output", read)
    frontier.git_source(tmp_path, ["check-attr", "--source=pinned", "-z", "--stdin", "text"])


def test_real_pinned_numpy_attributes_authorize_its_original_documentation_batch_file():
    lock = manifest()
    commit = frontier.git_source(frontier.ROOT, ["rev-parse", "--verify", "HEAD^{commit}"], text=True).strip()
    tree = frontier.git_source(frontier.ROOT, ["rev-parse", commit + ":" + lock["directory"]], text=True).strip()
    assert tree == lock["git_tree"]
    path = lock["directory"] + "/doc/make.bat"
    values = frontier.checkout_attributes(frontier.ROOT, commit, [path])[path]
    assert values["text"] == "set" and values["eol"] == "crlf"


def test_missing_source_attribute_capability_fails_without_fallback(tmp_path, monkeypatch):
    calls = []

    def read(root, arguments, **kwargs):
        calls.append(arguments)
        if arguments[0] == "rev-parse":
            return str(tmp_path / "absent-info-attributes") + "\n"
        raise subprocess.CalledProcessError(129, arguments, stderr=b"unknown option source")

    monkeypatch.setattr(frontier, "git_source", read)
    with pytest.raises(ValueError, match="git check-attr --source is required"):
        frontier.checkout_attributes(tmp_path, "pinned", ["project/original.bat"])
    assert len(calls) == 2 and calls[1][1] == "--source=pinned"


def test_canonical_fallback_bytes_must_match_the_exact_pinned_blob(tmp_path, monkeypatch):
    original = b"original\n"
    monkeypatch.setattr(frontier, "git_source", lambda *args, **kwargs: b"tampered\n")
    with pytest.raises(ValueError, match="canonical Git blob identity mismatch"):
        frontier.canonical_source_bytes(tmp_path, "original.bat", b"100644",
                                        frontier.blob_identity(original), b"original\r\n",
                                        {"text": "set", "eol": "crlf"})


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

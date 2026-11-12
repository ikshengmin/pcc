"""Host-only identity contracts, never native proof (docs/compiler-contract.md).

Synthetic native headers and mocked runtime admission exercise export guards;
no compiler/runtime process is executed and no binary is exported.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess

import pytest

from scripts import bootstrap_platform as producer
from scripts import ci_failed_stage_identity as diagnostic
from scripts import ci_stage1_bundle as bundle
from scripts import ci_validation_native_inputs as native_inputs
from tests.python.test_platform_bootstrap_shared import _fake_platform

COMMIT = "a" * 40
TREE = "b" * 40
SOURCE = "c" * 64
PRIVATE = "private-recovery-token-not-for-artifacts"
SOURCE_IDENTITY = {"commit": COMMIT, "source_sha256": SOURCE, "clean_commit": True,
                   "source_scope": "compiler-runtime-headers-build-harness-v1", "source_file_count": 1467}
TARGETS = {"linux-x86_64": "x86_64-unknown-linux-gnu", "linux-aarch64": "aarch64-unknown-linux-gnu",
           "windows-x86_64": "x86_64-pc-windows-msvc"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def native_header(platform_id):
    data = bytearray(512)
    if platform_id.startswith("linux"):
        data[:7] = b"\x7fELF\x02\x01\x01"
        struct.pack_into("<HHI", data, 16, 2, 183 if platform_id.endswith("aarch64") else 62, 1)
        struct.pack_into("<H", data, 52, 64)
    else:
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 60, 128)
        data[128:132] = b"PE\0\0"
        struct.pack_into("<HH", data, 132, 0x8664, 1)
        struct.pack_into("<HHH", data, 148, 240, 2, 0x20b)
    # Deliberately unsafe *input* bytes prove no payload/string copying occurs.
    return bytes(data) + b"/home/private/recovery/" + PRIVATE.encode()


@pytest.fixture
def env(monkeypatch):
    values = {"GITHUB_REPOSITORY": "owner/pcc", "GITHUB_SHA": COMMIT,
              "GITHUB_WORKFLOW_REF": "owner/pcc/.github/workflows/pcc1-package-parity.yml@refs/heads/master",
              "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("UNRELATED_PRIVATE_SECRET", PRIVATE)
    monkeypatch.setattr(diagnostic, "_tree", lambda root, commit: TREE)
    return values


@pytest.fixture
def fixture(tmp_path, monkeypatch, env):
    def make(platform_id="linux-x86_64", phase="canary_compile", returncode=-6):
        root = tmp_path / platform_id
        suffix = ".exe" if platform_id.startswith("windows") else ""
        compiler = root / "build/platform-qualification/shared" / ("pcc1" + suffix)
        out = root / "build/platform-qualification/gc1"
        runtime = root / bundle.RUNTIME
        for parent in (compiler.parent, out, runtime.parent):
            parent.mkdir(parents=True, exist_ok=True)
        compiler.write_bytes(native_header(platform_id))
        compiler.chmod(0o755)
        runtime_identity = {"path": str(runtime)}
        for ending, key in (("", "sha256"), (".provenance.json", "provenance_sha256"),
                            (".capi_syms", "capi_inventory_sha256")):
            path = Path(str(runtime) + ending)
            path.write_bytes(("synthetic private fixture " + PRIVATE + ending).encode())
            runtime_identity[key] = sha(path)
        incomplete = {"stage": 1, "sha256": sha(compiler), "canary_status": "pending", "reused": True,
                      "image": {"path": str(compiler), "file_size": compiler.stat().st_size},
                      "command": ["/home/private/" + PRIVATE], "log": "/private/log"}
        source = dict(SOURCE_IDENTITY, private_path="/home/private/" + PRIVATE)
        target = TARGETS[platform_id]
        context = diagnostic.capture(root, source, target)
        # Synthetic original failure bytes, not a reusable native receipt.
        (out / "failure.json").write_text(json.dumps({
            "source": source, "stages": [], "incomplete_stage": incomplete, "error": PRIVATE,
            diagnostic.ASSOCIATION_FIELD: context[diagnostic.ASSOCIATION_FIELD],
        }, indent=2), encoding="utf-8")
        command = ([str(compiler), str(out / "canary.py"), "-o", str(out / ("canary1" + suffix)),
                    "--backend", "self"] if phase == "canary_compile" else [str(out / ("canary1" + suffix))])
        error = subprocess.CalledProcessError(returncode, command, output=PRIVATE, stderr=PRIVATE)
        monkeypatch.setattr(diagnostic, "_verify_runtime", lambda *args: None)
        return {"context": context, "root": root, "out_dir": out, "source": source,
                "current_source": lambda: source, "target": target, "gc": "1", "incomplete": incomplete,
                "runtime_identity": runtime_identity, "phase": phase, "command": command, "error": error}
    return make


def output(case):
    return case["out_dir"] / "failure-identity.json"


def emit(case):
    case = dict(case)
    context = case.pop("context")
    return diagnostic.record(context, **case)


@pytest.mark.parametrize("platform_id, code, exit_code, signal", [
    ("linux-x86_64", -6, None, 6), ("linux-aarch64", -11, None, 11),
    ("linux-x86_64", 1, 1, None), ("windows-x86_64", -1073740791, 3221226505, None),
    ("windows-x86_64", 3221226505, 3221226505, None),
])
@pytest.mark.parametrize("phase", ["canary_compile", "canary_run"])
def test_hash_only_identity_has_exact_fields_and_no_payload(fixture, platform_id, code, exit_code, signal, phase):
    case = fixture(platform_id, phase, code)
    before = {p: p.read_bytes() for p in case["root"].rglob("*") if p.is_file()}
    assert emit(case)
    raw = output(case).read_bytes()
    value = json.loads(raw)
    assert diagnostic.validate_identity(value) == value
    assert set(value) == {"schema", "status", "qualified", "admissible", "producer", "source",
                          "platform", "target", "stage", "gc", "failure", "hashes"}
    assert value["schema"] == "pcc.failed-stage-identity.v2"
    assert value["status"] == "FAIL" and value["qualified"] is value["admissible"] is False
    assert value["source"] == dict(SOURCE_IDENTITY, tree_sha1=TREE)
    assert value["failure"] == {"phase": phase, "returncode": code, "exit_code": exit_code, "signal": signal}
    assert value["hashes"] == {
        "compiler_sha256": case["incomplete"]["sha256"],
        "runtime_sha256": case["runtime_identity"]["sha256"],
        "runtime_provenance_sha256": case["runtime_identity"]["provenance_sha256"],
        "capi_inventory_sha256": case["runtime_identity"]["capi_inventory_sha256"],
        "failure_receipt_sha256": sha(case["out_dir"] / "failure.json"),
    }
    assert all(p.read_bytes() == data for p, data in before.items())
    assert {p for p in case["root"].rglob("*") if p.is_file()} - set(before) == {output(case)}
    assert not any(token in raw for token in (PRIVATE.encode(), str(case["root"]).encode(),
                   b"/home/", b"/private/", b"recovery", b"stderr", b"command", b"source_path"))
    assert len(raw) < 4096
    if os.name != "nt":
        assert output(case).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("fault", ["dirty", "source_commit", "source_hash", "scope", "count", "tree",
                                  "repository", "run", "attempt", "lease", "target"])
def test_capture_fails_closed_without_complete_run_source_tree_identity(tmp_path, monkeypatch, env, fault):
    source = dict(SOURCE_IDENTITY)
    target = TARGETS["linux-x86_64"]
    if fault == "dirty": source["clean_commit"] = False
    elif fault == "source_commit": source["commit"] = "d" * 40
    elif fault == "source_hash": source["source_sha256"] = PRIVATE
    elif fault == "scope": source["source_scope"] = PRIVATE
    elif fault == "count": source["source_file_count"] = True
    elif fault == "tree": monkeypatch.setattr(diagnostic, "_tree", lambda *args: (_ for _ in ()).throw(PermissionError()))
    elif fault == "repository": monkeypatch.setenv("GITHUB_REPOSITORY", "owner/../../private")
    elif fault == "run": monkeypatch.setenv("GITHUB_RUN_ID", "0")
    elif fault == "attempt": monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "-1")
    elif fault == "lease": monkeypatch.delenv("GITHUB_RUN_ID")
    elif fault == "target": target = "arm64-apple-darwin"
    assert diagnostic.capture(tmp_path, source, target) is None


@pytest.mark.parametrize("fault", [
    "source", "current_source", "dirty", "tree", "repo", "run", "attempt", "event", "target", "gc",
    "phase", "partial", "later_stage", "bool_stage", "old_canary", "canary_pass", "missing_image", "image_path",
    "traversal", "runtime_path", "missing_capi_hash", "bad_image_hash", "image_bytes", "runtime_bytes",
    "provenance_bytes", "capi_bytes", "missing_file", "image_size", "runtime_verify", "native_header", "child_command", "other_child",
    "zero", "timeout", "cancelled", "missing_context",
])
def test_wrong_or_incomplete_identity_is_never_published(fixture, monkeypatch, fault):
    case = fixture()
    if fault == "source": case["source"]["source_sha256"] = "e" * 64
    elif fault == "current_source": case["current_source"] = lambda: dict(SOURCE_IDENTITY, source_sha256="e" * 64)
    elif fault == "dirty": case["current_source"] = lambda: dict(SOURCE_IDENTITY, clean_commit=False)
    elif fault == "tree": monkeypatch.setattr(diagnostic, "_tree", lambda *args: "e" * 40)
    elif fault == "repo":
        monkeypatch.setenv("GITHUB_REPOSITORY", "other/pcc")
        monkeypatch.setenv("GITHUB_WORKFLOW_REF", "other/pcc/.github/workflows/pcc1-package-parity.yml@refs/heads/master")
    elif fault == "run": monkeypatch.setenv("GITHUB_RUN_ID", "124")
    elif fault == "attempt": monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "3")
    elif fault == "event": monkeypatch.setenv("GITHUB_SHA", "e" * 40)
    elif fault == "target": case["target"] = TARGETS["linux-aarch64"]
    elif fault == "gc": case["gc"] = "../gc1"
    elif fault == "phase": case["phase"] = "compiler_build"
    elif fault == "partial": case["incomplete"] = None
    elif fault == "later_stage": case["incomplete"]["stage"] = 2
    elif fault == "bool_stage": case["incomplete"]["stage"] = True
    elif fault == "old_canary": case["incomplete"]["canary"] = {"source_sha256": "a" * 64}
    elif fault == "canary_pass": case["incomplete"]["canary_status"] = "PASS"
    elif fault == "missing_image": case["incomplete"].pop("image")
    elif fault == "image_path": case["incomplete"]["image"]["path"] = "/private/pcc1"
    elif fault == "traversal": case["out_dir"] = case["out_dir"] / ".." / "gc1"
    elif fault == "runtime_path": case["runtime_identity"]["path"] = "/private/runtime.a"
    elif fault == "missing_capi_hash": case["runtime_identity"].pop("capi_inventory_sha256")
    elif fault == "bad_image_hash": case["incomplete"]["sha256"] = PRIVATE
    elif fault == "image_bytes": Path(case["incomplete"]["image"]["path"]).write_bytes(b"corrupt")
    elif fault in {"runtime_bytes", "provenance_bytes", "capi_bytes"}:
        ending = {"runtime_bytes": "", "provenance_bytes": ".provenance.json", "capi_bytes": ".capi_syms"}[fault]
        Path(case["runtime_identity"]["path"] + ending).write_bytes(b"corrupt")
    elif fault == "missing_file": Path(case["runtime_identity"]["path"]).unlink()
    elif fault == "image_size": case["incomplete"]["image"]["file_size"] = 1
    elif fault == "runtime_verify": monkeypatch.setattr(diagnostic, "_verify_runtime", lambda *a: (_ for _ in ()).throw(ValueError()))
    elif fault == "native_header":
        Path(case["incomplete"]["image"]["path"]).write_bytes(b"not native")
        case["incomplete"]["sha256"] = sha(Path(case["incomplete"]["image"]["path"]))
        case["incomplete"]["image"]["file_size"] = len(b"not native")
    elif fault == "child_command": case["command"] = ["different-child"]
    elif fault == "other_child": case["error"].cmd = ["different-child"]
    elif fault == "zero": case["error"].returncode = 0
    elif fault == "timeout": case["error"] = TimeoutError(PRIVATE)
    elif fault == "cancelled": case["error"] = RuntimeError(PRIVATE)
    elif fault == "missing_context": case["context"] = None
    assert not emit(case)
    assert not output(case).exists()


@pytest.mark.parametrize("which", ["compiler", "runtime", "provenance", "output_parent"])
def test_symlinks_are_rejected_without_following_alternate_inputs(fixture, which):
    case = fixture()
    if which == "output_parent":
        path = case["out_dir"]
        replacement = path.with_name("elsewhere")
        path.rename(replacement)
        path.symlink_to(replacement, target_is_directory=True)
    else:
        path = (Path(case["incomplete"]["image"]["path"]) if which == "compiler"
                else Path(case["runtime_identity"]["path"] + (".provenance.json" if which == "provenance" else "")))
        replacement = path.with_name(path.name + ".original")
        path.rename(replacement)
        path.symlink_to(replacement)
    assert not emit(case)
    assert not output(case).exists()


def test_denied_read_is_attempted_once_and_does_not_fallback(fixture, monkeypatch):
    case = fixture()
    path = Path(case["incomplete"]["image"]["path"])
    original = Path.open
    attempts = []
    def denied(self, *args, **kwargs):
        if self == path:
            attempts.append(self)
            raise PermissionError(PRIVATE)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", denied)
    assert not emit(case)
    assert attempts == [path]
    assert not output(case).exists()


def test_mutation_during_provenance_validation_suppresses_output(fixture, monkeypatch):
    case = fixture()
    def mutate(*args):
        Path(case["incomplete"]["image"]["path"]).write_bytes(b"changed after first hash")
    monkeypatch.setattr(diagnostic, "_verify_runtime", mutate)
    assert not emit(case)
    assert not output(case).exists()


@pytest.mark.parametrize("kind", ["regular", "symlink"])
def test_existing_output_is_not_overwritten(fixture, kind):
    case = fixture()
    destination = output(case)
    existing = case["out_dir"] / "existing.json" if kind == "symlink" else destination
    existing.write_bytes(b"existing")
    if kind == "symlink": destination.symlink_to(existing)
    assert not emit(case)
    assert existing.read_bytes() == b"existing"


@pytest.mark.parametrize("fault", ["qualified", "admissible", "schema", "extra", "path", "source_extra",
                                  "producer_extra", "hash_text", "failure_text", "signal", "bool_stage"])
def test_closed_schema_rejects_qualifications_and_free_text(fixture, fault):
    case = fixture()
    assert emit(case)
    value = json.loads(output(case).read_text())
    if fault in {"qualified", "admissible"}: value[fault] = True
    elif fault == "schema": value["schema"] = bundle.SCHEMA
    elif fault == "extra": value["environment"] = PRIVATE
    elif fault == "path": value["hashes"]["path"] = "/private/" + PRIVATE
    elif fault == "source_extra": value["source"]["path"] = PRIVATE
    elif fault == "producer_extra": value["producer"]["token"] = PRIVATE
    elif fault == "hash_text": value["hashes"]["compiler_sha256"] = PRIVATE
    elif fault == "failure_text": value["failure"]["error"] = PRIVATE
    elif fault == "signal": value["failure"]["signal"] = 11
    elif fault == "bool_stage": value["stage"] = True
    with pytest.raises(ValueError): diagnostic.validate_identity(value)


def test_diagnostic_cannot_alias_success_bundle_or_native_inputs(fixture, monkeypatch):
    case = fixture()
    assert emit(case)
    directory = case["out_dir"]
    (directory / "manifest.json").write_bytes(output(case).read_bytes())
    with pytest.raises(ValueError, match="schema"):
        bundle.import_bundle(case["root"], directory, COMMIT, "linux-x86_64", case["context"]["producer"])
    monkeypatch.setattr(native_inputs, "source_identity", lambda: dict(SOURCE_IDENTITY))
    monkeypatch.setattr(bundle, "host_platform", lambda: "linux-x86_64")
    for name in ("PCC_CURRENT_PCC1", "PCC_TEST_COMPILER", "PCC_PLATFORM_TEST_COMPILER", "PCC_RUNTIME_ARCHIVE",
                 "PCC_THREADED_RUNTIME_ARCHIVE", "PCC_CI_STAGE1_MANIFEST", "PCC_CI_STAGE1_MANIFEST_SHA256"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(native_inputs, "requires_native_inputs", lambda *args: True)
    with pytest.raises(ValueError, match="schema"):
        native_inputs.admit(case["root"], directory, domain="python", lane="integration")


@pytest.mark.parametrize("failure_phase", ["canary_compile", "canary_run", None])
def test_real_producer_owner_records_only_failed_child_and_preserves_budgets(fixture, monkeypatch, failure_phase):
    case = fixture()
    root = case["root"]
    events = _fake_platform(root, monkeypatch)
    monkeypatch.setattr(producer, "source_identity", lambda _dirty=False: dict(SOURCE_IDENTITY))
    from pcc.frontends.python import owned_runtime_build
    monkeypatch.setattr(owned_runtime_build, "ensure_target_runtime", lambda *a, **k: case["runtime_identity"]["path"])
    original_run = producer.run
    observed = []
    failure = None
    def run(command, **options):
        nonlocal failure
        observed.append((list(command), options["timeout"], options["rss_limit"], options.get("native", False)))
        name = Path(options["log_path"]).name
        if name == {"canary_compile": "canary1-compile.log", "canary_run": "canary1-run.log"}.get(failure_phase):
            failure = subprocess.CalledProcessError(-6, command, stderr=PRIVATE)
            raise failure
        result = original_run(command, **options)
        if "--ir-scaffold=on" in command:
            Path(command[command.index("-o") + 1]).write_bytes(native_header("linux-x86_64"))
        return result
    monkeypatch.setattr(producer, "run", run)
    out = root / "build/platform-qualification/shared"
    kwargs = dict(stage_limit=1, lock_held=True, runtime_archive=Path(case["runtime_identity"]["path"]),
                  timeout=321, stage1_timeout=654, rss_limit=1073741824)
    if failure_phase:
        with pytest.raises(subprocess.CalledProcessError) as raised:
            producer.run_chain("0", out, **kwargs)
        assert raised.value is failure
        value = json.loads((out / "failure-identity.json").read_text())
        assert value["gc"] == 0 and value["failure"]["phase"] == failure_phase
        assert value["qualified"] is False and value["admissible"] is False
        assert json.loads((out / "failure.json").read_text())["stages"] == []
        assert not (out / "receipt.json").exists()
    else:
        receipt = producer.run_chain("0", out, **kwargs)
        assert receipt["stages"][0]["canary"]
        assert not (out / "failure-identity.json").exists()
        assert not (out / "failure.json").exists()
    expected_deadlines = [654, 321] + ([60] if failure_phase != "canary_compile" else [])
    assert [item[1] for item in observed] == expected_deadlines
    assert all(item[2] == 1073741824 for item in observed)
    assert [item[3] for item in observed] == [False] + [True] * (len(observed) - 1)


@pytest.mark.parametrize("diagnostic_error", [ValueError(PRIVATE), SystemExit(0), KeyboardInterrupt()])
def test_optional_diagnostic_exception_cannot_replace_original_failure(tmp_path, monkeypatch, diagnostic_error):
    _fake_platform(tmp_path, monkeypatch)
    original = producer.run
    error = subprocess.CalledProcessError(-6, ["original"])
    def fail(command, **options):
        if Path(options["log_path"]).name == "canary1-compile.log": raise error
        return original(command, **options)
    monkeypatch.setattr(producer, "run", fail)
    monkeypatch.setattr(diagnostic, "capture", lambda *a, **k: {diagnostic.ASSOCIATION_FIELD: "d" * 64})
    diagnostic_calls = []
    def fail_diagnostic(*args, **kwargs):
        diagnostic_calls.append((args, kwargs))
        raise diagnostic_error
    monkeypatch.setattr(diagnostic, "record", fail_diagnostic)
    out = tmp_path / "chain"
    with pytest.raises(subprocess.CalledProcessError) as raised:
        producer.run_chain("0", out, stage_limit=1, lock_held=True)
    assert raised.value is error
    assert len(diagnostic_calls) == 1
    assert not (out / "receipt.json").exists()


def test_early_failure_removes_stale_identity_without_rebinding_it(tmp_path, monkeypatch):
    _fake_platform(tmp_path, monkeypatch)
    out = tmp_path / "chain"
    out.mkdir()
    stale = out / "failure-identity.json"
    stale.write_text('{"qualified": true}')
    error = RuntimeError("runtime failed")
    monkeypatch.setattr(producer, "run", lambda *a, **k: (_ for _ in ()).throw(error))
    with pytest.raises(RuntimeError) as raised:
        producer.run_chain("0", out, stage_limit=1, lock_held=True)
    assert raised.value is error
    assert not stale.exists()


def test_actual_shared_then_reused_gc1_failure_keeps_its_own_identity_and_limits(fixture, monkeypatch):
    case = fixture()
    root = case["root"]
    _fake_platform(root, monkeypatch)
    monkeypatch.setattr(producer, "source_identity", lambda _dirty=False: dict(SOURCE_IDENTITY))
    from pcc.frontends.python import owned_runtime_build
    monkeypatch.setattr(owned_runtime_build, "ensure_target_runtime", lambda *a, **k: case["runtime_identity"]["path"])
    original_run = producer.run
    seen = []
    original_error = None

    def run(command, **options):
        nonlocal original_error
        seen.append((list(command), options["timeout"], options["rss_limit"], options.get("native", False)))
        if Path(options["log_path"]).parent == case["out_dir"]:
            original_error = subprocess.CalledProcessError(-6, command, stderr=PRIVATE)
            raise original_error
        result = original_run(command, **options)
        if "--ir-scaffold=on" in command:
            Path(command[command.index("-o") + 1]).write_bytes(native_header("linux-x86_64"))
        return result

    monkeypatch.setattr(producer, "run", run)
    shared_dir = root / "build/platform-qualification/shared"
    shared = producer.run_chain("0", shared_dir, stage_limit=1, lock_held=True,
        runtime_archive=Path(case["runtime_identity"]["path"]), timeout=321, stage1_timeout=654,
        rss_limit=1073741824)
    shared_before = json.dumps(shared, sort_keys=True)
    compiler = Path(shared["compiler_path"])
    compiler_before = compiler.read_bytes()
    assert [item[1] for item in seen] == [654, 321, 60]
    seen.clear()
    with pytest.raises(subprocess.CalledProcessError) as raised:
        producer.run_chain("1", case["out_dir"], stage_limit=1, lock_held=True, shared=shared,
                           timeout=321, stage1_timeout=654, rss_limit=1073741824)
    assert raised.value is original_error
    expected = [str(compiler), str(case["out_dir"] / "canary.py"), "-o",
                str(case["out_dir"] / "canary1"), "--backend", "self"]
    assert seen == [(expected, 321, 1073741824, True)]
    assert original_error.cmd == expected
    assert compiler.read_bytes() == compiler_before
    assert json.dumps(shared, sort_keys=True) == shared_before
    pending = json.loads((case["out_dir"] / "failure.json").read_text())["incomplete_stage"]
    assert pending["reused"] is True and "canary" not in pending
    assert pending["sha256"] == shared["stages"][0]["sha256"]
    identity = json.loads(output(case).read_text())
    assert diagnostic.validate_identity(identity) == identity
    assert identity["gc"] == 1 and identity["stage"] == 1
    assert identity["hashes"]["compiler_sha256"] == shared["stages"][0]["sha256"]
    assert identity["failure"] == {"phase": "canary_compile", "returncode": -6, "signal": 6, "exit_code": None}
    assert not (case["out_dir"] / "receipt.json").exists()
    assert not (case["out_dir"] / "progress.json").exists()


@pytest.mark.parametrize("fault", ["create", "write", "write_cleanup", "close", "publish", "cleanup_published"])
def test_output_faults_preserve_original_failure_and_never_publish_partial_json(fixture, monkeypatch, fault):
    case = fixture()
    root = case["root"]
    _fake_platform(root, monkeypatch)
    monkeypatch.setattr(producer, "source_identity", lambda _dirty=False: dict(SOURCE_IDENTITY))
    from pcc.frontends.python import owned_runtime_build
    monkeypatch.setattr(owned_runtime_build, "ensure_target_runtime", lambda *a, **k: case["runtime_identity"]["path"])
    original_run = producer.run
    original_error = None
    write_failed = False
    published = False
    fault_seen = []

    def run(command, **options):
        nonlocal original_error
        if Path(options["log_path"]).name == "canary1-compile.log":
            original_error = subprocess.CalledProcessError(-6, command, stderr=PRIVATE)
            raise original_error
        result = original_run(command, **options)
        if "--ir-scaffold=on" in command:
            Path(command[command.index("-o") + 1]).write_bytes(native_header("linux-x86_64"))
        return result

    monkeypatch.setattr(producer, "run", run)
    original_open, original_fdopen = os.open, os.fdopen
    original_link, original_unlink = os.link, Path.unlink

    def open_file(path, *args, **kwargs):
        if fault == "create":
            fault_seen.append("create")
            raise PermissionError("create denied")
        return original_open(path, *args, **kwargs)

    class Writer:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def write(self, data):
            nonlocal write_failed
            if fault in {"write", "write_cleanup"}:
                self.stream.write(data[: len(data) // 2])
                write_failed = True
                fault_seen.append("write")
                raise OSError("write failed")
            return self.stream.write(data)
        def __exit__(self, *args):
            self.stream.close()
            if fault == "close":
                fault_seen.append("close")
                raise OSError("close failed")

    def fdopen(fd, *args, **kwargs):
        return Writer(original_fdopen(fd, *args, **kwargs))

    def link(source, destination, *args, **kwargs):
        nonlocal published
        if fault == "publish":
            fault_seen.append("publish")
            raise PermissionError("publish denied")
        result = original_link(source, destination, *args, **kwargs)
        published = True
        return result

    def unlink(path, *args, **kwargs):
        if fault == "write_cleanup" and write_failed:
            fault_seen.append("cleanup")
            raise PermissionError("cleanup denied")
        if fault == "cleanup_published" and published and path.name != "failure-identity.json":
            fault_seen.append("cleanup")
            raise PermissionError("cleanup denied")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", open_file)
    monkeypatch.setattr(os, "fdopen", fdopen)
    monkeypatch.setattr(os, "link", link)
    monkeypatch.setattr(Path, "unlink", unlink)
    out = root / "build/platform-qualification/shared"
    with pytest.raises(subprocess.CalledProcessError) as raised:
        producer.run_chain("0", out, stage_limit=1, lock_held=True,
            runtime_archive=Path(case["runtime_identity"]["path"]), timeout=321, stage1_timeout=654,
            rss_limit=1073741824)
    assert raised.value is original_error
    expected_faults = {"create": {"create"}, "write": {"write"},
                       "write_cleanup": {"write", "cleanup"}, "close": {"close"},
                       "publish": {"publish"}, "cleanup_published": {"cleanup"}}
    assert set(fault_seen) == expected_faults[fault], "the intended I/O failure must actually be exercised"
    for event in expected_faults[fault] - {"create"}:
        assert fault_seen.count(event) == 1
    if fault == "create":
        # CPython 3.15 can retry random same-directory names on Windows.
        assert 1 <= fault_seen.count("create") <= 20
    final = out / "failure-identity.json"
    temporary = list(out.glob(".failure-identity-*.tmp"))
    if fault in {"write_cleanup", "cleanup_published"}:
        assert len(temporary) == 1, "denied cleanup must leave only the non-artifact temporary name"
        assert PRIVATE.encode() not in temporary[0].read_bytes()
        assert not temporary[0].match("*.json") and not temporary[0].match("*.log")
        if fault == "cleanup_published":
            assert temporary[0].read_bytes() == final.read_bytes()
        else:
            with pytest.raises(json.JSONDecodeError):
                json.loads(temporary[0].read_text())
    else:
        assert temporary == []
    if fault == "cleanup_published":
        assert diagnostic.validate_identity(json.loads(final.read_text()))["qualified"] is False
    else:
        assert not final.exists(), "failed write/close/publication must not expose the final JSON filename"
    assert not (out / "receipt.json").exists()
    # Any temporary residue from denied cleanup is outside existing artifact
    # globs. The pre-existing raw failure receipt remains unchanged in scope.
    assert set(path.name for path in out.glob("*.json")) <= {"failure.json", "failure-identity.json"}


# The independent review counterexample below retains its original success
# and exception oracle unchanged. It failed on the earlier c6 candidate.

@pytest.mark.parametrize('child_fails', [False, True])
def test_optional_identity_cleanup_must_not_change_primary_outcome(tmp_path, monkeypatch, child_fails):
    events = _fake_platform(tmp_path, monkeypatch)
    modeled_run = producer.run
    original_error = subprocess.CalledProcessError(-6, ['modeled canary child'])

    def modeled_child(command, **options):
        if child_fails and Path(options['log_path']).name == 'canary1-compile.log':
            raise original_error
        return modeled_run(command, **options)

    monkeypatch.setattr(producer, 'run', modeled_child)
    out = tmp_path / 'build/platform-qualification/shared'
    out.mkdir(parents=True)
    stale = out / 'failure-identity.json'
    stale.write_text('{"synthetic_stale": true}\n')
    original_unlink = Path.unlink

    def deny_optional_cleanup(path, *args, **kwargs):
        if path == stale:
            raise PermissionError('simulated optional sidecar cleanup denied')
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', deny_optional_cleanup)
    if child_fails:
        with pytest.raises(subprocess.CalledProcessError) as raised:
            producer.run_chain('0', out, stage_limit=1, lock_held=True,
                               timeout=321, stage1_timeout=654, rss_limit=1073741824)
        assert raised.value is original_error
    else:
        receipt = producer.run_chain('0', out, stage_limit=1, lock_held=True,
                                     timeout=321, stage1_timeout=654, rss_limit=1073741824)
        assert [stage['stage'] for stage in receipt['stages']] == [1]
        assert len(events) == 4



def _modeled_producer(case, monkeypatch, *, child_fails):
    root = case["root"]
    events = _fake_platform(root, monkeypatch)
    monkeypatch.setattr(producer, "source_identity", lambda _dirty=False: dict(SOURCE_IDENTITY))
    from pcc.frontends.python import owned_runtime_build
    monkeypatch.setattr(owned_runtime_build, "ensure_target_runtime", lambda *a, **k: case["runtime_identity"]["path"])
    modeled_run = producer.run
    state = {"error": None, "calls": [], "failure_writes": 0}
    original_write_text = Path.write_text

    def write_text(path, *args, **kwargs):
        if path == case["out_dir"] / "failure.json": state["failure_writes"] += 1
        return original_write_text(path, *args, **kwargs)

    def run(command, **options):
        state["calls"].append((list(command), options["timeout"], options["rss_limit"], options.get("native", False)))
        if child_fails and Path(options["log_path"]).name == "canary1-compile.log":
            state["error"] = subprocess.CalledProcessError(-6, command, stderr=PRIVATE)
            raise state["error"]
        result = modeled_run(command, **options)
        if "--ir-scaffold=on" in command:
            Path(command[command.index("-o") + 1]).write_bytes(native_header("linux-x86_64"))
        return result

    monkeypatch.setattr(producer, "run", run)
    monkeypatch.setattr(Path, "write_text", write_text)
    return state


def _run_modeled_producer(case):
    return producer.run_chain("1", case["out_dir"], stage_limit=1, lock_held=True,
        runtime_archive=Path(case["runtime_identity"]["path"]), timeout=321,
        stage1_timeout=654, rss_limit=1073741824)


@pytest.mark.parametrize("child_fails", [False, True])
def test_denied_initial_cleanup_preserves_outcome_and_rejects_untouched_stale_identity(fixture, monkeypatch, child_fails):
    case = fixture()
    assert emit(case)
    assert diagnostic.verify_current_identity(case["root"], case["out_dir"],
            expected_invocation=case["context"][diagnostic.ASSOCIATION_FIELD])
    stale = output(case)
    old_bytes, old_mode = stale.read_bytes(), stale.stat().st_mode
    state = _modeled_producer(case, monkeypatch, child_fails=child_fails)
    original_unlink = Path.unlink
    cleanup_attempts, forbidden_calls = [], []

    def denied_cleanup(path, *args, **kwargs):
        if path == stale:
            cleanup_attempts.append(path)
            raise PermissionError("optional old sidecar deletion denied")
        return original_unlink(path, *args, **kwargs)

    def forbidden_diagnostic(*args, **kwargs):
        forbidden_calls.append((args, kwargs))
        raise AssertionError("denied cleanup must disable this invocation's diagnostics")

    monkeypatch.setattr(Path, "unlink", denied_cleanup)
    monkeypatch.setattr(diagnostic, "capture", forbidden_diagnostic)
    monkeypatch.setattr(diagnostic, "record", forbidden_diagnostic)
    if child_fails:
        with pytest.raises(subprocess.CalledProcessError) as raised:
            _run_modeled_producer(case)
        assert raised.value is state["error"]
        failure = json.loads((case["out_dir"] / "failure.json").read_text())
        assert failure[diagnostic.ASSOCIATION_FIELD] is None
        assert set(failure) == {"source", "stages", "incomplete_stage", "error", diagnostic.ASSOCIATION_FIELD}
        assert failure["error"] == str(state["error"]) and failure["stages"] == []
        assert state["failure_writes"] == 1
        assert not (case["out_dir"] / "receipt.json").exists()
    else:
        receipt = _run_modeled_producer(case)
        assert [stage["stage"] for stage in receipt["stages"]] == [1]
        assert diagnostic.ASSOCIATION_FIELD not in receipt
        assert not (case["out_dir"] / "failure.json").exists()
        assert state["failure_writes"] == 0
    assert cleanup_attempts == [stale] and forbidden_calls == []
    assert stale.read_bytes() == old_bytes and stale.stat().st_mode == old_mode
    assert [row[1] for row in state["calls"]] == ([654, 321] if child_fails else [654, 321, 60])
    assert all(row[2] == 1073741824 for row in state["calls"])
    with pytest.raises(ValueError, match="current failure identity is unavailable"):
        diagnostic.verify_current_identity(case["root"], case["out_dir"],
            expected_invocation=case["context"][diagnostic.ASSOCIATION_FIELD])
    assert stale.read_bytes() == old_bytes and cleanup_attempts == [stale]


def test_current_binding_reads_the_actual_failure_and_returns_no_original_contents(fixture):
    case = fixture()
    assert emit(case)
    value = diagnostic.verify_current_identity(case["root"], case["out_dir"],
            expected_invocation=case["context"][diagnostic.ASSOCIATION_FIELD])
    assert value == json.loads(output(case).read_text())
    assert value["hashes"]["failure_receipt_sha256"] == sha(case["out_dir"] / "failure.json")
    assert diagnostic.ASSOCIATION_FIELD not in value
    assert PRIVATE not in json.dumps(value)


def test_mandatory_cleanup_denial_cannot_authenticate_an_intact_old_pair(fixture, monkeypatch):
    case = fixture()
    assert emit(case)
    old_invocation = case["context"][diagnostic.ASSOCIATION_FIELD]
    assert diagnostic.verify_current_identity(case["root"], case["out_dir"],
        expected_invocation=old_invocation)
    failure = case["out_dir"] / "failure.json"
    pair = {path: (path.read_bytes(), path.stat().st_mode) for path in (failure, output(case))}
    state = _modeled_producer(case, monkeypatch, child_fails=True)
    original_unlink = Path.unlink
    original_error = PermissionError("mandatory failure receipt cleanup denied")
    attempts, forbidden_calls = [], []

    def denied_cleanup(path, *args, **kwargs):
        attempts.append(path)
        if path == failure:
            raise original_error
        return original_unlink(path, *args, **kwargs)

    def forbidden_diagnostic(*args, **kwargs):
        forbidden_calls.append((args, kwargs))
        raise AssertionError("mandatory cleanup must fail before diagnostic initialization")

    monkeypatch.setattr(Path, "unlink", denied_cleanup)
    monkeypatch.setattr(diagnostic, "capture", forbidden_diagnostic)
    monkeypatch.setattr(diagnostic, "record", forbidden_diagnostic)
    with pytest.raises(PermissionError) as raised:
        _run_modeled_producer(case)
    assert raised.value is original_error
    assert attempts == [case["out_dir"] / "receipt.json", failure]
    assert forbidden_calls == [] and state["calls"] == [] and state["failure_writes"] == 0
    assert {path: (path.read_bytes(), path.stat().st_mode) for path in pair} == pair
    # No new context exists because the original mandatory failure happened
    # first. Neither omission nor a separately supplied new invocation may
    # authenticate the retained pair, even though its schema/hash/lease match.
    with pytest.raises(TypeError, match="expected_invocation"):
        diagnostic.verify_current_identity(case["root"], case["out_dir"])
    new_invocation = "e" * 64 if old_invocation != "e" * 64 else "d" * 64
    for expected in (None, new_invocation):
        with pytest.raises(ValueError, match="current failure identity is unavailable"):
            diagnostic.verify_current_identity(case["root"], case["out_dir"],
                expected_invocation=expected)
    assert diagnostic.validate_identity(json.loads(output(case).read_bytes()))
    assert {path: (path.read_bytes(), path.stat().st_mode) for path in pair} == pair
    assert attempts == [case["out_dir"] / "receipt.json", failure]


@pytest.mark.parametrize("expected", [None, "", "a" * 63, "a" * 65, "G" * 64,
                                      "A" * 64, 1, b"a" * 64])
def test_current_verification_requires_a_valid_independent_expectation_before_reads(fixture, monkeypatch, expected):
    case = fixture()
    assert emit(case)
    reads = []

    def forbidden_read(*args, **kwargs):
        reads.append((args, kwargs))
        raise AssertionError("invalid expectation must reject before reading evidence")

    monkeypatch.setattr(diagnostic, "_read_json_bytes", forbidden_read)
    with pytest.raises(ValueError, match="current failure identity is unavailable"):
        diagnostic.verify_current_identity(case["root"], case["out_dir"],
            expected_invocation=expected)
    with pytest.raises(TypeError, match="expected_invocation"):
        diagnostic.verify_current_identity(case["root"], case["out_dir"])
    assert reads == []


def test_current_verification_rejects_a_valid_but_wrong_independent_expectation(fixture):
    case = fixture()
    assert emit(case)
    expected = case["context"][diagnostic.ASSOCIATION_FIELD]
    wrong = "e" * 64 if expected != "e" * 64 else "d" * 64
    with pytest.raises(ValueError, match="current failure identity is unavailable"):
        diagnostic.verify_current_identity(case["root"], case["out_dir"], expected_invocation=wrong)
    assert diagnostic.verify_current_identity(case["root"], case["out_dir"], expected_invocation=expected)


@pytest.mark.parametrize("fault", ["missing_file", "changed_bytes", "missing_association", "null_association",
                                  "invalid_association", "missing_hash", "old_schema", "wrong_lease", "symlink"])
def test_current_binding_never_accepts_missing_null_stale_or_tampered_failure(fixture, monkeypatch, fault):
    case = fixture()
    assert emit(case)
    current = case["out_dir"] / "failure.json"
    value = json.loads(output(case).read_text())
    if fault == "missing_file": current.unlink()
    elif fault == "changed_bytes": current.write_bytes(current.read_bytes() + b"\n")
    elif fault in {"missing_association", "null_association", "invalid_association"}:
        raw = json.loads(current.read_text())
        if fault == "missing_association": raw.pop(diagnostic.ASSOCIATION_FIELD)
        else: raw[diagnostic.ASSOCIATION_FIELD] = None if fault == "null_association" else PRIVATE
        current.write_text(json.dumps(raw))
        # Rehashing a broken association must not turn it into valid evidence.
        value["hashes"]["failure_receipt_sha256"] = sha(current)
        output(case).write_text(json.dumps(value))
    elif fault == "missing_hash":
        value["hashes"].pop("failure_receipt_sha256")
        output(case).write_text(json.dumps(value))
    elif fault == "old_schema":
        value["schema"] = "pcc.failed-stage-identity.v1"
        output(case).write_text(json.dumps(value))
    elif fault == "wrong_lease": monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "3")
    elif fault == "symlink":
        original = current.with_suffix(".original")
        current.rename(original)
        current.symlink_to(original)
    with pytest.raises(ValueError, match="current failure identity is unavailable"):
        diagnostic.verify_current_identity(case["root"], case["out_dir"],
            expected_invocation=case["context"][diagnostic.ASSOCIATION_FIELD])


@pytest.mark.parametrize("association", [None, "missing", PRIVATE, "f" * 64])
def test_emission_requires_this_captured_invocation_not_just_valid_failure_json(fixture, association):
    case = fixture()
    current = case["out_dir"] / "failure.json"
    raw = json.loads(current.read_text())
    if association == "missing": raw.pop(diagnostic.ASSOCIATION_FIELD)
    else: raw[diagnostic.ASSOCIATION_FIELD] = association
    current.write_text(json.dumps(raw))
    assert not emit(case)
    assert not output(case).exists()


def test_repeated_same_lease_failure_cannot_reuse_previous_occurrence(fixture, monkeypatch):
    case = fixture()
    state = _modeled_producer(case, monkeypatch, child_fails=True)
    independent_invocations = ("1" * 64, "2" * 64)
    identities = iter(independent_invocations)
    monkeypatch.setattr(diagnostic, "_new_invocation", lambda: next(identities))
    failures, sidecars = [], []
    for expected_invocation in independent_invocations:
        with pytest.raises(subprocess.CalledProcessError) as raised:
            _run_modeled_producer(case)
        assert raised.value is state["error"]
        failures.append(json.loads((case["out_dir"] / "failure.json").read_text()))
        sidecars.append(output(case).read_bytes())
        assert diagnostic.verify_current_identity(case["root"], case["out_dir"],
            expected_invocation=expected_invocation)
    with pytest.raises(ValueError, match="current failure identity is unavailable"):
        diagnostic.verify_current_identity(case["root"], case["out_dir"],
            expected_invocation=independent_invocations[0])
    assert state["failure_writes"] == 2  # One original write per invocation, never a rewrite.
    first, second = (json.loads(data) for data in sidecars)
    assert first["producer"] == second["producer"]
    assert first["source"] == second["source"]
    assert first["failure"] == second["failure"]
    assert first["hashes"]["failure_receipt_sha256"] != second["hashes"]["failure_receipt_sha256"]
    assert failures[0].pop(diagnostic.ASSOCIATION_FIELD) == "1" * 64
    assert failures[1].pop(diagnostic.ASSOCIATION_FIELD) == "2" * 64
    assert failures[0] == failures[1], "the pre-existing failure fields are byte-equivalent inputs"
    # Simulate the old sidecar being retained; do not change the new failure.
    output(case).write_bytes(sidecars[0])
    with pytest.raises(ValueError, match="current failure identity is unavailable"):
        diagnostic.verify_current_identity(case["root"], case["out_dir"],
            expected_invocation=expected_invocation)
    assert output(case).read_bytes() == sidecars[0]


@pytest.mark.parametrize("child_fails", [False, True])
def test_occurrence_generation_failure_disables_only_diagnostics(fixture, monkeypatch, child_fails):
    case = fixture()
    state = _modeled_producer(case, monkeypatch, child_fails=child_fails)
    attempts, unexpected_writers = [], []
    def failed_occurrence():
        attempts.append(True)
        raise OSError("random occurrence source unavailable")
    monkeypatch.setattr(diagnostic, "_new_invocation", failed_occurrence)
    monkeypatch.setattr(diagnostic, "record", lambda *a, **k: unexpected_writers.append((a, k)))
    if child_fails:
        with pytest.raises(subprocess.CalledProcessError) as raised:
            _run_modeled_producer(case)
        assert raised.value is state["error"]
        assert json.loads((case["out_dir"] / "failure.json").read_text())[diagnostic.ASSOCIATION_FIELD] is None
        assert state["failure_writes"] == 1
    else:
        receipt = _run_modeled_producer(case)
        assert [stage["stage"] for stage in receipt["stages"]] == [1]
        assert diagnostic.ASSOCIATION_FIELD not in receipt and state["failure_writes"] == 0
    assert attempts == [True] and unexpected_writers == []
    assert not output(case).exists()


@pytest.mark.parametrize("fault", ["random_source", "hash"])
def test_capture_catches_random_or_hash_failure_without_retaining_material(tmp_path, monkeypatch, env, fault):
    calls = []
    original_hash = hashlib.sha256
    material = b"r" * 32
    def random_bytes(size):
        calls.append(("random", size))
        if fault == "random_source": raise OSError("random source denied")
        return material
    def digest(data=b"", *args, **kwargs):
        calls.append(("hash", len(data)))
        raise ValueError("hash unavailable")
    with monkeypatch.context() as scoped:
        scoped.setattr(os, "urandom", random_bytes)
        if fault == "hash": scoped.setattr(hashlib, "sha256", digest)
        assert diagnostic.capture(tmp_path, dict(SOURCE_IDENTITY), TARGETS["linux-x86_64"]) is None
    assert calls == ([("random", 32)] if fault == "random_source" else [("random", 32), ("hash", 32)])
    assert original_hash(material).hexdigest() not in json.dumps(calls)


def test_occurrence_is_a_digest_not_retained_random_material(tmp_path, monkeypatch, env):
    material = b"q" * 32
    monkeypatch.setattr(os, "urandom", lambda size: material if size == 32 else None)
    value = diagnostic.capture(tmp_path, dict(SOURCE_IDENTITY), TARGETS["linux-x86_64"])
    assert value[diagnostic.ASSOCIATION_FIELD] == hashlib.sha256(material).hexdigest()
    assert material.hex() not in json.dumps(value)


def test_current_failure_read_denial_is_not_retried_or_redirected(fixture, monkeypatch):
    case = fixture()
    assert emit(case)
    current = case["out_dir"] / "failure.json"
    original_open, reads = Path.open, []
    def denied(path, *args, **kwargs):
        if path == current:
            reads.append(path)
            raise PermissionError(PRIVATE)
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(ValueError, match="current failure identity is unavailable"):
        diagnostic.verify_current_identity(case["root"], case["out_dir"],
            expected_invocation=case["context"][diagnostic.ASSOCIATION_FIELD])
    assert reads == [current]

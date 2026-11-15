"""Host orchestration tests, NOT native capture/codec/performance evidence.

The real public entry, capture implementation, parsers, receipts and input
revalidation run here. Only platform/build/current-source admission, watchdog and process
boundaries are simulated. The two-module coordinator output is a wire fixture,
not a plan supplied by a caller. No executable fixture is ever launched.

Native integration remains UNRUN: requires a strict Stage1 receipt, frozen full
source/runtime closure, actual enclosing process-tree sampler and performance
lock on Darwin ARM. Run the capability check, fresh coordinator and selected
singleton frontend there; then validate with the real codec and emitted program.
A host pass cannot qualify that boundary or renew an optimization claim.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import subprocess
from types import FunctionType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TARGET = "arm64-apple-darwin"


def _budget_boundary(**kwargs):
    return _capture_test_harness.admit(kwargs)


def _process_boundary(command, **kwargs):
    return _capture_test_harness.process(command, **kwargs)


class Budget:
    receipt = {"test_boundary": "SIMULATED_WATCHDOG_NOT_NATIVE_EVIDENCE"}

    def __init__(self):
        self.expired = False
        self.checks = 0

    def verify(self):
        self.checks += 1
        if self.expired:
            raise RuntimeError("simulated guard expired")

    def remaining_timeout(self, requested):
        self.verify()
        return min(requested, 3)


class Harness:
    def __init__(self, root, ab, capture):
        self.root, self.ab, self.capture = root, ab, capture
        self.calls = []
        self.budget = Budget()
        self.token = object()
        self.fault = None
        self.timing = False
        self.source = root / "source"
        (self.source / "pcc").mkdir(parents=True)
        # Full original source paths retained in every singleton assignment.
        self.sources = [self.source / "pcc/__main__.py", self.source / "pcc/helper.py"]
        for path, text in zip(self.sources, ("from pcc.helper import answer\n", "answer = 42\n")):
            path.write_text(text)
        self.source_manifest = {"bootstrap_source_sha256": "a" * 64,
                                "files": {p.relative_to(self.source).as_posix(): ab.sha256_path(p)
                                          for p in self.sources}}
        ab._seal_source_snapshot(self.source, "test source")
        runtime_root = root / "runtime"
        runtime_root.mkdir()
        self.runtime = runtime_root / "runtime.a"
        self.runtime.write_bytes(b"SIMULATED runtime, never linked")
        self.runtime.chmod(0o444)
        runtime_root.chmod(0o555)
        self.bundle = {"archive": str(self.runtime),
                       "files": {self.runtime.name: ab._path_receipt(self.runtime)}}
        self.build = root / "build"
        self.build.mkdir()
        self.compiler = self.build / "pcc1"
        self.compiler.write_bytes(b"\xcf\xfa\xed\xfeSIMULATED compiler, never executed")
        self.compiler.chmod(0o555)
        self.receipt = self.build / "receipt.json"
        self.receipt.write_text('{"test_boundary":"build admission mocked"}')
        (self.build / "manifest.json").write_text(json.dumps(
            {"status": "SUCCEEDED", "runtime_bundle": self.bundle}))
        self.evidence = {}
        for name in ("source-manifest.json", "stage-result.json", "stdout.log", "producer.py"):
            path = self.build / name
            path.write_text("test-only retained build evidence: " + name)
            self.evidence[name] = path
        self.args = SimpleNamespace(compiler=str(self.compiler), build_receipt=str(self.receipt),
            compiler_arm="candidate", runtime_archive=str(self.runtime), module_name="pcc.helper",
            output_dir=str(root / "capture"), gc_backend=0, timeout=5,
            expected_target=TARGET, python_ir_passes="off")
        self.owner = dict(ab=ab, performance_lock_token=self.token,
                          guard_result_path=root / "guard.json", expected_guard_command=["test"],
                          expected_guard_cwd=root, campaign_deadline_monotonic=123)

    def admit(self, kwargs):
        assert kwargs == {key: self.owner[key] for key in (
            "guard_result_path", "expected_guard_command", "expected_guard_cwd",
            "campaign_deadline_monotonic")}
        return self.budget

    def loaded(self, *args, **kwargs):
        assert kwargs["compiler_sha256"] == self.ab.sha256_path(self.compiler)
        assert kwargs["runtime_sha256"] == self.ab.sha256_path(self.runtime)
        return {"source_snapshot_root": str(self.source), "source_manifest": self.source_manifest,
                "origin_source_root": str(self.source), "external_tools": [],
                "source_manifest_path": str(self.evidence["source-manifest.json"]),
                "stage_result_path": str(self.evidence["stage-result.json"]),
                "artifact_paths": {"stdout": str(self.evidence["stdout.log"])},
                "receipt": {"runtime_bundle": self.bundle,
                            "producer_tools": {"test": {"path": "producer.py"}}},
                "sha256": self.ab.sha256_path(self.receipt)}

    def coordinator(self, output, native):
        artifacts, ast = output / "original-artifacts", output / "original-ast"
        artifacts.mkdir()
        ast.mkdir()
        # Payloads are deliberately opaque to host orchestration. Semantic AST
        # and export decoding belongs to the native integration boundary.
        for index in range(2):
            (ast / f"module_{index}.json").write_text(json.dumps(
                {"test_only_ast": index, "source": str(self.sources[index])}))
        (ast / "shared-context.json").write_text('{"test_only_shared_context":true}')
        exports = output / "original-exports.json"
        exports.write_text('{"test_only_exports":{"pcc.helper":["answer"]}}')
        self.ast, self.exports = ast, exports
        self.manifests = []
        for index in range(2):
            rows = [self.capture.WORKER_SCHEMA, str(output / f"unused-result-{index}"),
                    str(artifacts), str(exports), "codegen", str(ast), "pcc.__main__",
                    "on", "0", "1", "pcc.helper", "2",
                    "0\tpcc.__main__\t" + str(self.sources[0]),
                    "1\tpcc.helper\t" + str(self.sources[1]), "1", str(index)]
            if self.fault == "context" and index == 1:
                rows[8] = "1"
            if self.fault == "ordinal":
                rows[12] = rows[12].replace("0\t", "00\t", 1)
            if self.fault == "name":
                rows[13] = rows[13].replace("pcc.helper", "pcc.__main__")
            if self.fault == "source":
                rows[13] = "1\tpcc.helper\t" + str(self.compiler)
            if self.fault == "artifact":
                rows[2] = str(self.root)
            manifest = output / f"original-worker-{index}.manifest"
            manifest.write_text("\n".join(rows) + "\n")
            self.manifests.append(manifest)
        plan = [self.capture.PLAN_SCHEMA, str(native), str(output / "link"), str(self.runtime),
                str(output / "pcc2"), str(output / "link-result"), str(artifacts),
                "2", "0", "1", "2", "pidx-pco-v1", *map(str, self.manifests)]
        (output / "capture.pcc-codegen-plan").write_text("\n".join(plan) + "\n")

    def worker(self, path):
        rows = path.read_text().splitlines()
        assert rows[3] == str(self.exports) and rows[5] == str(self.ast)
        assert rows[12:14] == ["0\tpcc.__main__\t" + str(self.sources[0]),
                               "1\tpcc.helper\t" + str(self.sources[1])]
        assert rows[-2:] == ["1", "1"]
        assert all(not p.stat().st_mode & 0o222 for p in self.ast.iterdir())
        artifacts = Path(rows[2])
        ir = artifacts / "module_1.ll"
        ir.write_text("; simulated IR \u03bb\n")
        pidx = artifacts / "module_1.direct.pidx"
        header = {"schema": "pcc.self-backend.indexed-module.v3", "scalar_width": 8,
                  "byte_order": "little", "triple": TARGET, "total_scalars": 1}
        if self.fault == "target":
            header["triple"] = "x86_64-unknown-linux"
        if self.fault == "debug":
            header["source_debug"] = {}
        data = json.dumps(header).encode()
        pidx.write_bytes(b"PCCIDXMOD1\n" + str(len(data)).encode() + b"\n" + data + b"\0" * 8)
        if self.fault == "truncated":
            pidx.write_bytes(pidx.read_bytes()[:-1])
        parts = ["OK", "1", "pcc.helper", "0", "0", str(len(ir.read_text())), str(ir)]
        if self.timing:
            parts.extend(["0", "1", "2"])
        parts.extend(["PIDX", str(pidx)])
        if self.fault == "result-index":
            parts[1] = "0"
        if self.fault == "ir-length":
            parts[5] = "999"
        Path(rows[1]).write_text("\t".join(parts) + "\n")
        if self.fault == "missing":
            pidx.unlink()
        if self.fault == "extra":
            (artifacts / "unexpected.o").write_bytes(b"forbidden backend output")
        if self.fault == "mutation":
            self.exports.chmod(0o644)
            self.exports.write_text("{}")
        if self.fault == "expired":
            self.budget.expired = True

    def process(self, command, **kwargs):
        self.calls.append((command, kwargs))
        assert self.ab._ACTIVE_WORKER_BUDGET is self.budget
        assert kwargs["timeout"] == 3
        env = kwargs["env"]
        assert env["PCC_PYTHON_IR_PASSES"] == "off"
        assert env["PATH"] == "/nonexistent"
        assert not any(key.startswith("PCC_DEBUG") for key in env)
        if "--pcc-native-deferred-worker" in command:
            assert command[-1] == "--check"
        elif "--backend" in command:
            self.coordinator(Path(self.args.output_dir), Path(command[0]))
        else:
            assert command[1] == "--pcc-python-multi-codegen-worker"
            assert env["PCC_DIRECT_INDEXED_SIDECAR"] == "1"
            assert env["PCC_DEFER_FRONTEND_CODEGEN_PLAN"] == ""
            if self.fault == "nonzero":
                return subprocess.CompletedProcess(command, 7, "partial", "worker failed")
            if self.fault == "timeout":
                raise subprocess.TimeoutExpired(command, 3)
            self.worker(Path(command[2]))
        return subprocess.CompletedProcess(command, 0, "simulated", "")


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    ab = importlib.import_module("run_pcc_compile_ab")
    capture = importlib.import_module("pcc_indexed_capture")
    rank = importlib.import_module("pcc_emit_rank")
    return ab, capture, rank


@pytest.fixture
def harness(tmp_path, monkeypatch, modules):
    if (os.name != "posix" or not hasattr(os, "O_DIRECTORY")
            or not hasattr(os, "O_NOFOLLOW") or os.mkdir not in os.supports_dir_fd):
        pytest.skip("POSIX no-follow/dir_fd orchestration only; native Darwin boundary remains UNRUN")
    ab, capture, rank = modules
    h = Harness(tmp_path, ab, capture)
    h.rank = rank
    monkeypatch.setattr(ab, "_capture_test_harness", h, raising=False)
    # Preserve the production globals-identity admission check: these two
    # boundary stubs actually have the selected support module's globals.
    for name, template in (("_validated_worker_budget", _budget_boundary),
                           ("_run_process", _process_boundary)):
        monkeypatch.setattr(ab, name, FunctionType(template.__code__, vars(ab)))
    monkeypatch.setattr(ab, "_HELD_PERFORMANCE_LOCK", h.token)
    monkeypatch.setattr(ab, "_ACTIVE_WORKER_BUDGET", None)
    monkeypatch.setattr(ab, "require_claim_platform", lambda: None)
    monkeypatch.setattr(ab, "_load_build_receipt", h.loaded)
    monkeypatch.setattr(ab, "runtime_bundle_evidence", lambda bundle: bundle)
    monkeypatch.setattr(ab, "_verify_source_snapshot", lambda *a, **kw: None)
    monkeypatch.setattr(ab, "_verify_runtime_bundle", lambda *a, **kw: None)
    monkeypatch.setattr(ab, "_measurement_env", lambda *a, **kw: {"PCC_DEBUG_TEST": "1"})
    # This fixture is not the repository's complete native build source.
    # Freeze a separate expected mapping so manifest mutations cannot silently
    # modify both sides of the real admission comparison.
    current_files = dict(h.source_manifest["files"])
    monkeypatch.setattr(capture, "_current_source_files", lambda selected_ab: dict(current_files))
    # Hash/stat and tree revalidation remain real, including entire AST closure.
    yield h
    for _, context, _ in list(capture._LIVE_CAPTURES.values()):
        context.output_owner.close()
    capture._LIVE_CAPTURES.clear()
    # Never follow an attack's directory alias while restoring our fixture.
    # Visit parents first so sealed source/AST directories remain traversable.
    for raw, directories, _ in os.walk(tmp_path, topdown=True, followlinks=False):
        path = Path(raw)
        assert stat.S_ISDIR(path.lstat().st_mode)
        path.chmod(0o755)
        directories[:] = [name for name in directories
                          if stat.S_ISDIR((path / name).lstat().st_mode)]


@pytest.fixture
def directory_descriptors(monkeypatch, modules):
    """Observe the actual retained descriptors, not owner implementation fields."""
    _, capture, _ = modules
    opened, closed = capture.os.open, capture.os.close
    held = set()

    def track_open(path, flags, *args, **kwargs):
        descriptor = opened(path, flags, *args, **kwargs)
        if flags & os.O_DIRECTORY:
            held.add(descriptor)
        return descriptor

    def track_close(descriptor):
        closed(descriptor)
        held.discard(descriptor)

    monkeypatch.setattr(capture.os, "open", track_open)
    monkeypatch.setattr(capture.os, "close", track_close)
    return held


def _snapshot_without_following_links(root):
    """Record bytes and identities, including aliases themselves, not targets."""
    records = {}

    def visit(path):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            content = ("symlink", os.readlink(path))
        elif stat.S_ISREG(metadata.st_mode):
            content = ("file", path.read_bytes())
        else:
            assert stat.S_ISDIR(metadata.st_mode)
            content = ("directory",)
        records[path.relative_to(root).as_posix()] = (
            metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
            metadata.st_mtime_ns, metadata.st_ctime_ns, content)
        if stat.S_ISDIR(metadata.st_mode):
            for child in sorted(path.iterdir()):
                visit(child)

    visit(root)
    return records


def _foreign_capture_tree(root):
    (root / "corpus").mkdir(parents=True)
    for name in ("capture.json", "capture.failure.json", "capture.json.tmp",
                 "capture.failure.json.tmp", "corpus/manifest.json",
                 "corpus/manifest.json.tmp", "corpus/module_1.direct.pidx"):
        (root / name).write_bytes(("foreign sentinel: " + name + "\n").encode())
    return root


def _assert_live_failure(h, result, monkeypatch, descriptors, *, original=None):
    """Check the public verifier, invalidation cause, revocation and fd lifetime."""
    context = h.capture._LIVE_CAPTURES[id(result)][1]
    invalidate, owner_verify = h.capture._invalidate, context.output_owner.verify
    failures, cleanup_admissions = [], []

    def observe_invalidation(selected, error):
        assert selected is context
        failures.append(error)
        return invalidate(selected, error)

    def observe_owner_verify():
        if failures:
            # Revocation precedes even the read-only admission for disk cleanup.
            # Record rather than raise here: invalidation deliberately catches
            # verification errors, so an assertion inside it could be swallowed.
            cleanup_admissions.append(id(result) not in h.capture._LIVE_CAPTURES)
        return owner_verify()

    monkeypatch.setattr(h.capture, "_invalidate", observe_invalidation)
    monkeypatch.setattr(context.output_owner, "verify", observe_owner_verify)
    if original is not None:
        def fail_inputs():
            raise original
        monkeypatch.setattr(context, "verify_inputs", fail_inputs)
    retained = set(descriptors)
    assert retained, "a live capture must retain its output directory descriptors"
    expected = type(original) if original is not None else h.capture.IndexedCaptureError
    with pytest.raises(expected) as caught:
        h.rank.verify_guarded_indexed_input_capture(
            result, ab=h.ab, performance_lock_token=h.token)
    assert len(failures) == 1 and caught.value is failures[0]
    assert cleanup_admissions and all(cleanup_admissions)
    if original is not None:
        assert caught.value is original
    assert not h.capture._LIVE_CAPTURES
    assert h.ab._ACTIVE_WORKER_BUDGET is None
    assert context.output_owner.closed
    assert not descriptors, "failed captures must close every retained directory fd"
    for descriptor in retained:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    with pytest.raises(h.capture.IndexedCaptureError, match="live producer"):
        h.rank.verify_guarded_indexed_input_capture(
            result, ab=h.ab, performance_lock_token=h.token)


@pytest.mark.parametrize("timing", [False, True])
def test_public_capture_runs_three_commands_and_seals_input_only(harness, timing):
    h = harness
    h.timing = timing
    result = h.rank.capture_indexed_input_guarded(h.args, **h.owner)
    assert len(h.calls) == 3
    assert result["status"] == "PREPARED"
    assert result["qualification"] == "UNQUALIFIED_INPUT_ONLY"
    assert result["origin"] == "NEW_CAPTURE_NATIVE_DEFERRED_COORDINATOR"
    assert result["backend_emission"] == result["performance_measurement"] == "NOT_RUN"
    assert result["source_promotion_authorized"] is False
    assert result["producer_source_admission"] == "CURRENT_TOOL_CHECKOUT_COMPLETE_BUILD_CLOSURE"
    assert result["runtime_pass_observation"] == "NOT_COLLECTED"
    assert result["target_header"]["semantic_validation"] == "PENDING_REAL_CODEC_AND_EXECUTION"
    manifest = Path(result["input_manifest"]["path"])
    item = json.loads(manifest.read_text())["items"][0]
    pidx = manifest.parent / item["path"]
    assert item["sha256"] == h.ab.sha256_path(pidx)
    assert not pidx.stat().st_mode & 0o222
    assert h.ab._ACTIVE_WORKER_BUDGET is None
    h.rank.verify_guarded_indexed_input_capture(result, ab=h.ab, performance_lock_token=h.token)
    assert h.ab._ACTIVE_WORKER_BUDGET is None


@pytest.mark.parametrize("fault", ["context", "ordinal", "name", "source", "artifact",
    "target", "debug", "truncated", "missing", "extra", "result-index", "ir-length",
    "nonzero", "timeout", "expired", "mutation"])
def test_public_capture_rejects_boundary_failures_and_releases_budget(harness, fault):
    h = harness
    h.fault = fault
    with pytest.raises((h.capture.IndexedCaptureError, h.ab.CompileABError,
                        FileNotFoundError, RuntimeError, subprocess.TimeoutExpired)):
        h.rank.capture_indexed_input_guarded(h.args, **h.owner)
    assert h.ab._ACTIVE_WORKER_BUDGET is None
    output = Path(h.args.output_dir)
    failure = json.loads((output / "capture.failure.json").read_text())
    assert failure["status"] == "FAILED"
    assert not (output / "corpus/manifest.json").exists()
    assert not h.capture._LIVE_CAPTURES
    if fault == "nonzero":
        assert (output / "frontend.stderr").read_text() == "worker failed"


@pytest.mark.parametrize("fault", ["no-token", "wrong-module", "nested-budget"])
def test_real_public_admission_rejects_without_process(harness, monkeypatch, fault):
    h = harness
    previous = None
    if fault == "no-token":
        h.owner["performance_lock_token"] = None
    elif fault == "wrong-module":
        monkeypatch.setattr(h.ab, "__file__", str(h.compiler))
    else:
        previous = object()
        monkeypatch.setattr(h.ab, "_ACTIVE_WORKER_BUDGET", previous)
    with pytest.raises(h.capture.IndexedCaptureError, match="live support module/lock"):
        h.rank.capture_indexed_input_guarded(h.args, **h.owner)
    assert not h.calls
    assert h.ab._ACTIVE_WORKER_BUDGET is previous
    assert not Path(h.args.output_dir).exists()


@pytest.mark.parametrize("fault", ["copy", "json", "mutate-result", "mutate-pidx",
                                    "mutate-ast", "mutate-source", "mutate-evidence", "expire", "lock"])
def test_live_verification_rejects_forgery_and_dependency_changes(harness, fault, monkeypatch):
    h = harness
    result = h.rank.capture_indexed_input_guarded(h.args, **h.owner)
    if fault == "copy":
        result = dict(result)
    elif fault == "json":
        result = json.loads(json.dumps(result))
    elif fault == "mutate-result":
        result["qualification"] = "FORGED"
    elif fault.startswith("mutate-"):
        path = {"mutate-pidx": Path(h.args.output_dir) / "corpus/module_1.direct.pidx",
                "mutate-ast": h.ast / "shared-context.json", "mutate-source": h.sources[0],
                "mutate-evidence": h.evidence["source-manifest.json"]}[fault]
        path.chmod(0o644)
        path.write_bytes(path.read_bytes() + b"x")
    elif fault == "expire":
        h.budget.expired = True
    else:
        monkeypatch.setattr(h.ab, "_HELD_PERFORMANCE_LOCK", object())
    with pytest.raises((h.capture.IndexedCaptureError, h.ab.CompileABError, RuntimeError)):
        h.rank.verify_guarded_indexed_input_capture(result, ab=h.ab, performance_lock_token=h.token)
    assert h.ab._ACTIVE_WORKER_BUDGET is None
    if fault not in ("copy", "json"):
        output = Path(h.args.output_dir)
        assert json.loads((output / "capture.json").read_text())["status"] == "FAILED"
        assert not (output / "corpus/manifest.json").exists()
        assert not h.capture._LIVE_CAPTURES


@pytest.mark.parametrize("alias", ["symlink", "hardlink", "escape", "relative"])
def test_path_parser_rejects_aliases_and_nonowned_paths(harness, alias):
    h = harness
    path = h.root / "alias"
    if alias == "symlink":
        path.symlink_to(h.receipt)
    elif alias == "hardlink":
        os.link(h.receipt, path)
    elif alias == "escape":
        path = h.receipt
    else:
        path = Path("relative")
    with pytest.raises(h.capture.IndexedCaptureError):
        h.capture._path(str(path), root=h.source if alias == "escape" else h.root)


@pytest.mark.parametrize("raw", ["01", "-1", "+1", " 1", "1.0", "\u0661", ""])
def test_ordinal_parser_requires_canonical_ascii(harness, raw):
    with pytest.raises(harness.capture.IndexedCaptureError):
        harness.capture._integer(raw)


def test_duplicate_json_keys_rejected(harness):
    path = harness.root / "duplicate.json"
    path.write_text('{"schema":1,"schema":2}')
    with pytest.raises(harness.capture.IndexedCaptureError, match="duplicate JSON"):
        harness.capture._json(path)


def test_invalidation_storage_failure_preserves_original_exception(harness, monkeypatch):
    h = harness
    original = h.capture._persist

    def fail_failure_record(owner, name, value, *, directory=""):
        if value.get("status") == "FAILED":
            raise OSError("simulated storage failure")
        return original(owner, name, value, directory=directory)

    monkeypatch.setattr(h.capture, "_persist", fail_failure_record)
    h.fault = "nonzero"
    with pytest.raises(h.capture.IndexedCaptureError, match="rc=7"):
        h.rank.capture_indexed_input_guarded(h.args, **h.owner)
    assert h.ab._ACTIVE_WORKER_BUDGET is None
    assert not h.capture._LIVE_CAPTURES


@pytest.mark.parametrize("fault", ["symlink-parent", "input-overlap"])
def test_capture_rejects_output_alias_or_input_overlap(harness, fault):
    h = harness
    if fault == "symlink-parent":
        alias = h.root / "aliased-parent"
        target = h.root / "unrelated-output-parent"
        target.mkdir()
        alias.symlink_to(target, target_is_directory=True)
        h.args.output_dir = str(alias / "capture")
    else:
        h.args.output_dir = str(h.build / "capture")
    with pytest.raises(h.capture.IndexedCaptureError):
        h.rank.capture_indexed_input_guarded(h.args, **h.owner)
    assert not h.calls
    assert h.ab._ACTIVE_WORKER_BUDGET is None


def test_input_only_manifest_cannot_enter_standalone_rank(harness):
    h = harness
    result = h.rank.capture_indexed_input_guarded(h.args, **h.owner)
    with pytest.raises(h.rank.EmitRankError, match="live owner"):
        h.rank._load_items(Path(result["input_manifest"]["path"]), "all", 0)
    assert len(h.calls) == 3


@pytest.mark.parametrize("fault", ["final-input-validation", "registry-canonicalization"])
def test_failure_after_prepared_publication_revokes_all_success_receipts(harness, monkeypatch, fault):
    h = harness
    persist = h.capture._persist
    published = []

    def observe_publication(owner, name, value, *, directory=""):
        persist(owner, name, value, directory=directory)
        if name == "capture.json" and value.get("status") == "PREPARED":
            path = owner.path / directory / name
            assert json.loads(path.read_text())["status"] == "PREPARED"
            assert (path.parent / "corpus/manifest.json").exists()
            published.append(path)
            if fault == "final-input-validation":
                # Final real verify_inputs must discover a dependency changed
                # after success was persisted, not a stubbed validator failure.
                dependency = h.ast / "shared-context.json"
                dependency.chmod(0o644)
                dependency.write_text('{"changed_after_publication":true}')

    monkeypatch.setattr(h.capture, "_persist", observe_publication)
    if fault == "registry-canonicalization":
        def fail_canonicalization(value):
            assert published and value["status"] == "PREPARED"
            raise ValueError("simulated registration canonicalization failure")
        monkeypatch.setattr(h.ab, "_canonical_sha256", fail_canonicalization)
    with pytest.raises((h.capture.IndexedCaptureError, h.ab.CompileABError, ValueError)):
        h.rank.capture_indexed_input_guarded(h.args, **h.owner)
    assert len(published) == 1 and len(h.calls) == 3
    output = Path(h.args.output_dir)
    for name in ("capture.json", "capture.failure.json"):
        failure = json.loads((output / name).read_text())
        assert failure["status"] == "FAILED"
        assert failure["source_promotion_authorized"] is False
    assert not (output / "corpus/manifest.json").exists()
    assert not h.capture._LIVE_CAPTURES
    assert h.ab._ACTIVE_WORKER_BUDGET is None


@pytest.mark.parametrize("platform,machine", [("win32", "ARM64"), ("linux", "aarch64"),
                                             ("darwin", "x86_64")])
def test_public_entry_rejects_unsupported_platform_before_filesystem_capture(
        modules, monkeypatch, tmp_path, platform, machine):
    ab, capture, rank = modules
    budget, token = Budget(), object()
    calls = []

    def no_process(*args, **kwargs):
        calls.append(args)
        raise AssertionError("unsupported platform reached process boundary")

    boundary = SimpleNamespace(admit=lambda kwargs: budget, process=no_process)
    monkeypatch.setattr(ab, "_capture_test_harness", boundary, raising=False)
    for name, template in (("_validated_worker_budget", _budget_boundary),
                           ("_run_process", _process_boundary)):
        monkeypatch.setattr(ab, name, FunctionType(template.__code__, vars(ab)))
    monkeypatch.setattr(ab, "_HELD_PERFORMANCE_LOCK", token)
    monkeypatch.setattr(ab, "_ACTIVE_WORKER_BUDGET", None)
    # Replace only the support module bindings, not global sys/platform state.
    # require_claim_platform itself and the public dispatch are production code.
    monkeypatch.setattr(ab, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(ab, "platform", SimpleNamespace(machine=lambda: machine))
    with pytest.raises(ab.CompileABError, match="require Darwin arm64"):
        rank.capture_indexed_input_guarded(SimpleNamespace(), ab=ab,
            performance_lock_token=token, guard_result_path=tmp_path / "not-opened.json",
            expected_guard_command=["test"], expected_guard_cwd=tmp_path,
            campaign_deadline_monotonic=123)
    assert not calls
    assert ab._ACTIVE_WORKER_BUDGET is None
    assert not capture._LIVE_CAPTURES


@pytest.mark.parametrize("fault", ["unknown-source", "changed-hash"])
def test_public_capture_rejects_unadmitted_source_manifest(harness, fault):
    h = harness
    if fault == "unknown-source":
        h.source_manifest["files"]["pcc/unknown.py"] = "0" * 64
    else:
        h.source_manifest["files"]["pcc/helper.py"] = "0" * 64
    with pytest.raises(h.capture.IndexedCaptureError, match="source closure"):
        h.rank.capture_indexed_input_guarded(h.args, **h.owner)
    assert not h.calls
    assert not Path(h.args.output_dir).exists()
    assert h.ab._ACTIVE_WORKER_BUDGET is None
    assert not h.capture._LIVE_CAPTURES


def test_current_source_files_hashes_every_enumerated_member(modules, tmp_path, monkeypatch):
    ab, capture, _ = modules
    paths = [tmp_path / "first.py", tmp_path / "nested/second.py"]
    paths[1].parent.mkdir()
    for path, content in zip(paths, (b"first source\n", b"second source\n")):
        path.write_bytes(content)
    monkeypatch.setattr(capture, "REPO_ROOT", tmp_path)

    def enumerated(root):
        assert root == tmp_path
        return paths

    # Enumeration boundary only: real helper and real sha256_path consume all
    # members. This is not a test of repository build-closure enumeration.
    monkeypatch.setattr(ab, "build_source_files", enumerated)
    before = capture._current_source_files(ab)
    assert before == {p.relative_to(tmp_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in paths}
    paths[1].write_bytes(b"changed second source\n")
    after = capture._current_source_files(ab)
    assert before["first.py"] == after["first.py"]
    assert before["nested/second.py"] != after["nested/second.py"]


def test_invalidation_preserves_exception_with_broken_string_conversion(harness, monkeypatch):
    h = harness

    class UnprintableFailure(Exception):
        def __str__(self):
            raise RuntimeError("exception formatting failed")

    original = UnprintableFailure()

    def fail_worker(path):
        raise original

    monkeypatch.setattr(h, "worker", fail_worker)
    with pytest.raises(UnprintableFailure) as caught:
        h.rank.capture_indexed_input_guarded(h.args, **h.owner)
    assert caught.value is original
    output = Path(h.args.output_dir)
    for name in ("capture.json", "capture.failure.json"):
        failure = json.loads((output / name).read_text())
        assert failure["status"] == "FAILED"
        assert failure["exception_type"] == "UnprintableFailure"
        assert failure["message"] == "exception text unavailable"
    assert not (output / "corpus/manifest.json").exists()
    assert h.ab._ACTIVE_WORKER_BUDGET is None
    assert not h.capture._LIVE_CAPTURES


@pytest.mark.parametrize("target", ["output", "parent", "corpus"])
@pytest.mark.parametrize("replacement", ["symlink", "directory"])
@pytest.mark.parametrize("failure", ["ownership-check", "original-exception"])
def test_replaced_capture_directories_revoke_without_touching_either_tree(
        harness, monkeypatch, directory_descriptors, target, replacement, failure):
    h = harness
    parent = h.root / "output-parent"
    parent.mkdir()
    output = parent / "capture"
    h.args.output_dir = str(output)
    result = h.rank.capture_indexed_input_guarded(h.args, **h.owner)
    victim = {"output": output, "parent": parent, "corpus": output / "corpus"}[target]
    retained = h.root / "retained-owned-directory"
    victim.rename(retained)
    foreign = h.root / "foreign-directory"
    foreign.mkdir()
    foreign_capture = foreign / "capture" if target == "parent" else foreign
    _foreign_capture_tree(foreign_capture)
    if target == "corpus":
        (foreign / "manifest.json").write_bytes(b"foreign corpus manifest must not be removed\n")
    if replacement == "symlink":
        victim.symlink_to(foreign, target_is_directory=True)
        replacement_root = foreign
    else:
        foreign.rename(victim)
        replacement_root = victim
    foreign_before = _snapshot_without_following_links(replacement_root)
    retained_before = _snapshot_without_following_links(retained)
    victim_before = _snapshot_without_following_links(victim)
    output_before = _snapshot_without_following_links(output) if target == "corpus" else None
    original = RuntimeError("original failure after directory replacement") if (
        failure == "original-exception") else None

    _assert_live_failure(h, result, monkeypatch, directory_descriptors, original=original)

    assert _snapshot_without_following_links(replacement_root) == foreign_before
    assert _snapshot_without_following_links(retained) == retained_before
    assert _snapshot_without_following_links(victim) == victim_before
    if output_before is not None:
        assert _snapshot_without_following_links(output) == output_before
    if replacement == "symlink":
        assert victim.is_symlink() and os.readlink(victim) == str(foreign)


@pytest.mark.parametrize("failure", ["ownership-check", "original-exception"])
def test_replaced_parent_is_rejected_even_when_owned_output_inode_returns(
        harness, monkeypatch, directory_descriptors, failure):
    h = harness
    parent = h.root / "output-parent"
    parent.mkdir()
    output = parent / "capture"
    h.args.output_dir = str(output)
    result = h.rank.capture_indexed_input_guarded(h.args, **h.owner)
    output_identity = output.stat().st_dev, output.stat().st_ino
    retained = h.root / "retained-parent"
    parent.rename(retained)
    parent.mkdir()
    (retained / "capture").rename(output)
    assert (output.stat().st_dev, output.stat().st_ino) == output_identity
    (parent / "foreign-sentinel").write_bytes(b"new parent is not owned\n")
    before = _snapshot_without_following_links(parent)
    retained_before = _snapshot_without_following_links(retained)
    original = RuntimeError("original failure after parent replacement") if (
        failure == "original-exception") else None

    _assert_live_failure(h, result, monkeypatch, directory_descriptors, original=original)

    assert _snapshot_without_following_links(parent) == before
    assert _snapshot_without_following_links(retained) == retained_before


@pytest.mark.parametrize("name", ["capture.json", "corpus/manifest.json"])
@pytest.mark.parametrize("replacement", ["symlink", "hardlink", "regular"])
@pytest.mark.parametrize("failure", ["ownership-check", "original-exception"])
def test_replaced_owned_publications_revoke_without_removal_or_overwrite(
        harness, monkeypatch, directory_descriptors, name, replacement, failure):
    h = harness
    result = h.rank.capture_indexed_input_guarded(h.args, **h.owner)
    output = Path(h.args.output_dir)
    victim = output / name
    original_bytes, original_mode = victim.read_bytes(), stat.S_IMODE(victim.stat().st_mode)
    retained = victim.with_name("retained-" + victim.name)
    victim.rename(retained)
    foreign = h.root / "foreign-publication"
    foreign.mkdir()
    sentinel = foreign / "sentinel.json"
    # Equal content and mode make inode ownership, rather than a hash mismatch,
    # decisive. Replacing a read-only name needs no write to its old inode.
    sentinel.write_bytes(original_bytes)
    sentinel.chmod(original_mode)
    if replacement == "symlink":
        victim.symlink_to(sentinel)
    elif replacement == "hardlink":
        os.link(sentinel, victim)
    else:
        victim.write_bytes(original_bytes)
        victim.chmod(original_mode)
    assert victim.lstat().st_ino != retained.lstat().st_ino
    foreign_before = _snapshot_without_following_links(foreign)
    output_before = _snapshot_without_following_links(output)
    original = RuntimeError("original failure after publication replacement") if (
        failure == "original-exception") else None

    _assert_live_failure(h, result, monkeypatch, directory_descriptors, original=original)

    assert _snapshot_without_following_links(foreign) == foreign_before
    assert _snapshot_without_following_links(output) == output_before
    assert sentinel.read_bytes() == original_bytes


def test_legacy_fixed_temporary_aliases_are_never_opened_or_removed(
        harness, monkeypatch, directory_descriptors):
    h = harness
    foreign = h.root / "foreign-temporary-targets"
    foreign.mkdir()
    targets = ("capture.json", "capture.failure.json", "manifest.json")
    for name in targets:
        (foreign / name).write_bytes(("must not truncate: " + name + "\n").encode())
    foreign_before = _snapshot_without_following_links(foreign)
    persist, aliases = h.capture._persist, {}

    def plant_legacy_temporary(owner, name, value, *, directory=""):
        legacy = owner.path / directory / (name + ".tmp")
        if name in targets and name not in aliases:
            legacy.symlink_to(foreign / name)
            aliases[name] = legacy, _snapshot_without_following_links(legacy), os.readlink(legacy)
        return persist(owner, name, value, directory=directory)

    monkeypatch.setattr(h.capture, "_persist", plant_legacy_temporary)
    result = h.rank.capture_indexed_input_guarded(h.args, **h.owner)
    h.rank.verify_guarded_indexed_input_capture(result, ab=h.ab, performance_lock_token=h.token)
    original = RuntimeError("force ordinary cleanup with legacy aliases present")
    _assert_live_failure(h, result, monkeypatch, directory_descriptors, original=original)

    assert set(aliases) == set(targets)
    for path, metadata, target in aliases.values():
        assert path.is_symlink() and _snapshot_without_following_links(path) == metadata
        assert os.readlink(path) == target
    assert _snapshot_without_following_links(foreign) == foreign_before
    output = Path(h.args.output_dir)
    assert json.loads((output / "capture.json").read_text())["status"] == "FAILED"
    assert json.loads((output / "capture.failure.json").read_text())["status"] == "FAILED"
    assert not (output / "corpus/manifest.json").exists()


@pytest.mark.parametrize("directory,name", [("", "capture.json"), ("corpus", "manifest.json")])
@pytest.mark.parametrize("replacement", ["symlink", "hardlink", "regular"])
def test_exclusive_random_temporary_collisions_preserve_foreign_entries_and_error(
        harness, monkeypatch, directory_descriptors, directory, name, replacement):
    h = harness
    foreign = h.root / "foreign-collision-target"
    foreign.mkdir()
    sentinel = foreign / "sentinel"
    sentinel.write_bytes(b"exclusive temporary collision must never truncate this\n")
    persist, invalidate = h.capture._persist, h.capture._invalidate
    observed, invalidations = {}, []
    temporary_name = ".pcc-capture-controlled-collision.tmp"

    def collide(owner, published_name, value, *, directory=""):
        if published_name != name or directory != target_directory:
            return persist(owner, published_name, value, directory=directory)
        temporary = owner.path / directory / temporary_name
        if not observed:
            if replacement == "symlink":
                temporary.symlink_to(sentinel)
            elif replacement == "hardlink":
                os.link(sentinel, temporary)
            else:
                temporary.write_bytes(b"preexisting random temporary name\n")
            observed.update(path=temporary,
                            entry=_snapshot_without_following_links(temporary),
                            foreign=_snapshot_without_following_links(foreign))
        with monkeypatch.context() as patch:
            patch.setattr(h.capture, "_temporary_name", lambda: temporary_name)
            return persist(owner, published_name, value, directory=directory)

    def observe_invalidation(context, error):
        invalidations.append(error)
        return invalidate(context, error)

    target_directory = directory
    monkeypatch.setattr(h.capture, "_persist", collide)
    monkeypatch.setattr(h.capture, "_invalidate", observe_invalidation)
    with pytest.raises(h.capture.IndexedCaptureError, match="exclusive temporary") as caught:
        h.rank.capture_indexed_input_guarded(h.args, **h.owner)
    assert len(invalidations) == 1 and caught.value is invalidations[0]
    assert observed
    assert _snapshot_without_following_links(observed["path"]) == observed["entry"]
    assert _snapshot_without_following_links(foreign) == observed["foreign"]
    assert sentinel.read_bytes() == b"exclusive temporary collision must never truncate this\n"
    assert not directory_descriptors
    assert not h.capture._LIVE_CAPTURES
    assert h.ab._ACTIVE_WORKER_BUDGET is None


@pytest.mark.parametrize("directory,name", [("", "capture.json"), ("corpus", "manifest.json")])
@pytest.mark.parametrize("replacement", ["symlink", "hardlink", "regular", "shared-inode"])
def test_replaced_transient_temporary_inode_is_not_published_or_cleaned(
        harness, monkeypatch, directory_descriptors, directory, name, replacement):
    h = harness
    foreign = h.root / "foreign-transient-target"
    foreign.mkdir()
    sentinel = foreign / "sentinel"
    sentinel.write_bytes(b"foreign transient alias must remain untouched\n")
    sentinel.chmod(0o444)
    persist, invalidate = h.capture._persist, h.capture._invalidate
    observed, invalidations = {}, []
    temporary_name = ".pcc-capture-controlled-transient.tmp"
    target_directory = directory

    def swap_before_publication(owner, published_name, value, *, directory=""):
        selected = (published_name == name and directory == target_directory
                    and (directory or value.get("status") == "PREPARED"))
        if not selected or observed:
            return persist(owner, published_name, value, directory=directory)
        verify = owner.verify
        temporary = owner.path / directory / temporary_name
        retained = temporary.with_name("retained-owned-temporary")

        def replace_after_verification():
            verify()
            # The first verify runs before the temporary is opened. The next
            # runs after its bytes/mode are final but before its name is used.
            if observed or not temporary.exists():
                return
            if replacement == "shared-inode":
                # Keep the owned name/inode intact but revoke exclusivity. A
                # cleanup unlink would still mutate the foreign alias's nlink.
                sentinel.unlink()
                os.link(temporary, sentinel)
                retained_path = temporary
                assert temporary.samefile(sentinel) and temporary.stat().st_nlink == 2
            else:
                temporary.rename(retained)
                retained_path = retained
                if replacement == "symlink":
                    temporary.symlink_to(sentinel)
                elif replacement == "hardlink":
                    os.link(sentinel, temporary)
                else:
                    temporary.write_bytes(retained.read_bytes())
                    temporary.chmod(stat.S_IMODE(retained.stat().st_mode))
            observed.update(path=temporary, retained=retained_path,
                            entry=_snapshot_without_following_links(temporary),
                            original=_snapshot_without_following_links(retained_path),
                            foreign=_snapshot_without_following_links(foreign))

        with monkeypatch.context() as patch:
            patch.setattr(h.capture, "_temporary_name", lambda: temporary_name)
            patch.setattr(owner, "verify", replace_after_verification)
            return persist(owner, published_name, value, directory=directory)

    def observe_invalidation(context, error):
        invalidations.append(error)
        return invalidate(context, error)

    monkeypatch.setattr(h.capture, "_persist", swap_before_publication)
    monkeypatch.setattr(h.capture, "_invalidate", observe_invalidation)
    with pytest.raises(h.capture.IndexedCaptureError, match="temporary file was replaced") as caught:
        h.rank.capture_indexed_input_guarded(h.args, **h.owner)
    assert observed
    assert len(invalidations) == 1 and caught.value is invalidations[0]
    assert _snapshot_without_following_links(observed["path"]) == observed["entry"]
    assert _snapshot_without_following_links(observed["retained"]) == observed["original"]
    assert _snapshot_without_following_links(foreign) == observed["foreign"]
    if replacement == "shared-inode":
        assert observed["path"].samefile(sentinel) and sentinel.stat().st_nlink == 2
    output = Path(h.args.output_dir)
    assert json.loads((output / "capture.json").read_text())["status"] == "FAILED"
    assert not (output / "corpus/manifest.json").exists()
    assert not directory_descriptors
    assert not h.capture._LIVE_CAPTURES
    assert h.ab._ACTIVE_WORKER_BUDGET is None

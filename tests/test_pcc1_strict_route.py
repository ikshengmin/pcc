"""Finite route/argv contracts. Mock process outcomes are NOT native qualification."""
import ast
import inspect
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from pcc.frontends.python import pipeline
from tests import pcc1_route as route

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu",
           "arm64-apple-darwin", "x86_64-pc-windows-msvc")


@pytest.fixture(autouse=True)
def no_external_process(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("finite route controls must not execute a compiler")
    monkeypatch.setattr(subprocess, "Popen", forbidden)


@pytest.fixture
def bound_route(tmp_path, monkeypatch):
    from pcc.frontends.c.evaluator import c_evaluator

    compiler = tmp_path / "pcc1-fixture-not-executed"
    compiler.write_text("argv fixture, not a native executable\n")
    runtime = tmp_path / "runtime-fixture-not-linked.a"
    runtime.write_text("identity path only, not a runtime archive\n")
    log = tmp_path / "routes.jsonl"
    state = SimpleNamespace(native=[], host=[], multi=[], compiler=compiler,
                            runtime=runtime, log=log, response=None,
                            original_signature=inspect.signature(pipeline.compile_python))
    for key, value in (("PCC_TEST_COMPILER", compiler), ("PCC_RUNTIME_ARCHIVE", runtime),
                       (route.LOG_ENV, log), (route.STRICT_ENV, "1"), ("PCC_SELF_LINK", "pcc")):
        monkeypatch.setenv(key, str(value))
    monkeypatch.delenv("PCC_TEST_COMPILER_ROUTED", raising=False)

    def host(*args, **kwargs):
        state.host.append((args, kwargs))
        return "host-recorder-only"

    def multi(*args, **kwargs):
        state.multi.append((args, kwargs))
        return "multi-recorder-only"

    def run(argv, **kwargs):
        state.native.append((argv, kwargs))
        if isinstance(state.response, BaseException):
            raise state.response
        return state.response or SimpleNamespace(returncode=0, stdout="", stderr="")

    # Each explicit seam is restored even though install() assigns its wrappers.
    monkeypatch.setattr(pipeline, "compile_python", host)
    monkeypatch.setattr(pipeline, "compile_python_multi", multi)
    monkeypatch.setattr(c_evaluator, "_compile_translation_unit_artifact_job",
                        c_evaluator._compile_translation_unit_artifact_job)
    monkeypatch.setattr(route, "_HOST_C_ARTIFACT", route._HOST_C_ARTIFACT)
    monkeypatch.setattr(route, "subprocess", SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired))
    route.install()
    state.single = pipeline.compile_python
    state.multiple = pipeline.compile_python_multi
    return state


def _rows(state):
    return [json.loads(line) for line in state.log.read_text().splitlines()]


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("emit_ir", (False, True))
def test_single_native_argv_preserves_target_output_and_identity(bound_route, target, emit_ir):
    s = bound_route
    s.single("source with space.py", "output with space", target_triple=target,
             emit_llvm_only=emit_ir, verbose=True, runtime_archive=str(s.runtime),
             link_args=("-lm", "-Wl,--as-needed"))
    assert not s.host and not s.multi and len(s.native) == 1
    argv, options = s.native[0]
    assert argv == [str(s.compiler), "--backend", "self", "--python-libpython", "off",
                    "--ir-scaffold", "on", "--target", target, "--verbose",
                    "--link-arg", "-lm", "--link-arg", "-Wl,--as-needed"] + (
        ["--emit-llvm", "output with space", "source with space.py"] if emit_ir else
        ["source with space.py", "-o", "output with space"])
    assert options["timeout"] == 900 and options["capture_output"] is True and options["text"] is True
    assert options["env"]["PCC_RUNTIME_ARCHIVE"] == str(s.runtime)
    assert options["env"]["PCC_TEST_COMPILER_ROUTED"] == "1"
    assert options["env"]["PCC_HOST_PYTHON"] == "/usr/bin/false"
    rows = _rows(s)
    assert [r["event"] for r in rows] == ["attempt", "complete"]
    assert rows[0]["invocation"] == rows[1]["invocation"]
    assert rows[1]["outcome"] == "passed" and rows[1]["returncode"] == 0
    assert all(r["compiler"] == str(s.compiler) and r["runtime_archive"] == str(s.runtime)
               and r["target_triple"] == target for r in rows)


UNSUPPORTED = (
    {"libpython_mode": "on"}, {"libpython_mode": "auto"}, {"backend": "llvm"},
    {"recursive_stdlib": True}, {"profile": {}}, {"gpu_backend": "metal"},
    {"link_args": ("raw.o",)}, {"link_args": ("raw.obj",)},
)


@pytest.mark.parametrize("options", UNSUPPORTED)
def test_strict_unsupported_never_compiles_on_host(bound_route, options):
    s = bound_route
    with pytest.raises(pipeline.PyPipelineError, match="strict pcc1 route unsupported"):
        s.single("input.py", "output", **options)
    assert not s.host and not s.multi and not s.native
    assert len(_rows(s)) == 1 and _rows(s)[0]["route"] == "unsupported"


def test_strict_multi_is_visible_gap_not_host_fallback(bound_route):
    s = bound_route
    with pytest.raises(pipeline.PyPipelineError, match="multi-module API route"):
        s.multiple(["one.py", "two.py"], "output", module_names=["one", "two"])
    assert not s.host and not s.multi and not s.native
    assert _rows(s)[0]["route"] == "unsupported"


@pytest.mark.parametrize("kind", ("libpython", "profile", "object-container", "multi"))
def test_non_strict_retains_real_host_call_contract(bound_route, monkeypatch, kind):
    s = bound_route
    monkeypatch.setenv(route.STRICT_ENV, "0")
    if kind == "multi":
        options = {"module_names": ["one", "two"], "object_inputs": ("keep.o",)}
        assert s.multiple(["one.py", "two.py"], "out", **options) == "multi-recorder-only"
        assert s.multi == [((["one.py", "two.py"], "out"), options)]
    else:
        options = {"libpython_mode": "on"} if kind == "libpython" else (
            {"profile": {}} if kind == "profile" else {"object_inputs": "not-a-sequence.o"})
        assert s.single("in.py", "out", **options) == "host-recorder-only"
        assert len(s.host) == 1 and s.host[0][0] == ("in.py", "out")
        for key, value in options.items():
            assert s.host[0][1][key] is value
        assert "object_inputs" in s.host[0][1]
    assert not s.native and _rows(s)[0]["route"] == "host"


def _elf(path, machine):
    from pcc.backend.elf_x86_64 import (ElfObject, ElfSection, ElfSymbol, emit_relocatable,
        SHT_PROGBITS, SHF_ALLOC, SHF_EXECINSTR, STB_GLOBAL, STT_FUNC)
    data = b"\xc3" if machine == 62 else b"\xc0\x03\x5f\xd6"
    obj = ElfObject((ElfSection(".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 4, data),),
                    (ElfSymbol.null(), ElfSymbol("entry", 1, 0, len(data), STB_GLOBAL, STT_FUNC)),
                    machine=machine)
    path.write_bytes(emit_relocatable(obj))
    return path


@pytest.mark.parametrize("machine,target", ((62, TARGETS[0]), (183, TARGETS[1])))
def test_explicit_real_elf_object_survives_cli_grouping(bound_route, tmp_path, machine, target):
    from pcc.driver.cli_core import _python_link_input_groups
    from pcc.frontends.python.pipeline_self_backend_link import validate_object_inputs
    s = bound_route
    first = _elf(tmp_path / "first.o", machine)
    second = _elf(tmp_path / "second.obj", machine)
    s.single("in.py", "out", object_inputs=(first, second), target_triple=target)
    argv = s.native[0][0]
    args = [argv[i + 1] for i, value in enumerate(argv[:-1]) if value == "--link-arg"]
    objects, flags = _python_link_input_groups("self", args)
    assert objects == [str(first), str(second)] and flags == []
    assert validate_object_inputs(objects, out_path=str(tmp_path / "out"), target_triple=target) == tuple(objects)
    assert not s.host and len(s.native) == 1


@pytest.mark.parametrize("case", ("missing", "ir-output", "wrong-machine", "cc-gap"))
def test_object_negative_expressibility_does_not_fake_native_rejection(bound_route, tmp_path, monkeypatch, case):
    s = bound_route
    obj = tmp_path / "input.o"
    if case != "missing":
        _elf(obj, 62)
    if case == "cc-gap":
        monkeypatch.setenv("PCC_SELF_LINK", "cc")
        with pytest.raises(pipeline.PyPipelineError) as error:
            s.single("in.py", "out", object_inputs=(obj,))
        assert str(error.value) == ("strict pcc1 route unsupported: explicit object_inputs "
                                    "with the cc linker cannot retain their API meaning in the native CLI")
        assert not s.native and _rows(s)[0]["route"] == "unsupported"
    else:
        # This only verifies the adapter sends expressible invalid inputs to
        # the native invocation seam. A real pcc1 rejection is still unrun.
        s.response = SimpleNamespace(returncode=7, stdout="", stderr="fixture native rejection")
        with pytest.raises(pipeline.PyPipelineError, match="exited 7"):
            s.single("in.py", "out", object_inputs=(obj,), emit_llvm_only=case == "ir-output",
                     target_triple=TARGETS[1] if case == "wrong-machine" else TARGETS[0])
        assert len(s.native) == 1 and str(obj) in s.native[0][0]
        assert _rows(s)[-1]["outcome"] == "failed"
    assert not s.host


@pytest.mark.parametrize("mode", ("on", "auto"))
def test_launcher_strict_libpython_never_enters_host_cli(bound_route, mode):
    from pcc.driver.cli_launcher import _route_to_test_compiler
    with pytest.raises(RuntimeError, match="strict pcc1 route unsupported"):
        _route_to_test_compiler(["--python-libpython=" + mode, "in.py"])
    assert _rows(bound_route)[0]["route"] == "unsupported"
    assert not bound_route.host and not bound_route.native


def test_launcher_exec_preserves_exact_compiler_argv_and_guard(bound_route, monkeypatch):
    from pcc.driver.cli_launcher import _route_to_test_compiler
    calls = []
    monkeypatch.setattr(os, "execve", lambda path, argv, env: calls.append((path, argv, env)))
    args = ["--target", TARGETS[0], "--link-arg", "input.o", "in.py", "-o", "out"]
    _route_to_test_compiler(args)
    assert len(calls) == 1 and calls[0][:2] == (str(bound_route.compiler), [str(bound_route.compiler)] + args)
    assert calls[0][2]["PCC_TEST_COMPILER_ROUTED"] == "1"
    assert calls[0][2]["PCC_RUNTIME_ARCHIVE"] == str(bound_route.runtime)
    rows = _rows(bound_route)
    assert len(rows) == 1 and rows[0]["event"] == "exec_attempt"
    assert rows[0]["compiler"] == str(bound_route.compiler)
    assert rows[0]["runtime_archive"] == str(bound_route.runtime)
    assert "outcome" not in rows[0] and "returncode" not in rows[0]
    counts = route.summarize(str(bound_route.log))["counts"]
    assert counts["native_exec_attempted"] == 1 and counts["native"] == 0
    assert counts["native_attempted"] == 0 and counts["native_incomplete"] == 0
    finish = _source_function(ROOT / "tests/conftest.py", "pytest_sessionfinish", {"os": os})
    session = SimpleNamespace(config=SimpleNamespace(), exitstatus=0)
    finish(session, 0)
    assert session.exitstatus == 0  # Caller assertions, not exec attempts, own success.


def _source_function(path, name, namespace):
    tree = ast.parse(path.read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


@pytest.mark.parametrize("record_route", ("host", "unsupported"))
def test_actual_session_finish_fails_caught_gap(bound_route, record_route):
    route._record(record_route, "compile_python", "fixture gap")
    finish = _source_function(ROOT / "tests/conftest.py", "pytest_sessionfinish", {"os": os})
    session = SimpleNamespace(config=SimpleNamespace(), exitstatus=0)
    finish(session, 0)
    assert session.exitstatus == 1


def test_installed_route_has_real_pipeline_keyword_signature(bound_route):
    def shape(signature):
        return [(p.name, p.kind, p.default) for p in signature.parameters.values()]
    assert shape(inspect.signature(bound_route.single)) == shape(bound_route.original_signature)


@pytest.mark.parametrize("gaps", (False, True))
def test_actual_marker_run_binds_admitted_paths_and_refuses_gaps(tmp_path, monkeypatch, gaps):
    from scripts import ci_pytest_markers as gate, ci_worker_size_prior_gate as prior, bootstrap_platform
    from tests import runtime_fixture_provenance
    with monkeypatch.context() as inherited:
        for key in ("PYTEST_ADDOPTS", "PCC_PYTEST_AUTO_CLEAN", "PCC_WITH_THREADS", "PCC_REFCOUNT_KIND"):
            inherited.delenv(key, raising=False)
        inherited.setenv("PCC_TEST_COMPILER", "not-the-admitted-compiler")
        with pytest.raises(AssertionError, match="explicit pcc0/pcc1 fixture"):
            prior.environment()
    root = tmp_path / "root"
    root.mkdir()
    compiler = root / "compiler"
    runtime = root / "runtime.a"
    compiler.write_bytes(b"not executed compiler fixture")
    runtime.write_bytes(b"not linked runtime fixture")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    manifest_path = bundle / "manifest.json"
    manifest_path.write_text("{}")
    source = {"commit": "a" * 40, "source_sha256": "b" * 64, "clean_commit": True}
    manifest = {"source": source, "producer": {"run_id": 17}, "platform": "macos-arm64",
                "paths": {"compiler": "compiler", "runtime": "runtime.a", "shared_receipt": None},
                "files": {"compiler": {"sha256": gate.digest(compiler)},
                          "runtime.a": {"sha256": gate.digest(runtime)}}}
    events = []
    def admission(*args):
        events.append("admission")
        assert args[0] == root and args[1] == bundle
        return manifest
    def environment():
        events.append("environment")
        assert not os.environ.get("PCC_TEST_COMPILER")
        return {"PCC_TEST_COMPILER_STRICT": "1"}
    def guarded(directory, marker, env, platform_id, **options):
        events.append("guarded")
        assert env["PCC_TEST_COMPILER"] == env["PCC_CURRENT_PCC1"] == str(compiler)
        assert env["PCC_RUNTIME_ARCHIVE"] == str(runtime)
        assert env["PCC_CI_STAGE1_MANIFEST_SHA256"] == gate.digest(manifest_path)
        assert env["PCC_TEST_COMPILER_LOG"] == str(directory / "pcc1-routing.jsonl")
        assert options["timeout"] == 1800 and options["partition"] == "bootstrap"
        (directory / "result.json").write_text("{}")
    monkeypatch.setattr(gate, "ROOT", root)
    monkeypatch.setattr(gate, "host_platform", lambda: "macos-arm64")
    monkeypatch.setattr(gate, "workflow_lease", lambda: {"run_id": 17})
    monkeypatch.setattr(gate, "admit_in_place", admission)
    monkeypatch.setattr(prior, "environment", environment)
    monkeypatch.setattr(bootstrap_platform, "source_identity", lambda: source)
    monkeypatch.setattr(runtime_fixture_provenance, "_verified_test_runtime_archive", lambda *a, **k: None)
    monkeypatch.setattr(gate, "run_guarded", guarded)
    monkeypatch.setattr(gate, "summarize", lambda *a: {"status": "PASS_WITH_GAPS" if gaps else "PASS"})
    monkeypatch.setattr(os, "environ", dict(os.environ))
    os.environ.pop("PCC_TEST_COMPILER", None)
    args = SimpleNamespace(partition="bootstrap", marker="integration", in_place=True,
                           owner_report=None, platform="macos-arm64", bundle=bundle,
                           research_source=None, out=tmp_path / "out")
    if gaps:
        with pytest.raises(ValueError, match="missing, gated, skipped or xfailed"):
            gate.run(args)
        assert json.loads((args.out / "receipt.json").read_text())["status"] == "FAIL"
    else:
        assert gate.run(args)["status"] == "PASS"
    assert events == ["admission", "environment", "guarded"]
    # This tests binding/control flow using explicit admission/runner seams,
    # not the already separately qualified bundle validator or any native code.


@pytest.mark.parametrize("outcome", ("failed", "timeout", "error", "passed", "attempt-only"))
def test_native_invocation_outcomes_are_truthful_not_double_counted(bound_route, outcome):
    s = bound_route
    if outcome == "attempt-only":
        route._record("native", "compile_python", "", event="attempt", invocation="fixture:1")
        counts = route.summarize(str(s.log))["counts"]
        assert counts["native_attempted"] == 1 and counts["native"] == 0
        assert counts["native_completed"] == 0 and counts["native_incomplete"] == 1
        finish = _source_function(ROOT / "tests/conftest.py", "pytest_sessionfinish", {"os": os})
        session = SimpleNamespace(config=SimpleNamespace(), exitstatus=0)
        finish(session, 0)
        assert session.exitstatus == 1
        # Equal raw totals must not hide unfinished A behind duplicate B.
        route._record("native", "compile_python", "", event="attempt", invocation="fixture:2")
        for _ in range(2):
            route._record("native", "compile_python", "", event="complete",
                          invocation="fixture:2", outcome="passed", returncode=0)
        counts = route.summarize(str(s.log))["counts"]
        assert counts["native_attempted"] == 2 and counts["native_completed"] == 1
        assert counts["native"] == 1 and counts["native_incomplete"] == 1
        session.exitstatus = 0
        finish(session, 0)
        assert session.exitstatus == 1
        return
    if outcome == "failed":
        s.response = SimpleNamespace(returncode=3, stdout="", stderr="native failure fixture")
        expected = pipeline.PyPipelineError
    elif outcome == "timeout":
        s.response = subprocess.TimeoutExpired([str(s.compiler)], 900)
        expected = subprocess.TimeoutExpired
    elif outcome == "error":
        s.response = FileNotFoundError("invocation never launched")
        expected = FileNotFoundError
    else:
        expected = None
    if expected:
        with pytest.raises(expected):
            s.single("in.py", "out", target_triple=TARGETS[0])
    else:
        s.single("in.py", "out", target_triple=TARGETS[0])
    rows = _rows(s)
    assert len(rows) == 2 and rows[0]["event"] == "attempt" and rows[1]["event"] == "complete"
    assert rows[0]["invocation"] == rows[1]["invocation"]
    assert rows[1]["outcome"] == ("invocation_error" if outcome == "error" else outcome)
    counts = route.summarize(str(s.log))["counts"]
    assert counts["native_attempted"] == 1 and counts["native"] == int(outcome == "passed")
    assert counts["native_completed"] == 1 and counts["native_incomplete"] == 0
    assert sum(counts[k] for k in ("native_failed", "native_timeout", "native_error")) == int(outcome != "passed")
    assert len(s.native) == 1 and not s.host
    finish = _source_function(ROOT / "tests/conftest.py", "pytest_sessionfinish", {"os": os})
    session = SimpleNamespace(config=SimpleNamespace(), exitstatus=0)
    finish(session, 0)
    assert session.exitstatus == 0  # Expected negative outcomes are not hidden gaps.

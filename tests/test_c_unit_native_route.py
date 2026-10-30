"""Real C IR through the installed route, plus explicitly labeled process seams.

Without PCC_TEST_COMPILER the real cases are host controls, not native proof.
With an admitted compiler they must use it; this module must not unhook routing.
"""
from functools import partial
import hashlib
import inspect
import json
import multiprocessing
import os
from pathlib import Path
import pickle
import subprocess
from types import SimpleNamespace

import pytest

from pcc.driver.project import TranslationUnit
from pcc.frontends.c.evaluator import c_evaluator
from pcc.frontends.c.evaluator import native_artifact as worker
from pcc.frontends.c.parse.plyparser import ParseError
from pcc.frontends.python import pipeline
from tests import pcc1_route as route

TARGETS = ("x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu",
           "arm64-apple-darwin", "x86_64-pc-windows-msvc")


def _artifact_process_guard(original, compiler, runtime):
    def guarded(argv, *args, **kwargs):
        if (not compiler or not runtime or not isinstance(argv, (list, tuple))
                or len(argv) != 4 or argv[0] != compiler
                or argv[1] != "--pcc-c-unit-artifact-worker"
                or kwargs.get("shell", False)
                or not isinstance(kwargs.get("env"), dict)
                or kwargs["env"].get("PCC_RUNTIME_ARCHIVE") != runtime):
            raise AssertionError("C artifact controls forbid unbound or unrelated external processes")
        return original(argv, *args, **kwargs)
    return guarded


@pytest.fixture(autouse=True)
def forbid_external_process(monkeypatch):
    # Capture before any explicit process-seam fixture changes its environment.
    # No binding in the finite host gate => all external Popen stays forbidden.
    # Full strict CI => allow only the already admitted real native C entry.
    guard = _artifact_process_guard(subprocess.Popen,
                                   os.environ.get("PCC_TEST_COMPILER"),
                                   os.environ.get("PCC_RUNTIME_ARCHIVE"))
    monkeypatch.setattr(subprocess, "Popen", guard)


def request(tmp_path, **updates):
    value = {
        "schema": worker.REQUEST_SCHEMA,
        "unit": {"name": "independent-unit", "path": None,
                 "source": "int entry(void) { return 7; }"},
        "base_dir": str(tmp_path), "use_system_cpp": False,
        "include_dirs": None, "cpp_args": None, "cache_dir": str(tmp_path / "cache"),
        "use_compile_cache": False, "frontend_opt_level": None, "backend_sig": None,
        "target_triple": TARGETS[0], "fsanitize": None, "evaluation_entry": "entry",
    }
    value.update(updates)
    return value


def roundtrip(req):
    original = worker.compile_request(req)
    wire = json.loads(json.dumps(worker.artifact_to_wire(original)))
    restored = worker.artifact_from_wire(wire, req["unit"]["name"])
    assert restored == original
    assert type(restored["return_type"]) is type(original["return_type"])
    for name in original["func_return_types"]:
        assert type(restored["func_return_types"][name]) is type(original["func_return_types"][name])
    return restored


def _producer_cache_identity(req, monkeypatch):
    # The host helper's module constant is NOT the producer fingerprint when
    # the installed job is a native partial. Use its frozen compiler binding.
    job = c_evaluator._compile_translation_unit_artifact_job
    if getattr(job, "func", None) is route.compile_c_artifact:
        from pcc.tools.compiler_identity import native_executable_identity
        fingerprint = native_executable_identity(job.keywords["_compiler"])
        namespace = "native-compiler"
    else:
        fingerprint = c_evaluator._COMPILER_CACHE_FINGERPRINT
        namespace = "host-source"
    unit = req["unit"]
    base_dir = os.path.dirname(unit["path"]) if unit["path"] else req["base_dir"]
    preprocessed = c_evaluator._preprocess_translation_unit_source(
        unit["source"], base_dir, req["use_system_cpp"], req["include_dirs"],
        req["cpp_args"], req["target_triple"])
    name = unit["name"] + ("|entry=" + req["evaluation_entry"] if req["evaluation_entry"] else "")
    with monkeypatch.context() as local:
        local.setattr(c_evaluator, "_COMPILER_CACHE_FINGERPRINT", fingerprint)
        key = c_evaluator._compile_cache_key(name, preprocessed,
                                            req["frontend_opt_level"], req["backend_sig"], req["target_triple"])
    return {"key": key, "fingerprint": fingerprint, "namespace": namespace}


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("level", (None, 0))
def test_real_frontend_preserves_target_nullable_opt_and_actual_artifact(tmp_path, target, level):
    artifact = roundtrip(request(tmp_path, target_triple=target, frontend_opt_level=level))
    assert set(artifact) == set(worker.ARTIFACT_FIELDS)
    assert artifact["unit_name"] == "independent-unit"
    assert 'target triple = "' + target + '"' in artifact["ir_text"]
    assert artifact["func_return_types"]["entry"] == ("int", 32)
    assert artifact["pass_report"]["opt_level"] is level
    symbol = artifact["function_symbols"]["entry"]
    assert ["function", symbol, "entry"] in artifact["external_defs"]
    assert "define" in artifact["ir_text"] and "i32" in artifact["ir_text"]
    assert isinstance(artifact["pass_stats"], str)


def test_real_in_memory_source_name_and_local_include_origin(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    source = origin / "stale.c"
    source.write_text("this file must not be parsed")
    (origin / "local.h").write_text("#define LOCAL 7\n")
    wrong = tmp_path / "wrong"
    wrong.mkdir()
    (wrong / "local.h").write_text("#error wrong include origin\n")
    req = request(tmp_path, base_dir=str(wrong), cpp_args=["-DEXTRA=2"],
                  unit={"name": "not-the-file-name", "path": str(source),
                        "source": '#include "local.h"\nint entry(void) { return LOCAL + EXTRA; }'})
    artifact = roundtrip(req)
    assert artifact["unit_name"] == "not-the-file-name"
    assert artifact["func_return_types"]["entry"] == ("int", 32)
    assert source.read_text() == "this file must not be parsed"


@pytest.mark.parametrize("source,descriptor", (
    ("void entry(void) {}", ("void",)),
    ("float entry(void) { return 1.0f; }", ("float",)),
    ("double entry(void) { return 1.0; }", ("double",)),
    ("int *entry(int *p) { return p; }", ("ptr", ("int", 32))),
))
def test_real_return_descriptors_keep_recursive_container_type(tmp_path, source, descriptor):
    req = request(tmp_path)
    req["unit"]["source"] = source
    artifact = roundtrip(req)
    assert artifact["func_return_types"]["entry"] == descriptor
    if descriptor[0] == "ptr":
        assert type(artifact["func_return_types"]["entry"][1]) is tuple


def test_actual_frontend_parse_error_is_not_success(tmp_path):
    req = request(tmp_path)
    req["unit"]["source"] = "int entry( {"
    with pytest.raises(ParseError):
        worker.compile_request(req)
    for level in ("0", 0.75, False):
        assert worker.compile_request(request(tmp_path, frontend_opt_level=level))["pass_report"]["opt_level"] == 0
    with pytest.raises(TypeError):
        worker.compile_request(request(tmp_path, frontend_opt_level={"invalid": 1}))


def test_actual_cache_hit_list_descriptor_is_not_changed_to_tuple(tmp_path, monkeypatch):
    monkeypatch.delenv("PCC_DISABLE_COMPILE_CACHE", raising=False)
    req = request(tmp_path, use_compile_cache=True)
    cold = roundtrip(req)
    warm = roundtrip(req)
    assert type(cold["func_return_types"]["entry"]) is tuple
    assert type(warm["func_return_types"]["entry"]) is list
    assert warm["func_return_types"]["entry"] == list(cold["func_return_types"]["entry"])
    for field in worker.ARTIFACT_FIELDS:
        if field not in ("return_type", "func_return_types"):
            assert warm[field] == cold[field]
    identity = _producer_cache_identity(req, monkeypatch)
    cached_path = Path(c_evaluator._compile_cache_path(req["cache_dir"], identity["key"]))
    # Check the actual producer's file, not an assumed host key. Preprocessing
    # here only verifies the key; returned IR/metadata still came from the job.
    cached_bytes = cached_path.read_bytes()
    assert json.loads(cached_bytes) == json.loads(json.dumps(cold))
    # Exercise actual key construction with its real native identity function,
    # using inert bytes (not a native executable). Same source/options do not
    # alias the host process's key. This is partition, not native execution proof.
    host_key = c_evaluator._compile_cache_key("unit", "same source")
    image = tmp_path / "inert-identity-bytes"
    image.write_bytes(b"not an executable; identity fixture only")
    with monkeypatch.context() as local:
        local.setattr(c_evaluator, "sys", SimpleNamespace(
            implementation=SimpleNamespace(name="pcc"), executable=str(image)))
        native_identity = c_evaluator._compiler_cache_fingerprint()
    assert native_identity != c_evaluator._COMPILER_CACHE_FINGERPRINT
    with monkeypatch.context() as local:
        local.setattr(c_evaluator, "_COMPILER_CACHE_FINGERPRINT", native_identity)
        native_key = c_evaluator._compile_cache_key("unit", "same source")
    assert native_key != host_key
    assert not Path(c_evaluator._compile_cache_path(req["cache_dir"], native_key)).exists()
    assert roundtrip(req) == warm  # Existing producer cache was neither cleared nor changed.
    assert cached_path.read_bytes() == cached_bytes


@pytest.mark.parametrize("bad", ("extra-field", "unit-fields", "sequence"))
def test_invalid_request_is_rejected_without_compiling(tmp_path, monkeypatch, bad):
    req = request(tmp_path)
    if bad == "extra-field":
        req["unexpected"] = True
    elif bad == "unit-fields":
        req["unit"]["unexpected"] = True
    else:
        req["include_dirs"] = {"invalid": "directory"}
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid request reached compiler")
    monkeypatch.setattr(c_evaluator, "_compile_translation_unit_artifact_job", forbidden)
    with pytest.raises(ValueError):
        worker.compile_request(req)


@pytest.mark.parametrize("case", ("alias", "existing-output", "malformed-json"))
def test_fresh_output_ownership_and_error_envelope(tmp_path, case):
    src = tmp_path / "request.json"
    src.write_text("{" if case == "malformed-json" else json.dumps(request(tmp_path)))
    out = src if case == "alias" else tmp_path / "out.json"
    if case == "existing-output":
        out.write_text("keep-existing")
    if case in ("alias", "existing-output"):
        before = out.read_bytes()
        with pytest.raises(ValueError):
            worker.run_native_c_artifact(str(src), str(out))
        assert out.read_bytes() == before
    else:
        assert worker.run_native_c_artifact(str(src), str(out)) == 1
        response = json.loads(out.read_text())
        assert set(response) == {"schema", "request_sha256", "outcome", "error_type", "error_message"}
        assert response["outcome"] == "failed" and response["error_type"] == "JSONDecodeError"
        assert response["request_sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()


@pytest.mark.parametrize("bad", ("missing", "unit", "external", "return", "symbol", "function-type"))
def test_wire_rejects_missing_or_fabricated_metadata(tmp_path, bad):
    artifact = worker.artifact_to_wire(worker.compile_request(request(tmp_path)))
    if bad == "missing":
        del artifact["pass_report"]
    elif bad == "unit":
        artifact["unit_name"] = "other-unit"
    elif bad == "external":
        artifact["external_defs"] = [["unknown", "symbol", "name"]]
    elif bad == "return":
        artifact["return_type"] = {"container": "tuple", "items": ["int", -1]}
    elif bad == "symbol":
        artifact["function_symbols"]["entry"] = 3
    else:
        artifact["func_return_types"]["entry"] = {"container": "set", "items": ["int", 32]}
    with pytest.raises(ValueError):
        worker.artifact_from_wire(artifact, "independent-unit")


@pytest.fixture
def bound(tmp_path, monkeypatch):
    compiler = tmp_path / "pcc1-fixture-NOT-EXECUTED"
    compiler.write_text("not executable")
    runtime = tmp_path / "runtime-fixture-NOT-LINKED.a"
    runtime.write_text("identity only")
    log = tmp_path / "routing.jsonl"
    installed_job = c_evaluator._compile_translation_unit_artifact_job
    # This fixture explicitly simulates a native process using the genuine host
    # frontend. Do not recursively invoke a previously installed native partial.
    # The direct real-IR tests outside this fixture retain the installed route.
    actual_job = (route._HOST_C_ARTIFACT
                  if getattr(installed_job, "func", None) is route.compile_c_artifact
                  else installed_job)
    assert actual_job is not None and getattr(actual_job, "func", None) is not route.compile_c_artifact
    state = SimpleNamespace(compiler=compiler, runtime=runtime, log=log, calls=[],
                            mode=None, host_calls=[], actual_job=actual_job)
    for key, value in (("PCC_TEST_COMPILER", compiler), ("PCC_RUNTIME_ARCHIVE", runtime),
                       (route.LOG_ENV, log), (route.STRICT_ENV, "1")):
        monkeypatch.setenv(key, str(value))
    monkeypatch.setattr(pipeline, "compile_python", pipeline.compile_python)
    monkeypatch.setattr(pipeline, "compile_python_multi", pipeline.compile_python_multi)
    monkeypatch.setattr(c_evaluator, "_compile_translation_unit_artifact_job", installed_job)
    monkeypatch.setattr(route, "_HOST_C_ARTIFACT", route._HOST_C_ARTIFACT)

    def run(argv, **options):
        req_path, out_path = Path(argv[-2]), Path(argv[-1])
        raw = req_path.read_bytes()
        state.calls.append((argv, options, json.loads(raw)))
        if isinstance(state.mode, BaseException):
            raise state.mode
        if state.mode == "missing-output":
            return SimpleNamespace(returncode=0, stdout="", stderr="missing fixture output")
        if state.mode in ("ParseError", "TypeError", "FileNotFoundError"):
            response = {"schema": worker.RESPONSE_SCHEMA,
                        "request_sha256": hashlib.sha256(raw).hexdigest(),
                        "outcome": "failed", "error_type": state.mode,
                        "error_message": "explicit native-process failure seam"}
            out_path.write_text(json.dumps(response))
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        # This seam executes the genuine host frontend, not a native compiler.
        # Restore only its actual job while producing the real artifact.
        with monkeypatch.context() as local:
            local.setattr(c_evaluator, "_compile_translation_unit_artifact_job", actual_job)
            rc = worker.run_native_c_artifact(str(req_path), str(out_path))
        if state.mode in ("wrong-digest", "bad-outcome"):
            response = json.loads(out_path.read_text())
            if state.mode == "wrong-digest":
                response["request_sha256"] = "0" * 64
            else:
                response["outcome"] = "failed"
            out_path.write_text(json.dumps(response))
        return SimpleNamespace(returncode=rc, stdout="", stderr="")

    monkeypatch.setattr(route, "subprocess", SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired))
    route.install()
    state.job = c_evaluator._compile_translation_unit_artifact_job
    return state


def invoke(state, tmp_path, **updates):
    req = request(tmp_path, **updates)
    unit = TranslationUnit(**req["unit"])
    return state.job(unit, req["base_dir"], req["use_system_cpp"], req["include_dirs"],
                     req["cpp_args"], req["cache_dir"], req["use_compile_cache"],
                     req["frontend_opt_level"], req["backend_sig"], req["target_triple"],
                     req["fsanitize"], req["evaluation_entry"])


def records(state):
    return [json.loads(line) for line in state.log.read_text().splitlines()]


def test_route_exact_bound_argv_identity_metadata_and_cleanup(bound, tmp_path, monkeypatch):
    monkeypatch.setenv("PCC_TEST_COMPILER", "untrusted-later-override")
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", "untrusted-later-runtime")
    artifact = invoke(bound, tmp_path, use_compile_cache=None, use_system_cpp=0,
                      fsanitize=(), frontend_opt_level=None)
    assert artifact["func_return_types"]["entry"] == ("int", 32)
    argv, options, req = bound.calls[0]
    assert argv[:2] == [str(bound.compiler), "--pcc-c-unit-artifact-worker"] and len(argv) == 4
    assert options["timeout"] == 900 and options["capture_output"] is True and options["text"] is True
    assert options["env"]["PCC_RUNTIME_ARCHIVE"] == str(bound.runtime)
    assert options["env"]["PCC_HOST_PYTHON"] == "/usr/bin/false"
    assert req["use_compile_cache"] is None and req["use_system_cpp"] == 0
    assert req["frontend_opt_level"] is None and req["fsanitize"] == []
    assert not Path(argv[2]).exists() and not Path(argv[3]).exists()
    rows = records(bound)
    assert [r["event"] for r in rows] == ["attempt", "complete"]
    assert rows[0]["invocation"] == rows[1]["invocation"]
    assert rows[1]["outcome"] == "passed"
    assert all(r["compiler"] == str(bound.compiler) and r["runtime_archive"] == str(bound.runtime) for r in rows)


@pytest.mark.parametrize("kind,error", (("ParseError", ParseError), ("TypeError", TypeError)))
def test_reported_native_failure_retains_canonical_error_not_success(bound, tmp_path, kind, error):
    bound.mode = kind
    with pytest.raises(error, match="explicit native-process failure seam"):
        invoke(bound, tmp_path)
    assert records(bound)[-1]["outcome"] == "failed"
    counts = route.summarize(str(bound.log))["counts"]
    assert counts["native"] == 0 and counts["native_failed"] == 1 and counts["native_incomplete"] == 0
    assert not Path(bound.calls[0][0][-1]).exists()
    if kind == "TypeError":
        bound.mode = "FileNotFoundError"
        with pytest.raises(RuntimeError, match="native C FileNotFoundError"):
            invoke(bound, tmp_path)
        assert records(bound)[-1]["route"] == "unsupported"
        assert route.summarize(str(bound.log))["counts"]["unsupported"] == 1


@pytest.mark.parametrize("mode", ("missing-output", "wrong-digest", "bad-outcome"))
def test_native_response_gap_never_uses_host_fallback(bound, tmp_path, mode):
    bound.mode = mode
    with pytest.raises(RuntimeError, match="artifact unavailable or invalid"):
        invoke(bound, tmp_path)
    assert records(bound)[-1]["outcome"] == "invalid_artifact"
    assert route.summarize(str(bound.log))["counts"]["native"] == 0
    assert not Path(bound.calls[0][0][-1]).exists()


@pytest.mark.parametrize("failure", (subprocess.TimeoutExpired("fixture", 900), OSError("fixture error")))
def test_invocation_failure_is_recorded_and_private_files_removed(bound, tmp_path, failure):
    bound.mode = failure
    with pytest.raises(type(failure)):
        invoke(bound, tmp_path)
    rows = records(bound)
    assert rows[-1]["outcome"] == ("timeout" if isinstance(failure, subprocess.TimeoutExpired) else "invocation_error")
    assert rows[0]["invocation"] == rows[-1]["invocation"]
    assert not Path(bound.calls[0][0][-2]).exists()


@pytest.mark.parametrize("strict", (True, False))
def test_nonrepresentable_option_is_visible_gap_or_original_nonstrict_call(bound, tmp_path, monkeypatch, strict):
    token = object()
    def host(*args):
        bound.host_calls.append(args)
        return "host seam"
    monkeypatch.setattr(route, "_HOST_C_ARTIFACT", host)
    monkeypatch.setenv(route.STRICT_ENV, "1" if strict else "0")
    if strict:
        with pytest.raises(pipeline.PyPipelineError, match="no JSON representation"):
            invoke(bound, tmp_path, frontend_opt_level=token)
        assert not bound.host_calls and records(bound)[0]["route"] == "unsupported"
    else:
        assert invoke(bound, tmp_path, frontend_opt_level=token) == "host seam"
        assert len(bound.host_calls) == 1 and bound.host_calls[0][7] is token
        assert records(bound)[0]["route"] == "host"
    assert not bound.calls


class _HostOptLevel:
    def __int__(self):
        return 0


def _spawn_host_fallback(job, base_dir, output):
    # One harmless spawn process, not a compiler executable. No pytest/conftest
    # setup in this process: exercise the actual uninstalled-child fallback.
    def forbidden(*args, **kwargs):
        raise AssertionError("spawn fixture must not invoke external processes")
    subprocess.Popen = forbidden
    os.environ[route.STRICT_ENV] = "0"
    try:
        assert route._HOST_C_ARTIFACT is None
        assert c_evaluator._compile_translation_unit_artifact_job is not job
        unit = TranslationUnit("spawn-unit", None, "int entry(void) { return 7; }")
        artifact = job(unit, base_dir, False, None, None, None, False,
                       _HostOptLevel(), None, TARGETS[0], None, "entry")
        output.put({"unit": artifact["unit_name"], "descriptor": artifact["func_return_types"]["entry"],
                    "opt_level": artifact["pass_report"]["opt_level"]})
    except BaseException as error:
        output.put({"error": type(error).__name__, "message": str(error)})


def test_installed_partial_is_picklable_and_carries_exact_admission(bound, tmp_path, monkeypatch):
    restored = pickle.loads(pickle.dumps(bound.job))
    assert isinstance(restored, partial) and restored.func is route.compile_c_artifact
    assert restored.keywords == {"_compiler": str(bound.compiler), "_runtime_archive": str(bound.runtime)}
    original = inspect.signature(bound.actual_job)
    current = inspect.signature(restored)
    for name, parameter in original.parameters.items():
        assert current.parameters[name] == parameter
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    child = context.Process(target=_spawn_host_fallback, args=(restored, str(tmp_path), output))
    child.start()
    try:
        value = output.get(timeout=15)
        child.join(timeout=5)
        assert not child.is_alive() and child.exitcode == 0
        assert value == {"unit": "spawn-unit", "descriptor": ("int", 32), "opt_level": 0}
    finally:
        if child.is_alive():
            child.terminate()
            child.join(timeout=5)
        if child.is_alive():
            child.kill()
            child.join(timeout=5)
        output.close()
        output.join_thread()
    assert not child.is_alive()
    # The earlier Python-route fixture also calls install(): exercise its real
    # teardown registration so this new C hook cannot leak into later tests.
    from tests.test_pcc1_strict_route import bound_route
    before = c_evaluator._compile_translation_unit_artifact_job
    host_before = route._HOST_C_ARTIFACT
    with monkeypatch.context() as local:
        bound_route.__wrapped__(tmp_path, local)
        assert c_evaluator._compile_translation_unit_artifact_job is not before
    assert c_evaluator._compile_translation_unit_artifact_job is before
    assert route._HOST_C_ARTIFACT is host_before


def test_existing_multi_unit_executor_policy_and_order_are_preserved(bound, tmp_path, monkeypatch):
    import concurrent.futures
    seen = []
    class Executor:
        def __init__(self, max_workers):
            seen.append(("workers", max_workers))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def map(self, function, *iterables):
            assert function is bound.job
            rows = list(zip(*iterables))
            seen.append(("units", [args[0].name for args in rows]))
            return [function(*args) for args in rows]
    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", Executor)
    units = [TranslationUnit(name, None, "int entry(void) { return 7; }") for name in ("first", "second")]
    evaluator = SimpleNamespace(backend_sig=None, target_triple=TARGETS[0])
    outputs = c_evaluator.CEvaluator._compile_translation_units(
        evaluator, units, str(tmp_path), False, 6, cache_dir=str(tmp_path / "cache"), use_compile_cache=False)
    assert seen == [("workers", 2), ("units", ["first", "second"])]
    assert [a["unit_name"] for a in outputs] == ["first", "second"]
    assert len(bound.calls) == 2


def test_actual_cli_refuses_host_entry_before_compile(tmp_path, monkeypatch):
    from pcc.driver import cli_bootstrap
    def forbidden(*args, **kwargs):
        raise AssertionError("host CLI entered native worker")
    monkeypatch.setattr(worker, "run_native_c_artifact", forbidden)
    assert cli_bootstrap._bootstrap_cli_main_impl(
        ["--pcc-c-unit-artifact-worker", str(tmp_path / "in"), str(tmp_path / "out")]) == 2
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("case", ("unbound", "exact", "wrong-compiler", "wrong-entry", "extra-arg", "wrong-runtime", "shell"))
def test_process_guard_requires_original_exact_compiler_and_runtime(case):
    calls = []
    def original(argv, *args, **kwargs):
        calls.append((argv, args, kwargs))
        return "Popen seam only; no process"
    compiler = None if case == "unbound" else "/admitted/pcc1"
    guard = _artifact_process_guard(original, compiler, "/admitted/runtime.a")
    argv = ["/admitted/pcc1", "--pcc-c-unit-artifact-worker", "request.json", "artifact.json"]
    options = {"env": {"PCC_RUNTIME_ARCHIVE": "/admitted/runtime.a"}}
    if case == "wrong-compiler":
        argv[0] = "/other/pcc1"
    elif case == "wrong-entry":
        argv[1] = "--not-artifact"
    elif case == "extra-arg":
        argv.append("extra")
    elif case == "wrong-runtime":
        options["env"]["PCC_RUNTIME_ARCHIVE"] = "/other/runtime.a"
    elif case == "shell":
        options["shell"] = True
    if case == "exact":
        assert guard(argv, **options) == "Popen seam only; no process"
        assert len(calls) == 1 and calls[0][0] == argv
    else:
        with pytest.raises(AssertionError, match="forbid unbound or unrelated"):
            guard(argv, **options)
        assert not calls
        if case == "wrong-runtime":
            with pytest.raises(AssertionError, match="forbid unbound or unrelated"):
                guard(argv, env=None)
            assert not calls


def test_explicit_process_fixture_preserves_preinstalled_route_without_recursion(tmp_path, monkeypatch):
    original_job = c_evaluator._compile_translation_unit_artifact_job
    host_job = (route._HOST_C_ARTIFACT
                if getattr(original_job, "func", None) is route.compile_c_artifact else original_job)
    prior = partial(route.compile_c_artifact, _compiler="/outer/admitted/pcc1",
                    _runtime_archive="/outer/admitted/runtime.a")
    monkeypatch.setattr(c_evaluator, "_compile_translation_unit_artifact_job", prior)
    monkeypatch.setattr(route, "_HOST_C_ARTIFACT", host_job)
    with monkeypatch.context() as local:
        state = bound.__wrapped__(tmp_path, local)
        assert state.actual_job is host_job
        result = invoke(state, tmp_path)
        assert result["func_return_types"]["entry"] == ("int", 32)
        assert len(state.calls) == 1
    assert c_evaluator._compile_translation_unit_artifact_job is prior
    assert route._HOST_C_ARTIFACT is host_job


def test_cache_identity_uses_installed_compiler_binding_not_host_constant(tmp_path, monkeypatch):
    from pcc.tools.compiler_identity import native_executable_identity
    req = request(tmp_path)
    current = c_evaluator._compile_translation_unit_artifact_job
    host = route._HOST_C_ARTIFACT if getattr(current, "func", None) is route.compile_c_artifact else current
    assert host is not None
    monkeypatch.setattr(c_evaluator, "_compile_translation_unit_artifact_job", host)
    host_identity = _producer_cache_identity(req, monkeypatch)
    image = tmp_path / "inert-compiler-identity-NOT-EXECUTED"
    image.write_bytes(b"finite identity bytes, not executable")
    native = partial(route.compile_c_artifact, _compiler=str(image), _runtime_archive="not-linked")
    monkeypatch.setattr(c_evaluator, "_compile_translation_unit_artifact_job", native)
    monkeypatch.setenv("PCC_TEST_COMPILER", "wrong-later-env-path")
    native_identity = _producer_cache_identity(req, monkeypatch)
    assert host_identity["namespace"] == "host-source"
    assert native_identity["namespace"] == "native-compiler"
    assert native_identity["fingerprint"] == native_executable_identity(str(image))
    assert native_identity["fingerprint"] != host_identity["fingerprint"]
    assert native_identity["key"] != host_identity["key"]
    assert c_evaluator._COMPILER_CACHE_FINGERPRINT == host_identity["fingerprint"]

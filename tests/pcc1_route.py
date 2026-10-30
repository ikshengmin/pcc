"""Route the test suite's compiles through a native pcc.

``PCC_TEST_COMPILER=/path/to/pcc1`` makes every in-process
``pipeline.compile_python`` call in the session compile with that binary
instead of host CPython pcc, and ``pcc.driver.cli_launcher`` (``uv run pcc``,
``.venv/bin/pcc``) exec it.  The whole suite then checks the native compiler
the same way it checks the host one.

A call the native CLI cannot express (libpython on/auto, the LLVM backend,
``recursive_stdlib``, ``profile=`` or multi-module compiles) is rejected before
host compilation in strict mode. Non-strict use retains the host fallback.
Every compile -- routed or not -- is appended to
``PCC_TEST_COMPILER_LOG`` (a per-session default is created when it is unset),
and the session summary counts native compiles against host fallbacks by
reason, so the host-only surface is measured rather than silently skipped.
``PCC_TEST_COMPILER_STRICT=1`` fails the session on any unsupported call or host
fallback. Target, raw linker flags and admitted object inputs use the existing
native full CLI; object-shaped raw linker arguments remain explicitly unsupported
because that CLI would change their Python API meaning by reclassifying them.

``PCC_RUNTIME_ARCHIVE`` is required: pcc1 does not rebuild its own runtime.
"""

from __future__ import annotations

import json
import itertools
from functools import partial
import os
import subprocess
import sys


LOG_ENV = "PCC_TEST_COMPILER_LOG"
STRICT_ENV = "PCC_TEST_COMPILER_STRICT"
_CALL_IDS = itertools.count()
_HOST_C_ARTIFACT = None


def _record(route: str, kind: str, detail: str, **identity) -> None:
    path = os.environ.get(LOG_ENV, "")
    if not path:
        raise RuntimeError(f"{LOG_ENV} is unset; pcc1_route.install() sets it")
    record = {
        "route": route,
        "kind": kind,
        "detail": detail,
        "test": os.environ.get("PYTEST_CURRENT_TEST", ""),
        **identity,
    }
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def log_routed(kind: str, **identity) -> None:
    _record("native", kind, "", **identity)


def log_unrouted(kind: str, detail: str) -> None:
    _record("host", kind, detail)


def summarize(path: str) -> dict:
    """Count compiles per route, and host fallbacks per (kind, reason)."""
    counts = {"native": 0, "native_attempted": 0, "native_completed": 0, "native_failed": 0,
              "native_exec_attempted": 0,
              "native_timeout": 0, "native_error": 0, "host": 0, "unsupported": 0}
    reasons: dict[str, int] = {}
    attempted: set[str] = set()
    completed: set[str] = set()
    anonymous_attempts = 0
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                route = record.get("route", "host")
                if route == "native":
                    if record.get("event") == "exec_attempt":
                        counts["native_exec_attempted"] += 1
                        continue  # exec cannot report completion from this process.
                    if record.get("event") == "attempt":
                        invocation = record.get("invocation")
                        if isinstance(invocation, str) and invocation:
                            if invocation in attempted:
                                continue
                            attempted.add(invocation)
                        else:
                            anonymous_attempts += 1
                        counts["native_attempted"] += 1
                        continue  # An attempt is not a completed or passed compile.
                    if record.get("event") == "complete":
                        invocation = record.get("invocation")
                        if isinstance(invocation, str) and invocation:
                            if invocation in completed:
                                continue
                            completed.add(invocation)
                        counts["native_completed"] += 1
                    outcome = record.get("outcome", "passed")
                    if outcome != "passed":
                        key = {"failed": "native_failed", "timeout": "native_timeout"}.get(outcome, "native_error")
                        counts[key] += 1
                        continue
                counts[route] = counts.get(route, 0) + 1
                if route in ("host", "unsupported"):
                    key = record.get("kind", "?") + ": " + record.get("detail", "")
                    reasons[key] = reasons.get(key, 0) + 1
    # A duplicate completion for B cannot cancel an unfinished invocation A.
    # Missing attempt identities stay incomplete rather than matching by count.
    counts["native_incomplete"] = len(attempted - completed) + anonymous_attempts
    return {"counts": counts, "host_reasons": reasons}


def native_env(runtime_archive=None) -> dict:
    """Environment for a native compile: no host Python, cc, or auto-build."""
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    if runtime_archive:
        env["PCC_RUNTIME_ARCHIVE"] = str(runtime_archive)
    env.update(
        PCC_HOST_PYTHON="/usr/bin/false",
        PCC_RUNTIME_CC="/usr/bin/false",
        PCC_NO_AUTO_PCC1="1",
        PCC_TEST_COMPILER_ROUTED="1",
    )
    return env


def compile_c_artifact(
    unit, base_dir, use_system_cpp, include_dirs, cpp_args, cache_dir,
    use_compile_cache, frontend_opt_level=None, backend_sig=None,
    target_triple=None, fsanitize=None, evaluation_entry=None, *,
    _compiler=None, _runtime_archive=None,
):
    import hashlib
    import tempfile

    # The installed partial carries the admitted identity through the existing
    # ProcessPoolExecutor, including spawn, not a later environment override.
    if _compiler is None or _runtime_archive is None:
        raise RuntimeError("C artifact route has no installed compiler identity")
    compiler = _compiler
    from pcc.frontends.c.evaluator.native_artifact import (
        REQUEST_SCHEMA, RESPONSE_SCHEMA, artifact_from_wire,
    )

    def path(value):
        return os.fspath(value) if isinstance(value, os.PathLike) else value

    try:
        request = {
            "schema": REQUEST_SCHEMA,
            "unit": {"name": unit.name, "path": path(unit.path), "source": unit.source},
            "base_dir": path(base_dir), "use_system_cpp": use_system_cpp,
            "include_dirs": [path(x) for x in include_dirs] if isinstance(include_dirs, (list, tuple)) else include_dirs,
            "cpp_args": cpp_args,
            "cache_dir": path(cache_dir), "use_compile_cache": use_compile_cache,
            "frontend_opt_level": frontend_opt_level, "backend_sig": backend_sig,
            "target_triple": target_triple,
            "fsanitize": fsanitize,
            "evaluation_entry": evaluation_entry,
        }
        request_bytes = json.dumps(request, ensure_ascii=True, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as error:
        reason = "C artifact API value has no JSON representation: " + str(error)
        if os.environ.get(STRICT_ENV) == "1":
            from pcc.frontends.python import pipeline

            _record("unsupported", "compile_c_artifact", reason)
            raise pipeline.PyPipelineError(reason) from error
        log_unrouted("compile_c_artifact", reason)
        from pcc.frontends.c.evaluator import c_evaluator
        host_job = _HOST_C_ARTIFACT or c_evaluator._compile_translation_unit_artifact_job
        if host_job is compile_c_artifact or getattr(host_job, "func", None) is compile_c_artifact:
            raise RuntimeError("original C artifact job unavailable")
        return host_job(
            unit, base_dir, use_system_cpp, include_dirs, cpp_args, cache_dir,
            use_compile_cache, frontend_opt_level, backend_sig, target_triple,
            fsanitize, evaluation_entry,
        )
    request_sha = hashlib.sha256(request_bytes).hexdigest()
    env = native_env(_runtime_archive)
    identity = {"compiler": compiler, "runtime_archive": env.get("PCC_RUNTIME_ARCHIVE"),
                "target_triple": target_triple, "request_sha256": request_sha,
                "invocation": str(os.getpid()) + ":" + str(next(_CALL_IDS))}
    with tempfile.TemporaryDirectory(prefix="pcc_native_c_unit_") as directory:
        request_path = os.path.join(directory, "request.json")
        output_path = os.path.join(directory, "artifact.json")
        with open(request_path, "xb") as stream:
            stream.write(request_bytes)
        argv = [compiler, "--pcc-c-unit-artifact-worker", request_path, output_path]
        _record("native", "compile_c_artifact", "", event="attempt", **identity)
        try:
            completed = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired:
            _record("native", "compile_c_artifact", "", event="complete", outcome="timeout", **identity)
            raise
        except BaseException as error:
            _record("native", "compile_c_artifact", type(error).__name__,
                    event="complete", outcome="invocation_error", **identity)
            raise
        try:
            with open(output_path, encoding="utf-8") as stream:
                response = json.load(stream)
            if (not isinstance(response, dict) or response.get("schema") != RESPONSE_SCHEMA
                    or response.get("request_sha256") != request_sha):
                raise ValueError("native C artifact response identity differs")
            if completed.returncode == 0:
                if set(response) != {"schema", "request_sha256", "outcome", "artifact"} or response["outcome"] != "passed":
                    raise ValueError("native C success response differs")
                artifact = artifact_from_wire(response["artifact"], unit.name)
            else:
                if (set(response) != {"schema", "request_sha256", "outcome", "error_type", "error_message"}
                        or response["outcome"] != "failed"
                        or not isinstance(response["error_type"], str)
                        or not isinstance(response["error_message"], str)):
                    raise ValueError("native C failure response differs")
        except (OSError, ValueError, TypeError) as error:
            _record("native", "compile_c_artifact", str(error), event="complete",
                    outcome="invalid_artifact", returncode=completed.returncode, **identity)
            raise RuntimeError("native C artifact unavailable or invalid: " + str(error)
                               + "\n" + completed.stderr[-8000:]) from error
        _record("native", "compile_c_artifact", "", event="complete",
                outcome="passed" if completed.returncode == 0 else "failed",
                returncode=completed.returncode, **identity)
        if completed.returncode == 0:
            return artifact
        # Preserve common actual frontend rejection types, not arbitrary
        # imports or success inferred from an expected negative test.
        from pcc.frontends.c.parse.plyparser import ParseError
        error_types = {"ParseError": ParseError, "TypeError": TypeError,
                       "ValueError": ValueError, "RuntimeError": RuntimeError,
                       "NotImplementedError": NotImplementedError,
                       "AssertionError": AssertionError}
        kind = response["error_type"]
        if kind in error_types:
            raise error_types[kind](response["error_message"])
        # Classes with additional state (for example OSError.errno/filename)
        # are not equivalent to a generic message. Keep this visible as a gap,
        # even when a caller happens to catch the RuntimeError below.
        _record("unsupported", "compile_c_artifact",
                "native exception contract not represented: " + kind, **identity)
        raise RuntimeError("native C " + kind + ": " + response["error_message"])


def install() -> None:
    compiler = os.environ["PCC_TEST_COMPILER"]
    if not os.path.isfile(compiler):
        raise RuntimeError(f"PCC_TEST_COMPILER is not a file: {compiler}")
    if not os.environ.get("PCC_RUNTIME_ARCHIVE"):
        raise RuntimeError("PCC_TEST_COMPILER needs PCC_RUNTIME_ARCHIVE")
    if not os.environ.get(LOG_ENV):
        # Set in the controlling process before xdist starts its workers, so
        # every worker appends to the one session log.
        import tempfile

        os.environ[LOG_ENV] = os.path.join(
            tempfile.gettempdir(), f"pcc-test-compiler-{os.getpid()}.jsonl"
        )

    from pcc.frontends.python import pipeline
    from pcc.frontends.c.evaluator import c_evaluator

    host_compile = pipeline.compile_python
    host_multi = pipeline.compile_python_multi
    global _HOST_C_ARTIFACT
    if getattr(c_evaluator._compile_translation_unit_artifact_job, "func", None) is not compile_c_artifact:
        _HOST_C_ARTIFACT = c_evaluator._compile_translation_unit_artifact_job

    def compile_python(
        src_path,
        out_path,
        *,
        verbose=False,
        emit_llvm_only=False,
        libpython_mode=None,
        ir_scaffold_mode=None,
        backend=None,
        gpu_backend=None,
        target_triple=None,
        recursive_stdlib=False,
        python_library=False,
        runtime_archive=None,
        link_args=(),
        object_inputs=(),
        profile=None,
    ):
        reason = ""
        if libpython_mode not in (None, "off"):
            reason = "libpython_mode=" + str(libpython_mode)
        elif backend not in (None, "self"):
            reason = "backend=" + str(backend)
        elif recursive_stdlib:
            reason = "recursive_stdlib"
        elif profile is not None:
            reason = "profile"
        elif gpu_backend is not None:
            reason = "gpu_backend=" + str(gpu_backend)
        elif (str(os.environ.get("PCC_SELF_LINK", "")).strip().lower() != "cc"
              and any(not str(arg).startswith(("-", "@"))
                      and str(arg).lower().endswith((".o", ".obj")) for arg in link_args)):
            reason = "object-shaped raw link_args cannot retain their API meaning in the native CLI"
        native_objects = []
        if not reason and object_inputs:
            if not isinstance(object_inputs, (tuple, list)):
                reason = "object_inputs container cannot retain its API meaning in the native CLI"
            elif str(os.environ.get("PCC_SELF_LINK", "")).strip().lower() == "cc":
                reason = "explicit object_inputs with the cc linker cannot retain their API meaning in the native CLI"
            else:
                for value in object_inputs:
                    try:
                        path = os.fspath(value)
                    except TypeError:
                        path = None
                    if (not isinstance(path, str) or path.startswith(("-", "@"))
                            or not path.lower().endswith((".o", ".obj"))):
                        reason = "object_inputs path cannot retain its API meaning in the native CLI"
                        break
                    native_objects.append(path)
        if reason:
            if os.environ.get(STRICT_ENV) == "1":
                _record("unsupported", "compile_python", reason)
                raise pipeline.PyPipelineError("strict pcc1 route unsupported: " + reason)
            log_unrouted("compile_python", reason)
            return host_compile(
                src_path,
                out_path,
                verbose=verbose,
                emit_llvm_only=emit_llvm_only,
                libpython_mode=libpython_mode,
                ir_scaffold_mode=ir_scaffold_mode,
                backend=backend,
                gpu_backend=gpu_backend,
                target_triple=target_triple,
                recursive_stdlib=recursive_stdlib,
                python_library=python_library,
                runtime_archive=runtime_archive,
                link_args=link_args,
                object_inputs=object_inputs,
                profile=profile,
            )
        argv = [
            compiler,
            "--backend",
            "self",
            "--python-libpython",
            "off",
            "--ir-scaffold",
            ir_scaffold_mode or "on",
        ]
        if target_triple is not None:
            argv += ["--target", target_triple]
        if verbose:
            argv.append("--verbose")
        for argument in link_args:
            argv += ["--link-arg", str(argument)]
        # Do not validate object existence/content/output aliases here: an
        # expressible negative must reach the real native admission path too.
        for path in native_objects:
            argv += ["--link-arg", path]
        if python_library:
            argv.append("--python-library")
        if emit_llvm_only:
            argv += ["--emit-llvm", str(out_path), str(src_path)]
        else:
            argv += [str(src_path), "-o", str(out_path)]
        env = native_env(runtime_archive)
        identity = {"compiler": compiler, "runtime_archive": env.get("PCC_RUNTIME_ARCHIVE"),
                    "target_triple": target_triple,
                    "invocation": str(os.getpid()) + ":" + str(next(_CALL_IDS))}
        _record("native", "compile_python", "", event="attempt", **identity)
        try:
            completed = subprocess.run(
                argv, env=env, capture_output=True, text=True, timeout=900,
            )
        except subprocess.TimeoutExpired:
            _record("native", "compile_python", "", event="complete", outcome="timeout", **identity)
            raise
        except BaseException as error:
            _record("native", "compile_python", type(error).__name__,
                    event="complete", outcome="invocation_error", **identity)
            raise
        _record("native", "compile_python", "", event="complete",
                outcome="passed" if completed.returncode == 0 else "failed",
                returncode=completed.returncode, **identity)
        if verbose:
            sys.stderr.write(completed.stderr)
        if completed.returncode != 0:
            raise pipeline.PyPipelineError(
                f"{compiler} exited {completed.returncode}:\n"
                + completed.stdout[-4000:]
                + completed.stderr[-8000:]
            )
        return None

    def compile_python_multi(*args, **kwargs):
        if os.environ.get(STRICT_ENV) == "1":
            reason = "no equivalent native multi-module API route"
            _record("unsupported", "compile_python_multi", reason)
            raise pipeline.PyPipelineError("strict pcc1 route unsupported: " + reason)
        log_unrouted("compile_python_multi", "no native multi-module CLI")
        return host_multi(*args, **kwargs)


    pipeline.compile_python = compile_python
    pipeline.compile_python_multi = compile_python_multi
    c_evaluator._compile_translation_unit_artifact_job = partial(
        compile_c_artifact, _compiler=compiler,
        _runtime_archive=os.environ["PCC_RUNTIME_ARCHIVE"],
    )

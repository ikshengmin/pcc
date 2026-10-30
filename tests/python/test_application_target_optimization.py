"""Production emission routing selects target optimization in every app lane.

Frontend/assembler boundaries are substituted for the worker-routing tests.
The actual AArch64 prepared emitter chooses debug/O0 behavior; these are host
unit checks, not native application or new-source bootstrap qualification.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcc.frontends.python import pipeline_frontend_worker_execution as worker
from pcc.frontends.python import pipeline_self_backend_cache as cache
from pcc.frontends.python import pipeline_self_backend_emit as emit


def _ir(target="arm64-apple-darwin"):
    return ('target triple = "' + target + '"\n'
            'define i64 @answer() {\nentry:\n ret i64 42\n}\n')


def _in_process(text, callback):
    from pcc.backend.self_backend_parse import parse_self_backend_target_triple
    from pcc.backend.self_backend_target_match import is_aarch64_darwin_triple

    return emit.emit_in_process(
        text, parse_target_triple=parse_self_backend_target_triple,
        host_target_triple=lambda: "arm64-apple-darwin",
        target_supported=is_aarch64_darwin_triple, emit_asm=callback,
    )


@pytest.mark.parametrize("placeholder", [False, True])
def test_normal_in_process_and_emit_worker_enable_optimizer(tmp_path, placeholder):
    calls = []

    def callback(text, optimize):
        calls.append((text, optimize))
        return "application assembly\n"

    text = _ir("unknown-unknown-unknown" if placeholder else "arm64-apple-darwin")
    assert _in_process(text, callback) == (
        "self-aarch64-darwin-v0", "application assembly\n",
    )
    source = tmp_path / "input.ll"
    result = tmp_path / "result.txt"
    source.write_text(text)
    assert emit.run_emit_worker(
        str(source), str(result), normalize_ir=lambda value: value,
        emit_native=lambda value: _in_process(value, callback),
    ) == 0
    assert len(calls) == 2
    assert all(optimize is True for _, optimize in calls)
    assert all('target triple = "arm64-apple-darwin"' in source for source, _ in calls)
    assert "application assembly" in result.read_text()


def test_host_subprocess_payload_uses_the_same_optimized_default(monkeypatch, tmp_path, capsys):
    from pcc.backend import self_backend_dispatch
    from pcc.frontends.python.pipeline_self_backend_host import _SELF_BACKEND_HOST_CODE

    source = tmp_path / "host-input.ll"
    source.write_text(_ir())
    calls = []

    def callback(text, optimize=True):
        calls.append((text, optimize))
        return "host application assembly\n"

    monkeypatch.setattr(
        self_backend_dispatch, "resolve_self_asm_emitter",
        lambda _target: ("self-aarch64-darwin-v0", callback),
    )
    monkeypatch.setattr(self_backend_dispatch, "run_self_target_pass_pipeline", lambda text, _target: text)
    monkeypatch.setattr(sys, "argv", ["host-emitter", "", str(source)])
    exec(_SELF_BACKEND_HOST_CODE, {"__name__": "__main__"})
    assert calls == [(_ir(), True)]
    assert capsys.readouterr().out == "self-aarch64-darwin-v0\nhost application assembly\n"


@pytest.mark.parametrize("handoff", [False, True])
def test_indexed_sidecar_worker_enables_optimizer(monkeypatch, handoff):
    from pcc.frontends.python import pipeline, pipeline_indexed_handoff
    from pcc.frontends.python import worker_resource_plan

    calls = []
    publications = []

    def callback(sidecar, output, kind, optimize=False, **kwargs):
        calls.append((sidecar, output, kind, optimize, kwargs))

    monkeypatch.setattr(pipeline, "_emit_indexed_module_file", callback)
    monkeypatch.setattr(worker_resource_plan, "publish_worker_resource", lambda *_a: None)
    monkeypatch.setattr(pipeline._pipeline_frontend_workers, "worker_timing_enabled", lambda *_a: False)
    if handoff:
        monkeypatch.setenv("PCC_INDEXED_HANDOFF_REQUEST", "request.json")
        monkeypatch.setattr(pipeline_indexed_handoff, "validate_handoff_input", lambda *_a: ({}, {}))
        monkeypatch.setattr(pipeline_indexed_handoff, "publish_handoff_result", publications.append)
    else:
        monkeypatch.delenv("PCC_INDEXED_HANDOFF_REQUEST", raising=False)
    assert pipeline.run_self_backend_indexed_emit_worker("input.pidx", "out.pco", "PCO") == 0
    assert len(calls) == 1
    assert calls[0][:4] == ("input.pidx", "out.pco", "PCO", True)
    if handoff:
        assert calls[0][4]["handoff_request"] == "request.json"
    assert publications == (["request.json"] if handoff else [])


@pytest.mark.parametrize("requested", ["0", "1"])
def test_diagnostic_indexed_driver_keeps_explicit_optimization_choice(monkeypatch, requested):
    from pcc.backend import owned_emit_driver

    calls = []
    monkeypatch.setattr(owned_emit_driver, "emit_indexed_module_file", lambda *args: calls.append(args))
    monkeypatch.setattr(sys, "argv", ["owned-emit", "input.pidx", "out.pco", "PCO", requested])
    owned_emit_driver.main()
    assert calls == [("input.pidx", "out.pco", "PCO", requested == "1")]


@pytest.mark.parametrize("debug,requested", [(False, True), (True, True), (False, False)])
@pytest.mark.parametrize("route", ["text", "indexed", "transport"])
def test_real_emitter_keeps_source_debug_and_explicit_o0(monkeypatch, debug, requested, route):
    from pcc.backend import self_backend_aarch64_darwin as arm
    from pcc.backend.self_backend_parse import parse_self_backend_module
    from tests.python.test_owned_source_debug_info import program

    text = program() if debug else _ir()
    selected = []

    def active(prepared, optimize, **kwargs):
        selected.append(optimize)
        counts = kwargs.get("structured_counts")
        if counts is not None:
            counts.extend([0] * 7)
        return []

    monkeypatch.setattr(arm, "_emit_prepared_aarch64_darwin_lines_active", active)
    if route == "text":
        if requested:
            _in_process(text, arm.emit_aarch64_darwin_asm)
        else:
            arm.emit_aarch64_darwin_asm(text, optimize=False)
    elif route == "indexed":
        arm.emit_aarch64_darwin_indexed_module(
            parse_self_backend_module(text), optimize=requested,
        )
    else:
        result = arm.emit_aarch64_darwin_indexed_transport(
            parse_self_backend_module(text), optimize=requested,
        )
        if result.encoded_line_records is not None:
            result.encoded_line_records.close()
    assert selected == [requested and not debug]


@pytest.mark.parametrize("native_worker", [False, True], ids=["host", "native-worker"])
@pytest.mark.parametrize("target", ["arm64-apple-darwin", "aarch64-unknown-linux-gnu"])
@pytest.mark.parametrize("route", ["transport", "indexed-asm", "validation", "text-control"])
def test_frontend_worker_routes_use_the_same_optimizer(
    monkeypatch, tmp_path, native_worker, target, route,
):
    from pcc.backend import arm64_elf_driver, elf_x86_64, native_object, target_objects
    from pcc.backend import self_backend_aarch64_darwin as arm
    from pcc.backend import self_backend_dispatch, self_backend_parse
    from pcc.frontends.python import type_infer
    from pcc.frontends.python.codegen import layer1
    from pcc.ir import direct_indexed_kernel

    text = _ir(target)
    module = self_backend_parse.parse_self_backend_module(text)
    calls = []
    errors = []
    assembly = "same optimized application assembly\n"

    class FrontendModule:
        _direct_indexed_fallback_records = 0

        def __str__(self):
            return text

    class Codegen:
        def __init__(self, *_args):
            self.module = FrontendModule()
            self._direct_indexed_module = module

        def _generate_initial_module(self):
            return self.module

    def indexed(module_arg, optimize=False, **kwargs):
        assert module_arg is module
        calls.append(("indexed", optimize))
        return assembly

    def transport(module_arg, optimize=False, **kwargs):
        assert module_arg is module
        calls.append(("transport", optimize))
        return SimpleNamespace(
            assemble_sections=lambda: ([], []), encoded_line_records=None,
        )

    def text_emitter(text_arg, optimize=True):
        assert text_arg == text
        calls.append(("text", optimize))
        return assembly

    for name in (
        "PCC_HOST_CODEGEN_BATCH", "PCC_INDEXED_HANDOFF_REQUEST",
        "PCC_DIRECT_INDEXED_SIDECAR", "PCC_DIRECT_INDEXED_KERNEL_VALIDATE",
        "PCC_DIRECT_INDEXED_KERNEL_EMIT", "PCC_TEXT_INDEXED_KERNEL_EMIT",
        "PCC_DIRECT_INDEXED_KERNEL_RELEASE_FRONTEND",
        "PCC_DIRECT_INDEXED_KERNEL_REQUIRE_ZERO_FALLBACK", "PCC_DEBUG_WORKER_RERAISE",
        "PCC_COMPILE_PROGRESS_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0" if route == "text-control" else "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_NATIVE_OBJECT", "1" if route == "transport" else "0")
    if route == "validation":
        monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_VALIDATE", "1")
    if route == "text-control":
        monkeypatch.setenv("PCC_TEXT_INDEXED_KERNEL_EMIT", "1")
    monkeypatch.setattr(type_infer, "infer_module", lambda *_a, **_k: object())
    monkeypatch.setattr(layer1, "L1CodeGen", Codegen)
    monkeypatch.setattr(worker, "publish_worker_resource", lambda *_a: None)
    monkeypatch.setattr(worker, "_note_worker_module", lambda *_a: None)
    monkeypatch.setattr(worker, "_direct_owned_pass_names", lambda *_a: [])
    monkeypatch.setattr(direct_indexed_kernel, "direct_indexed_module_first_libpython_edge", lambda _m: "")
    monkeypatch.setattr(arm, "emit_aarch64_darwin_indexed_transport", transport)
    monkeypatch.setattr(arm, "emit_aarch64_darwin_asm", text_emitter)
    monkeypatch.setattr(target_objects, "emit_indexed_assembly", indexed)
    monkeypatch.setattr(self_backend_dispatch, "emit_self_asm", text_emitter)
    monkeypatch.setattr(native_object, "encode_native_object_from_sections", lambda *_a, **_k: b"mock-owned-object")
    monkeypatch.setattr(arm64_elf_driver, "from_sections", lambda *_a, **_k: object())
    monkeypatch.setattr(elf_x86_64, "emit_relocatable", lambda *_a, **_k: b"mock-owned-elf")
    result = tmp_path / "result.tsv"
    manifest = {
        "result_path": str(result), "src_paths": [str(tmp_path / "app.py")],
        "module_names": ["app"], "entry_module": "app", "sibling_inits": (),
        "libpython_mode": "off", "ir_scaffold_mode": "on", "verbose": False,
        "assigned_indices": [0], "ir_dir": str(tmp_path),
    }
    status = worker.run_codegen_worker(
        "manifest", read_manifest=lambda _p: manifest,
        run_export_worker_callback=lambda _m: pytest.fail("wrong export route"),
        run_summary_worker_callback=lambda _m: pytest.fail("wrong summary route"),
        worker_timing_enabled=lambda: False,
        native_worker_executable=lambda: "pcc1" if native_worker else "",
        read_native_exports_wire=lambda _p: ({}, {}),
        read_native_exports_wire_for_module=lambda *_a: ({}, {}, None, False),
        read_ast_wire=lambda _p: object(),
        build_closed_world_context=lambda *_a, **_k: ([object()], {}, {}),
        module_imports_native_extension=lambda *_a, **_k: False,
        contextual_host_params_for_module=lambda *_a: {},
        module_uses_default_native_exports=lambda _m: False,
        copy_native_module_exports=lambda value: value,
        closed_world_function_object_exports=lambda *_a: {},
        log=lambda *_a: None, ir_needs_libpython=lambda _s: False,
        safe_exception_text=str, write_worker_error=lambda _p, message: errors.append(message),
        pipeline_error=RuntimeError,
    )
    assert status == 0, errors
    expected_routes = {
        "transport": ["transport"], "indexed-asm": ["indexed"],
        "validation": ["indexed", "text"], "text-control": ["text"],
    }
    assert calls == [(name, True) for name in expected_routes[route]]
    assert result.read_text().startswith("OK\t0\tapp\t")


def test_old_forced_o0_cache_entry_is_not_reused(tmp_path, monkeypatch):
    from pcc.backend import self_backend_cache_identity

    monkeypatch.delenv("PCC_CODE_PROFILE", raising=False)
    monkeypatch.setenv(cache.OBJECT_CACHE_ENV, "1")
    monkeypatch.setenv(cache.OBJECT_CACHE_IDENTITY_ENV, "same-native-compiler")
    monkeypatch.setenv(cache.OBJECT_CACHE_DIR_ENV, str(tmp_path / "cache"))
    monkeypatch.setattr(self_backend_cache_identity, "self_backend_emitter_source_identity", lambda: "same-backend")
    source = tmp_path / "input.ll"
    source.write_text(_ir())
    result = tmp_path / "result.txt"
    output = tmp_path / "object.pco"
    items = [(str(result), str(output), str(source))]
    effective = cache._effective_backend_configuration
    configuration = json.loads(effective())
    assert ["application-emitter-optimize", True] in configuration
    legacy = json.dumps(
        [field for field in configuration if field[0] != "application-emitter-optimize"],
        separators=(",", ":"),
    )
    monkeypatch.setattr(cache, "_effective_backend_configuration", lambda: legacy)

    def plan():
        return cache.plan(
            items, "self-aarch64-darwin-v0", "pcc-native-pco-v1", str(tmp_path),
            host_python_command=None, plan_host_code="", small_int_decimal=None,
        )

    old = plan()
    output.write_bytes(b"legacy-forced-O0-object")
    assert cache.publish(items, old, "", host_python_command=None, publish_host_code="")
    assert plan()[0][1] == "hit"
    monkeypatch.setattr(cache, "_effective_backend_configuration", effective)
    current = plan()
    assert current[0][1] == "miss"
    assert current[0][0] != old[0][0]

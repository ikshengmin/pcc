"""Host-only ownership checks for the real singleton handoff worker path.

Frontend generation and sidecar publication are replaced at their boundaries;
the production worker and release helper execute without building a compiler,
runtime, native object, or executable.
"""

import gc
import weakref
from types import SimpleNamespace

import pytest

from pcc.frontends.python import pipeline_frontend_worker_execution as worker


_CLEARED_FIELDS = (
    "functions", "runtime", "env", "_module_globals",
    "_module_global_init_flags", "_funcdef_functions", "_native_symbol_funcdefs",
    "_fn_err_exit_blocks",
)


class _Owner:
    def __init__(self, **attributes):
        self.__dict__.update(attributes)


def _codegen():
    module = _Owner(_functions={"old": 1}, _globals={"old": 1}, globals={"old": 1})
    codegen = _Owner(module=module, _direct_indexed_module=object())
    for name in _CLEARED_FIELDS:
        setattr(codegen, name, {"old": 1})
    return codegen


def _assert_cleared(codegen):
    assert codegen.module._functions == {}
    assert codegen.module._globals == {}
    assert codegen.module.globals == {}
    for name in _CLEARED_FIELDS:
        assert getattr(codegen, name) == {}
    assert codegen._direct_indexed_module is None


@pytest.mark.parametrize("explicit", [False, True], ids=["ordinary-default", "explicit-collect"])
def test_release_keeps_ordinary_collection_after_clearing(monkeypatch, explicit):
    codegen = _codegen()
    calls = []

    def collect():
        _assert_cleared(codegen)
        calls.append("collect")
        return 0

    monkeypatch.setattr(gc, "collect", collect)
    if explicit:
        worker._release_direct_frontend_state(codegen, collect=True)
    else:
        worker._release_direct_frontend_state(codegen)
    assert calls == ["collect"]


def test_release_can_defer_collection_without_retaining_cleared_state(monkeypatch):
    codegen = _codegen()
    monkeypatch.setattr(gc, "collect", lambda: pytest.fail("premature collection"))
    worker._release_direct_frontend_state(codegen, collect=False)
    _assert_cleared(codegen)


@pytest.mark.parametrize("deferred", [False, True], ids=["ordinary", "handoff"])
def test_release_preserves_clear_failure_before_collection(monkeypatch, deferred):
    codegen = _codegen()
    failure = RuntimeError("cannot clear owner")

    class FailingClear(dict):
        def clear(self):
            raise failure

    codegen.runtime = FailingClear(old=1)
    monkeypatch.setattr(gc, "collect", lambda: pytest.fail("collected after clear failure"))
    with pytest.raises(RuntimeError) as caught:
        if deferred:
            worker._release_direct_frontend_state(codegen, collect=False)
        else:
            worker._release_direct_frontend_state(codegen)
    assert caught.value is failure
    assert codegen.module._functions == {}
    assert codegen.functions == {}
    assert codegen.env == {"old": 1}


@pytest.mark.parametrize("failure_at", [None, "collect", "publish"], ids=["success", "collect-error", "publish-error"])
def test_handoff_collects_once_after_final_owners_before_publication(monkeypatch, tmp_path, failure_at):
    from pcc.frontends.python import pipeline_indexed_handoff, type_infer
    from pcc.frontends.python.codegen import layer1
    from pcc.ir import direct_indexed_kernel

    events = []
    owner_refs = []
    collected_owners = []
    errors = []
    direct_module = SimpleNamespace(triple="arm64-apple-darwin")
    result_path = tmp_path / "result.tsv"
    manifest = {
        "result_path": str(result_path),
        "src_paths": [str(tmp_path / "consumer.py")],
        "module_names": ["consumer"],
        "entry_module": "consumer",
        "sibling_inits": (),
        "libpython_mode": "off",
        "ir_scaffold_mode": "on",
        "verbose": False,
        "assigned_indices": [0],
        "ir_dir": str(tmp_path),
        "exports_path": str(tmp_path / "exports.json"),
        "ast_dir": str(tmp_path / "ast"),
    }

    def owned(**attributes):
        value = _Owner(**attributes)
        value.cycle = value
        owner_refs.append(weakref.ref(value))
        return value

    class Codegen:
        def __init__(self, typed, *_args):
            self.__dict__.update(_codegen().__dict__)
            self.typed = typed
            self.module._direct_indexed_fallback_records = 0
            self._direct_indexed_module = direct_module
            self.cycle = self
            owner_refs.append(weakref.ref(self))
            owner_refs.append(weakref.ref(self.module))

        def _generate_initial_module(self):
            events.append("generate")
            return self.module

    real_collect = gc.collect

    def collect():
        events.append("collect")
        if failure_at == "collect":
            raise ValueError("collection failed")
        result = real_collect()
        # The helper's old first collection runs with AST, typed, codegen and
        # generated-module locals still alive. This is the negative control.
        collected_owners.append(all(ref() is None for ref in owner_refs))
        return result

    def publish(request, manifest_path, index, module, frozen, passes,
                needs_libpython, needs_native_exports, encode_callback):
        events.append("publish")
        assert events == ["generate", "collect", "publish"]
        assert all(ref() is None for ref in owner_refs)
        assert frozen is direct_module
        assert (request, manifest_path, index, module) == ("request.json", "manifest.json", 0, "consumer")
        assert passes == []
        assert not needs_libpython and not needs_native_exports
        assert callable(encode_callback)
        if failure_at == "publish":
            raise ValueError("publication failed")
        return str(tmp_path / "module_0.direct.pidx")

    def unexpected(*_args, **_kwargs):
        pytest.fail("unselected worker path")

    for name in (
        "PCC_HOST_CODEGEN_BATCH", "PCC_DIRECT_INDEXED_KERNEL_VALIDATE",
        "PCC_TEXT_INDEXED_KERNEL_EMIT", "PCC_DEBUG_WORKER_RERAISE",
        "PCC_COMPILE_PROGRESS_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PCC_INDEXED_HANDOFF_REQUEST", "request.json")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_SIDECAR", "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_REQUIRE_ZERO_FALLBACK", "1")
    monkeypatch.setattr(type_infer, "infer_module", lambda ast, **_kwargs: owned(ast=ast))
    monkeypatch.setattr(layer1, "L1CodeGen", Codegen)
    monkeypatch.setattr(worker, "publish_worker_resource", lambda *_args: None)
    monkeypatch.setattr(worker, "_freeze_worker_survivors", lambda: None)
    monkeypatch.setattr(worker, "_note_worker_module", lambda *_args: None)
    monkeypatch.setattr(worker, "_direct_owned_pass_names", lambda *_args: [])
    monkeypatch.setattr(direct_indexed_kernel, "direct_indexed_module_first_libpython_edge", lambda _module: "")
    monkeypatch.setattr(pipeline_indexed_handoff, "publish_handoff", publish)
    monkeypatch.setattr(gc, "collect", collect)

    status = worker.run_codegen_worker(
        "manifest.json",
        read_manifest=lambda _path: manifest,
        run_export_worker_callback=unexpected,
        run_summary_worker_callback=unexpected,
        worker_timing_enabled=lambda: False,
        native_worker_executable=lambda: "",
        read_native_exports_wire=unexpected,
        read_native_exports_wire_for_module=lambda *_args: ({"consumer": {}}, {}, None, False),
        read_ast_wire=lambda _path: owned(),
        build_closed_world_context=unexpected,
        module_imports_native_extension=lambda *_args, **_kwargs: False,
        contextual_host_params_for_module=lambda *_args: {},
        module_uses_default_native_exports=lambda _module: False,
        copy_native_module_exports=unexpected,
        closed_world_function_object_exports=lambda *_args: {},
        log=unexpected,
        ir_needs_libpython=lambda _text: False,
        safe_exception_text=str,
        write_worker_error=lambda path, message: errors.append((path, message)),
        pipeline_error=RuntimeError,
    )
    expected_events = ["generate", "collect"]
    if failure_at != "collect":
        expected_events.append("publish")
    assert events == expected_events
    if failure_at != "collect":
        assert collected_owners == [True], "collection ran before final frontend owners were released"
    if failure_at is None:
        assert status == 0 and errors == []
        fields = result_path.read_text().strip().split("\t")
        assert fields[:5] == ["OK", "0", "consumer", "0", "0"]
        assert fields[-2:] == ["PIDX", str(tmp_path / "module_0.direct.pidx")]
        assert (tmp_path / "module_0.ll").read_text() == ""
    else:
        assert status == 1 and len(errors) == 1
        assert errors[0][0] == str(result_path)
        detail = "collection failed" if failure_at == "collect" else "publication failed"
        assert "codegen[consumer]: ValueError: " + detail in errors[0][1]
        assert not result_path.exists()
        assert not (tmp_path / "module_0.ll").exists()

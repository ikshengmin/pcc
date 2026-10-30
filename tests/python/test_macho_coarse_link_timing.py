"""Finite AST/stub contracts: no compiler, object parser, or native execution."""

import ast
import io
from pathlib import Path
import sys
import types

import pytest

from pcc.backend import indexed_phase_timing as timing_module


SOURCE = "pcc/frontends/python/pipeline_self_backend_link.py"
PHASES = (
    "macho_stage_inputs", "macho_prepare_inputs", "macho_link_image",
    "macho_verify_signature", "macho_write_image",
)
PREPARE_PHASES = (
    "macho_prepare_validate", "macho_prepare_archives", "macho_prepare_merge",
)


class LinkError(RuntimeError):
    pass


class Abort(BaseException):
    pass


class Timer:
    def __init__(self):
        self.events = []

    def begin(self, phase):
        token = len(self.events) + 1
        self.events.append(("begin", phase, token))
        return token

    def complete(self, phase, token):
        self.events.append(("complete", phase, token))


def _function(name, namespace, source=SOURCE):
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / source).read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.Module(
        body=[ast.parse("from __future__ import annotations").body[0], function],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), "<owned Mach-O AST contract>", "exec"), namespace)
    return namespace[name]


def _stub(monkeypatch, name, **attrs):
    module = types.ModuleType(name)
    vars(module).update(attrs)
    monkeypatch.setitem(sys.modules, name, module)


def _owner(monkeypatch, timer, *, failure=None, error=None, host_owned=None,
           extra_input=False):
    operations = []
    image = b"payloadsignature"
    prepared = object()
    ticks = []
    budget = types.SimpleNamespace(check_peak=lambda *args, **kwargs: operations.append("budget"))

    def operation(name):
        operations.append(name)
        if failure == name:
            raise error

    def clock():
        ticks.append(1)
        return len(ticks) / 1000

    class Writer(io.BytesIO):
        def write(self, value):
            operation("write")
            assert value is image
            operations.append(("bytes", value))
            return super().write(value)

    def open_file(path, mode, **kwargs):
        operation("open:" + path)
        if mode == "wb":
            assert path == "out.tmp.owned.tmp"
            return Writer()
        assert mode == "rb" and not kwargs
        return io.BytesIO({"object.pco": b"encoded", "runtime.a": b"archive",
                           "extra.o": b"extra"}[path])

    def decode(value):
        operation("decode")
        assert value == b"encoded"
        return ("decoded", value)

    def prepare(objects, **kwargs):
        operation("prepare")
        assert objects == [("decoded", b"encoded")] + ([b"extra"] if extra_input else [])
        expected = dict(archives=[b"archive"], _consume_inputs=True, _source_view=True)
        if host_owned is not None:
            expected["compact_budget"] = budget
        if timer is not None:
            expected["coordinator_timing"] = timer
        assert kwargs == expected
        return prepared

    def link(value, **kwargs):
        assert value is prepared
        assert kwargs["entry"] == "_main"
        expected = {"entry", "phase_callback"}
        if host_owned is not None:
            expected.add("compact_budget")
            assert kwargs["compact_budget"] is budget
        if host_owned and not extra_input:
            expected.update(("caller_owners", "_phase_callback"))
            assert kwargs["_phase_callback"] is None
        elif timer is not None:
            expected.add("coordinator_timing")
            assert kwargs["coordinator_timing"] is timer
        assert set(kwargs) == expected
        kwargs["phase_callback"]("sign_begin")
        try:
            operation("link")
        finally:
            kwargs["phase_callback"]("sign_end")
        return image

    def parse(value):
        operation("parse")
        assert value is image
        return types.SimpleNamespace(
            identifier=b"pcc-linked", dataoff=7, datasize=9,
            exec_seg_base=0, exec_seg_limit=7, exec_seg_flags=1,
        )

    def verify(value, end, **kwargs):
        operation("verify")
        assert value is image and end == 7
        assert kwargs == dict(identifier=b"pcc-linked", exec_seg_base=0,
                              exec_seg_limit=7, exec_seg_flags=1)
        return b"stale" if failure == "stale" else b"signature"

    def chmod(path, mode):
        operation("chmod")
        assert (path, mode) == ("out.tmp.owned.tmp", 0o755)

    def replace(source, destination):
        operation("replace")
        assert (source, destination) == ("out.tmp.owned.tmp", "out.tmp")

    _stub(monkeypatch, "pcc.backend.arm64_asm_driver", assemble_file=None)
    _stub(monkeypatch, "pcc.backend.macho_exec", prepare_executable_object=prepare,
          link_prepared_executable=link)
    _stub(monkeypatch, "pcc.backend.macho_codesign", parse_signature=parse,
          _build_signature_prefix=verify)
    _stub(monkeypatch, "pcc.backend.native_object", NativeObject=None,
          decode_packed_native_object=decode)
    _stub(monkeypatch, "pcc.backend.macho_internal_inputs",
          read_internal_input_manifest=lambda path: [("PCO", "object.pco")])
    if host_owned is not None:
        def prepare_paths(paths, **kwargs):
            operation("prepare_paths")
            assert paths == ["object.pco"]
            assert kwargs["archive_paths"] == ("runtime.a",)
            assert kwargs["compact_budget"] is budget and kwargs["_phase_callback"] is None
            assert kwargs["_staged_rows"] is host_owned
            return prepared

        _stub(monkeypatch, "pcc.backend.stackmap_host_budget", host_compact_stackmap_budget=lambda: budget)
        _stub(monkeypatch, "pcc.backend.macho_host_prepare",
              prepare_executable_from_paths=prepare_paths,
              close_staged_source_rows=lambda *args, **kwargs: operation("close_source"))
        _stub(monkeypatch, "pcc.backend.macho_host_output",
              _owned_output_supported=lambda: host_owned, link_owned_executable=link,
              check_signature_verification=lambda *args, **kwargs: operation("verify_budget"))
    _stub(monkeypatch, "sys", implementation=types.SimpleNamespace(
        name="pcc" if host_owned is None else "cpython"))
    invoke = _function("_owned_macho_link_in_process", dict(
        open=open_file, time=types.SimpleNamespace(monotonic=clock),
        os=types.SimpleNamespace(chmod=chmod, replace=replace, environ={}),
        SelfBackendLinkError=LinkError,
        traceback=types.SimpleNamespace(format_exc=lambda: "stub traceback"),
    ))

    def run():
        return invoke(asm_path=None, pcc_asm_inputs=(), pcc_native_object_inputs=(),
                      runtime_archive="runtime.a", extra_link_inputs=("extra.o",) if extra_input else (),
                      tmp_out_path="out.tmp", pcc_internal_input_manifest="inputs",
                      coordinator_timing=timer)

    return run, operations, ticks


def test_macho_bridge_preserves_untimed_kwargs_and_timer_identity(monkeypatch):
    for enabled in (False, True):
        with monkeypatch.context() as patch:
            timer = Timer() if enabled else None
            calls = []
            target = "arm64-apple-darwin"
            _stub(patch, "pcc.frontends.python.pipeline_targets", host_target_triple=lambda: target)
            namespace = dict(
                SelfBackendLinkError=LinkError,
                _owned_macho_link_covers_surface=lambda **kwargs: True,
                _owned_macho_link_in_process=lambda **kwargs: calls.append(kwargs),
                os=types.SimpleNamespace(path=types.SimpleNamespace(isfile=lambda path: True),
                                         access=lambda path, mode: True, X_OK=1),
            )
            invoke = _function("run_link_command", namespace)
            for _ in range(2):
                invoke([], None, "out", "runtime.a", (), False, target_triple=target,
                       pcc_internal_input_manifest="inputs", coordinator_timing=timer,
                       resolve_self_link_mode=lambda **kwargs: "pcc",
                       validate_pcc_self_link_surface=lambda **kwargs: None,
                       repo_root_for_link=None, host_python_command=None,
                       build_pcc_link_command=None, log=lambda *args: None, join_strings=None)
            expected = dict(asm_path=None, pcc_asm_inputs=(), pcc_native_object_inputs=(),
                            runtime_archive="runtime.a", extra_link_inputs=(), tmp_out_path="out",
                            link_profile_path=None, pcc_internal_input_manifest="inputs")
            if enabled:
                expected["coordinator_timing"] = timer
            assert calls == [expected, expected]


def test_macho_owner_keeps_stub_bytes_modes_order_and_repeated_boundaries(monkeypatch):
    reference = None
    for enabled in (False, True):
        with monkeypatch.context() as patch:
            timer = Timer() if enabled else None
            invoke, operations, ticks = _owner(patch, timer)
            assert invoke() is None
            first = list(operations), len(ticks)
            assert invoke() is None
            assert operations == first[0] * 2 and len(ticks) == first[1] * 2
            if reference is None:
                reference = first
            else:
                assert first == reference
                assert [(e[0], e[1]) for e in timer.events] == [
                    (event, phase) for _ in range(2) for phase in PHASES
                    for event in ("begin", "complete")
                ]
            assert operations.count(("bytes", b"payloadsignature")) == 2
            assert operations.count("verify") == operations.count("chmod") == operations.count("replace") == 2
    _prepare_success_contracts()


def test_macho_owner_failures_keep_incomplete_boundary_and_error(monkeypatch):
    failures = (("decode", PHASES[0]), ("prepare", PHASES[1]), ("link", PHASES[2]),
                ("parse", PHASES[3]), ("verify", PHASES[3]), ("stale", PHASES[3]),
                ("write", PHASES[4]), ("chmod", PHASES[4]), ("replace", PHASES[4]))
    for failure, phase in failures:
        for error_type in (LinkError, Abort):
            with monkeypatch.context() as patch:
                timer = Timer()
                error = error_type("injected")
                invoke, operations, _ticks = _owner(patch, timer, failure=failure, error=error)
                expected_type = LinkError if failure == "stale" else error_type
                with pytest.raises(expected_type) as caught:
                    invoke()
                if failure == "stale":
                    assert "stale signature page hashes" in str(caught.value)
                else:
                    assert caught.value is error
                assert timer.events[-1][:2] == ("begin", phase)
                assert sum(e[0] == "begin" and e[1] == phase for e in timer.events) == 1
                if phase != PHASES[4]:
                    assert "write" not in operations
    _prepare_failure_contracts()


def test_host_windowed_routes_keep_original_selection_and_output(monkeypatch):
    for owned in (False, True):
        reference = None
        for enabled in (False, True):
            with monkeypatch.context() as patch:
                timer = Timer() if enabled else None
                invoke, operations, ticks = _owner(patch, timer, host_owned=owned)
                assert invoke() is None
                if reference is None:
                    reference = list(operations), len(ticks)
                else:
                    assert (operations, len(ticks)) == reference
                    assert [(e[0], e[1]) for e in timer.events] == [
                        (event, phase) for phase in PHASES for event in ("begin", "complete")
                    ]
                assert "decode" not in operations and operations.count("prepare_paths") == 1
                assert operations[-3:] == [("bytes", b"payloadsignature"), "chmod", "replace"]
    reference = None
    for enabled in (False, True):
        with monkeypatch.context() as patch:
            timer = Timer() if enabled else None
            invoke, operations, ticks = _owner(patch, timer, host_owned=False, extra_input=True)
            assert invoke() is None
            assert "prepare_paths" not in operations and operations.count("prepare") == 1
            if enabled:
                assert (operations, len(ticks)) == reference
                assert [(e[0], e[1]) for e in timer.events] == [
                    (event, phase) for phase in PHASES for event in ("begin", "complete")
                ]
            else:
                reference = list(operations), len(ticks)


def test_macho_optional_clock_and_sink_faults_preserve_stub_output(monkeypatch):
    class BadSink:
        def write(self, value):
            raise OSError("closed diagnostic sink")

        def flush(self):
            raise OSError("closed diagnostic sink")

    for fault in ("clock", "sink"):
        with monkeypatch.context() as patch:
            if fault == "clock":
                def bad_clock():
                    raise OSError("clock unavailable")
                patch.setattr(timing_module.time, "perf_counter_ns", bad_clock)
            patch.setattr(timing_module.sys, "stderr", BadSink())
            timer = timing_module.CoarsePhaseTiming()
            invoke, operations, _ticks = _owner(patch, timer)
            assert invoke() is None
            assert operations[-3:] == [("bytes", b"payloadsignature"), "chmod", "replace"]
            prepare, operations, _receipts = _prepare_owner(timer)
            assert prepare() == b"unchanged merged payload"
            assert operations[-1][0] == "merge"


def _prepare_owner(timer, *, archives=(b"archive",), consume=True, budget=None,
                   failure=None, error=None, selections=False):
    """Execute the real prepare body; only format/merge boundaries are stubs."""
    operations = []
    receipts = [] if selections else None
    result = b"unchanged merged payload"

    def operation(name, *args):
        operations.append((name, *args))
        if failure == name:
            raise error

    def runtime_format():
        operation("format")
        return 2

    def coerce(objects, *, start_index=0):
        operation("coerce:" + str(start_index), tuple(objects))
        return objects

    def validate(objects):
        operation("validate:" + str(len(objects)), tuple(objects))

    def state(objects):
        operation("state", tuple(objects))
        return {"provided"}, {"pending"}

    def read(archive):
        operation("archive", archive)
        return [types.SimpleNamespace(name="member", data=archive + b":object")]

    def select(members, pending, *, already_defined, selected_members):
        operation("select", tuple(pending), tuple(already_defined))
        if selected_members is not None:
            selected_members.extend(members)
        return [member.data for member in members], {"remaining"}

    def merge(objects, **kwargs):
        expected = dict(_consume_inputs=consume, _source_view=consume, _final_executable=True)
        if budget is not None:
            expected["compact_budget"] = budget
        assert kwargs == expected
        operation("merge", tuple(objects))
        if consume:
            objects.clear()
        return result

    class ArchiveFailure(ValueError):
        pass

    namespace = dict(
        LinkError=LinkError, ArchiveError=ArchiveFailure,
        spec=types.SimpleNamespace(MachOFormatError=ArchiveFailure),
        PreciseStackMapError=ArchiveFailure, RUNTIME_TABLE_VERSION=3,
        precise_stackmap_runtime_format=runtime_format,
        _coerce_link_objects=coerce, _validate_input_load_commands=validate,
        _external_symbol_state=state, read_archive=read, select_members=select,
        link_relocatable_native=merge,
    )
    invoke = _function("prepare_executable_object", namespace, "pcc/backend/macho_exec.py")

    def run():
        inputs = [b"input"]
        before = list(inputs)
        kwargs = dict(archives=archives, _consume_inputs=consume,
                      _source_view=consume, archive_selections=receipts,
                      compact_budget=budget)
        if timer is not None:
            kwargs["coordinator_timing"] = timer
        output = invoke(inputs, **kwargs)
        assert output is result
        assert inputs == ([] if consume else before)
        return output

    return run, operations, receipts


def _prepare_success_contracts():
    for archives in ((), (b"archive",), [b"archive", b"second"]):
        for consume in (False, True):
            for budget in (None, object()):
                for selections in (False, True):
                    reference = None
                    for enabled in (False, True):
                        timer = Timer() if enabled else None
                        run, operations, receipts = _prepare_owner(
                            timer, archives=archives, consume=consume,
                            budget=budget, selections=selections,
                        )
                        first = run()
                        count = len(operations)
                        assert run() is first
                        assert operations[:count] == operations[count:]
                        if enabled:
                            assert (operations, receipts) == reference
                            assert [(e[0], e[1]) for e in timer.events] == [
                                (event, phase) for _ in range(2) for phase in PREPARE_PHASES
                                for event in ("begin", "complete")
                            ]
                        else:
                            reference = list(operations), receipts
                        assert len(receipts or ()) == (2 * len(archives) if selections else 0)


def _prepare_failure_contracts():
    failures = (("format", PREPARE_PHASES[0]), ("coerce:0", PREPARE_PHASES[0]),
                ("validate:1", PREPARE_PHASES[0]), ("state", PREPARE_PHASES[1]),
                ("archive", PREPARE_PHASES[1]), ("select", PREPARE_PHASES[1]),
                ("coerce:1", PREPARE_PHASES[1]), ("validate:2", PREPARE_PHASES[1]),
                ("merge", PREPARE_PHASES[2]))
    for failure, phase in failures:
        for error_type in (LinkError, Abort):
            reference = None
            for enabled in (False, True):
                timer = Timer() if enabled else None
                error = error_type("prepare fault")
                run, operations, _receipts = _prepare_owner(timer, failure=failure, error=error)
                with pytest.raises(error_type) as caught:
                    run()
                assert caught.value is error
                if enabled:
                    assert operations == reference
                    assert timer.events[-1][:2] == ("begin", phase)
                    assert not any(e[:2] == ("complete", phase) for e in timer.events)
                else:
                    reference = list(operations)

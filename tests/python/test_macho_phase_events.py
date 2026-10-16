"""Opt-in phase evidence without profiler hooks or retained linker owners."""

import io
import json
import os
from pathlib import Path
import re
import sys
import weakref

import pytest

from pcc.backend import macho_codesign as codesign
from pcc.backend import macho_exec, macho_host_output as output
from pcc.backend import macho_host_phase as events, macho_host_prepare as host
from pcc.backend import native_object as native, precise_stackmap as maps, stackmap_host_budget
from pcc.frontends.python import pipeline_self_backend_link as pipeline
from tests.python.test_macho_owned_output import _stack_inputs, _tracked_buffers
from tests.python.test_macho_windowed_parity import _prepared_state
from tests.python.test_macho_windowed_prepare import _budget, _write_symbol_inputs


PREFIX = "PCC_MACHO_LINK_PHASE "


class _Stderr(io.StringIO):
    def __init__(self, failure=None):
        super().__init__()
        self.failure = failure
        self.writes = 0
        self.flushes = 0
        self.persisted = ""

    def write(self, value):
        self.writes += 1
        if self.failure == "write":
            raise OSError("diagnostic write failed")
        return super().write(value)

    def flush(self):
        self.flushes += 1
        if self.failure == "flush":
            raise OSError("diagnostic flush failed")
        self.persisted = self.getvalue()


def _rows(stream):
    return [json.loads(line[len(PREFIX):]) for line in stream.persisted.splitlines()
            if line.startswith(PREFIX)]


def _sink(monkeypatch, failure=None):
    stream = _Stderr(failure)
    monkeypatch.setattr(events.sys, "stderr", stream)
    monkeypatch.setenv("PCC_MACHO_LINK_PHASE_EVENTS", "1")
    return stream, events.phase_callback_from_environment()


def _pipeline(paths, destination, profile=None):
    pipeline._owned_macho_link_in_process(
        asm_path=None, pcc_asm_inputs=(), pcc_native_object_inputs=tuple(paths),
        runtime_archive=None, extra_link_inputs=(), tmp_out_path=str(destination),
        link_profile_path=None if profile is None else str(profile),
    )


@pytest.mark.parametrize("setting", (None, "", "0", "true", "yes", "2"))
def test_phase_events_are_disabled_unless_explicitly_one(monkeypatch, setting):
    if setting is None:
        monkeypatch.delenv("PCC_MACHO_LINK_PHASE_EVENTS", raising=False)
    else:
        monkeypatch.setenv("PCC_MACHO_LINK_PHASE_EVENTS", setting)
    assert events.phase_callback_from_environment() is None


def test_sink_is_bounded_scalar_closure_and_flushes_each_event(monkeypatch):
    stream, callback = _sink(monkeypatch)
    assert set(callback.__code__.co_freevars) == {"pid", "sequence", "failed"}
    assert all(type(cell.cell_contents) in (int, bool) for cell in callback.__closure__)
    callback("link_begin")
    callback("link_end")
    rows = _rows(stream)
    assert stream.writes == stream.flushes == 2
    assert [row["sequence"] for row in rows] == [0, 1]
    assert [row["event"] for row in rows] == ["link_begin", "link_end"]
    assert all(set(row) == {"schema", "pid", "sequence", "monotonic_ns", "event"}
               for row in rows)
    assert all(row["schema"] == "pcc.macho-link-phase.v1" and row["pid"] == os.getpid()
               for row in rows)
    assert rows[0]["monotonic_ns"] <= rows[1]["monotonic_ns"]


@pytest.mark.parametrize("failure", ("write", "flush"))
def test_failed_sink_stops_without_replacing_work_exception(monkeypatch, failure):
    stream, callback = _sink(monkeypatch, failure)
    callback("link_begin")
    callback("link_end")
    assert stream.writes == 1
    assert stream.flushes == (failure == "flush")
    assert all(type(cell.cell_contents) in (int, bool) for cell in callback.__closure__)
    original = ValueError("original compiler failure")
    with pytest.raises(ValueError) as caught:
        try:
            raise original
        finally:
            callback("cleanup")
    assert caught.value is original


def test_private_callback_and_factory_failures_are_fail_open(monkeypatch):
    def failed(*_args):
        raise MemoryError("diagnostic allocation")
    events.emit_phase(failed, "event")
    monkeypatch.setenv("PCC_MACHO_LINK_PHASE_EVENTS", "1")
    monkeypatch.setattr(events.os, "getpid", failed)
    assert events.phase_callback_from_environment() is None


@pytest.mark.parametrize("enabled", (False, True))
def test_owned_bytes_addresses_and_public_signature_events_are_unchanged(
    tmp_path, monkeypatch, enabled,
):
    paths = _stack_inputs(tmp_path, records=32, data_bytes=64)
    expected = macho_exec.link_prepared_executable(
        host.prepare_executable_from_paths(paths, compact_budget=_budget(50_000_000)),
        compact_budget=_budget(50_000_000),
    )
    stream, callback = _sink(monkeypatch)
    callback = callback if enabled else None
    budget = _budget(50_000_000)
    budget.retained_bytes = 137
    pending = [host.prepare_executable_from_paths(
        paths, compact_budget=budget, _phase_callback=callback,
    )]
    source = weakref.ref(pending[0])
    owners = _tracked_buffers(monkeypatch)
    public_events = []

    def public_phase(event):
        public_events.append(event)
        assert source() is None
        assert len([owner for owner in owners if not owner.closed]) == 1

    actual = output.link_owned_executable(
        pending.pop(), compact_budget=budget, phase_callback=public_phase,
        _phase_callback=callback,
    )
    assert actual == expected
    assert public_events == ["sign_begin", "sign_end"]
    assert budget.retained_bytes == 137 and source() is None
    assert all(owner.closed and len(set(owner.addresses)) <= 1 for owner in owners)
    names = [row["event"] for row in _rows(stream)]
    assert ("stackmap_validate_begin" in names) is enabled
    assert ("stackmap_validate_end" in names) is enabled


@pytest.mark.parametrize("failure", (None, "write", "flush", "factory"))
def test_real_pipeline_events_preserve_owned_route_image_and_profile(
    tmp_path, monkeypatch, failure,
):
    paths = _stack_inputs(tmp_path, records=32, data_bytes=64)
    monkeypatch.setattr(stackmap_host_budget, "host_compact_stackmap_budget",
                        lambda: _budget(50_000_000))
    monkeypatch.delenv("PCC_MACHO_LINK_PHASE_EVENTS", raising=False)
    _pipeline(paths, tmp_path / "off")
    stream, _callback = _sink(monkeypatch, failure)
    if failure == "factory":
        def failed_factory():
            raise MemoryError("diagnostic factory")
        monkeypatch.setattr(events, "phase_callback_from_environment", failed_factory)
    trace, profile = sys.gettrace(), sys.getprofile()
    monitoring = [(sys.monitoring.get_tool(index), sys.monitoring.get_events(index))
                  for index in range(6)]
    supported = output._owned_output_supported()
    _pipeline(paths, tmp_path / "on", tmp_path / "profile.json")
    assert (tmp_path / "on").read_bytes() == (tmp_path / "off").read_bytes()
    assert output._owned_output_supported() is supported is True
    assert sys.gettrace() is trace and sys.getprofile() is profile
    assert monitoring == [(sys.monitoring.get_tool(index), sys.monitoring.get_events(index))
                          for index in range(6)]
    receipt = json.loads((tmp_path / "profile.json").read_text())
    assert receipt["schema"] == "pcc.macho-link-profile.v1"
    assert set(receipt["phases_ms"]) == {
        "assemble_pool", "decode_pco", "prepare_link", "sign", "validate", "write",
    }
    if failure:
        assert stream.writes == (failure != "factory")
        return
    all_names = [row["event"] for row in _rows(stream)]
    names = [name for name in all_names
             if not name.startswith(("section_rows_", "section_payload_"))]
    assert names == [
        "link_begin", "host_budget_available", "prepare_inputs_begin",
        "input_scan_begin", "input_scan_end", "placement_begin", "placement_end",
        "input_detach_begin", "input_detach_end", "merge_index_begin", "merge_index_end",
        "stackmap_merge_begin", "stackmap_merge_end", "final_layout_begin", "final_layout_end",
        "section_joins_begin", "section_joins_end", "source_view_begin", "source_view_end",
        "prepare_inputs_end", "owned_output_selected", "output_model_begin", "output_model_end",
        "output_prepare_begin", "relocations_begin", "relocations_end",
        "stackmap_validate_begin", "stackmap_payload_validate_begin", "stackmap_payload_validate_end",
        "stackmap_thaw_begin", "stackmap_thaw_end", "stackmap_validate_end", "fixups_begin", "fixups_end",
        "symbols_begin", "symbols_end", "output_prepare_end", "output_spool_begin",
        "output_spool_end", "final_backing_begin", "final_backing_end", "sign_begin", "sign_end",
        "signature_verify_begin", "signature_verify_end", "write_begin", "write_end", "link_end",
    ]
    assert stream.writes == stream.flushes == len(all_names)


@pytest.mark.parametrize("failure", ("validator", "sign"))
@pytest.mark.parametrize("sink_failure", (None, "write", "flush"))
def test_failure_phase_is_durable_and_does_not_mask_original_error(
    tmp_path, monkeypatch, failure, sink_failure,
):
    paths = _stack_inputs(tmp_path, records=32, data_bytes=64)
    stream, callback = _sink(monkeypatch, sink_failure)
    budget = _budget(50_000_000)
    budget.retained_bytes = 137
    pending = [host.prepare_executable_from_paths(paths, compact_budget=budget)]
    source = weakref.ref(pending[0])
    owners = _tracked_buffers(monkeypatch)
    original = ValueError("original " + failure + " failure")
    public_events = []

    def failed(*_args, **_kwargs):
        if sink_failure is None:
            assert _rows(stream)[-1]["event"] == (
                "stackmap_payload_validate_begin" if failure == "validator" else "sign_begin"
            )
        raise original

    if failure == "validator":
        monkeypatch.setattr(maps, "validate_stack_map_payload", failed)
    else:
        monkeypatch.setattr(codesign, "build_signature", failed)
    with pytest.raises(ValueError) as caught:
        output.link_owned_executable(
            pending.pop(), compact_budget=budget, phase_callback=public_events.append,
            _phase_callback=callback,
        )
    assert caught.value is original
    assert all(owner.closed for owner in owners)
    assert budget.retained_bytes == 137 and source() is None
    assert public_events == ([] if failure == "validator" else ["sign_begin", "sign_end"])
    if sink_failure is None:
        names = [row["event"] for row in _rows(stream)]
        assert ("stackmap_validate_end" not in names if failure == "validator"
                else "sign_end" not in names)


def test_phase_adapter_stays_out_of_native_import_closure():
    from pcc.frontends.python.pipeline_dependency_closure import _package_import_targets
    from pcc.frontends.python.pipeline_import_scan import _without_inactive_implementation_imports
    path = Path(pipeline.__file__)
    source = path.read_text()
    marker = "from pcc.backend.macho_host_phase import"
    assert marker in source
    assert marker not in _without_inactive_implementation_imports(source, implementation="pcc")
    assert marker in _without_inactive_implementation_imports(source, implementation="cpython")
    dependencies = {name for _path, name in _package_import_targets(
        str(path), pipeline.__name__, root_dir=str(Path(__file__).resolve().parents[2]),
    )}
    assert "pcc.backend.macho_host_phase" not in dependencies


@pytest.mark.parametrize("event", ("stackmap_payload_validate_end", "stackmap_thaw_begin"))
@pytest.mark.parametrize("instrument", ("trace", "profile"))
def test_phase_callback_cannot_open_an_instrumented_thaw_window(
    tmp_path, monkeypatch, event, instrument,
):
    paths = _stack_inputs(tmp_path, count=1, records=32, data_bytes=64)
    budget = _budget(50_000_000)
    pending = [host.prepare_executable_from_paths(paths, compact_budget=budget)]
    owners = _tracked_buffers(monkeypatch)
    prior_trace, prior_profile = sys.gettrace(), sys.getprofile()

    def hook(_frame, _event, _arg):
        return hook

    def phase(name):
        if name == event:
            if instrument == "trace":
                sys.settrace(hook)
            else:
                sys.setprofile(hook)

    try:
        with pytest.raises(output.LinkError, match="uninstrumented"):
            output.link_owned_executable(pending.pop(), compact_budget=budget, _phase_callback=phase)
    finally:
        sys.settrace(prior_trace)
        sys.setprofile(prior_profile)
    assert all(owner.closed and len(set(owner.addresses)) <= 1 for owner in owners)
    assert max(len(owner.addresses) for owner in owners) == 3  # snapshot, never thaw


def test_profile_write_failure_has_no_successful_link_end(tmp_path, monkeypatch):
    paths = _stack_inputs(tmp_path, records=8, data_bytes=64)
    monkeypatch.setattr(stackmap_host_budget, "host_compact_stackmap_budget",
                        lambda: _budget(50_000_000))
    stream, _callback = _sink(monkeypatch)
    with pytest.raises(pipeline.SelfBackendLinkError, match="FileNotFoundError"):
        _pipeline(paths, tmp_path / "program", tmp_path / "missing" / "profile.json")
    names = [row["event"] for row in _rows(stream)]
    assert names[-1] == "write_end" and "link_end" not in names


def test_unbudgeted_reference_route_is_identified(tmp_path, monkeypatch):
    paths = _stack_inputs(tmp_path, records=8, data_bytes=64)
    monkeypatch.setattr(stackmap_host_budget, "host_compact_stackmap_budget", lambda: None)
    monkeypatch.delenv("PCC_MACHO_LINK_PHASE_EVENTS", raising=False)
    _pipeline(paths, tmp_path / "off")
    stream, _callback = _sink(monkeypatch)
    _pipeline(paths, tmp_path / "on")
    assert (tmp_path / "off").read_bytes() == (tmp_path / "on").read_bytes()
    names = [row["event"] for row in _rows(stream)]
    assert names[:6] == ["link_begin", "host_budget_unavailable", "reference_prepare_begin",
                         "reference_prepare_end", "reference_link_begin", "reference_link_end"]
    assert names[-1] == "link_end"


def test_native_ir_omits_optional_host_event_callback():
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.type_infer import infer_module
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    source = '''def run(value):
    import sys as execution_runtime
    if execution_runtime.implementation.name == "cpython":
        phase_events = None
        try:
            from unavailable_host_adapter import phase_callback_from_environment
            phase_events = phase_callback_from_environment()
        except BaseException:
            phase_events = None
    import sys as execution_runtime
    if execution_runtime.implementation.name == "cpython":
        if phase_events is not None:
            phase_events("link_begin")
    return value + 1
'''
    module = infer_module(parse_and_lift(source, "phase_boundary.py", "phase_boundary"))
    generator = L1CodeGen(module, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")
    generator._strict_no_libpython = True
    result = str(generator.generate())
    assert "unavailable_host_adapter" not in result
    definition = re.search(r"^define[^\n]*@user_phase_boundary_run\([^\n]*\) \{", result, re.MULTILINE)
    assert definition is not None
    body = result[definition.start():].split("\n}\n", 1)[0]
    assert not re.search(r"\bcall[^\n]*phase_callback_from_environment", body)
    assert "phase_events.obj.call" not in body
    assert "@py_cpy_" not in body


def test_ci_enables_events_only_for_the_existing_mac_stage1_build():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/pcc1-package-parity.yml").read_text()
    marker = '          PCC_MACHO_LINK_PHASE_EVENTS: "1"'
    assert workflow.count(marker) == 1
    mac = workflow.split("  pcc1-package-parity:\n", 1)[1]
    build = mac.split("      - name: Build current pcc1\n", 1)[1].split("      - name:", 1)[0]
    assert marker in build and 'PCC_HOST_INDEXED_PROCESS_SPLIT: "1"' in build
    assert "--phase stage1" in build
    assert "build/bootstrap/stage*.process.*/target.stderr" in mac
    assert 'PCC_BOOTSTRAP_STAGE_TIMEOUT: "7200"' in mac
    assert 'PCC_BOOTSTRAP_AUTO_TREE_RSS_CEILING_BYTES: "4294967296"' in mac
    assert 'PCC_BOOTSTRAP_MIN_TREE_RSS_BYTES: "2147483648"' in mac
    assert 'PCC_BOOTSTRAP_HOST_MEMORY_RESERVE_BYTES: "536870912"' in mac


def test_ci_selects_phase_tests_on_mac_with_default_xdist_and_existing_receipts():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/pcc1-package-parity.yml").read_text()
    path = "tests/python/test_macho_phase_events.py"
    assert workflow.count('      - "' + path + '"') == 2
    focused = workflow.split("      - name: Verify Windows global addressing and PE relocations\n", 1)[1]
    focused = focused.split("      - name:", 1)[0]
    assert "timeout-minutes: 2" in focused
    assert "${{ matrix.platform == 'macos-arm64' && '" + path + "' || '' }}" in focused
    assert "--junitxml=build/worker-prior-focused-host.xml" in focused
    originals = (
        "test_windows_symbol_immediates.py", "test_windows_global_memory_scratch.py",
        "test_windows_pe_absolute_relocations.py", "test_pe_stackmap_merge.py",
        "test_pe_host_budget.py", "test_pe_image_size_limit.py",
    )
    assert all("tests/python/" + name in focused for name in originals)
    assert not any(flag in focused for flag in ("-n0", "--noconftest", "addopts=", "--dist="))
    assert "build/worker-prior-preflight/**/*.xml" in workflow
    assert "            build/worker-prior-focused-host.xml\n" in workflow


def test_focused_junit_publication_preserves_the_gate_fresh_output_root(tmp_path):
    from _pytest.junitxml import LogXML

    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/pcc1-package-parity.yml").read_text()
    preflight = workflow.split("  worker-prior-preflight:\n", 1)[1].split("\n  windows-rss-publication:", 1)[0]
    focused = preflight.split("      - name: Verify Windows global addressing and PE relocations\n", 1)[1]
    focused = focused.split("      - name:", 1)[0]
    junit_path = re.search(r"--junitxml=(\S+)", focused).group(1)
    output_roots = set(re.findall(r"--out-dir (\S+) --phase preflight", preflight))
    assert output_roots == {"build/worker-prior-preflight"}
    out = tmp_path / output_roots.pop()
    destination = tmp_path / junit_path
    assert not destination.is_relative_to(out)
    assert "            " + junit_path + "\n" in preflight

    def publish(path):
        # Exercise pytest's actual directory-creation behavior. A path-only
        # assertion missed this ordering interaction in the original change.
        xml = LogXML(str(path), prefix=None)
        xml.pytest_sessionstart()
        xml.pytest_sessionfinish()
        assert path.is_file()

    publish(destination)
    phase = "preflight"
    # Preserve the gate's existing precondition, including its error text.
    assert not out.exists(), phase + " requires a fresh output root"

    # Reproduce the old placement with the same real publisher: it creates
    # the forbidden root before the gate, so the unchanged precondition fails.
    out = tmp_path / "old-placement" / "build" / "worker-prior-preflight"
    publish(out / "focused-host.xml")
    with pytest.raises(AssertionError, match="preflight requires a fresh output root"):
        assert not out.exists(), phase + " requires a fresh output root"


@pytest.mark.parametrize("consuming", (False, True))
@pytest.mark.parametrize("stackmaps", (False, True))
def test_join_construction_indexes_retire_without_mutating_aliases_or_ledger(
    tmp_path, monkeypatch, consuming, stackmaps,
):
    paths = (_stack_inputs(tmp_path, count=3, records=8, data_bytes=64) if stackmaps
             else _write_symbol_inputs(tmp_path, count=3, symbols=8))
    expected = _prepared_state(macho_exec.prepare_executable_object(
        [native.decode_packed_native_object(Path(path).read_bytes()) for path in paths],
        _consume_inputs=True, _source_view=True, compact_budget=_budget(50_000_000),
    ))
    monkeypatch.setattr(host, "_single_buffer_window_supported", lambda: consuming)
    aliases, snapshots, charges, retired = [], [], [], []
    names = ("stack_offsets", "stack_symbols", "symbol_indices", "section_indices")

    def phase(event, **_details):
        before = "stackmap_merge_end" if stackmaps else "merge_index_end"
        if event not in (before, "final_layout_begin"):
            return
        # Inspect only this test's stopped production caller. Do not retain the
        # frame/local mapping across the next ownership-sensitive operation.
        frame = sys._getframe(2)
        assert frame.f_code.co_name == "_merge_paths"
        local = frame.f_locals
        try:
            charges.append(local["ledger"].retained)
            if event == before:
                for name in names:
                    if name in local:
                        value = local[name]
                        aliases.append(value)
                        snapshots.append(dict(value) if isinstance(value, dict) else tuple(value))
            else:
                retired.append(all(local[name] is None for name in names))
        finally:
            del frame, local

    budget = _budget(50_000_000)
    budget.retained_bytes = 137
    prepared = host.prepare_executable_from_paths(paths, compact_budget=budget, _phase_callback=phase)
    assert retired == [True] and len(charges) == 2 and charges[0] == charges[1]
    assert all(alias == snapshot for alias, snapshot in zip(aliases, snapshots))
    assert _prepared_state(prepared) == expected
    assert budget.retained_bytes == 137


@pytest.mark.parametrize("consuming", (False, True))
def test_section_join_events_report_existing_scalar_sizes_and_selected_branches(
    tmp_path, monkeypatch, consuming,
):
    paths = _stack_inputs(tmp_path, count=3, records=8, data_bytes=64)
    monkeypatch.setattr(host, "_single_buffer_window_supported", lambda: consuming)
    stream, callback = _sink(monkeypatch)
    prepared = host.prepare_executable_from_paths(
        paths, compact_budget=_budget(50_000_000), _phase_callback=callback,
    )
    state = _prepared_state(prepared)
    selected = [row for row in _rows(stream) if "section_index" in row]
    assert len(selected) == 4 * len(state[0])
    expected_fields = {"schema", "pid", "sequence", "monotonic_ns", "event", "section_index",
                       "segment", "section", "size_bytes", "parts", "consuming"}
    for index, section in enumerate(state[0]):
        rows = selected[index * 4:(index + 1) * 4]
        assert [row["event"] for row in rows] == [
            "section_rows_begin", "section_rows_end", "section_payload_begin", "section_payload_end",
        ]
        for row in rows:
            assert set(row) == expected_fields
            assert row["section_index"] == index
            assert (row["segment"], row["section"]) == (section["segname_str"], section["sectname_str"])
            assert all(type(value) in (str, int, bool) for value in row.values())
        assert rows[0]["size_bytes"] == rows[1]["size_bytes"] == len(state[3][index])
        assert rows[2]["size_bytes"] == rows[3]["size_bytes"] == len(state[2][index])
        assert rows[0]["parts"] == rows[1]["parts"]
        assert rows[2]["parts"] == rows[3]["parts"]
        assert rows[0]["consuming"] is (consuming and rows[0]["parts"] > 1)
        assert rows[2]["consuming"] is (consuming and rows[2]["parts"] > 1)
    assert all(type(cell.cell_contents) in (int, bool) for cell in callback.__closure__)
    assert stream.writes == stream.flushes == len(_rows(stream))


@pytest.mark.parametrize("point", ("section_rows_begin", "section_rows_end", "section_payload_begin"))
@pytest.mark.parametrize("instrument", ("trace", "profile"))
def test_section_join_callbacks_cannot_open_an_instrumented_consuming_window(
    tmp_path, monkeypatch, point, instrument,
):
    from pcc.backend import macho_host_join as join, macho_link

    paths = _stack_inputs(tmp_path, count=3, records=8, data_bytes=64)
    original = host._consume_parts
    calls = []

    def consume(parts, ledger, **kwargs):
        calls.append((join._join_instrumentation_absent(), len(parts)))
        return original(parts, ledger, **kwargs)

    monkeypatch.setattr(host, "_consume_parts", consume)
    previous_trace, previous_profile = sys.gettrace(), sys.getprofile()
    reached = []

    def hook(_frame, _event, _arg):
        return hook

    def phase(event, **details):
        # __data has multi-part rows and payload; changing a hook after the
        # earlier boolean selection must still hit the helper's refusal.
        if event == point and details.get("section") == "__data":
            reached.append(event)
            getattr(sys, "set" + instrument)(hook)

    try:
        with pytest.raises(macho_link.LinkError, match="uninstrumented host"):
            host.prepare_executable_from_paths(paths, compact_budget=_budget(50_000_000),
                                               _phase_callback=phase)
    finally:
        sys.settrace(previous_trace)
        sys.setprofile(previous_profile)
    assert reached == [point] and calls[-1] == (False, 3)


def test_section_event_adapter_preserves_legacy_sink_and_original_exception():
    old_events = []
    events.emit_section_phase(old_events.append, "section_rows_begin", 0,
                              ("__TEXT", "__text"), 16, 2, True)
    events.emit_phase(old_events.append, "section_joins_end")
    assert old_events == ["section_joins_end"]

    def failed(*_args, **_kwargs):
        raise MemoryError("section diagnostic failed")

    original = ValueError("original join failure")
    with pytest.raises(ValueError) as caught:
        try:
            raise original
        finally:
            events.emit_section_phase(failed, "section_rows_end", 0,
                                      ("__TEXT", "__text"), 16, 2, True)
    assert caught.value is original


@pytest.mark.parametrize("point", ("section_rows_begin", "section_payload_begin"))
def test_join_failure_keeps_its_scalar_begin_without_a_false_completion(tmp_path, monkeypatch, point):
    paths = _stack_inputs(tmp_path, count=3, records=8, data_bytes=64)
    stream, callback = _sink(monkeypatch)
    consume = host._consume_parts
    original = ValueError("original section join failure")

    def failed(parts, ledger, **kwargs):
        last = _rows(stream)[-1]
        if last["event"] == point and last["section"] == "__data":
            raise original
        return consume(parts, ledger, **kwargs)

    monkeypatch.setattr(host, "_consume_parts", failed)
    with pytest.raises(ValueError) as caught:
        host.prepare_executable_from_paths(paths, compact_budget=_budget(50_000_000),
                                           _phase_callback=callback)
    assert caught.value is original
    rows = _rows(stream)
    assert rows[-1]["event"] == point and rows[-1]["section"] == "__data"
    assert rows[-1]["consuming"] is True
    assert "section_joins_end" not in [row["event"] for row in rows]


def test_section_diagnostics_never_run_inside_a_mutable_join_export(tmp_path, monkeypatch):
    from pcc.backend import macho_host_join as join
    from tests.python.test_macho_consuming_join import _track_buffers

    paths = _stack_inputs(tmp_path, count=3, records=8, data_bytes=64)
    owners = _track_buffers(monkeypatch)
    stream, callback = _sink(monkeypatch)
    joined = []
    original = host._consume_parts

    def consume(parts, ledger, **kwargs):
        result = original(parts, ledger, **kwargs)
        joined.append(len(result))
        return result

    def phase(event, **details):
        if event.startswith(("section_rows_", "section_payload_")):
            assert all(owner.closed for owner in owners)
        callback(event, **details)

    monkeypatch.setattr(host, "_consume_parts", consume)
    host.prepare_executable_from_paths(paths, compact_budget=_budget(50_000_000),
                                       _phase_callback=phase)
    assert joined and all(owner.closed and len(set(owner.addresses)) <= 1 for owner in owners)
    assert len([row for row in _rows(stream) if "section_index" in row]) == 12
    assert join._join_instrumentation_absent()


def test_single_part_section_target_reports_consuming_patch_branch(tmp_path, monkeypatch):
    from tests.python.test_macho_consuming_join import _section_targets
    from tests.python.test_macho_windowed_parity import _write_pcos

    paths = _write_pcos(tmp_path, _section_targets())
    stream, callback = _sink(monkeypatch)
    host.prepare_executable_from_paths(paths, compact_budget=_budget(50_000_000),
                                       _phase_callback=callback)
    rows = [row for row in _rows(stream)
            if row["event"].startswith("section_payload_") and row["section"] == "__data"]
    assert [row["event"] for row in rows] == ["section_payload_begin", "section_payload_end"]
    assert all(row["parts"] == 1 and row["consuming"] is True for row in rows)

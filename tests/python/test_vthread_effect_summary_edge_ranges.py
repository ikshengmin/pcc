"""Caller-local edge ranges preserve summary order, aliases and failures."""

from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pcc.frontends.python.codegen import vthread_effect_analysis as analysis
from pcc.frontends.python.py_lift import parse_and_lift


SOURCE = """import pcc.virtual_thread as vt
from external import ext_left, ext_right, RemoteOne, RemoteTwo

def old_alias():
    return leaf()
def keep():
    return leaf()
def new_alias():
    return quiet()
def entry():
    old_alias()
    new_alias()
    LocalOne().run()
    LocalTwo().run()
    RemoteOne().run()
    RemoteTwo().run()
    ext_left()
    ext_right()
    return 0
def leaf():
    vt.yield_now()
    return 1
def quiet():
    return 0
class LocalOne:
    def run(self):
        return leaf()
class LocalTwo:
    def run(self):
        return quiet()
"""


def _function(module_name, name):
    return {
        "kind": "function",
        "owning_module": module_name,
        "export_name": name,
    }


def _class(module_name, name):
    return {
        "kind": "class",
        "owning_module": module_name,
        "export_name": name,
        "methods": [{"name": "run", "may_park": False}],
    }


def _exports():
    shared_function = _function("unit", "shared_fn")
    shared_local_class = _class("unit", "SharedLocal")
    shared_remote_function = _function("dep", "shared_fn")
    shared_remote_class = _class("dep", "SharedRemote")
    return {
        "unit": {
            "old_alias": shared_function,
            "new_alias": shared_function,
            "keep": _function("unit", "keep"),
            "entry": _function("unit", "entry"),
            "leaf": _function("unit", "leaf"),
            "quiet": _function("unit", "quiet"),
            "LocalOne": shared_local_class,
            "LocalTwo": shared_local_class,
        },
        "external": {
            "ext_left": shared_remote_function,
            "ext_right": shared_remote_function,
            "RemoteOne": shared_remote_class,
            "RemoteTwo": shared_remote_class,
        },
        "dep": {
            "shared_fn": shared_remote_function,
            "SharedRemote": shared_remote_class,
        },
    }


def _empty_summary():
    return {
        "schema": "pcc.vthread.effect-summary.v1",
        "module_name": "unit",
        "seeds": [],
        "edges": [],
        "publish": [],
    }


def _expected_summary():
    return {
        "schema": "pcc.vthread.effect-summary.v1",
        "module_name": "unit",
        "seeds": ["f:unit:leaf"],
        "edges": [
            "f:unit:keep", "f:unit:leaf",
            "f:unit:shared_fn", "f:unit:quiet",
            "f:unit:entry", "f:unit:shared_fn",
            "f:unit:entry", "m:unit:SharedLocal:run",
            "f:unit:entry", "m:dep:SharedRemote:run",
            "f:unit:entry", "f:dep:shared_fn",
            "m:unit:SharedLocal:run", "f:unit:quiet",
        ],
        "publish": [
            "f:unit:shared_fn", "f:unit:keep", "f:unit:entry",
            "f:unit:leaf", "f:unit:quiet", "m:unit:SharedLocal:run",
        ],
    }


def _force_full_scan(original):
    def full_scan(summary, caller, callee, caller_edges_start=0):
        # The unchanged default range is the former all-prefix algorithm.
        return original(summary, caller, callee)

    return full_scan


@pytest.mark.parametrize("summary_only", [False, True])
def test_caller_ranges_match_full_scans_for_all_edge_sources(
    monkeypatch, tmp_path, summary_only,
):
    module = parse_and_lift(SOURCE, "unit.py", "unit")
    exports = _exports()
    before = deepcopy(exports)
    original = analysis._vthread_summary_add_edge
    full = _empty_summary()
    with monkeypatch.context() as reference:
        reference.setattr(analysis, "_vthread_summary_add_edge", _force_full_scan(original))
        expected_result = analysis._compute_local_vthread_effects(
            module, exports, effect_summary=full, summary_module_name="unit",
            summary_only=summary_only,
        )
    actual = _empty_summary()
    actual_result = analysis._compute_local_vthread_effects(
        module, exports, effect_summary=actual, summary_module_name="unit",
        summary_only=summary_only,
    )
    assert actual_result == expected_result
    assert actual == full == _expected_summary()
    assert analysis.build_closed_world_vthread_effect_summary(module, "unit", exports) == full
    assert exports == before
    assert exports["unit"]["old_alias"] is exports["unit"]["new_alias"]
    assert exports["unit"]["LocalOne"] is exports["unit"]["LocalTwo"]
    assert exports["external"]["ext_left"] is exports["dep"]["shared_fn"]
    assert exports["external"]["RemoteTwo"] is exports["dep"]["SharedRemote"]
    full_path = tmp_path / "full.wire"
    actual_path = tmp_path / "ranges.wire"
    analysis.write_closed_world_vthread_effect_summary(str(full_path), full)
    analysis.write_closed_world_vthread_effect_summary(str(actual_path), actual)
    assert actual_path.read_bytes() == full_path.read_bytes()
    assert analysis.read_closed_world_vthread_effect_summary(str(actual_path)) == full


class _ReadCountingList(list):
    def __init__(self):
        super().__init__()
        self.reads = 0

    def __getitem__(self, index):
        self.reads += 1
        return super().__getitem__(index)


@pytest.mark.parametrize("callers", [16, 64, 256])
def test_edge_ranges_do_not_read_prior_callers_for_deduplication(callers):
    # Count this helper's item reads, not runtime or total summary complexity.
    # Reset, publish and within-caller scans are intentionally unchanged.
    ranged_edges = _ReadCountingList()
    full_edges = _ReadCountingList()
    ranged = {"edges": ranged_edges}
    full = {"edges": full_edges}
    for index in range(callers):
        caller = f"caller:{index}"
        start = len(ranged_edges)
        analysis._vthread_summary_add_edge(ranged, caller, "target", start)
        analysis._vthread_summary_add_edge(ranged, caller, "target", start)
        analysis._vthread_summary_add_edge(full, caller, "target")
        analysis._vthread_summary_add_edge(full, caller, "target")
    assert ranged_edges == full_edges
    assert ranged["edges"] is ranged_edges
    assert full["edges"] is full_edges
    assert ranged_edges.reads == 2 * callers
    assert full_edges.reads == callers * (callers + 1)


def test_range_is_captured_after_reset_and_canonical_caller_replacement():
    edges = ["shared", "old", "other", "keep", "shared", "old2"]
    summary = {"edges": edges, "seeds": ["shared", "other", "shared"]}
    analysis._vthread_summary_reset_caller(summary, "shared")
    assert summary == {"edges": ["other", "keep"], "seeds": ["other"]}
    assert edges == ["shared", "old", "other", "keep", "shared", "old2"]
    assert summary["edges"] is not edges
    start = len(summary["edges"])
    analysis._vthread_summary_add_edge(summary, "shared", "new", start)
    analysis._vthread_summary_add_edge(summary, "shared", "new", start)
    analysis._vthread_summary_add_edge(summary, "shared", "other", start)
    assert summary["edges"] == ["other", "keep", "shared", "new", "shared", "other"]


@pytest.mark.parametrize("failed_append", [1, 2])
@pytest.mark.parametrize("error_type", [MemoryError, RuntimeError])
def test_edge_append_failures_preserve_exact_partial_list_and_exception(
    failed_append, error_type,
):
    failure = error_type("edge append failed")

    class FailingAppend(list):
        def __init__(self):
            super().__init__(["previous", "target"])
            self.calls = 0

        def append(self, value):
            self.calls += 1
            if self.calls == failed_append:
                raise failure
            super().append(value)

    outcomes = []
    for start in (0, 2):
        edges = FailingAppend()
        summary = {"edges": edges}
        with pytest.raises(error_type) as raised:
            analysis._vthread_summary_add_edge(summary, "new", "callee", start)
        assert raised.value is failure
        assert summary["edges"] is edges
        outcomes.append((list(edges), edges.calls))
    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0] == ["previous", "target"] + (
        ["new"] if failed_append == 2 else []
    )


@pytest.mark.parametrize("kind,failed_call", [
    ("function", 1), ("function", 2), ("function", 5), ("function", 9),
    ("method", 1), ("method", 2), ("method", 4),
])
def test_key_failures_preserve_summary_mutation_order(
    monkeypatch, kind, failed_call,
):
    module = parse_and_lift(SOURCE, "unit.py", "unit")
    exports = _exports()
    add_edge = analysis._vthread_summary_add_edge
    key_name = "_vthread_effect_" + kind + "_key"
    make_key = getattr(analysis, key_name)
    failure = ValueError("canonical key failed")
    outcomes = []
    for full_scan in (True, False):
        summary = _empty_summary()
        summary["seeds"] = ["f:unit:shared_fn", "existing"]
        summary["edges"] = ["f:unit:shared_fn", "old", "existing", "kept"]
        previous_edges = summary["edges"]
        events = []

        def fail_key(*args):
            events.append(args[:-1])
            if len(events) == failed_call:
                raise failure
            return make_key(*args)

        with monkeypatch.context() as fault:
            fault.setattr(analysis, key_name, fail_key)
            if full_scan:
                fault.setattr(analysis, "_vthread_summary_add_edge", _force_full_scan(add_edge))
            with pytest.raises(ValueError) as raised:
                analysis._compute_local_vthread_effects(
                    module, exports, effect_summary=summary,
                    summary_module_name="unit", summary_only=True,
                )
        assert raised.value is failure
        assert previous_edges == ["f:unit:shared_fn", "old", "existing", "kept"]
        outcomes.append((summary, summary["edges"] is previous_edges, events))
    assert outcomes[0] == outcomes[1]


@pytest.mark.integration
def test_edge_range_program_matches_same_source_cpython(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    """Native pcc0/pcc1 fixture, not timing, GC pressure or Stage2 acceptance."""
    program = (
        "import sys\n"
        "from pcc.frontends.python.py_lift import parse_and_lift\n"
        "from pcc.frontends.python.codegen.vthread_effect_analysis import (\n"
        "    build_closed_world_vthread_effect_summary,\n"
        "    write_closed_world_vthread_effect_summary,\n"
        ")\n"
        f"SOURCE = {SOURCE!r}\n"
        f"EXPORTS = {_exports()!r}\n"
        "def main():\n"
        "    module = parse_and_lift(SOURCE, 'unit.py', 'unit')\n"
        "    summary = build_closed_world_vthread_effect_summary(module, 'unit', EXPORTS)\n"
        "    write_closed_world_vthread_effect_summary(sys.argv[1], summary)\n"
        "    print('|'.join(summary['publish']))\n"
        "    print('|'.join(summary['seeds']))\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    source = tmp_path / "edge_ranges.py"
    executable = tmp_path / "edge_ranges"
    reference_path = tmp_path / "reference.wire"
    source.write_text(program, encoding="utf-8")
    repo = Path(__file__).resolve().parents[2]
    reference_env = dict(os.environ)
    reference_env["PYTHONPATH"] = str(repo) + os.pathsep + reference_env.get("PYTHONPATH", "")
    reference = subprocess.run(
        [sys.executable, str(source), str(reference_path)],
        cwd=tmp_path, env=reference_env, capture_output=True, text=True, timeout=30,
    )
    assert reference.returncode == 0, reference.stdout + reference.stderr
    assert reference.stderr == ""
    expected = _expected_summary()
    assert analysis.read_closed_world_vthread_effect_summary(str(reference_path)) == expected
    assert reference.stdout.splitlines() == [
        "|".join(expected["publish"]), "|".join(expected["seeds"]),
    ]
    python_program_compiler(
        str(source), str(executable), backend="self",
        runtime_archive=str(pcc_runtime_archive), ir_scaffold_mode="on",
    )
    expected_wire = reference_path.read_bytes()
    for collector in ("0", "1", "2", "3", "4"):
        actual_path = tmp_path / f"native_gc{collector}.wire"
        result = subprocess.run(
            [str(executable), str(actual_path)], cwd=tmp_path,
            env={**os.environ, "PCC_GC_BACKEND": collector},
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, (collector, result.stdout, result.stderr)
        assert result.stderr == "", collector
        assert result.stdout == reference.stdout, collector
        assert actual_path.read_bytes() == expected_wire, collector

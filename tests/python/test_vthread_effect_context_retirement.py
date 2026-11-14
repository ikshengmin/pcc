"""Retire fresh analysis tables after collection, without changing effects.

Observed local references and weakrefs are host-only ownership assertions,
not RSS, native reclamation, timing, or full bootstrap measurements.
"""

from copy import deepcopy
from inspect import getsource, getsourcelines
import os
from pathlib import Path
import subprocess
import sys
import weakref
from typing import Optional

import pytest

from pcc.frontends.python.codegen import vthread_effect_analysis as analysis
from pcc.frontends.python.codegen.vthread_effect_analysis import (
    _add_local_callable_effect,
    _compute_local_vthread_effects,
    _cross_module_bindings,
    _export_target_is_may_park,
    _local_vthread_context,
)
from pcc.frontends.python.py_ast import Module
from pcc.frontends.python.py_lift import parse_and_lift


CORPUS = [
    ("empty", "pass\n"),
    ("quiet", "def one():\n    return 1\ndef two():\n    return one()\n"),
    ("chain", "from pcc.virtual_thread import yield_now\n"
     "def first():\n    second()\ndef second():\n    third()\n"
     "def third():\n    fourth()\ndef fourth():\n    yield_now()\n"
     "class Worker:\n    def outer(self):\n        self.middle()\n"
     "    def middle(self):\n        self.park()\n    def park(self):\n        yield_now()\n"),
    ("cycles", "from pcc.virtual_thread import yield_now\n"
     "def a():\n    b()\ndef b():\n    a()\n"
     "def c():\n    d()\ndef d():\n    c()\n    yield_now()\n"),
    ("shadowed", "from pcc.virtual_thread import yield_now\n"
     "def local(yield_now):\n    yield_now()\n"
     "def global_write():\n    global yield_now\n    yield_now = 1\n"
     "def changed():\n    yield_now()\n"),
    ("redefined", "from pcc.virtual_thread import yield_now\n"
     "def same():\n    yield_now()\ndef same():\n    pass\n"
     "class C:\n    def same(self):\n        yield_now()\n"
     "class C:\n    def same(self):\n        pass\n"),
    ("threading", "from threading import Event, Condition\n"
     "class Worker:\n    def make(self):\n        self.ready = Event()\n"
     "    def wait(self):\n        self.ready.wait()\n"
     "def local():\n    signal = Condition()\n    signal.wait()\n"),
    ("imports", "from other import remote, Remote\n"
     "def function():\n    remote()\n"
     "def method():\n    Remote().run()\n"),
]
EXPORTS = {
    "other": {
        "remote": {"kind": "function", "may_park": True},
        "Remote": {"kind": "class", "methods": ({"name": "run", "may_park": True},)},
    },
}
RETIRED = (
    "local_context", "module_aliases", "value_aliases", "methods", "bases",
    "callable_context", "threading_context", "proof_cache", "function_names",
    "imported_modules",
)
CONTEXT_TABLES = (
    ("module_aliases", 0), ("value_aliases", 1), ("methods", 3),
    ("bases", 4), ("callable_context", 6),
)


def _summary():
    return {"schema": analysis._VTHREAD_EFFECT_SUMMARY_SCHEMA,
            "module_name": "unit", "seeds": [], "edges": [], "publish": []}


class _TrackedDict(dict):
    pass


def _observe(monkeypatch, function, module, exports, *, provided, mode,
             failure=""):
    refs = {}
    snapshots = []
    calls = []
    stage = ["collect"]
    builder = analysis._local_vthread_context
    add = analysis._add_local_callable_effect
    exported_effect = analysis._export_target_is_may_park

    def build_context(node):
        if failure == "context":
            raise ValueError("injected context failure")
        context = list(builder(node))
        for name, index in CONTEXT_TABLES:
            value = _TrackedDict(context[index])
            refs[name] = weakref.ref(value)
            context[index] = value
        return tuple(context)

    def add_callable(*args, **kwargs):
        calls.append(args[0])
        if failure == "before-callable" and len(calls) == 1:
            raise RuntimeError("injected before callable")
        add(*args, **kwargs)
        if failure == "after-callable" and len(calls) == 1:
            raise RuntimeError("injected after callable")

    def target_effect(*args, **kwargs):
        if failure == "result" and stage[0] == "solve":
            raise LookupError("injected result failure")
        return exported_effect(*args, **kwargs)

    lines, first_line = getsourcelines(function)
    solve_line = first_line + next(index for index, line in enumerate(lines)
                                  if line.strip() == "changed = True")

    def trace(frame, event, value):
        if frame.f_code is not function.__code__:
            return None
        if event == "line" and frame.f_lineno == solve_line:
            stage[0] = "solve"
            record(frame, "solve")
        elif event == "return":
            record(frame, "return")
        return trace

    def record(frame, event):
        local = frame.f_locals
        snapshots.append((event, tuple(name in local and local[name] is None
                                      for name in RETIRED),
                          tuple(ref() is None for ref in refs.values())))

    context = build_context(module) if provided else None
    before_context = repr(context)
    summary = None if mode == "full" else _summary()
    previous = sys.gettrace()
    with monkeypatch.context() as patch:
        patch.setitem(function.__globals__, "_local_vthread_context", build_context)
        patch.setitem(function.__globals__, "_add_local_callable_effect", add_callable)
        patch.setitem(function.__globals__, "_export_target_is_may_park", target_effect)
        try:
            sys.settrace(trace)
            result = function(
                module, exports, effect_summary=summary, summary_module_name="unit",
                local_context=context, summary_only=mode == "summary",
                fresh_summary=mode == "summary",
            )
            outcome = ("result", result)
        except Exception as error:
            outcome = ("error", type(error), str(error))
        finally:
            sys.settrace(previous)
    assert repr(context) == before_context
    return outcome, summary, snapshots, calls


@pytest.mark.parametrize("name, source", CORPUS)
@pytest.mark.parametrize("provided", [False, True])
@pytest.mark.parametrize("mode", ["full", "side-summary", "summary"])
def test_fresh_retirement_preserves_results_and_supplied_contexts(
    monkeypatch, name, source, provided, mode,
):
    module = parse_and_lift(source, name + ".py", "unit")
    exports = deepcopy(EXPORTS)
    before = deepcopy(exports)
    expected = _observe(monkeypatch, _legacy_compute, module, exports,
                        provided=provided, mode=mode)
    actual = _observe(monkeypatch, _compute_local_vthread_effects, module, exports,
                      provided=provided, mode=mode)
    assert actual[:2] == expected[:2]
    assert actual[3] == expected[3]
    assert actual[0][0] == "result"
    assert exports == before
    for event, retired, dead in expected[2]:
        assert not any(retired)
        assert not any(dead)
    if provided or mode == "summary":
        assert actual[2] == expected[2]
    else:
        assert [event for event, _retired, _dead in actual[2]] == ["solve", "return"]
        for _event, retired, dead in actual[2]:
            assert all(retired)
            assert len(dead) == len(CONTEXT_TABLES) and all(dead)


@pytest.mark.parametrize("count", [32, 128])
@pytest.mark.parametrize("provided", [False, True])
def test_long_fixed_points_preserve_definition_ids_and_names(monkeypatch, count, provided):
    lines = ["from pcc.virtual_thread import yield_now"]
    for index in range(count):
        target = "yield_now" if index + 1 == count else "f" + str(index + 1)
        lines.append("def f" + str(index) + "():\n    " + target + "()")
    module = parse_and_lift("\n".join(lines) + "\n", "chain.py", "unit")
    expected = _observe(monkeypatch, _legacy_compute, module, None,
                        provided=provided, mode="full")
    actual = _observe(monkeypatch, _compute_local_vthread_effects, module, None,
                      provided=provided, mode="full")
    assert actual[:2] == expected[:2]
    assert actual[0][0] == "result"
    assert actual[0][1][1] == {"f" + str(index) for index in range(count)}
    assert len(actual[0][1][0]) == count


@pytest.mark.parametrize("failure, provided", [
    ("context", False), ("before-callable", False), ("before-callable", True),
    ("after-callable", False), ("after-callable", True),
    ("result", False), ("result", True),
])
def test_original_exceptions_and_partial_summary_are_preserved(monkeypatch, failure, provided):
    module = parse_and_lift(CORPUS[-1][1], "imports.py", "unit")
    expected = _observe(monkeypatch, _legacy_compute, module, deepcopy(EXPORTS),
                        provided=provided, mode="side-summary", failure=failure)
    actual = _observe(monkeypatch, _compute_local_vthread_effects, module, deepcopy(EXPORTS),
                      provided=provided, mode="side-summary", failure=failure)
    assert actual[:2] == expected[:2]
    assert actual[3] == expected[3]
    assert actual[0][0] == "error"
    if provided or failure != "result":
        assert actual[2] == expected[2]


NATIVE_BODY = """
def make_summary():
    return {'schema': 'pcc.vthread.effect-summary.v1', 'module_name': 'unit',
            'seeds': [], 'edges': [], 'publish': []}

def main():
    index = 0
    for name, source in CORPUS:
        module = parse_and_lift(source, name + '.py', 'unit')
        assert _compute_local_vthread_effects(module, None) == _legacy_compute(module, None)
        context = _local_vthread_context(module)
        for provided in (False, True):
            for mode in (0, 1, 2):
                actual_summary = None
                expected_summary = None
                if mode != 0:
                    actual_summary = make_summary()
                    expected_summary = make_summary()
                selected = context if provided else None
                expected = _legacy_compute(
                    module, EXPORTS, effect_summary=expected_summary,
                    summary_module_name='unit', local_context=selected,
                    summary_only=mode == 2, fresh_summary=mode == 2,
                )
                actual = _compute_local_vthread_effects(
                    module, EXPORTS, effect_summary=actual_summary,
                    summary_module_name='unit', local_context=selected,
                    summary_only=mode == 2, fresh_summary=mode == 2,
                )
                assert actual == expected
                assert actual_summary == expected_summary
                again = _compute_local_vthread_effects(module, EXPORTS, local_context=context)
                assert again == _legacy_compute(module, EXPORTS, local_context=context)
                if mode == 2:
                    assert actual is None
                else:
                    print(name + ':' + '|'.join(sorted(actual[1])) + ':' + '|'.join(sorted(actual[3])))
                if actual_summary is not None:
                    path = sys.argv[1] + '_' + str(index) + '.wire'
                    write_closed_world_vthread_effect_summary(path, actual_summary)
                index += 1
    print('EFFECT_CONTEXT_RETIREMENT_OK')

if __name__ == '__main__':
    main()
"""


@pytest.mark.integration
def test_native_retirement_matches_same_source_cpython(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    program = (
        "import sys\nfrom typing import Optional\n"
        "from pcc.frontends.python.py_ast import Module\n"
        "from pcc.frontends.python.py_lift import parse_and_lift\n"
        "from pcc.frontends.python.codegen.vthread_effect_analysis import (\n"
        "    _add_local_callable_effect, _compute_local_vthread_effects,\n"
        "    _cross_module_bindings, _export_target_is_may_park, _local_vthread_context,\n"
        "    write_closed_world_vthread_effect_summary,\n"
        ")\n"
        + getsource(_legacy_compute)
        + f"\nCORPUS = {CORPUS!r}\nEXPORTS = {EXPORTS!r}\n"
        + NATIVE_BODY
    )
    source, executable = tmp_path / "retire_program.py", tmp_path / "retire_program"
    source.write_text(program, encoding="utf-8")
    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo) + os.pathsep + env.get("PYTHONPATH", "")
    reference_prefix = tmp_path / "reference"
    reference = subprocess.run([sys.executable, str(source), str(reference_prefix)],
                               cwd=tmp_path, env=env, capture_output=True,
                               text=True, timeout=30)
    assert reference.returncode == 0, reference.stdout + reference.stderr
    assert reference.stderr == ""
    assert reference.stdout.endswith("EFFECT_CONTEXT_RETIREMENT_OK\n")
    expected_wires = {}
    for index in range(len(CORPUS) * 6):
        if index % 3 != 0:
            expected_wires[index] = Path(str(reference_prefix) + "_" + str(index) + ".wire").read_bytes()
    python_program_compiler(str(source), str(executable), backend="self",
                            runtime_archive=str(pcc_runtime_archive), ir_scaffold_mode="on")
    for collector in ("0", "1", "2", "3", "4"):
        prefix = tmp_path / ("native_gc" + collector)
        result = subprocess.run([str(executable), str(prefix)], cwd=tmp_path,
                                env={**os.environ, "PCC_GC_BACKEND": collector},
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, (collector, result.stdout, result.stderr)
        assert result.stderr == "", collector
        assert result.stdout == reference.stdout, collector
        for index, expected in expected_wires.items():
            assert Path(str(prefix) + "_" + str(index) + ".wire").read_bytes() == expected, (collector, index)


# Frozen 8e28dfa _compute_local_vthread_effects; only the name differs.
def _legacy_compute(
    module: Module,
    native_exports: Optional[dict],
    *,
    effect_summary: Optional[dict] = None,
    summary_module_name: str = "",
    local_context=None,
    summary_only: bool = False,
    fresh_summary: bool = False,
) -> Optional[tuple[set[int], set[str], set[int], set[str]]]:
    """Collect callable effects, then normally solve the local fixed point.

    Summary workers only need the seeds, edges and publish keys collected in
    ``effect_summary``.  Their parent solves the cross-module fixed point, so
    ``summary_only`` does not populate the unused local graph/effect set and
    returns None without deriving unused local result sets.  All callable
    analyses and their summary-producing early returns are still performed.
    All ordinary callers, including those collecting a summary as a side
    output, retain the complete local function/method result.

    ``fresh_summary`` is reserved for the builder's exclusively owned, empty
    summary.  Supplied summaries retain their original reset/alias behavior.
    """
    # Supplied contexts keep their original per-callable snapshot behavior.
    borrow_methods = local_context is None
    if local_context is None:
        local_context = _local_vthread_context(module)
    (
        module_aliases,
        value_aliases,
        functions,
        methods,
        bases,
        method_defs,
        callable_context,
        threading_context,
    ) = local_context
    imported_functions, imported_modules = _cross_module_bindings(
        module,
        native_exports,
    )
    function_names = {fd.name for fd in functions}
    for local_name in tuple(imported_functions):
        if local_name in function_names:
            imported_functions.pop(local_name, None)
    proof_cache = {}
    graph: dict[str, list[str]] = {}
    effects: set[str] = set()

    for fd in functions:
        _add_local_callable_effect(
            "function:" + fd.name,
            fd,
            None,
            function_names,
            module_aliases,
            value_aliases,
            imported_functions,
            imported_modules,
            native_exports,
            methods,
            bases,
            module,
            graph,
            effects,
            threading_context,
            callable_context,
            proof_cache,
            effect_summary,
            summary_module_name,
            fresh_summary,
            summary_only,
            borrow_methods,
        )
    for class_name, fd in method_defs:
        _add_local_callable_effect(
            "method:" + class_name + "." + fd.name,
            fd,
            class_name,
            function_names,
            module_aliases,
            value_aliases,
            imported_functions,
            imported_modules,
            native_exports,
            methods,
            bases,
            module,
            graph,
            effects,
            threading_context,
            callable_context,
            proof_cache,
            effect_summary,
            summary_module_name,
            fresh_summary,
            summary_only,
            borrow_methods,
        )

    if summary_only:
        return None

    changed = True
    while changed:
        changed = False
        for caller, callees in graph.items():
            if caller in effects:
                continue
            for callee in callees:
                if callee in effects:
                    effects.add(caller)
                    changed = True
                    break

    function_effect_names: set[str] = set()
    function_effect_ids: set[int] = set()
    for fd in functions:
        if "function:" + fd.name in effects:
            function_effect_names.add(fd.name)
            function_effect_ids.add(id(fd))
    for local_name, target in imported_functions.items():
        if _export_target_is_may_park(native_exports, target):
            function_effect_names.add(local_name)

    method_effect_keys: set[str] = set()
    method_effect_ids: set[int] = set()
    for class_name, fd in method_defs:
        key = class_name + "." + fd.name
        if "method:" + key in effects:
            method_effect_keys.add(key)
            method_effect_ids.add(id(fd))
    return (
        function_effect_ids,
        function_effect_names,
        method_effect_ids,
        method_effect_keys,
    )

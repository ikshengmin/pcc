"""Threading classes acquire conflict sets only after a real disagreement."""

import builtins
from inspect import getsource
import os
from pathlib import Path
import subprocess
import sys
from typing import Optional

import pytest

from pcc.frontends.python.codegen import vthread_effect_analysis as analysis
from pcc.frontends.python.codegen.vthread_effect_analysis import (
    FuncDef,
    Module,
    _class_method_defs,
    _module_function_defs,
    _threading_assignment_hints,
    _threading_import_bindings,
    _threading_module_name_hints,
)
from pcc.frontends.python.py_lift import parse_and_lift


IMPORTS = "from threading import Event, Condition, Semaphore\nimport threading as threads\n"


# Rows retain method order; a nonconsecutive owner starts another class
# definition, as does every row of the explicit redefinition case.
CASES = [
    ("empty", [], {}, []),
    ("first_only", [("C", "self.x = Event()")], {"C": {"x": "Event"}}, []),
    ("only_empty", [("C", "pass")] * 8, {"C": {}}, []),
    ("empty_first", [("C", "pass"), ("C", "self.x = Event()")],
     {"C": {"x": "Event"}}, []),
    ("same_many", [("C", "self.x = Event()")] * 64,
     {"C": {"x": "Event"}}, []),
    ("late_conflict", [("C", "self.x = Event()")] * 64 +
     [("C", "self.x = Condition()"), ("C", "self.x = Event()")],
     {"C": {}}, [{"x"}]),
    ("two_in_one_method", [
        ("C", "self.x = Event()\nself.y = Condition()"),
        ("C", "self.x = Condition()\nself.y = Semaphore()"),
        ("C", "self.x = Event()\nself.y = Condition()"),
    ], {"C": {}}, [{"x", "y"}]),
    ("later_conflicts", [
        ("C", "self.x = Event()\nself.y = Event()\nself.z = Event()"),
        ("C", "self.x = Condition()"), ("C", "self.y = Condition()"),
        ("C", "self.z = Condition()"),
        ("C", "self.x = Event()\nself.y = Event()\nself.z = Event()"),
    ], {"C": {}}, [{"x", "y", "z"}]),
    ("interleaved", [
        ("A", "self.x = Event()"), ("B", "self.x = Event()"),
        ("A", "self.x = Condition()"), ("B", "self.x = Event()"),
        ("A", "self.x = Event()"), ("B", "self.y = Semaphore()"),
    ], {"A": {}, "B": {"x": "Event", "y": "Semaphore"}}, [{"x"}]),
    ("independent", [
        ("A", "self.x = Event()"), ("B", "self.x = Event()"),
        ("A", "self.x = Condition()"), ("B", "self.x = Semaphore()"),
        ("A", "self.x = Event()"), ("B", "self.x = Event()"),
    ], {"A": {}, "B": {}}, [{"x"}, {"x"}]),
    ("redefined", [
        ("C", "self.x = Event()"), ("C", "self.x = Condition()"),
        ("C", "self.x = Event()"),
    ], {"C": {}}, [{"x"}]),
    ("within_method", [("C", "self.x = Event()\nself.y = Semaphore()\n"
                        "self.x = Condition()\nself.x = None\nself.x = unknown()")],
     {"C": {"x": "Condition", "y": "Semaphore"}}, []),
    ("many_classes", [(f"C{owner}", "self.x = Event()")
                      for owner in range(8) for _ in range(4)],
     {f"C{owner}": {"x": "Event"} for owner in range(8)}, []),
    ("unrelated_order", [
        ("C", "self.keep = Event()\nself.conflict = Event()"),
        ("C", "self.late = Semaphore()\nself.conflict = Condition()"),
        ("C", "self.conflict = Event()\nself.other = Event()"),
    ], {"C": {"keep": "Event", "late": "Semaphore", "other": "Event"}},
     [{"conflict"}]),
]


def _module(shape, rows):
    lines = [IMPORTS]
    previous = None
    for index, (owner, body) in enumerate(rows):
        if owner != previous or shape == "redefined":
            lines.append(f"class {owner}:")
        lines.append(f"    def m{index}(self):")
        lines.extend("        " + line for line in body.splitlines())
        previous = owner
    return parse_and_lift("\n".join(lines) + "\n", "conflicts.py", "unit")


def _observe(monkeypatch, function, module, functions, methods, cache, record,
             *, fail_at=0, failure=None):
    original = analysis._threading_assignment_hints
    record.update(events=[], hints=[], sets=[])

    def hints(fd, module_aliases, value_aliases, scope_nodes=None):
        record["events"].append(id(fd))
        if len(record["events"]) == fail_at:
            raise failure
        local, fields = original(fd, module_aliases, value_aliases, scope_nodes)
        record["hints"].append((local, fields))
        return local, fields

    def make_set(*args, **kwargs):
        value = builtins.set(*args, **kwargs)
        record["sets"].append(value)
        return value

    with monkeypatch.context() as patch:
        # The frozen and production contexts call the same real helpers.
        # Count their common module-alias set too, then exclude that exact
        # returned owner rather than guessing from its contents.
        patch.setitem(analysis.__dict__, "set", make_set)
        patch.setitem(function.__globals__, "set", make_set)
        patch.setitem(function.__globals__, "_threading_assignment_hints", hints)
        return function(module, functions, methods, cache)


def _compare(monkeypatch, module, *, cached=False, functions=None, methods=None):
    if functions is None:
        functions = analysis._module_function_defs(module)
    if methods is None:
        methods = analysis._class_method_defs(module)
    cache = None
    if cached:
        cache = {id(fd): (analysis._function_scope_nodes(fd),)
                 for fd in functions + [fd for _owner, fd in methods]}
    before = None if cache is None else {key: tuple(value[0]) for key, value in cache.items()}
    old, new = {}, {}
    expected = _observe(monkeypatch, _legacy_context, module, functions, methods, cache, old)
    actual = _observe(monkeypatch, analysis._threading_analysis_context,
                      module, functions, methods, cache, new)
    assert actual == expected
    assert new["events"] == old["events"] == [id(fd) for fd in functions] + [id(fd) for _owner, fd in methods]
    assert list(actual[2]) == list(expected[2])
    assert list(actual[3]) == list(expected[3])
    for owner in actual[3]:
        assert list(actual[3][owner]) == list(expected[3][owner])
    for context, record in ((expected, old), (actual, new)):
        assert sum(value is context[0] for value in record["sets"]) == 1
        record["conflict_sets"] = [value for value in record["sets"] if value is not context[0]]
        record["context"] = context
    assert len(old["conflict_sets"]) == len(methods)
    if cache is not None:
        assert {key: tuple(value[0]) for key, value in cache.items()} == before
    return actual, old, new


@pytest.mark.parametrize("shape,rows,expected,conflicts", CASES, ids=[case[0] for case in CASES])
@pytest.mark.parametrize("cached", [False, True])
def test_only_conflicted_classes_allocate_sets(monkeypatch, shape, rows, expected, conflicts, cached):
    module = _module(shape, rows)
    context, old, new = _compare(monkeypatch, module, cached=cached)
    assert context[3] == expected
    assert list(context[3]) == list(expected)
    for owner in expected:
        assert list(context[3][owner]) == list(expected[owner])
    assert new["conflict_sets"] == conflicts
    assert len({id(value) for value in new["conflict_sets"]}) == len(conflicts)
    assert all(value for value in new["conflict_sets"])
    assert len({id(value) for value in context[3].values()}) == len(context[3])
    # This observes Python constructor calls, not native allocation totals,
    # GC liveness, peak RSS or elapsed time.


def test_repeated_function_identity_does_not_reuse_another_class_conflict(monkeypatch):
    module = _module("identity", [("C", "self.x = Event()"),
                                  ("C", "self.x = Condition()")])
    first, second = [fd for _owner, fd in analysis._class_method_defs(module)]
    methods = [("A", first), ("B", first), ("A", second), ("B", first), ("A", first)]
    context, old, new = _compare(monkeypatch, module, cached=True,
                                 functions=[first], methods=methods)
    assert context[3] == {"A": {}, "B": {"x": "Event"}}
    assert new["conflict_sets"] == [{"x"}]
    assert context[2][id(first)] is new["hints"][-1][0]
    assert context[3]["A"] is new["hints"][1][1]
    assert context[3]["B"] is new["hints"][2][1]
    assert context[3]["A"] is not context[3]["B"]


@pytest.mark.parametrize("fail_at", [1, 2, 4])
@pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
def test_helper_boundary_failures_keep_call_order_and_exception_identity(monkeypatch, fail_at, error_type):
    module = _module("failure", [("C", "self.x = Event()"),
                                 ("C", "self.x = Condition()"),
                                 ("C", "self.x = Event()"), ("D", "pass")])
    methods = analysis._class_method_defs(module)
    cache = {id(fd): (analysis._function_scope_nodes(fd),) for _owner, fd in methods}
    before = {key: tuple(value[0]) for key, value in cache.items()}
    failure = error_type("threading helper boundary failed")
    records = []
    for function in (_legacy_context, analysis._threading_analysis_context):
        record = {}
        with pytest.raises(error_type) as raised:
            _observe(monkeypatch, function, module, [], methods, cache, record,
                     fail_at=fail_at, failure=failure)
        assert raised.value is failure
        records.append(record)
    assert records[0]["events"] == records[1]["events"] == [id(fd) for _owner, fd in methods[:fail_at]]
    assert {key: tuple(value[0]) for key, value in cache.items()} == before


SOURCE = IMPORTS + """gate = Event()
def top(self):
    local = Event()
    return local.wait()
class Stable:
    def first(self):
        self.state = Event()
    def repeat(self):
        self.state = Event()
    def wait(self):
        return self.state.wait()
    def wrapper(self):
        return self.wait()
class Multi:
    def first(self):
        self.x = Event()
        self.y = Condition()
    def clash(self):
        self.x = Condition()
        self.y = Semaphore()
    def restore(self):
        self.x = Event()
        self.y = Condition()
    def wait(self):
        return self.x.wait()
class Other:
    def first(self):
        self.x = Event()
    def wait(self):
        return self.x.wait()
class Empty:
    def first(self):
        pass
    def second(self):
        self.state = Semaphore()
    def wait(self):
        return self.state.acquire()
class Within:
    def first(self):
        self.state = Event()
        self.state = Condition()
        self.state = None
    def wait(self):
        return self.state.wait()
class Repeated:
    def first(self):
        self.state = Event()
class Repeated:
    def second(self):
        self.state = Condition()
    def restore(self):
        self.state = Event()
    def wait(self):
        return self.state.wait()
"""

METHODS = {
    "Stable": ["first", "repeat", "wait", "wrapper"],
    "Multi": ["first", "clash", "restore", "wait"],
    "Other": ["first", "wait"], "Empty": ["first", "second", "wait"],
    "Within": ["first", "wait"], "Repeated": ["first", "second", "restore", "wait"],
}
EXPORTS = {"unit": {
    "top": {"kind": "function", "owning_module": "unit", "export_name": "top"},
    **{name: {"kind": "class", "owning_module": "unit", "export_name": name,
              "methods": [{"name": method} for method in methods]}
       for name, methods in METHODS.items()},
}}
EXPECTED_FIELDS = {
    "Stable": {"state": "Event"}, "Multi": {}, "Other": {"x": "Event"},
    "Empty": {"state": "Semaphore"}, "Within": {"state": "Condition"}, "Repeated": {},
}
EXPECTED_METHODS = {"Stable.wait", "Stable.wrapper", "Other.wait", "Empty.wait", "Within.wait"}
EXPECTED_SEEDS = ["f:unit:top", "m:unit:Stable:wait", "m:unit:Other:wait",
                  "m:unit:Empty:wait", "m:unit:Within:wait"]


def test_real_summary_and_full_consumers_keep_conflicts_and_field_parking(monkeypatch):
    module = parse_and_lift(SOURCE, "conflicts.py", "unit")
    context, old, new = _compare(monkeypatch, module)
    assert context[3] == EXPECTED_FIELDS and context[4] == {"gate": "Event"}
    assert new["conflict_sets"] == [{"x", "y"}, {"state"}]
    with monkeypatch.context() as patch:
        patch.setattr(analysis, "_threading_analysis_context", _legacy_context)
        old_summary = analysis.build_closed_world_vthread_effect_summary(module, "unit", EXPORTS)
        old_result = analysis.compute_vthread_may_park_callables(module, EXPORTS)
    summary = analysis.build_closed_world_vthread_effect_summary(module, "unit", EXPORTS)
    result = analysis.compute_vthread_may_park_callables(module, EXPORTS)
    assert summary == old_summary and result == old_result
    assert summary["seeds"] == EXPECTED_SEEDS
    assert summary["edges"] == ["m:unit:Stable:wrapper", "m:unit:Stable:wait"]
    assert result[1] == {"top"} and result[3] == EXPECTED_METHODS


NATIVE_BODY = """
def main():
    module = parse_and_lift(SOURCE, 'conflicts.py', 'unit')
    context = _threading_analysis_context(module)
    if context != _legacy_context(module) or context[3] != EXPECTED_FIELDS:
        raise RuntimeError('threading conflict context differs from preimage')
    if context[4] != {'gate': 'Event'}:
        raise RuntimeError('global hint changed')
    methods = _class_method_defs(module)
    # Interleave Multi and Other without changing either class's method order.
    # Entering Other after a Multi conflict must not reuse Multi's set.
    interleaved = []
    for owner, fd in methods:
        if owner != 'Multi' and owner != 'Other':
            interleaved.append((owner, fd))
    for name in ('first', 'clash', 'restore', 'wait'):
        for owner, fd in methods:
            if owner == 'Multi' and fd.name == name:
                interleaved.append((owner, fd))
        for owner, fd in methods:
            if owner == 'Other' and ((name == 'first' and fd.name == 'first') or
                                     (name == 'clash' and fd.name == 'wait')):
                interleaved.append((owner, fd))
    mixed = _threading_analysis_context(module, None, interleaved)
    if mixed != _legacy_context(module, None, interleaved) or mixed[3] != EXPECTED_FIELDS:
        raise RuntimeError('interleaved class conflict state leaked')
    for owner, fd in methods:
        if fd.name == 'wait':
            call = _function_attr_calls(fd)[0]
            receiver = call.func.obj
            if isinstance(receiver.ty, ClassType):
                raise RuntimeError('typed receiver bypasses field hints')
            field = 'x' if owner == 'Multi' or owner == 'Other' else 'state'
            expected_kind = EXPECTED_FIELDS[owner].get(field)
            actual_kind = _threading_receiver_kind(receiver, module, {}, context[3][owner], context[4], 'self')
            if actual_kind != expected_kind:
                raise RuntimeError('field conflict fallback changed')
            if _threading_receiver_kind(receiver, module, {}, {}, context[4], 'self') is not None:
                raise RuntimeError('fieldless receiver unexpectedly proven')
    summary = build_closed_world_vthread_effect_summary(module, 'unit', EXPORTS)
    if summary['seeds'] != EXPECTED_SEEDS or summary['edges'] != ['m:unit:Stable:wrapper', 'm:unit:Stable:wait']:
        raise RuntimeError('summary conflict effects changed')
    result = compute_vthread_may_park_callables(module, EXPORTS)
    if result[1] != {'top'} or result[3] != EXPECTED_METHODS:
        raise RuntimeError('full conflict effects changed')
    write_closed_world_vthread_effect_summary(sys.argv[1], summary)
    print(len(context[3]))
    print('|'.join(sorted(result[1])))
    print('|'.join(sorted(result[3])))
if __name__ == '__main__':
    main()
"""


@pytest.mark.integration
def test_native_lazy_conflicts_match_frozen_context_and_cpython(tmp_path, pcc_runtime_archive, python_program_compiler):
    program = (
        "import sys\n"
        "from typing import Optional\n"
        "from pcc.frontends.python.py_lift import parse_and_lift\n"
        "from pcc.frontends.python.py_ast import ClassType\n"
        "from pcc.frontends.python.codegen.vthread_effect_analysis import (\n"
        "    FuncDef, Module, _class_method_defs, _module_function_defs,\n"
        "    _threading_assignment_hints, _threading_import_bindings, _threading_module_name_hints,\n"
        "    _threading_analysis_context, _threading_receiver_kind, _function_attr_calls,\n"
        "    build_closed_world_vthread_effect_summary, compute_vthread_may_park_callables,\n"
        "    write_closed_world_vthread_effect_summary,\n"
        ")\n"
        + getsource(_legacy_context)
        + f"\nSOURCE = {SOURCE!r}\nEXPORTS = {EXPORTS!r}\n"
        + f"EXPECTED_FIELDS = {EXPECTED_FIELDS!r}\nEXPECTED_METHODS = {EXPECTED_METHODS!r}\nEXPECTED_SEEDS = {EXPECTED_SEEDS!r}\n"
        + NATIVE_BODY
    )
    source, executable = tmp_path / "conflicts_program.py", tmp_path / "conflicts_program"
    source.write_text(program, encoding="utf-8")
    wire = tmp_path / "reference.wire"
    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo) + os.pathsep + env.get("PYTHONPATH", "")
    reference = subprocess.run([sys.executable, str(source), str(wire)], cwd=tmp_path,
                               env=env, capture_output=True, text=True, timeout=30)
    assert reference.returncode == 0, reference.stdout + reference.stderr
    assert reference.stderr == ""
    assert reference.stdout == "6\ntop\n" + "|".join(sorted(EXPECTED_METHODS)) + "\n"
    expected_wire = wire.read_bytes()
    python_program_compiler(str(source), str(executable), backend="self",
                            runtime_archive=str(pcc_runtime_archive), ir_scaffold_mode="on")
    for collector in ("0", "1", "2", "3", "4"):
        wire = tmp_path / f"native_gc{collector}.wire"
        result = subprocess.run([str(executable), str(wire)], cwd=tmp_path,
                                env={**os.environ, "PCC_GC_BACKEND": collector},
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, (collector, result.stdout, result.stderr)
        assert result.stderr == "", collector
        assert result.stdout == reference.stdout, collector
        assert wire.read_bytes() == expected_wire, collector


# Frozen b21016a context; only the function name differs.
def _legacy_context(
    module: Module,
    functions: Optional[list[FuncDef]] = None,
    method_defs: Optional[list[tuple[str, FuncDef]]] = None,
    callable_context=None,
):
    """Precompute module-wide threading receiver evidence once.

    The old per-callable path rescanned every module import/global and every
    method in the current class for each function.  Large compiler mixins then
    paid a quadratic AST walk before emitting any IR.  Keep exactly the same
    conservative evidence, but index it by the exact ``FuncDef`` and class.
    """

    if functions is None:
        functions = _module_function_defs(module)
    if method_defs is None:
        method_defs = _class_method_defs(module)
    module_aliases, value_aliases = _threading_import_bindings(module)
    global_hints = _threading_module_name_hints(
        module,
        module_aliases,
        value_aliases,
    )
    local_hints_by_id: dict[int, dict[str, str]] = {}
    field_hints_by_class: dict[str, dict[str, str]] = {}
    field_conflicts_by_class: dict[str, set[str]] = {}

    for fd in functions:
        scope_nodes = None
        if callable_context is not None:
            scope_nodes = callable_context[id(fd)][0]
        local_hints, _fields = _threading_assignment_hints(
            fd,
            module_aliases,
            value_aliases,
            scope_nodes,
        )
        local_hints_by_id[id(fd)] = local_hints

    for class_name, fd in method_defs:
        scope_nodes = None
        if callable_context is not None:
            scope_nodes = callable_context[id(fd)][0]
        local_hints, fields = _threading_assignment_hints(
            fd,
            module_aliases,
            value_aliases,
            scope_nodes,
        )
        local_hints_by_id[id(fd)] = local_hints
        # The helper returns fresh, independent locals/fields dictionaries.
        # Give the first fields dictionary to its class instead of copying
        # it; later methods still use the same sticky-conflict merge below.
        class_hints = field_hints_by_class.setdefault(class_name, fields)
        conflicts = field_conflicts_by_class.setdefault(class_name, set())
        if class_hints is fields:
            continue
        for name, kind in fields.items():
            if name in conflicts:
                continue
            prior = class_hints.get(name)
            if prior is None or prior == kind:
                class_hints[name] = kind
            else:
                class_hints.pop(name, None)
                conflicts.add(name)

    return (
        module_aliases,
        value_aliases,
        local_hints_by_id,
        field_hints_by_class,
        global_hints,
    )

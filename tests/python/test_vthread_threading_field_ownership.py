"""Fresh method field dictionaries can become their class's context owner."""

from inspect import getsource
import os
from pathlib import Path
import subprocess
import sys
from typing import Optional

import pytest

from pcc.frontends.python.codegen import vthread_effect_analysis as analysis
from pcc.frontends.python.codegen.vthread_effect_analysis import (
    FuncDef, Module, _class_method_defs, _module_function_defs,
    _threading_assignment_hints, _threading_import_bindings,
    _threading_module_name_hints,
)
from pcc.frontends.python.py_lift import parse_and_lift


IMPORTS = "from threading import Event, Condition, Semaphore, Lock\nimport threading as threads\n"


def _module(source):
    return parse_and_lift(IMPORTS + source, "fields.py", "unit")


def _repeat_source(method_count, class_count):
    rows = []
    for owner in range(class_count):
        rows.append(f"class C{owner}:")
        if not method_count:
            rows.append("    pass")
        for index in range(method_count):
            rows.extend([
                f"    def m{index}(self):", "        local = Condition()",
                "        self.ready = Event()", "        self.changed = threads.Condition()",
                "        self.ready = Event()",
            ])
    return "\n".join(rows) + "\n"


def _observe(monkeypatch, function, module, functions, methods, cache, record,
             *, fail_at=0, failure=None):
    original = analysis._threading_assignment_hints
    record.update(events=[], results=[])

    def hints(fd, module_aliases, value_aliases, scope_nodes=None):
        record["events"].append(id(fd))
        if len(record["events"]) == fail_at:
            raise failure
        local, fields = original(fd, module_aliases, value_aliases, scope_nodes)
        # Retain real helper results, not substitute shared or borrowed maps.
        record["results"].append((fd, local, fields, dict(fields)))
        return local, fields

    with monkeypatch.context() as patch:
        patch.setitem(function.__globals__, "_threading_assignment_hints", hints)
        return function(module, functions, methods, cache)


def _compare(monkeypatch, module, functions=None, methods=None, cached=False):
    if functions is None:
        functions = analysis._module_function_defs(module)
    if methods is None:
        methods = analysis._class_method_defs(module)
    cache = None
    if cached:
        cache = {id(fd): (analysis._function_scope_nodes(fd),)
                 for fd in functions + [fd for _owner, fd in methods]}
    cache_before = None if cache is None else {key: tuple(value[0]) for key, value in cache.items()}
    old, new = {}, {}
    expected = _observe(monkeypatch, _legacy_context, module, functions, methods, cache, old)
    actual = _observe(monkeypatch, analysis._threading_analysis_context,
                      module, functions, methods, cache, new)
    old["context"], new["context"] = expected, actual
    assert actual == expected
    assert new["events"] == old["events"] == [id(fd) for fd in functions] + [id(fd) for _owner, fd in methods]
    assert list(actual[2]) == list(expected[2])
    assert list(actual[3]) == list(expected[3])
    for owner in actual[3]:
        assert list(actual[3][owner]) == list(expected[3][owner])
    if cache is not None:
        assert {key: tuple(value[0]) for key, value in cache.items()} == cache_before
    return actual, old, new


@pytest.mark.parametrize("empty", [False, True])
def test_assignment_helper_really_returns_fresh_independent_maps(empty):
    module = _module(_repeat_source(1, 1) if not empty else "class C0:\n    def m0(self):\n        pass\n")
    fd = analysis._class_method_defs(module)[0][1]
    aliases, values = analysis._threading_import_bindings(module)
    nodes = analysis._function_scope_nodes(fd)
    before = tuple(nodes)
    first = analysis._threading_assignment_hints(fd, aliases, values, nodes)
    second = analysis._threading_assignment_hints(fd, aliases, values, nodes)
    assert first == second
    owners = [first[0], first[1], second[0], second[1]]
    assert len({id(value) for value in owners}) == 4
    first[1]["probe"] = "Event"
    assert all("probe" not in value for value in (first[0], second[0], second[1]))
    assert tuple(nodes) == before


@pytest.mark.parametrize("method_count,class_count", [(0, 0), (0, 3), (1, 1), (16, 1), (64, 1), (4, 8)])
@pytest.mark.parametrize("cached", [False, True])
def test_first_method_fields_become_class_owner_without_an_extra_copy(
    monkeypatch, method_count, class_count, cached,
):
    module = _module(_repeat_source(method_count, class_count))
    context, old, new = _compare(monkeypatch, module, cached=cached)
    assert len(new["results"]) == method_count * class_count
    assert len(context[3]) == (class_count if method_count else 0)
    fields = [row[2] for row in new["results"]]
    assert len({id(value) for value in fields}) == len(fields)
    for owner in range(class_count if method_count else 0):
        index = owner * method_count
        class_fields = context[3][f"C{owner}"]
        assert class_fields is new["results"][index][2]
        assert class_fields == {"ready": "Event", "changed": "Condition"}
        assert all(class_fields is not row[1] for row in new["results"])
        assert old["context"][3][f"C{owner}"] is not old["results"][index][2]
    # Identity/owner checks are not native allocation or process-RSS measures.
    assert len({id(value) for value in context[3].values()}) == len(context[3])


def test_repeated_function_identity_does_not_share_fields_across_classes(monkeypatch):
    module = _module(_repeat_source(1, 1))
    fd = analysis._class_method_defs(module)[0][1]
    context, old, new = _compare(
        monkeypatch, module, functions=[fd],
        methods=[("A", fd), ("B", fd), ("A", fd)], cached=True,
    )
    assert len(new["results"]) == 4
    assert context[2][id(fd)] is new["results"][-1][1]
    assert context[3]["A"] is new["results"][1][2]
    assert context[3]["B"] is new["results"][2][2]
    assert context[3]["A"] is not context[3]["B"]
    assert all(context[3][owner] is not row[1]
               for owner in ("A", "B") for row in new["results"])
    context[3]["A"]["probe"] = "Event"
    assert "probe" not in context[3]["B"] and "probe" not in context[2][id(fd)]


def _history_source(shape):
    methods = {
        "same": ["self.x = Event()", "self.x = Event()", "self.x = Event()"],
        "sticky": ["self.x = Event()", "self.x = Condition()", "self.x = Event()"],
        "empty_first": ["pass", "self.x = Event()"],
        "last_in_method": ["self.x = Event()\nself.y = Semaphore()\nself.x = Condition()\nself.x = None\nself.x = unknown()"],
        "unrelated_order": ["self.keep = Event()\nself.conflict = Event()",
                            "self.late = Semaphore()\nself.conflict = Condition()",
                            "self.conflict = Event()\nself.other = Event()"],
        "redefined": ["self.x = Event()", "self.x = Condition()", "self.x = Event()"],
    }[shape]
    rows = [] if shape == "redefined" else ["class C:"]
    for index, body in enumerate(methods):
        if shape == "redefined":
            rows.append("class C:")
        rows.append(f"    def m{index}(self):")
        rows.extend("        " + line for line in body.splitlines())
    return "\n".join(rows) + "\n"


@pytest.mark.parametrize("shape", ["same", "sticky", "empty_first", "last_in_method", "unrelated_order", "redefined"])
def test_owned_first_fields_keep_assignment_order_and_sticky_conflicts(monkeypatch, shape):
    module = _module(_history_source(shape))
    context, old, new = _compare(monkeypatch, module)
    expected = {
        "same": {"x": "Event"}, "sticky": {}, "empty_first": {"x": "Event"},
        "last_in_method": {"x": "Condition", "y": "Semaphore"},
        "unrelated_order": {"keep": "Event", "late": "Semaphore", "other": "Event"},
        "redefined": {},
    }[shape]
    assert context[3] == {"C": expected}
    assert list(context[3]["C"]) == list(expected)
    assert context[3]["C"] is new["results"][0][2]
    if shape == "empty_first":
        assert new["results"][0][3] == {}
        assert context[3]["C"] is not new["results"][1][2]


@pytest.mark.parametrize("fail_at", [1, 2, 4])
@pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
def test_assignment_hint_failure_keeps_call_order_and_exception_identity(monkeypatch, fail_at, error_type):
    module = _module(_repeat_source(4, 1))
    methods = analysis._class_method_defs(module)
    cache = {id(fd): (analysis._function_scope_nodes(fd),) for _owner, fd in methods}
    before = {key: tuple(value[0]) for key, value in cache.items()}
    failure = error_type("threading hint scan failed")
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
    self.outside = Condition()
    return local.wait()
class First:
    def __init__(self):
        local = Semaphore()
        self.ready = Event()
        self.changed = threads.Condition()
        self.ready = Event()
    def repeat(self):
        self.ready = Event()
    def wait(self):
        return self.ready.wait()
    def changed_wait(self):
        return self.changed.wait()
    def wrapper(self):
        return self.wait()
class Conflicted:
    def first(self):
        self.state = Event()
    def second(self):
        self.state = Condition()
    def restore(self):
        self.state = Event()
    def wait(self):
        return self.state.wait()
class EmptyFirst:
    def first(self):
        pass
    def seed(self):
        self.state = Semaphore()
    def wait(self):
        return self.state.acquire()
class LastWithinMethod:
    def first(self):
        self.state = Event()
        self.state = Condition()
        self.state = None
    def wait(self):
        return self.state.wait()
class Negative:
    def first(self):
        self.lock = Lock()
        self.unknown = other()
    def wait(self):
        return self.lock.acquire()
"""

METHODS = {
    "First": ["__init__", "repeat", "wait", "changed_wait", "wrapper"],
    "Conflicted": ["first", "second", "restore", "wait"],
    "EmptyFirst": ["first", "seed", "wait"],
    "LastWithinMethod": ["first", "wait"], "Negative": ["first", "wait"],
}
EXPORTS = {"unit": {
    "top": {"kind": "function", "owning_module": "unit", "export_name": "top"},
    **{name: {"kind": "class", "owning_module": "unit", "export_name": name,
              "methods": [{"name": method} for method in methods]}
       for name, methods in METHODS.items()},
}}
EXPECTED_FIELDS = {
    "First": {"ready": "Event", "changed": "Condition"}, "Conflicted": {},
    "EmptyFirst": {"state": "Semaphore"}, "LastWithinMethod": {"state": "Condition"},
    "Negative": {},
}
EXPECTED_METHODS = {"First.wait", "First.changed_wait", "First.wrapper", "EmptyFirst.wait", "LastWithinMethod.wait"}
EXPECTED_SEEDS = ["f:unit:top", "m:unit:First:wait", "m:unit:First:changed_wait",
                  "m:unit:EmptyFirst:wait", "m:unit:LastWithinMethod:wait"]


def test_real_summary_and_full_consumers_keep_field_based_parking(monkeypatch):
    module = parse_and_lift(SOURCE, "fields.py", "unit")
    context, old, new = _compare(monkeypatch, module)
    assert context[3] == EXPECTED_FIELDS and context[4] == {"gate": "Event"}
    with monkeypatch.context() as patch:
        patch.setattr(analysis, "_threading_analysis_context", _legacy_context)
        old_summary = analysis.build_closed_world_vthread_effect_summary(module, "unit", EXPORTS)
        old_result = analysis.compute_vthread_may_park_callables(module, EXPORTS)
    summary = analysis.build_closed_world_vthread_effect_summary(module, "unit", EXPORTS)
    result = analysis.compute_vthread_may_park_callables(module, EXPORTS)
    assert summary == old_summary and result == old_result
    assert summary["seeds"] == EXPECTED_SEEDS
    assert summary["edges"] == ["m:unit:First:wrapper", "m:unit:First:wait"]
    assert result[1] == {"top"} and result[3] == EXPECTED_METHODS


@pytest.mark.integration
def test_native_owned_fields_match_frozen_context_and_cpython(tmp_path, pcc_runtime_archive, python_program_compiler):
    """Exercise field-hint fallback, not a pre-inferred ClassType shortcut."""
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
        + "def main():\n"
        + "    module = parse_and_lift(SOURCE, 'fields.py', 'unit')\n"
        + "    context = _threading_analysis_context(module)\n"
        + "    if context != _legacy_context(module) or context[3] != EXPECTED_FIELDS:\n"
        + "        raise RuntimeError('threading context differs from preimage')\n"
        + "    if context[4] != {'gate': 'Event'}:\n"
        + "        raise RuntimeError('global hint changed')\n"
        + "    for owner, fd in _class_method_defs(module):\n"
        + "        if fd.name == 'wait':\n"
        + "            call = _function_attr_calls(fd)[0]\n"
        + "            receiver = call.func.obj\n"
        + "            if isinstance(receiver.ty, ClassType):\n"
        + "                raise RuntimeError('typed receiver bypasses field hints')\n"
        + "            expected_kind = EXPECTED_FIELDS[owner].get('ready' if owner == 'First' else 'state')\n"
        + "            actual_kind = _threading_receiver_kind(receiver, module, {}, context[3][owner], context[4], 'self')\n"
        + "            if actual_kind != expected_kind:\n"
        + "                raise RuntimeError('field hint fallback changed')\n"
        + "            if _threading_receiver_kind(receiver, module, {}, {}, context[4], 'self') is not None:\n"
        + "                raise RuntimeError('fieldless receiver unexpectedly proven')\n"
        + "    summary = build_closed_world_vthread_effect_summary(module, 'unit', EXPORTS)\n"
        + "    if summary['seeds'] != EXPECTED_SEEDS or summary['edges'] != ['m:unit:First:wrapper', 'm:unit:First:wait']:\n"
        + "        raise RuntimeError('summary field effects changed')\n"
        + "    result = compute_vthread_may_park_callables(module, EXPORTS)\n"
        + "    if result[1] != {'top'} or result[3] != EXPECTED_METHODS:\n"
        + "        raise RuntimeError('full field effects changed')\n"
        + "    write_closed_world_vthread_effect_summary(sys.argv[1], summary)\n"
        + "    print(len(context[3]))\n"
        + "    print('|'.join(sorted(result[1])))\n"
        + "    print('|'.join(sorted(result[3])))\n"
        + "if __name__ == '__main__':\n"
        + "    main()\n"
    )
    source, executable = tmp_path / "fields_program.py", tmp_path / "fields_program"
    source.write_text(program, encoding="utf-8")
    wire = tmp_path / "reference.wire"
    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo) + os.pathsep + env.get("PYTHONPATH", "")
    reference = subprocess.run([sys.executable, str(source), str(wire)], cwd=tmp_path,
                               env=env, capture_output=True, text=True, timeout=30)
    assert reference.returncode == 0, reference.stdout + reference.stderr
    assert reference.stderr == ""
    assert reference.stdout == "5\ntop\n" + "|".join(sorted(EXPECTED_METHODS)) + "\n"
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


# Frozen 62efda6 context; only the function name differs.
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
        class_hints = field_hints_by_class.setdefault(class_name, {})
        conflicts = field_conflicts_by_class.setdefault(class_name, set())
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

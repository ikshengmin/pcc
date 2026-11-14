"""Owned method tables need no second class-name container for membership."""

import builtins
from copy import deepcopy
from inspect import getsource
import os
from pathlib import Path
import subprocess
import sys
from typing import Optional

import pytest

from pcc.frontends.python.codegen import vthread_effect_analysis as analysis
from pcc.frontends.python.codegen.vthread_effect_analysis import (
    Call,
    ClassType,
    FuncDef,
    Module,
    Name,
    _annotation_local_class_name,
    _class_method_defs,
    _class_method_tables,
    _function_attr_calls,
    _function_local_class_hints,
    _function_scope_nodes,
    _function_vthread_bindings,
    _module_function_defs,
    _receiver_class_name,
    _resolve_local_method_key,
    _threading_analysis_context,
    _vthread_import_aliases,
)
from pcc.frontends.python.py_ast import Attr, DynType
from pcc.frontends.python.py_lift import parse_and_lift


SOURCE = """import pcc.virtual_thread as vt
class Base:
    def park(self):
        vt.yield_now()
    def other(self):
        return 7
class Leaf(Base):
    pass
class Empty:
    pass
class Worker:
    def outer(self, resource):
        return self.middle(resource)
    def middle(self, resource):
        return self.bad(resource)
    def bad(self, resource):
        return resource.park()
    def safe(self):
        return Leaf().park()
def top(resource):
    return Worker().outer(resource)
def good():
    obj = Leaf()
    return obj.park()
def hints(arg: Base):
    local = Leaf()
    empty = Empty()
    unknown = Missing()
    multi = extra = Base()
    repeated = Base()
    repeated = Leaf()
"""
EXPORTS = {"unit": {
    "top": {"kind": "function"}, "good": {"kind": "function"},
    "hints": {"kind": "function"},
    "Base": {"kind": "class", "methods": [{"name": "park"}, {"name": "other"}]},
    "Leaf": {"kind": "class", "methods": []},
    "Empty": {"kind": "class", "methods": []},
    "Worker": {"kind": "class", "methods": [
        {"name": "outer"}, {"name": "middle"}, {"name": "bad"}, {"name": "safe"},
    ]},
}}
EXPECTED_RECEIVERS = ["Leaf", "Leaf", "Leaf", "Empty", "Leaf", None, None,
                      "Base", "Base", "Base", "Leaf", None]
EXPECTED_HINTS = {"arg": "Base", "local": "Leaf", "empty": "Empty", "repeated": "Leaf"}
EXPECTED_METHODS = {"Base.park", "Worker.safe"}
EXPECTED_REJECTIONS = ["Worker.bad", "Worker.middle", "Worker.outer", "top"]
EXPECTED_EDGES = ["f:unit:top", "m:unit:Worker:outer", "f:unit:good", "m:unit:Base:park",
                  "m:unit:Worker:outer", "m:unit:Worker:middle",
                  "m:unit:Worker:middle", "m:unit:Worker:bad",
                  "m:unit:Worker:safe", "m:unit:Base:park"]


def _module(source=SOURCE):
    return parse_and_lift(source, "registry.py", "unit")


def _registry_results(registry, fd):
    span = fd.span
    dynamic = DynType("dyn")
    receivers = [
        (Name(span, dynamic, "self"), "Leaf", None),
        (Name(span, dynamic, "cls"), "Leaf", None),
        (Name(span, dynamic, "Leaf"), None, None),
        (Name(span, dynamic, "Empty"), None, None),
        (Name(span, dynamic, "alias"), None, {"alias": "Leaf"}),
        (Name(span, dynamic, "missing"), None, {}),
        (Name(span, dynamic, "stale"), None, {"stale": "Missing"}),
        (Name(span, ClassType("Base", "unit"), "typed"), None, None),
        (Name(span, ClassType("unit.Base", ""), "qualified"), None, None),
        (Attr(span, ClassType("unit.Base", ""), Name(span, dynamic, "obj"), "field"), None, None),
        (Call(span, dynamic, Name(span, dynamic, "Leaf"), ()), None, None),
        (Call(span, dynamic, Name(span, dynamic, "Missing"), ()), None, None),
    ]
    results = []
    for receiver, owner, hints in receivers:
        results.append(_receiver_class_name(receiver, owner, registry, hints))
    annotations = []
    for annotation in (ClassType("Base", "unit"), ClassType("unit.Base", ""),
                       ClassType("Missing", ""), dynamic, None):
        annotations.append(_annotation_local_class_name(annotation, registry))
    hints = _function_local_class_hints(fd, registry, _function_scope_nodes(fd))
    return results, annotations, hints


@pytest.mark.parametrize("kind", ["set", "dict"])
@pytest.mark.parametrize("empty", [False, True])
def test_union_registry_helpers_keep_receiver_and_hint_rules(kind, empty):
    module = _module()
    methods, _bases = analysis._class_method_tables(module)
    fd = analysis._module_function_defs(module)[2]
    registry = {} if empty else methods
    if kind == "set":
        registry = set(registry)
    receivers, annotations, hints = _registry_results(registry, fd)
    assert receivers == (["Leaf", "Leaf"] + [None] * 10 if empty else EXPECTED_RECEIVERS)
    assert annotations == ([""] * 5 if empty else ["Base", "Base", "", "", ""])
    assert hints == ({} if empty else EXPECTED_HINTS)
    assert analysis._function_local_class_hints(None, registry) == {}


def _resolver_case(shape):
    module = _module()
    park = analysis._class_method_tables(module)[0]["Base"]["park"]
    other = analysis._class_method_tables(module)[0]["Base"]["other"]
    owner, names = "Leaf", ["park", "park"]
    if shape == "empty":
        methods, bases, expected = {}, {}, []
    elif shape == "inherited":
        methods, bases = {"Leaf": {}, "Base": {"park": park}}, {"Leaf": ("Base",)}
        expected = ["Base.park"]
    elif shape == "own":
        methods = {"Leaf": {"park": park}, "Base": {"park": park}}
        bases, expected = {"Leaf": ("Base",)}, ["Leaf.park"]
    elif shape == "diamond":
        methods = {"Leaf": {}, "Left": {}, "Right": {"park": park}, "Base": {"park": park}}
        bases = {"Leaf": ("Left", "Right"), "Left": ("Base",), "Right": ("Base",)}
        expected = ["Right.park"]
    elif shape == "cycle":
        owner, methods, bases, expected = "A", {"A": {}, "B": {}}, {"A": ("B",), "B": ("A",)}, []
    elif shape == "chain":
        owner = "C0"
        methods = {f"C{i}": {} for i in range(32)}
        methods["C31"]["park"] = park
        bases = {f"C{i}": (f"C{i + 1}",) for i in range(31)}
        expected = ["C31.park"]
    elif shape == "repeated_bases":
        methods, bases = {"Leaf": {}, "Base": {"park": park}}, {"Leaf": ("Base", "Base")}
        expected = ["Base.park"]
    else:
        assert shape == "ordered_duplicates"
        methods, bases = {"Leaf": {}, "Base": {"park": park, "other": other}}, {"Leaf": ("Base",)}
        names, expected = ["park", "other", "park", "other"], ["Base.park", "Base.other"]
    caller = _module("def caller():\n" + "".join(f"    {owner}().{name}()\n" for name in names))
    fd = analysis._module_function_defs(caller)[0]
    return fd, methods, bases, expected


@pytest.mark.parametrize("shape", ["empty", "inherited", "own", "diamond", "cycle", "chain", "repeated_bases", "ordered_duplicates"])
@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("borrow", [False, True])
def test_borrowing_keeps_resolution_order_without_copying_class_keys(monkeypatch, shape, cached, borrow):
    fd, methods, bases, expected = _resolver_case(shape)
    options = {"attribute_calls": analysis._function_attr_calls(fd), "local_hints": {}} if cached else {}
    before = [(key, id(value), tuple(value.items())) for key, value in methods.items()]
    copies, registries = [], []
    receiver = analysis._receiver_class_name

    def make_set(*args):
        value = builtins.set(*args)
        if args and args[0] is methods:
            copies.append(value)
        return value

    def resolve(value, owner, registry, hints=None):
        registries.append(registry)
        return receiver(value, owner, registry, hints)

    with monkeypatch.context() as patch:
        patch.setitem(analysis.__dict__, "set", make_set)
        patch.setattr(analysis, "_receiver_class_name", resolve)
        actual = analysis._local_method_call_keys(fd, None, methods, bases,
                                                   borrow_methods=borrow, **options)
    assert actual == _legacy_method_keys(fd, None, methods, bases, **options) == expected
    assert len(copies) == (0 if borrow else 1)
    assert registries
    if borrow:
        assert all(value is methods for value in registries)
    else:
        assert all(isinstance(value, set) for value in registries)
    assert [(key, id(value), tuple(value.items())) for key, value in methods.items()] == before


@pytest.mark.parametrize("api", ["public", "private"])
@pytest.mark.parametrize("shape", ["contains", "mutation"])
def test_public_and_default_paths_keep_supplied_mapping_snapshots(api, shape):
    module = _module("class C:\n    def park(self):\n        pass\ndef caller():\n    C.park()\n")
    fd = analysis._module_function_defs(module)[0]
    call = analysis._function_attr_calls(fd)[0]
    plain, bases = analysis._class_method_tables(module)

    class ContainsTrap(dict):
        def __contains__(self, key):
            raise AssertionError("public/default class-name membership used a live mapping")

    methods = ContainsTrap(plain) if shape == "contains" else dict(plain)
    hints = {}
    if shape == "mutation":
        receiver = Name(call.span, DynType("dyn"), "alias")
        call = Call(call.span, DynType("dyn"), Attr(call.span, DynType("dyn"), receiver, "park"), ())

        class MutatingHints(dict):
            def get(self, key, default=None):
                methods["Late"] = plain["C"]
                return "Late"

        hints = MutatingHints()
    if api == "public":
        result = analysis.vthread_proven_method_call_key(
            call, None, None, module, fd, methods=methods, bases=bases, local_hints=hints,
        )
        assert result == ("C.park" if shape == "contains" else None)
    else:
        result = analysis._local_method_call_keys(fd, None, methods, bases,
                                                  attribute_calls=[call], local_hints=hints)
        assert result == (["C.park"] if shape == "contains" else [])
    if shape == "mutation":
        assert "Late" in methods


@pytest.mark.parametrize("subclass", [False, True])
@pytest.mark.parametrize("summary_only", [False, True])
def test_supplied_contexts_keep_snapshots_and_can_be_reused(monkeypatch, subclass, summary_only):
    module = _module("import pcc.virtual_thread as vt\nclass C:\n    def park(self):\n        vt.yield_now()\ndef entry():\n    return C().park()\n")
    context = list(analysis._local_vthread_context(module))
    before = context[3]

    class ContainsTrap(dict):
        def __contains__(self, key):
            raise AssertionError("supplied context lost its snapshot")

    if subclass:
        context[3] = ContainsTrap(context[3])
    original = analysis._local_method_call_keys
    modes = []

    def resolve(*args, **kwargs):
        modes.append(kwargs.get("borrow_methods", False))
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(analysis, "_local_method_call_keys", resolve)
        first = analysis._compute_local_vthread_effects(module, None, local_context=context, summary_only=summary_only)
        second = analysis._compute_local_vthread_effects(module, None, local_context=context, summary_only=summary_only)
    assert first == second
    assert modes == [False] * 4
    assert dict(context[3]) == before
    if summary_only:
        assert first is None
    else:
        assert first[1] == {"entry"} and first[3] == {"C.park"}


def _consumers(module):
    exports = deepcopy(EXPORTS)
    summary = analysis.build_closed_world_vthread_effect_summary(module, "unit", exports)
    result = analysis.compute_vthread_may_park_callables(module, exports)
    rejected = analysis.classify_vthread_park_boundaries(module, result[1], exports, result[3])
    analysis.annotate_closed_world_vthread_effects([module], ["unit"], exports)
    return summary, result, rejected, exports


def test_real_consumers_match_snapshot_oracle_and_reject_in_multiple_rounds(monkeypatch):
    module = _module()

    def snapshot(fd, current_class, methods, bases, *, attribute_calls=None,
                 local_hints=None, borrow_methods=False):
        return _legacy_method_keys(fd, current_class, methods, bases,
                                   attribute_calls=attribute_calls, local_hints=local_hints)

    with monkeypatch.context() as patch:
        patch.setattr(analysis, "_local_method_call_keys", snapshot)
        patch.setattr(analysis, "_local_vthread_context", _legacy_context)
        expected = _consumers(module)
    actual = _consumers(module)
    assert actual == expected
    summary, result, rejected, exports = actual
    assert list(rejected) == list(expected[2]) == EXPECTED_REJECTIONS
    assert result[1] == {"good"} and result[3] == EXPECTED_METHODS
    assert summary["seeds"] == ["m:unit:Base:park"]
    assert summary["edges"] == EXPECTED_EDGES
    assert exports["unit"]["good"]["may_park"] is True
    assert exports["unit"]["top"]["may_park"] is False


def test_new_contexts_and_classifier_pass_the_exact_owned_registry(monkeypatch):
    module = _module()
    tables, calls, copies, hint_registries = [], [], [], []
    original_tables = analysis._class_method_tables
    original_keys = analysis._local_method_call_keys
    original_hints = analysis._function_local_class_hints

    def get_tables(value):
        result = original_tables(value)
        tables.append(result[0])
        return result

    def keys(fd, owner, methods, bases, **kwargs):
        calls.append((methods, kwargs.get("borrow_methods", False)))
        return original_keys(fd, owner, methods, bases, **kwargs)

    def hints(fd, registry, scope_nodes=None):
        hint_registries.append(registry)
        return original_hints(fd, registry, scope_nodes)

    def make_set(*args):
        value = builtins.set(*args)
        if args and any(args[0] is table for table in tables):
            copies.append(args[0])
        return value

    with monkeypatch.context() as patch:
        patch.setattr(analysis, "_class_method_tables", get_tables)
        patch.setattr(analysis, "_local_method_call_keys", keys)
        patch.setattr(analysis, "_function_local_class_hints", hints)
        patch.setitem(analysis.__dict__, "set", make_set)
        summary = analysis.build_closed_world_vthread_effect_summary(module, "unit", deepcopy(EXPORTS))
        assert len(tables) == 1 and not copies
        assert len(calls) == 9 and all(borrow and table is tables[0] for table, borrow in calls)
        assert all(registry is tables[0] for registry in hint_registries)
        calls.clear()
        hint_registries.clear()
        rejected = analysis.classify_vthread_park_boundaries(module, {"good"}, deepcopy(EXPORTS), EXPECTED_METHODS)
    assert summary["seeds"] == ["m:unit:Base:park"]
    assert list(rejected) == EXPECTED_REJECTIONS
    assert calls and all(borrow and table is tables[-1] for table, borrow in calls)
    assert len(hint_registries) == 9
    assert all(registry is tables[-1] for registry in hint_registries)
    # Public proof calls inside classification intentionally still snapshot.
    # Constructor-call observations are not native allocation/RSS measurements.


NATIVE_BODY = """
def main():
    module = parse_and_lift(SOURCE, 'registry.py', 'unit')
    context = _local_vthread_context(module)
    methods, bases = context[3], context[4]
    fd = _module_function_defs(module)[2]
    for registry in (set(methods), methods):
        receivers, annotations, hints = _registry_results(registry, fd)
        if receivers != EXPECTED_RECEIVERS or annotations != ['Base', 'Base', '', '', ''] or hints != EXPECTED_HINTS:
            raise RuntimeError('union class registry changed helper results')
    for registry in (set(), {}):
        receivers, annotations, hints = _registry_results(registry, fd)
        if receivers != ['Leaf', 'Leaf'] + [None] * 10 or annotations != [''] * 5 or hints != {}:
            raise RuntimeError('empty union registry changed helper results')
    for owner, fd in [(None, value) for value in context[2]] + context[5]:
        calls, hints = context[6][id(fd)][2], context[6][id(fd)][3]
        expected = _legacy_method_keys(fd, owner, methods, bases, attribute_calls=calls, local_hints=hints)
        borrowed = _local_method_call_keys(fd, owner, methods, bases, attribute_calls=calls, local_hints=hints, borrow_methods=True)
        snapshot = _local_method_call_keys(fd, owner, methods, bases, attribute_calls=calls, local_hints=hints)
        if borrowed != expected or snapshot != expected:
            raise RuntimeError('method edge order changed')
    result = compute_vthread_may_park_callables(module, EXPORTS)
    if result[1] != {'good'} or result[3] != EXPECTED_METHODS:
        raise RuntimeError('full method effects changed')
    if _compute_local_vthread_effects(module, EXPORTS, local_context=context) != result:
        raise RuntimeError('supplied context differs')
    if _compute_local_vthread_effects(module, EXPORTS, local_context=context) != result:
        raise RuntimeError('context reuse differs')
    rejected = classify_vthread_park_boundaries(module, result[1], EXPORTS, result[3])
    if list(rejected) != EXPECTED_REJECTIONS:
        raise RuntimeError('multi-round rejection order changed')
    summary = build_closed_world_vthread_effect_summary(module, 'unit', EXPORTS)
    if summary['seeds'] != ['m:unit:Base:park'] or summary['edges'] != EXPECTED_EDGES:
        raise RuntimeError('summary seeds or edge order changed')
    annotate_closed_world_vthread_effects([module], ['unit'], EXPORTS)
    if not EXPORTS['unit']['good']['may_park'] or EXPORTS['unit']['top']['may_park']:
        raise RuntimeError('function publication changed')
    for owner in ('Base', 'Leaf', 'Empty', 'Worker'):
        for info in EXPORTS['unit'][owner]['methods']:
            if info['may_park'] != (owner + '.' + info['name'] in EXPECTED_METHODS):
                raise RuntimeError('method publication changed')
    write_closed_world_vthread_effect_summary(sys.argv[1], summary)
    print('|'.join(sorted(result[1])))
    print('|'.join(sorted(result[3])))
    print('|'.join(rejected))
if __name__ == '__main__':
    main()
"""


@pytest.mark.integration
def test_native_registry_union_and_analyzers_match_same_source_cpython(tmp_path, pcc_runtime_archive, python_program_compiler):
    program = (
        "import sys\nfrom typing import Optional\n"
        "from pcc.frontends.python.py_lift import parse_and_lift\n"
        "from pcc.frontends.python.py_ast import Attr, Call, ClassType, DynType, FuncDef, Module, Name\n"
        "from pcc.frontends.python.codegen.vthread_effect_analysis import (\n"
        "    _annotation_local_class_name, _function_attr_calls, _function_local_class_hints,\n"
        "    _function_scope_nodes, _local_method_call_keys, _local_vthread_context,\n"
        "    _module_function_defs, _receiver_class_name, _resolve_local_method_key,\n"
        "    _compute_local_vthread_effects, annotate_closed_world_vthread_effects,\n"
        "    build_closed_world_vthread_effect_summary, classify_vthread_park_boundaries,\n"
        "    compute_vthread_may_park_callables, write_closed_world_vthread_effect_summary,\n"
        ")\n"
        + getsource(_legacy_method_keys) + "\n" + getsource(_registry_results)
        + f"\nSOURCE = {SOURCE!r}\nEXPORTS = {EXPORTS!r}\n"
        + f"EXPECTED_RECEIVERS = {EXPECTED_RECEIVERS!r}\nEXPECTED_HINTS = {EXPECTED_HINTS!r}\n"
        + f"EXPECTED_METHODS = {EXPECTED_METHODS!r}\nEXPECTED_REJECTIONS = {EXPECTED_REJECTIONS!r}\n"
        + f"EXPECTED_EDGES = {EXPECTED_EDGES!r}\n"
        + NATIVE_BODY
    )
    source, executable = tmp_path / "registry_program.py", tmp_path / "registry_program"
    source.write_text(program, encoding="utf-8")
    wire = tmp_path / "reference.wire"
    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo) + os.pathsep + env.get("PYTHONPATH", "")
    reference = subprocess.run([sys.executable, str(source), str(wire)], cwd=tmp_path,
                               env=env, capture_output=True, text=True, timeout=30)
    assert reference.returncode == 0, reference.stdout + reference.stderr
    assert reference.stderr == ""
    assert reference.stdout == "good\nBase.park|Worker.safe\n" + "|".join(EXPECTED_REJECTIONS) + "\n"
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


# Frozen 5912d7f _local_method_call_keys; only the function name differs.
def _legacy_method_keys(
    fd: FuncDef,
    current_class: Optional[str],
    methods: dict[str, dict[str, FuncDef]],
    bases: dict[str, tuple[str, ...]],
    *,
    attribute_calls: Optional[list[Call]] = None,
    local_hints: Optional[dict[str, str]] = None,
) -> list[str]:
    keys: list[str] = []
    local_classes = set(methods)
    if local_hints is None:
        local_hints = _function_local_class_hints(fd, local_classes)
    calls = attribute_calls
    if calls is None:
        calls = _function_attr_calls(fd)
    for call in calls:
        class_name = _receiver_class_name(
                call.func.obj,
                current_class,
                local_classes,
                local_hints,
            )
        if class_name is not None:
            key = _resolve_local_method_key(
                class_name,
                call.func.name,
                methods,
                bases,
            )
            if key is not None and key not in keys:
                keys.append(key)
    return keys


# Frozen 5912d7f _local_vthread_context; only the function name differs.
def _legacy_context(module: Module):
    """Per-module analysis inputs that do not depend on ``native_exports``.

    Each callable's lexical nodes, virtual-thread bindings, attribute calls and
    local class hints, plus the module's threading evidence.  Computing them
    once lets every pass over the module share one walk of each body.
    """
    module_aliases, value_aliases = _vthread_import_aliases(module)
    functions = _module_function_defs(module)
    methods, bases = _class_method_tables(module)
    method_defs = _class_method_defs(module)
    local_classes = set(methods)
    callable_context = {}
    for fd in functions:
        nodes = _function_scope_nodes(fd)
        callable_context[id(fd)] = (
            nodes,
            _function_vthread_bindings(
                fd,
                module_aliases,
                value_aliases,
                nodes,
            ),
            _function_attr_calls(fd, nodes),
            _function_local_class_hints(fd, local_classes, nodes),
        )
    for _class_name, fd in method_defs:
        nodes = _function_scope_nodes(fd)
        callable_context[id(fd)] = (
            nodes,
            _function_vthread_bindings(
                fd,
                module_aliases,
                value_aliases,
                nodes,
            ),
            _function_attr_calls(fd, nodes),
            _function_local_class_hints(fd, local_classes, nodes),
        )
    threading_context = _threading_analysis_context(
        module,
        functions,
        method_defs,
        callable_context,
    )
    return (
        module_aliases,
        value_aliases,
        functions,
        methods,
        bases,
        method_defs,
        callable_context,
        threading_context,
    )

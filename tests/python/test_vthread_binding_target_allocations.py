"""Only lexical binding branches need a private target-name set."""

import builtins
from inspect import getsource
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pcc.frontends.python import py_ast as pa
from pcc.frontends.python.codegen import vthread_effect_analysis as analysis
from pcc.frontends.python.codegen.vthread_effect_analysis import (
    Assign, AugAssign, Call, ClassDef, Delete, ExceptHandler, For, FuncDef,
    Global, Import, ImportFrom, Name, Nonlocal, With,
    _BINDING_DYNAMIC, _BINDING_VTHREAD_MODULE, _BINDING_VTHREAD_VALUE_PREFIX,
    _VTHREAD_VALUE_EXPORTS, _collect_binding_target_names, _record_binding_kind,
)
from pcc.frontends.python.py_lift import parse_and_lift


SPAN = pa.SourceSpan("bindings.py", 1, 0, 1, 1)
DYN = pa.DynType("dyn")
NONE = pa.NoneLit(SPAN, DYN)


def _name(name):
    return pa.Name(SPAN, DYN, name)


def _attr():
    return pa.Attr(SPAN, DYN, _name("object"), "field")


def _binding_node(kind):
    nested = pa.TupleExpr(SPAN, DYN, (
        _name("a"), pa.ListExpr(SPAN, DYN, (_name("b"), _name("a"))),
    ))
    if kind == "assign":
        return pa.Assign(SPAN, (nested, _attr()), NONE), {"a", "b"}
    if kind == "augassign":
        return pa.AugAssign(SPAN, _name("a"), "+=", NONE), {"a"}
    if kind == "for":
        return pa.For(SPAN, nested, _name("items"), ()), {"a", "b"}
    if kind == "with":
        return pa.With(SPAN, ((_name("ctx"), nested), (_name("ctx"), None)), ()), {"a", "b"}
    if kind == "except":
        return pa.ExceptHandler(None, "error", (), SPAN), {"error"}
    if kind == "delete":
        return pa.Delete(SPAN, (nested, _attr())), {"a", "b"}
    if kind == "function":
        return pa.FuncDef(SPAN, "inner", (), None, ()), {"inner"}
    if kind == "class":
        return pa.ClassDef(SPAN, "Nested", (), (), ()), {"Nested"}
    if kind in ("_walrus", "__walrus__"):
        return pa.Call(SPAN, DYN, _name(kind), (_name("a"), NONE)), {"a"}
    if kind == "empty_assign":
        return pa.Assign(SPAN, (_attr(),), NONE), set()
    if kind == "empty_with":
        return pa.With(SPAN, ((_name("ctx"), None),), ()), set()
    if kind == "empty_except":
        return pa.ExceptHandler(None, None, (), SPAN), set()
    if kind == "empty_walrus":
        return pa.Call(SPAN, DYN, _name("_walrus"), (_attr(), NONE)), set()
    raise AssertionError(kind)


def _observe(monkeypatch, function, nodes, args, record, *, fail_at="", failure=None):
    collect = analysis._collect_binding_target_names
    bind = analysis._record_binding_kind
    record.update(sets=[], events=[], targets=[], bindings={})

    def new_set(*values):
        result = builtins.set(*values)
        record["sets"].append(result)
        return result

    def collect_target(target, names):
        record["targets"].append((target, names))
        name = target.ident if isinstance(target, pa.Name) else type(target).__name__
        record["events"].append(("collect", name))
        if fail_at == "collect" and name == "b":
            raise failure
        return collect(target, names)

    def bind_name(bindings, name, kind):
        record["bindings"] = bindings
        record["events"].append(("record", name, kind))
        if fail_at == "record" and name == "b":
            raise failure
        return bind(bindings, name, kind)

    with monkeypatch.context() as patch:
        # Helper globals and frozen-oracle globals are patched together so
        # both runs count the same real built-in set constructions. Keeping
        # the observed sets alive is instrumentation, not a memory measure.
        for namespace in (analysis.__dict__, function.__globals__):
            patch.setitem(namespace, "set", new_set)
            patch.setitem(namespace, "_collect_binding_target_names", collect_target)
            patch.setitem(namespace, "_record_binding_kind", bind_name)
        return function(nodes, args)


def _compare(monkeypatch, nodes, args=()):
    old, new = {}, {}
    expected = _observe(monkeypatch, _legacy_bindings, nodes, args, old)
    actual = _observe(monkeypatch, analysis._lexical_binding_kinds, nodes, args, new)
    assert actual == expected
    assert list(actual[0]) == list(expected[0])
    assert new["events"] == old["events"]
    assert [names for _target, names in new["targets"]] == [
        names for _target, names in old["targets"]
    ]
    return actual, old, new


@pytest.mark.parametrize("count", [0, 1, 16, 64, 256])
def test_nonbinding_nodes_do_not_construct_discarded_target_sets(monkeypatch, count):
    nodes = [_name("loaded")] * count
    actual, old, new = _compare(monkeypatch, nodes)
    assert actual == ({}, set(), set())
    # These are exact constructor-call expectations, not native allocation,
    # elapsed-time, instruction-count or process-RSS measurements.
    assert len(old["sets"]) == count + 2
    assert len(new["sets"]) == 2
    assert old["targets"] == new["targets"] == []


@pytest.mark.parametrize("kind", [
    "assign", "augassign", "for", "with", "except", "delete", "function",
    "class", "_walrus", "__walrus__", "empty_assign", "empty_with",
    "empty_except", "empty_walrus",
])
def test_each_binding_shape_keeps_its_own_real_set(monkeypatch, kind):
    node, names = _binding_node(kind)
    actual, old, new = _compare(monkeypatch, [node])
    assert actual == ({name: {_BINDING_DYNAMIC} for name in names}, set(), set())
    assert len(new["sets"]) == len(old["sets"]) == 3 + len(names)


@pytest.mark.parametrize("kind", ["ordinary", "short_walrus", "attribute", "literal"])
def test_call_condition_keeps_original_short_circuit_without_a_target_set(monkeypatch, kind):
    calls = {
        "ordinary": pa.Call(SPAN, DYN, _name("call"), (_name("a"), NONE)),
        "short_walrus": pa.Call(SPAN, DYN, _name("_walrus"), (_name("a"),)),
        "attribute": pa.Call(SPAN, DYN, _attr(), (_name("a"), NONE)),
        "literal": pa.Call(SPAN, DYN, NONE, (_name("a"), NONE)),
    }
    actual, old, new = _compare(monkeypatch, [calls[kind]])
    assert actual == ({}, set(), set())
    assert len(old["sets"]) == 3 and len(new["sets"]) == 2


def test_target_sets_are_not_reused_across_binding_and_quiet_nodes(monkeypatch):
    nodes = [
        pa.Assign(SPAN, (_name("a"),), NONE), _name("load"),
        pa.Assign(SPAN, (_name("b"),), NONE), _name("load"),
    ]
    actual, old, new = _compare(monkeypatch, nodes)
    assert actual[0] == {"a": {_BINDING_DYNAMIC}, "b": {_BINDING_DYNAMIC}}
    assert len(old["sets"]) - len(new["sets"]) == 2
    assert len(new["targets"]) == 2
    first, second = (row[1] for row in new["targets"])
    assert first is not second and first == {"a"} and second == {"b"}


def test_arguments_globals_nonlocals_imports_and_ambiguity_keep_order(monkeypatch):
    args = (pa.Arg("vt", None, None, "pos"), pa.Arg("arg", None, None, "pos"))
    nodes = [
        pa.Global(SPAN, ("shared",)), pa.Nonlocal(SPAN, ("closed",)),
        pa.Import(SPAN, (("pcc.virtual_thread", "vt"), ("os.path", None))),
        pa.ImportFrom(SPAN, "pcc", (("virtual_thread", "local_vt"),)),
        pa.ImportFrom(SPAN, "pcc.virtual_thread", (("yield_now", "park"), ("*", None))),
        pa.ImportFrom(SPAN, "pcc.virtual_thread", (("unknown", None),)),
        pa.ImportFrom(SPAN, "pcc.virtual_thread", (("yield_now", "relative"),), 1),
        _name("quiet"),
    ]
    actual, old, new = _compare(monkeypatch, nodes, args)
    assert actual == ({
        "vt": {_BINDING_DYNAMIC, _BINDING_VTHREAD_MODULE},
        "arg": {_BINDING_DYNAMIC}, "os": {_BINDING_DYNAMIC},
        "local_vt": {_BINDING_VTHREAD_MODULE},
        "park": {_BINDING_VTHREAD_VALUE_PREFIX + "yield_now"},
        "unknown": {_BINDING_DYNAMIC}, "relative": {_BINDING_DYNAMIC},
    }, {"shared"}, {"closed"})
    assert len(old["sets"]) - len(new["sets"]) == len(nodes)


@pytest.mark.parametrize("fail_at", ["collect", "record", "target_getter", "import_getter"])
@pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
def test_existing_analysis_failures_propagate_without_new_partial_bindings(
    monkeypatch, fail_at, error_type,
):
    failure = error_type("binding collection failed")

    class BadAssign(pa.Assign):
        def __getattribute__(self, name):
            if name == "targets":
                raise failure
            return super().__getattribute__(name)

    class BadImport(pa.Import):
        def __getattribute__(self, name):
            if name == "names":
                raise failure
            return super().__getattribute__(name)

    nodes = [pa.Assign(SPAN, (_name("a"),), NONE), pa.Assign(SPAN, (_name("b"),), NONE)]
    if fail_at == "target_getter":
        nodes[1] = BadAssign(SPAN, (), NONE)
    elif fail_at == "import_getter":
        nodes[1] = BadImport(SPAN, ())
    records = []
    for function in (_legacy_bindings, analysis._lexical_binding_kinds):
        record = {}
        with pytest.raises(error_type) as raised:
            _observe(monkeypatch, function, nodes, (), record,
                     fail_at=fail_at, failure=failure)
        assert raised.value is failure
        records.append(record)
    assert records[0]["events"] == records[1]["events"]
    assert records[0]["bindings"] == records[1]["bindings"] == {"a": {_BINDING_DYNAMIC}}


SOURCE = """import pcc.virtual_thread as vt
from pcc.virtual_thread import yield_now as park

def leaf():
    vt.yield_now()

def entry():
    return leaf()

def shadowed():
    vt = 0
    return vt.yield_now()

def bindings(arg, items, ctx):
    global shared
    local, [other, duplicate, local] = (1, [2, 3, 4])
    local += arg
    ctx.field = arg
    items[0] = arg
    for index in items:
        pass
    with ctx as opened, ctx:
        pass
    with ctx:
        pass
    try:
        1 / 0
    except ValueError:
        pass
    except Exception as error:
        pass
    del other
    (captured := arg)
    _walrus(ctx.field, arg)
    __walrus__(second_capture, arg)
    import pcc.virtual_thread as local_vt
    from pcc.virtual_thread import yield_now as local_park
    def inner():
        nonlocal local
        local += 1
        return local
    class Nested:
        pass
    return local
"""

EXPORTS = {"unit": {
    name: {"kind": "function", "owning_module": "unit", "export_name": name}
    for name in ("leaf", "entry", "shadowed", "bindings")
}}

EXPECTED_BINDINGS = ({
    **{name: {_BINDING_DYNAMIC} for name in (
        "arg", "items", "ctx", "local", "other", "duplicate", "index",
        "opened", "error", "captured", "second_capture", "inner", "Nested",
    )},
    "local_vt": {_BINDING_VTHREAD_MODULE},
    "local_park": {_BINDING_VTHREAD_VALUE_PREFIX + "yield_now"},
}, {"shared"}, set())


def test_summary_and_full_may_park_match_frozen_binding_analysis(monkeypatch):
    module = parse_and_lift(SOURCE, "bindings.py", "unit")
    fd = next(node for node in module.body if isinstance(node, pa.FuncDef)
              and node.name == "bindings")
    assert analysis._lexical_binding_kinds(
        analysis._function_scope_nodes(fd), fd.args,
    ) == EXPECTED_BINDINGS
    with monkeypatch.context() as patch:
        patch.setattr(analysis, "_lexical_binding_kinds", _legacy_bindings)
        old_summary = analysis.build_closed_world_vthread_effect_summary(module, "unit", EXPORTS)
        old_result = analysis.compute_vthread_may_park_callables(module, EXPORTS)
    summary = analysis.build_closed_world_vthread_effect_summary(module, "unit", EXPORTS)
    result = analysis.compute_vthread_may_park_callables(module, EXPORTS)
    assert summary == old_summary
    assert result == old_result
    assert summary["seeds"] == ["f:unit:leaf"]
    assert summary["edges"] == ["f:unit:entry", "f:unit:leaf"]
    assert summary["publish"] == ["f:unit:" + name for name in EXPORTS["unit"]]
    assert result[1] == {"leaf", "entry"} and result[3] == set()


@pytest.mark.integration
def test_native_binding_analysis_matches_frozen_helper_and_cpython(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    """Real parser and consumers; not Stage2/3, forced-GC or performance evidence."""
    program = (
        "import sys\n"
        "from pcc.frontends.python.py_lift import parse_and_lift\n"
        "from pcc.frontends.python.codegen.vthread_effect_analysis import (\n"
        "    Assign, AugAssign, Call, ClassDef, Delete, ExceptHandler, For, FuncDef,\n"
        "    Global, Import, ImportFrom, Name, Nonlocal, With,\n"
        "    _BINDING_DYNAMIC, _BINDING_VTHREAD_MODULE, _BINDING_VTHREAD_VALUE_PREFIX,\n"
        "    _VTHREAD_VALUE_EXPORTS, _collect_binding_target_names, _record_binding_kind,\n"
        "    _lexical_binding_kinds, _lexical_scope_nodes,\n"
        "    build_closed_world_vthread_effect_summary, compute_vthread_may_park_callables,\n"
        "    write_closed_world_vthread_effect_summary,\n"
        ")\n"
        + getsource(_legacy_bindings)
        + f"\nSOURCE = {SOURCE!r}\nEXPORTS = {EXPORTS!r}\n"
        + f"EXPECTED_BINDINGS = {EXPECTED_BINDINGS!r}\n"
        + "def check_scope(body, args, label):\n"
        + "    nodes = _lexical_scope_nodes(body)\n"
        + "    actual = _lexical_binding_kinds(nodes, args)\n"
        + "    if actual != _legacy_bindings(nodes, args):\n"
        + "        raise RuntimeError('binding analysis differs from preimage')\n"
        + "    if label == 'bindings' and actual != EXPECTED_BINDINGS:\n"
        + "        raise RuntimeError('binding kinds or globals changed')\n"
        + "    if label == 'inner' and actual != ({'local': {'dynamic'}}, set(), {'local'}):\n"
        + "        raise RuntimeError('nonlocal binding changed')\n"
        + "    count = 1\n"
        + "    for node in nodes:\n"
        + "        if isinstance(node, FuncDef):\n"
        + "            count += check_scope(node.body, node.args, node.name)\n"
        + "        elif isinstance(node, ClassDef):\n"
        + "            count += check_scope(node.body, (), node.name)\n"
        + "    return count\n"
        + "def main():\n"
        + "    module = parse_and_lift(SOURCE, 'bindings.py', 'unit')\n"
        + "    count = check_scope(module.body, (), 'module')\n"
        + "    if count != 7:\n"
        + "        raise RuntimeError('missing lexical scope coverage')\n"
        + "    summary = build_closed_world_vthread_effect_summary(module, 'unit', EXPORTS)\n"
        + "    if summary['seeds'] != ['f:unit:leaf']:\n"
        + "        raise RuntimeError('parking seed changed')\n"
        + "    if summary['edges'] != ['f:unit:entry', 'f:unit:leaf']:\n"
        + "        raise RuntimeError('propagation edge changed')\n"
        + "    if summary['publish'] != ['f:unit:leaf', 'f:unit:entry', 'f:unit:shadowed', 'f:unit:bindings']:\n"
        + "        raise RuntimeError('publication order changed')\n"
        + "    result = compute_vthread_may_park_callables(module, EXPORTS)\n"
        + "    if result[1] != {'leaf', 'entry'} or result[3] != set():\n"
        + "        raise RuntimeError('full may_park classification changed')\n"
        + "    write_closed_world_vthread_effect_summary(sys.argv[1], summary)\n"
        + "    print(count)\n"
        + "    print('|'.join(sorted(result[1])))\n"
        + "    print(len(result[3]))\n"
        + "if __name__ == '__main__':\n"
        + "    main()\n"
    )
    source, executable = tmp_path / "bindings_program.py", tmp_path / "bindings_program"
    source.write_text(program, encoding="utf-8")
    reference_path = tmp_path / "reference.wire"
    repo = Path(__file__).resolve().parents[2]
    reference_env = dict(os.environ)
    reference_env["PYTHONPATH"] = str(repo) + os.pathsep + reference_env.get("PYTHONPATH", "")
    reference = subprocess.run(
        [sys.executable, str(source), str(reference_path)], cwd=tmp_path,
        env=reference_env, capture_output=True, text=True, timeout=30,
    )
    assert reference.returncode == 0, reference.stdout + reference.stderr
    assert reference.stderr == ""
    assert reference.stdout == "7\nentry|leaf\n0\n"
    expected_wire = reference_path.read_bytes()
    python_program_compiler(
        str(source), str(executable), backend="self",
        runtime_archive=str(pcc_runtime_archive), ir_scaffold_mode="on",
    )
    for collector in ("0", "1", "2", "3", "4"):
        wire = tmp_path / f"native_gc{collector}.wire"
        result = subprocess.run(
            [str(executable), str(wire)], cwd=tmp_path,
            env={**os.environ, "PCC_GC_BACKEND": collector},
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, (collector, result.stdout, result.stderr)
        assert result.stderr == "", collector
        assert result.stdout == reference.stdout, collector
        assert wire.read_bytes() == expected_wire, collector


# Frozen f8b59d60 helper; only the function name differs.
def _legacy_bindings(
    nodes: list[object],
    args,
) -> tuple[dict[str, set[str]], set[str], set[str]]:
    """Collect binding sources within one already isolated lexical scope."""
    global_names: set[str] = set()
    nonlocal_names: set[str] = set()
    for node in nodes:
        if isinstance(node, Global):
            global_names.update(node.names)
        elif isinstance(node, Nonlocal):
            nonlocal_names.update(node.names)

    bindings: dict[str, set[str]] = {}
    for arg in args:
        _record_binding_kind(bindings, arg.name, _BINDING_DYNAMIC)

    for node in nodes:
        target_names: set[str] = set()
        if isinstance(node, Assign):
            for target in node.targets:
                _collect_binding_target_names(target, target_names)
        elif isinstance(node, AugAssign):
            _collect_binding_target_names(node.target, target_names)
        elif isinstance(node, For):
            _collect_binding_target_names(node.target, target_names)
        elif isinstance(node, With):
            for _context, target in node.items:
                if target is not None:
                    _collect_binding_target_names(target, target_names)
        elif isinstance(node, ExceptHandler):
            if node.name:
                target_names.add(node.name)
        elif isinstance(node, Delete):
            for target in node.targets:
                _collect_binding_target_names(target, target_names)
        elif isinstance(node, (FuncDef, ClassDef)):
            target_names.add(node.name)
        elif (
            isinstance(node, Call)
            and isinstance(node.func, Name)
            and node.func.ident in ("_walrus", "__walrus__")
            and len(node.args) == 2
        ):
            _collect_binding_target_names(node.args[0], target_names)
        for name in target_names:
            _record_binding_kind(bindings, name, _BINDING_DYNAMIC)

        if isinstance(node, Import):
            for module_name, as_name in node.names:
                local_name = as_name or module_name.split(".")[0]
                kind = _BINDING_DYNAMIC
                if module_name == "pcc.virtual_thread" and as_name is not None:
                    kind = _BINDING_VTHREAD_MODULE
                _record_binding_kind(bindings, local_name, kind)
        elif isinstance(node, ImportFrom):
            for imported_name, as_name in node.names:
                if imported_name == "*":
                    continue
                local_name = as_name or imported_name
                kind = _BINDING_DYNAMIC
                if (
                    node.level == 0
                    and node.module == "pcc"
                    and imported_name == "virtual_thread"
                ):
                    kind = _BINDING_VTHREAD_MODULE
                elif (
                    node.level == 0
                    and node.module == "pcc.virtual_thread"
                    and imported_name in _VTHREAD_VALUE_EXPORTS
                ):
                    kind = _BINDING_VTHREAD_VALUE_PREFIX + imported_name
                _record_binding_kind(bindings, local_name, kind)
    return bindings, global_names, nonlocal_names

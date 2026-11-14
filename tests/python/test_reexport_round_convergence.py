"""Re-exports converge on full-round state, not overwritten intermediate values.

Python Language Reference 7.11 (import) and 6.2.7 (dictionary displays/order)
motivate binding order and metadata identity. The finite-round checks here are
not time/RSS measurements. A genuine multi-round cycle is deliberately left
as a bounded negative case, not silently accepted as a fixed point.
"""

import ast
from copy import deepcopy
from inspect import getsourcelines
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from pcc.frontends.python import pipeline_closed_world as closed
from pcc.frontends.python.pipeline_closed_world import (
    _closed_world_star_export_items,
    _py_ast_field_value,
    _resolve_ast_import_from_module,
)
from pcc.frontends.python.py_lift import parse_and_lift


PROVIDERS = (("a", "def x():\n    return 1\n"),
             ("b", "def x():\n    return 2\n"))
CASES = [
    ("explicit-rebind", PROVIDERS + (("m", "from a import x\nfrom b import x\n"),),
     ("a", "b", "m"), (("a", "x", "A"), ("b", "x", "B")), (),
     (("m", (("x", "B"),)),), False),
    ("transient-self-read", PROVIDERS + (("m", "from a import x\nfrom m import x as saved\nfrom b import x\n"),),
     ("a", "b", "m"), (("a", "x", "A"), ("b", "x", "B")), (),
     (("m", (("x", "B"), ("saved", "A"))),), False),
    ("consumer-first", PROVIDERS + (("z", "from m import x\n"),
     ("m", "from a import x\nfrom b import x\n")), ("z", "a", "b", "m"),
     (("a", "x", "A"), ("b", "x", "B")), (),
     (("m", (("x", "B"),)), ("z", (("x", "B"),))), False),
    ("aliased-tables", PROVIDERS + (("m", "from a import x\n"),
     ("n", "from b import x\n")), ("a", "b", "m", "n"),
     (("a", "x", "A"), ("b", "x", "B"), ("m", "x", "B")), (("n", "m"),),
     (("m", (("x", "B"),)), ("n", (("x", "B"),))), False),
    ("equal-not-identical", PROVIDERS + (("m", "from a import x\nfrom b import x\n"),),
     ("a", "b", "m"), (("a", "x", "equal-A"), ("b", "x", "equal-B")), (),
     (("m", (("x", "equal-B"),)),), False),
    ("new-final-none", PROVIDERS + (("z", "from m import *\n"),
     ("m", "from a import x\nfrom b import x\n")), ("z", "a", "b", "m"),
     (("a", "x", "A"), ("b", "x", "none")), (),
     (("m", (("x", "none"),)), ("z", (("x", "none"),))), False),
    ("star-none-propagation", (("z", "from m import *\n"),
     ("m", "from a import *\n"), ("a", "x = None\n")), ("z", "m", "a"),
     (("a", "x", "none"),), (),
     (("m", (("x", "none"),)), ("z", (("x", "none"),))), True),
    ("explicit-none-skips-missing", (("a", "x = None\n"),
     ("m", "from a import x\n")), ("a", "m"), (("a", "x", "none"),), (),
     (("m", ()),), True),
    ("star-then-explicit", PROVIDERS + (("m", "from a import *\nfrom b import x\n"),),
     ("a", "b", "m"), (("a", "x", "A"), ("a", "extra", "A"),
     ("a", "_private", "A"), ("b", "x", "B")), (),
     (("m", (("x", "B"), ("extra", "A"))),), True),
    ("explicit-then-star", PROVIDERS + (("m", "from a import x\nfrom b import *\n"),),
     ("a", "b", "m"), (("a", "x", "A"), ("b", "x", "B"),
     ("b", "extra", "B")), (), (("m", (("x", "A"), ("extra", "B"))),), True),
    ("all-order-and-private", (("a", "pass\n"), ("m", "from a import *\n")),
     ("a", "m"), (("a", "first", "A"), ("a", "second", "B"),
     ("a", "_private", "A"), ("a", "__all__", "all")), (),
     (("m", (("second", "B"), ("_private", "A"), ("first", "A"))),), True),
    ("same-object-rebind", PROVIDERS + (("m", "from a import x\nfrom b import x\n"),),
     ("a", "b", "m"), (("a", "x", "A"), ("b", "x", "A")), (),
     (("m", (("x", "A"),)),), True),
    ("transient-star-capture", (("a", "def x():\n    return 1\n"),
     ("m", "from a import x\nfrom m import x as saved\nfrom b import x\n"),
     ("sink", "from m import *\n"), ("b", "from a import x\n")),
     ("a", "m", "sink", "b"), (("a", "x", "A"), ("b", "x", "B")), (),
     (("m", (("x", "A"), ("saved", "A"))),
      ("sink", (("x", "B"), ("saved", "A"))), ("b", (("x", "A"),))), True),
    ("relative-import", (("pkg.a", "def x():\n    return 1\n"),
     ("pkg.m", "from .a import x\n")), ("pkg.m", "pkg.a"),
     (("pkg.a", "x", "A"),), (), (("pkg.m", (("x", "A"),)),), True),
    ("repeated-module-name", (("a", "def x():\n    return 1\n"),
     ("m", "from a import x\n")), ("a", "m", "m"),
     (("a", "x", "A"),), (), (("m", (("x", "A"),)),), True),
    ("empty-cycle", (("m", "from n import x\n"), ("n", "from m import x\n")),
     ("m", "n"), (), (), (("m", ()), ("n", ())), True),
    ("seeded-cycle", (("m", "from n import x\n"), ("n", "from m import x\n")),
     ("m", "n"), (("m", "x", "A"),), (),
     (("m", (("x", "A"),)), ("n", (("x", "A"),))), True),
]


class _RoundLimit(RuntimeError):
    pass


def _bounded(function, args, limit=12):
    lines, first = getsourcelines(function)
    tree = ast.parse(textwrap.dedent("".join(lines)))
    loop = next(node for node in ast.walk(tree) if isinstance(node, ast.While))
    boundary = first + loop.body[0].lineno - 1
    rounds = [0]

    def trace(frame, event, value):
        if frame.f_code is not function.__code__:
            return None
        if event == "line" and frame.f_lineno == boundary:
            rounds[0] += 1
            if rounds[0] > limit:
                raise _RoundLimit("re-export round limit")
        return trace

    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        function(*args)
    finally:
        sys.settrace(previous)
    return rounds[0]


def _inputs(case):
    _name, sources, names, seeds, aliases, _expected, _finite = case
    source_map = dict(sources)
    paths = ["/tmp/round_case/" + name + ".py" for name in names]
    modules = [parse_and_lift(source_map[name], path, name)
               for name, path in zip(names, paths)]
    tokens = {"A": {"value": "A"}, "B": {"value": "B"}, "none": None,
              "equal-A": {"equal": 1}, "equal-B": {"equal": 1},
              "all": {"export_names": ("second", "_private", "first")}}
    exports = {name: {} for name in names}
    for module, key, token in seeds:
        exports[module][key] = tokens[token]
    for alias, owner in aliases:
        exports[alias] = exports[owner]
    edges = closed._closed_world_reexport_edges(modules, names, paths, names)
    return names, paths, modules, edges, tokens, exports


def _arguments(mode, names, paths, modules, edges, exports):
    if mode == "edges":
        return names, exports, edges
    return modules, names, paths, exports


def _snapshot(exports, tokens):
    def label(value):
        return next(name for name, token in tokens.items() if token is value)
    entries = tuple((module, tuple((key, label(value)) for key, value in table.items()))
                    for module, table in exports.items())
    aliases = tuple((left, right) for left in exports for right in exports
                    if exports[left] is exports[right])
    return entries, aliases


@pytest.mark.parametrize("mode", ["edges", "ast"])
@pytest.mark.parametrize("case", CASES, ids=[case[0] for case in CASES])
def test_full_round_state_preserves_bindings_order_and_identity(case, mode):
    names, paths, modules, edges, tokens, exports = _inputs(case)
    old_tokens, old_exports = deepcopy((tokens, exports))
    frozen_edges = tuple(edges)
    actual = (closed._merge_closed_world_reexport_edges if mode == "edges"
              else closed._merge_closed_world_reexports)
    legacy = _legacy_edge_merger if mode == "edges" else _legacy_ast_merger
    count = _bounded(actual, _arguments(mode, names, paths, modules, edges, exports))
    assert 1 <= count <= 4
    for module, bindings in case[5]:
        assert list(exports[module]) == [key for key, token in bindings]
        for key, token in bindings:
            assert exports[module][key] is tokens[token]
    for alias, owner in case[4]:
        assert exports[alias] is exports[owner]
    assert tuple(edges) == frozen_edges
    old_args = _arguments(mode, names, paths, modules, edges, old_exports)
    if case[6]:
        _bounded(legacy, old_args)
        assert _snapshot(exports, tokens) == _snapshot(old_exports, old_tokens)
    else:
        with pytest.raises(_RoundLimit, match="round limit"):
            _bounded(legacy, old_args)


def test_real_source_produces_both_explicit_bindings_in_order():
    names, _paths, _modules, edges, _tokens, _exports = _inputs(CASES[0])
    assert names == ("a", "b", "m")
    assert edges == [("m", "a", "x", "x", False),
                     ("m", "b", "x", "x", False)]


def test_dropping_shadowed_edge_changes_terminating_star_capture():
    case = next(case for case in CASES if case[0] == "transient-star-capture")
    names, paths, modules, edges, tokens, exports = _inputs(case)
    _bounded(_legacy_edge_merger, (names, exports, edges))
    assert exports["sink"]["saved"] is tokens["A"]
    names, paths, modules, edges, tokens, exports = _inputs(case)
    dropped = [edge for edge in edges if edge != ("m", "a", "x", "x", False)]
    _bounded(_legacy_edge_merger, (names, exports, dropped))
    assert exports["m"]["saved"] is tokens["A"]
    assert exports["sink"]["saved"] is tokens["B"]


@pytest.mark.parametrize("mode", ["edges", "ast"])
@pytest.mark.parametrize("length", [32, 128])
def test_long_reverse_chain_matches_frozen_oracle(mode, length):
    names = tuple("n" + str(index) for index in range(length + 1))
    sources = tuple((names[index], "from " + names[index + 1] + " import x\n")
                    for index in range(length)) + ((names[-1], "x = 1\n"),)
    case = ("chain", sources, names, ((names[-1], "x", "A"),), (), (), True)
    names, paths, modules, edges, tokens, exports = _inputs(case)
    old_tokens, old_exports = deepcopy((tokens, exports))
    actual = (closed._merge_closed_world_reexport_edges if mode == "edges"
              else closed._merge_closed_world_reexports)
    legacy = _legacy_edge_merger if mode == "edges" else _legacy_ast_merger
    count = _bounded(actual, _arguments(mode, names, paths, modules, edges, exports), length + 2)
    assert count == length + 1
    _bounded(legacy, _arguments(mode, names, paths, modules, edges, old_exports), length + 2)
    assert _snapshot(exports, tokens) == _snapshot(old_exports, old_tokens)
    assert all(exports[name]["x"] is tokens["A"] for name in names)


@pytest.mark.parametrize("mode", ["edges", "ast"])
def test_genuine_two_cycle_is_not_misreported_as_stable(mode):
    case = ("rotation", (("t", "from x import x\n"),
            ("x", "from y import x\n"), ("y", "from t import x\n")),
            ("t", "x", "y"), (("x", "x", "A"), ("y", "x", "B")), (), (), False)
    names, paths, modules, edges, tokens, exports = _inputs(case)
    old_tokens, old_exports = deepcopy((tokens, exports))
    actual = (closed._merge_closed_world_reexport_edges if mode == "edges"
              else closed._merge_closed_world_reexports)
    legacy = _legacy_edge_merger if mode == "edges" else _legacy_ast_merger
    with pytest.raises(_RoundLimit):
        _bounded(actual, _arguments(mode, names, paths, modules, edges, exports), 8)
    with pytest.raises(_RoundLimit):
        _bounded(legacy, _arguments(mode, names, paths, modules, edges, old_exports), 8)
    assert _snapshot(exports, tokens) == _snapshot(old_exports, old_tokens)


def test_malformed_edge_retains_prior_write_and_exception():
    value = {"value": "A"}
    edges = (("m", "a", "x", "x", False), ("ignored",),
             ("m", "a", "x", "x", False, "extra"))
    for function in (closed._merge_closed_world_reexport_edges, _legacy_edge_merger):
        exports = {"a": {"x": value}, "m": {}}
        with pytest.raises(ValueError, match="too many values to unpack"):
            _bounded(function, (("a", "m"), exports, edges))
        assert list(exports["m"]) == ["x"]
        assert exports["m"]["x"] is value


def test_unmatched_oversized_edge_is_not_eagerly_unpacked():
    value = {"value": "A"}
    edges = (("other", "a", "x", "x", False, "extra"), ("ignored",),
             ("m", "a", "x", "x", False))
    for function in (closed._merge_closed_world_reexport_edges, _legacy_edge_merger):
        exports = {"a": {"x": value}, "m": {}}
        _bounded(function, (("a", "m"), exports, edges))
        assert list(exports["m"]) == ["x"]
        assert exports["m"]["x"] is value


@pytest.mark.parametrize("mode", ["edges", "ast"])
def test_star_helper_exception_preserves_prior_binding(monkeypatch, mode):
    case = ("helper-error", PROVIDERS + (("m", "from a import x\nfrom b import *\n"),),
            ("a", "b", "m"), (("a", "x", "A"), ("b", "x", "B")), (), (), True)
    names, paths, modules, edges, tokens, exports = _inputs(case)
    old_tokens, old_exports = deepcopy((tokens, exports))

    def fail_star(source):
        raise RuntimeError("injected star failure")

    monkeypatch.setattr(closed, "_closed_world_star_export_items", fail_star)
    monkeypatch.setitem(globals(), "_closed_world_star_export_items", fail_star)
    actual = (closed._merge_closed_world_reexport_edges if mode == "edges"
              else closed._merge_closed_world_reexports)
    legacy = _legacy_edge_merger if mode == "edges" else _legacy_ast_merger
    for function, graph in ((actual, exports), (legacy, old_exports)):
        with pytest.raises(RuntimeError, match="injected star failure"):
            _bounded(function, _arguments(mode, names, paths, modules, edges, graph))
    assert exports["m"]["x"] is tokens["A"]
    assert _snapshot(exports, tokens) == _snapshot(old_exports, old_tokens)


@pytest.mark.parametrize("mode", ["edges", "ast"])
@pytest.mark.parametrize("fail_at", [1, 2])
def test_log_failure_precedes_the_pending_binding_write(monkeypatch, mode, fail_at):
    # This is the new failure boundary, not equivalence to a native OOM.
    names, paths, modules, edges, tokens, exports = _inputs(CASES[0])
    record = closed._record_closed_world_reexport_change
    calls = [0]

    def fail_record(*args):
        calls[0] += 1
        if calls[0] == fail_at:
            raise MemoryError("injected change-log failure")
        record(*args)

    monkeypatch.setattr(closed, "_record_closed_world_reexport_change", fail_record)
    actual = (closed._merge_closed_world_reexport_edges if mode == "edges"
              else closed._merge_closed_world_reexports)
    with pytest.raises(MemoryError, match="injected change-log failure"):
        _bounded(actual, _arguments(mode, names, paths, modules, edges, exports))
    if fail_at == 1:
        assert exports["m"] == {}
    else:
        assert list(exports["m"]) == ["x"]
        assert exports["m"]["x"] is tokens["A"]


NATIVE_BODY = '''
def main():
    for case in CASES:
        name, sources, names, seeds, aliases, expected, finite = case
        source_map = dict(sources)
        paths = []
        modules = []
        for module_name in names:
            path = module_name + '.py'
            paths.append(path)
            modules.append(parse_and_lift(source_map[module_name], path, module_name))
        edges = _closed_world_reexport_edges(modules, names, paths, names)
        for use_edges in (True, False):
            tokens = {'A': {'value': 'A'}, 'B': {'value': 'B'}, 'none': None,
                      'equal-A': {'equal': 1}, 'equal-B': {'equal': 1},
                      'all': {'export_names': ('second', '_private', 'first')}}
            exports = {}
            for module_name in names:
                exports[module_name] = {}
            for module_name, key, token in seeds:
                exports[module_name][key] = tokens[token]
            for alias, owner in aliases:
                exports[alias] = exports[owner]
            if use_edges:
                _merge_closed_world_reexport_edges(names, exports, edges)
            else:
                _merge_closed_world_reexports(modules, names, paths, exports)
            for module_name, bindings in expected:
                expected_names = []
                for key, token in bindings:
                    expected_names.append(key)
                    assert exports[module_name][key] is tokens[token]
                assert list(exports[module_name]) == expected_names
            for alias, owner in aliases:
                assert exports[alias] is exports[owner]
            print(name + ':' + str(use_edges))
    print('REEXPORT_ROUND_CONVERGENCE_OK')

if __name__ == '__main__':
    main()
'''


@pytest.mark.integration
def test_native_real_parser_and_both_mergers_match_cpython(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    program = (
        "from pcc.frontends.python.py_lift import parse_and_lift\n"
        "from pcc.frontends.python.pipeline_closed_world import (\n"
        "    _closed_world_reexport_edges, _merge_closed_world_reexport_edges,\n"
        "    _merge_closed_world_reexports,\n"
        ")\n"
        + f"CASES = {CASES!r}\n" + NATIVE_BODY
    )
    source, executable = tmp_path / "reexports.py", tmp_path / "reexports"
    source.write_text(program, encoding="utf-8")
    repo = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(repo) + os.pathsep + environment.get("PYTHONPATH", "")
    reference = subprocess.run([sys.executable, str(source)], cwd=tmp_path,
                               env=environment, capture_output=True, text=True, timeout=30)
    assert reference.returncode == 0, reference.stdout + reference.stderr
    assert reference.stderr == ""
    assert reference.stdout.endswith("REEXPORT_ROUND_CONVERGENCE_OK\n")
    python_program_compiler(str(source), str(executable), backend="self",
                            runtime_archive=str(pcc_runtime_archive), ir_scaffold_mode="on")
    for collector in ("0", "1", "2", "3", "4"):
        result = subprocess.run([str(executable)], cwd=tmp_path,
                                env={**os.environ, "PCC_GC_BACKEND": collector},
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, (collector, result.stdout, result.stderr)
        assert result.stderr == "", collector
        assert result.stdout == reference.stdout, collector


@pytest.mark.integration
@pytest.mark.parametrize("frontend_jobs", ["1", "2"])
def test_real_redefined_import_package_matches_cpython(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler, frontend_jobs,
):
    # Exercise the original multi-module compilation shape, not only helpers
    # called from a compiled program. Request one/two frontend jobs; resource
    # admission can narrow the actual width, so the knob is not a route receipt.
    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", frontend_jobs)
    (tmp_path / "provider_a.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (tmp_path / "provider_b.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    (tmp_path / "facade.py").write_text(
        "from provider_a import run\nfrom provider_b import run\n", encoding="utf-8")
    source, executable = tmp_path / "entry.py", tmp_path / "entry"
    source.write_text("from facade import run\ndef main():\n    print(run())\nmain()\n",
                      encoding="utf-8")
    reference = subprocess.run([sys.executable, str(source)], cwd=tmp_path,
                               capture_output=True, text=True, timeout=30)
    assert reference.returncode == 0, reference.stdout + reference.stderr
    assert reference.stderr == ""
    assert reference.stdout == "2\n"
    python_program_compiler(str(source), str(executable), backend="self",
                            runtime_archive=str(pcc_runtime_archive), ir_scaffold_mode="on")
    for collector in ("0", "1", "2", "3", "4"):
        result = subprocess.run([str(executable)], cwd=tmp_path,
                                env={**os.environ, "PCC_GC_BACKEND": collector},
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, (collector, result.stdout, result.stderr)
        assert result.stderr == "", collector
        assert result.stdout == reference.stdout, collector


# The two frozen 0bfe990 implementations below differ only in function name.
def _legacy_ast_merger(
    parsed_modules, module_names, src_paths, native_exports
):
    """Propagate native function/class/constant exports across package re-exports.

    Real packages commonly expose their public API through ``__init__.py`` using
    ``from .mod import name`` and ``from .mod import *``.  Without modelling
    that in the closed-world table, downstream modules see a missing native
    export and fall back to ``py_cpy_import`` even though both sides are compiled
    in the same native invocation.
    """
    from pcc.frontends.python.py_ast import ImportFrom as _ImportFrom

    module_set = set(module_names)
    module_to_ast = {
        mod_name: ast_mod for mod_name, ast_mod in zip(module_names, parsed_modules)
    }
    module_to_src = {
        mod_name: src_path for mod_name, src_path in zip(module_names, src_paths)
    }

    changed = True
    while changed:
        changed = False
        for mod_name in module_names:
            ast_mod = module_to_ast.get(mod_name)
            src_path = module_to_src.get(mod_name, "")
            if ast_mod is None:
                continue
            exports = native_exports.setdefault(mod_name, {})
            for stmt in _py_ast_field_value(ast_mod, "body", ()):
                if not isinstance(stmt, _ImportFrom):
                    continue
                src_mod = _resolve_ast_import_from_module(src_path, mod_name, stmt)
                if not src_mod:
                    continue

                # ``from . import submodule`` re-exports a module object, not a
                # function/class binding.  Import lowering already handles that
                # by registering native module aliases, so do not invent an
                # export-table entry for it here.
                for attr_name, as_name in _py_ast_field_value(stmt, "names", ()):
                    if attr_name == "*":
                        src_exports = native_exports.get(src_mod)
                        if not src_exports:
                            continue
                        for export_name, info in _closed_world_star_export_items(
                            src_exports
                        ):
                            if export_name in exports:
                                continue
                            exports[export_name] = info
                            changed = True
                        continue
                    local_name = as_name or attr_name
                    src_exports = native_exports.get(src_mod)
                    if src_exports and attr_name in src_exports:
                        if exports.get(local_name) is not src_exports[attr_name]:
                            exports[local_name] = src_exports[attr_name]
                            changed = True
                        continue
                    full_submodule = f"{src_mod}.{attr_name}"
                    if full_submodule in module_set:
                        continue

def _legacy_edge_merger(module_names, native_exports, edges):
    changed = True
    while changed:
        changed = False
        for mod_name in module_names:
            exports = native_exports.setdefault(mod_name, {})
            for edge in edges:
                if len(edge) < 5 or edge[0] != mod_name:
                    continue
                _dst_mod, src_mod, attr_name, local_name, is_star = edge
                src_exports = native_exports.get(src_mod)
                if not src_exports:
                    continue
                if is_star:
                    for export_name, info in _closed_world_star_export_items(
                        src_exports
                    ):
                        if export_name in exports:
                            continue
                        exports[export_name] = info
                        changed = True
                    continue
                if (
                    attr_name in src_exports
                    and exports.get(local_name) is not src_exports[attr_name]
                ):
                    exports[local_name] = src_exports[attr_name]
                    changed = True

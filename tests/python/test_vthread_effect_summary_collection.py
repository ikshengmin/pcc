"""Summary collection keeps its wire contract without solving local effects."""

from copy import deepcopy
from inspect import signature
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pcc.frontends.python.codegen import vthread_effect_analysis as analysis
from pcc.frontends.python.parser import parse


def _exports(module_name, function_names, method_names=()):
    rows = {
        name: {
            "kind": "function",
            "owning_module": module_name,
            "export_name": name,
        }
        for name in function_names
    }
    for class_name, method_name in method_names:
        row = rows.setdefault(
            class_name,
            {
                "kind": "class",
                "owning_module": module_name,
                "export_name": class_name,
                "methods": [],
            },
        )
        row["methods"].append({"name": method_name})
    return {module_name: rows}


def _empty_summary(module_name):
    return {
        "schema": "pcc.vthread.effect-summary.v1",
        "module_name": module_name,
        "seeds": [],
        "edges": [],
        "publish": [],
    }


def _compare_with_full_local_pass(module, module_name, exports, tmp_path):
    """Keep the former full-pass route as an executable wire oracle."""
    surface = analysis.closed_world_vthread_effect_export_surface(exports)
    before = deepcopy(surface)
    full_summary = _empty_summary(module_name)
    local_result = analysis._compute_local_vthread_effects(
        module,
        surface,
        effect_summary=full_summary,
        summary_module_name=module_name,
    )
    assert local_result is not None
    summary = analysis.build_closed_world_vthread_effect_summary(
        module, module_name, surface,
    )
    assert summary == full_summary
    assert surface == before
    full_path = tmp_path / "full.wire"
    path = tmp_path / "summary.wire"
    analysis.write_closed_world_vthread_effect_summary(str(full_path), full_summary)
    analysis.write_closed_world_vthread_effect_summary(str(path), summary)
    assert path.read_bytes() == full_path.read_bytes()
    assert analysis.read_closed_world_vthread_effect_summary(str(path)) == summary
    return summary, local_result, path


def _compare_published_with_eager(module, module_name, exports, path):
    eager = deepcopy(exports)
    summarized = deepcopy(exports)
    analysis.annotate_closed_world_vthread_effects([module], [module_name], eager)
    count, _nodes, _edges = analysis.annotate_closed_world_vthread_effect_summaries(
        [str(path)], summarized,
    )
    assert count == 1
    assert summarized == eager
    return summarized[module_name]


def _chain_source(length):
    rows = ["import pcc.virtual_thread as vt\n"]
    for index in range(length):
        rows.append(f"def f{index}():\n    return f{index + 1}()\n")
    rows.append(f"def f{length}():\n    vt.yield_now()\n    return 1\n")
    rows.append("def quiet():\n    return 0\n")
    return "\n".join(rows)


@pytest.mark.parametrize("length", [16, 64, 256])
def test_summary_long_chain_keeps_wire_and_complete_effects(length, tmp_path):
    # Caller-before-callee order requires multiple passes in the old local
    # solver.  These sizes are coverage, not elapsed-time or memory evidence.
    module_name = "long_chain"
    module = parse(_chain_source(length), module_name + ".py")
    names = [f"f{index}" for index in range(length + 1)]
    exports = _exports(module_name, [*names, "quiet"])
    summary, local_result, path = _compare_with_full_local_pass(
        module, module_name, exports, tmp_path,
    )
    assert summary == {
        "schema": "pcc.vthread.effect-summary.v1",
        "module_name": module_name,
        "seeds": [f"f:{module_name}:f{length}"],
        "edges": [
            key
            for index in range(length)
            for key in (
                f"f:{module_name}:f{index}",
                f"f:{module_name}:f{index + 1}",
            )
        ],
        "publish": [f"f:{module_name}:{name}" for name in [*names, "quiet"]],
    }
    assert local_result == (
        {
            id(fd)
            for fd in analysis._module_function_defs(module)
            if fd.name != "quiet"
        },
        set(names),
        set(),
        set(),
    )
    published = _compare_published_with_eager(module, module_name, exports, path)
    assert all(published[name]["may_park"] is True for name in names)
    assert published["quiet"]["may_park"] is False


@pytest.mark.parametrize("seeded", [False, True])
def test_summary_cycle_keeps_seeded_and_unseeded_fixed_points(seeded, tmp_path):
    module_name = "cycle"
    seed_call = "    vt.yield_now()\n" if seeded else ""
    module = parse(
        "import pcc.virtual_thread as vt\n"
        "def first():\n    return second()\n"
        "def second():\n    first()\n    return leaf()\n"
        "def leaf():\n" + seed_call + "    return 1\n"
        "def quiet():\n    return 0\n",
        module_name + ".py",
    )
    names = ["first", "second", "leaf", "quiet"]
    exports = _exports(module_name, names)
    summary, local_result, path = _compare_with_full_local_pass(
        module, module_name, exports, tmp_path,
    )
    assert summary["seeds"] == (["f:cycle:leaf"] if seeded else [])
    assert summary["edges"] == [
        "f:cycle:first", "f:cycle:second",
        "f:cycle:second", "f:cycle:first",
        "f:cycle:second", "f:cycle:leaf",
    ]
    assert summary["publish"] == [f"f:cycle:{name}" for name in names]
    assert local_result[1] == (set(names[:-1]) if seeded else set())
    assert local_result[2:] == (set(), set())
    published = _compare_published_with_eager(module, module_name, exports, path)
    assert [published[name]["may_park"] for name in names] == [
        seeded, seeded, seeded, False,
    ]


MIXED_SOURCE = """import pcc.virtual_thread as vt
def entry():
    return Worker().step()
def leaf():
    vt.yield_now()
    return 1
class Worker:
    def step(self):
        return leaf()
"""


def test_summary_collects_all_callables_without_local_propagation(
    monkeypatch, tmp_path,
):
    module = parse(MIXED_SOURCE, "mixed.py")
    exports = _exports("mixed", ["entry", "leaf"], [("Worker", "step")])
    surface = analysis.closed_world_vthread_effect_export_surface(exports)
    original = analysis._add_local_callable_effect
    parameters = signature(original)
    captured = []

    def observe(*args, **kwargs):
        arguments = parameters.bind(*args, **kwargs).arguments
        result = original(*args, **kwargs)
        captured.append((arguments["node_key"], arguments["effects"]))
        return result

    monkeypatch.setattr(analysis, "_add_local_callable_effect", observe)
    summary = analysis.build_closed_world_vthread_effect_summary(
        module, "mixed", surface,
    )
    assert [key for key, _effects in captured] == [
        "function:entry", "function:leaf", "method:Worker.step",
    ]
    assert all(effects is captured[0][1] for _key, effects in captured)
    # The former builder propagated entry and Worker.step before discarding
    # its local result.  Neither belongs in this collection-only effect set.
    assert captured[0][1] == {"function:leaf"}
    assert summary["seeds"] == ["f:mixed:leaf"]
    assert summary["edges"] == [
        "f:mixed:entry", "m:mixed:Worker:step",
        "m:mixed:Worker:step", "f:mixed:leaf",
    ]
    assert summary["publish"] == [
        "f:mixed:entry", "f:mixed:leaf", "m:mixed:Worker:step",
    ]

    captured.clear()
    full_summary = _empty_summary("mixed")
    context = analysis._local_vthread_context(module)
    result = analysis._compute_local_vthread_effects(
        module,
        surface,
        effect_summary=full_summary,
        summary_module_name="mixed",
        local_context=context,
    )
    assert full_summary == summary
    assert result == analysis.compute_vthread_may_park_callables(module, surface)
    assert result[1] == {"entry", "leaf"}
    assert result[3] == {"Worker.step"}
    assert captured[0][1] == {
        "function:entry", "function:leaf", "method:Worker.step",
    }
    functions = analysis.compute_vthread_may_park_functions(module, surface)
    methods = analysis.compute_vthread_may_park_methods(module, functions[1], surface)
    assert (functions[0], functions[1], methods[0], methods[1]) == result
    path = tmp_path / "mixed.wire"
    analysis.write_closed_world_vthread_effect_summary(str(path), summary)
    published = _compare_published_with_eager(module, "mixed", exports, path)
    assert published["entry"]["may_park"] is True
    assert published["Worker"]["methods"][0]["may_park"] is True


@pytest.mark.parametrize("last_calls_leaf", [False, True])
def test_summary_redefinition_preserves_last_edges_and_publish_order(
    last_calls_leaf, tmp_path,
):
    first_body = "return 0" if last_calls_leaf else "return leaf()"
    last_body = "return leaf()" if last_calls_leaf else "return 0"
    module = parse(
        "import pcc.virtual_thread as vt\n"
        f"def caller():\n    {first_body}\n"
        "def other():\n    return leaf()\n"
        "def leaf():\n    vt.yield_now()\n    return 1\n"
        f"def caller():\n    {last_body}\n",
        "redefined.py",
    )
    exports = _exports("redefined", ["caller", "other", "leaf"])
    summary, local_result, path = _compare_with_full_local_pass(
        module, "redefined", exports, tmp_path,
    )
    assert summary["publish"] == [
        "f:redefined:caller", "f:redefined:other", "f:redefined:leaf",
    ]
    assert summary["seeds"] == ["f:redefined:leaf"]
    assert summary["edges"] == ["f:redefined:other", "f:redefined:leaf"] + (
        ["f:redefined:caller", "f:redefined:leaf"] if last_calls_leaf else []
    )
    expected_names = {"leaf", "other"}
    if last_calls_leaf:
        expected_names.add("caller")
    assert local_result[1] == expected_names
    published = _compare_published_with_eager(module, "redefined", exports, path)
    assert published["caller"]["may_park"] is last_calls_leaf


@pytest.mark.parametrize("method", [False, True])
@pytest.mark.parametrize("last_parks", [False, True])
def test_summary_redefinition_preserves_seed_reset_and_local_conservatism(
    method, last_parks, tmp_path,
):
    # A directly parking earlier definition remains conservative in the
    # ordinary local analysis.  Summary seeds use the existing last-binding
    # rule instead.  This refactor must not silently unify those contracts.
    first_body = "return 0" if last_parks else "vt.yield_now()"
    last_body = "vt.yield_now()" if last_parks else "return 0"
    if method:
        definitions = (
            "class Worker:\n"
            f"    def run(self):\n        {first_body}\n"
            f"    def run(self):\n        {last_body}\n"
        )
        exports = _exports("seeds", [], [("Worker", "run")])
        key = "m:seeds:Worker:run"
    else:
        definitions = (
            f"def run():\n    {first_body}\n"
            f"def run():\n    {last_body}\n"
        )
        exports = _exports("seeds", ["run"])
        key = "f:seeds:run"
    module = parse("import pcc.virtual_thread as vt\n" + definitions, "seeds.py")
    summary, local_result, path = _compare_with_full_local_pass(
        module, "seeds", exports, tmp_path,
    )
    assert summary["publish"] == [key]
    assert summary["edges"] == []
    assert summary["seeds"] == ([key] if last_parks else [])
    assert local_result[1] == (set() if method else {"run"})
    assert local_result[3] == ({"Worker.run"} if method else set())
    expected_ids = {
        id(fd)
        for _class_name, fd in analysis._class_method_defs(module)
    } if method else {id(fd) for fd in analysis._module_function_defs(module)}
    assert local_result[2 if method else 0] == expected_ids
    summarized = deepcopy(exports)
    analysis.annotate_closed_world_vthread_effect_summaries([str(path)], summarized)
    row = summarized["seeds"]["Worker"]["methods"][0] if method else (
        summarized["seeds"]["run"]
    )
    assert row["may_park"] is last_parks


def test_summary_keeps_import_alias_keys_and_default_imported_effect_names(tmp_path):
    module = parse(
        "from provider import leaf as unused\n"
        "from facade import renamed as called\n"
        "def entry():\n    return called()\n"
        "def quiet():\n    return 0\n",
        "imports.py",
    )
    exports = _exports("imports", ["entry", "quiet"])
    exports.update(_exports("provider", ["leaf"]))
    exports["provider"]["leaf"]["may_park"] = True
    exports["facade"] = {"renamed": deepcopy(exports["provider"]["leaf"])}
    summary, result, path = _compare_with_full_local_pass(
        module, "imports", exports, tmp_path,
    )
    assert summary["seeds"] == []
    assert summary["edges"] == ["f:imports:entry", "f:provider:leaf"]
    assert summary["publish"] == ["f:imports:entry", "f:imports:quiet"]
    assert result[1] == {"entry", "called", "unused"}
    assert len(result[0]) == 1
    assert result[2:] == (set(), set())
    published = _compare_published_with_eager(module, "imports", exports, path)
    assert published["entry"]["may_park"] is True
    assert published["quiet"]["may_park"] is False


def test_summary_only_returns_no_local_result_and_reuses_context(monkeypatch):
    module = parse(MIXED_SOURCE, "mixed.py")
    exports = _exports("mixed", ["entry", "leaf"], [("Worker", "step")])
    context = analysis._local_vthread_context(module)

    def unexpected_context(_module):
        raise AssertionError("supplied local context was recomputed")

    monkeypatch.setattr(analysis, "_local_vthread_context", unexpected_context)
    summary = _empty_summary("mixed")
    result = analysis._compute_local_vthread_effects(
        module,
        exports,
        effect_summary=summary,
        summary_module_name="mixed",
        local_context=context,
        summary_only=True,
    )
    assert result is None
    assert len(summary["publish"]) == 3
    assert summary["seeds"] == ["f:mixed:leaf"]
    result = analysis._compute_local_vthread_effects(
        module, exports, local_context=context,
    )
    assert result[1] == {"entry", "leaf"}
    assert result[3] == {"Worker.step"}


def test_empty_summary_keeps_full_local_result_contract(tmp_path):
    module = parse("", "empty.py")
    summary, result, path = _compare_with_full_local_pass(
        module, "empty", {"empty": {}}, tmp_path,
    )
    assert summary == _empty_summary("empty")
    assert result == (set(), set(), set(), set())
    assert _compare_published_with_eager(module, "empty", {"empty": {}}, path) == {}


@pytest.mark.parametrize("summary_only", [False, True])
def test_summary_collection_errors_are_not_suppressed(monkeypatch, summary_only):
    module = parse(MIXED_SOURCE, "mixed.py")
    exports = _exports("mixed", ["entry", "leaf"], [("Worker", "step")])
    failure = RuntimeError("effect collection failed")

    def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(analysis, "_add_local_callable_effect", fail)
    with pytest.raises(RuntimeError) as raised:
        analysis._compute_local_vthread_effects(
            module,
            exports,
            effect_summary=_empty_summary("mixed"),
            summary_module_name="mixed",
            summary_only=summary_only,
        )
    assert raised.value is failure


@pytest.mark.integration
def test_summary_collection_native_program_matches_cpython(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    """Execute the changed analyzer in an owned binary under every collector.

    The shared compiler fixture supplies separate host-pcc0 and native-pcc1
    cases.  Both run py_lift.parse_and_lift, the production native parser
    route, rather than the host-only parser.parse/ast.parse adapter.  CPython
    runs the identical source through that same route as a labeled reference.
    This does not force GC pressure or establish full Stage2/Stage3 or
    performance qualification.
    """
    analyzed_source = _chain_source(8) + """
def entry():
    return Worker().step()
def cycle_a():
    return cycle_b()
def cycle_b():
    cycle_a()
    return f0()
def replaced():
    return f0()
def replaced():
    return 7
class Worker:
    def step(self):
        return f0()
"""
    names = [f"f{index}" for index in range(9)] + [
        "quiet", "entry", "cycle_a", "cycle_b", "replaced",
    ]
    exports = _exports("native_summary", names, [("Worker", "step")])
    program = (
        "import sys\n"
        "from pcc.frontends.python.py_lift import parse_and_lift\n"
        "from pcc.frontends.python.codegen.vthread_effect_analysis import (\n"
        "    build_closed_world_vthread_effect_summary,\n"
        "    compute_vthread_may_park_callables,\n"
        "    write_closed_world_vthread_effect_summary,\n"
        ")\n"
        f"SOURCE = {analyzed_source!r}\n"
        f"EXPORTS = {exports!r}\n"
        "def main():\n"
        "    module = parse_and_lift(\n"
        "        SOURCE, 'native_summary.py', 'native_summary')\n"
        "    summary = build_closed_world_vthread_effect_summary(\n"
        "        module, 'native_summary', EXPORTS)\n"
        "    write_closed_world_vthread_effect_summary(sys.argv[1], summary)\n"
        "    result = compute_vthread_may_park_callables(module, EXPORTS)\n"
        "    print('|'.join(sorted(result[1])))\n"
        "    print('|'.join(sorted(result[3])))\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    source = tmp_path / "summary_program.py"
    executable = tmp_path / "summary_program"
    reference_path = tmp_path / "reference.wire"
    source.write_text(program, encoding="utf-8")
    repo = Path(__file__).resolve().parents[2]
    reference_env = dict(os.environ)
    reference_env["PYTHONPATH"] = str(repo) + os.pathsep + reference_env.get(
        "PYTHONPATH", "",
    )
    reference = subprocess.run(
        [sys.executable, str(source), str(reference_path)],
        cwd=tmp_path,
        env=reference_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert reference.returncode == 0, reference.stdout + reference.stderr
    assert reference.stderr == ""
    expected_names = sorted([f"f{index}" for index in range(9)] + [
        "entry", "cycle_a", "cycle_b",
    ])
    assert reference.stdout.splitlines() == [
        "|".join(expected_names), "Worker.step",
    ]
    python_program_compiler(
        str(source),
        str(executable),
        backend="self",
        runtime_archive=str(pcc_runtime_archive),
        ir_scaffold_mode="on",
    )
    expected_wire = reference_path.read_bytes()
    for collector in ("0", "1", "2", "3", "4"):
        actual_path = tmp_path / f"native_gc{collector}.wire"
        result = subprocess.run(
            [str(executable), str(actual_path)],
            cwd=tmp_path,
            env={**os.environ, "PCC_GC_BACKEND": collector},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (collector, result.stdout, result.stderr)
        assert result.stderr == "", collector
        assert result.stdout == reference.stdout, collector
        assert actual_path.read_bytes() == expected_wire, collector

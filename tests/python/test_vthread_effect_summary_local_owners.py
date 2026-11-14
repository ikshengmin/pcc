"""Summary collection does not retain an unused second local effect graph."""

from copy import deepcopy
from inspect import signature
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from pcc.frontends.python.codegen import vthread_effect_analysis as analysis
from pcc.frontends.python.py_lift import parse_and_lift


def _empty_summary():
    return {
        "schema": "pcc.vthread.effect-summary.v1",
        "module_name": "unit",
        "seeds": [],
        "edges": [],
        "publish": [],
    }


def _chain_fixture(length):
    rows = ["import pcc.virtual_thread as vt\n"]
    for index in range(length):
        rows.append(f"def f{index}():\n    return f{index + 1}()\n")
    rows.append(f"def f{length}():\n    vt.yield_now()\n    return 1\n")
    rows.append("class Worker:\n    def run(self):\n        return f0()\n")
    exports = {
        f"f{index}": {"kind": "function", "owning_module": "unit",
                       "export_name": f"f{index}"}
        for index in range(length + 1)
    }
    exports["Worker"] = {
        "kind": "class", "owning_module": "unit", "export_name": "Worker",
        "methods": [{"name": "run"}],
    }
    return "\n".join(rows), {"unit": exports}


@pytest.mark.parametrize("length", [16, 64, 256])
def test_summary_only_retains_no_local_graph_or_effect_entries(
    monkeypatch, tmp_path, length,
):
    # Container-entry assertions identify the changed owner. These sizes are
    # coverage, not elapsed-time, native allocation or process-RSS evidence.
    source, exports = _chain_fixture(length)
    module = parse_and_lift(source, "unit.py", "unit")
    original = analysis._add_local_callable_effect
    parameters = signature(original)
    observations = []

    def observe(*args, **kwargs):
        arguments = parameters.bind(*args, **kwargs)
        arguments.apply_defaults()
        values = arguments.arguments
        result = original(*args, **kwargs)
        observations.append((
            values["summary_only"], values["graph"], values["effects"],
            len(values["graph"]), len(values["effects"]),
        ))
        return result

    before = deepcopy(exports)
    monkeypatch.setattr(analysis, "_add_local_callable_effect", observe)
    summary = analysis.build_closed_world_vthread_effect_summary(module, "unit", exports)
    assert len(observations) == length + 2
    assert all(row[0] is True and row[3:] == (0, 0) for row in observations)
    assert all(row[1] is observations[0][1] for row in observations)
    assert all(row[2] is observations[0][2] for row in observations)
    assert observations[0][1] == {}
    assert observations[0][2] == set()
    assert len(summary["edges"]) == 2 * (length + 1)
    assert summary["seeds"] == [f"f:unit:f{length}"]
    assert summary["publish"] == [
        *[f"f:unit:f{index}" for index in range(length + 1)], "m:unit:Worker:run",
    ]

    observations.clear()
    full_summary = _empty_summary()
    result = analysis._compute_local_vthread_effects(
        module, exports, effect_summary=full_summary, summary_module_name="unit",
    )
    assert summary == full_summary
    assert exports == before
    assert all(row[0] is False for row in observations)
    assert len(observations[0][1]) == length + 2
    assert len(observations[0][2]) == length + 2
    assert result[1] == {f"f{index}" for index in range(length + 1)}
    assert result[3] == {"Worker.run"}
    summary_path = tmp_path / "summary.wire"
    full_path = tmp_path / "full.wire"
    analysis.write_closed_world_vthread_effect_summary(str(summary_path), summary)
    analysis.write_closed_world_vthread_effect_summary(str(full_path), full_summary)
    assert summary_path.read_bytes() == full_path.read_bytes()


def _callable_fixture(monkeypatch, summary_only, branch, failure_at=None, failure=None):
    fd = SimpleNamespace(name="entry")
    events = []
    graph = {}
    effects = set()
    summary = _empty_summary()
    summary["publish"].append("f:unit:entry")
    summary["seeds"].append("f:unit:entry")
    summary["edges"].extend(["f:unit:entry", "stale", "unrelated", "kept"])
    old_edges = summary["edges"]
    old_seeds = summary["seeds"]
    exports = {"provider": {"Remote": {"kind": "class"}}}

    def event(name, value):
        events.append(name)
        if name == failure_at:
            raise failure
        return value

    def scan(*_args, **_kwargs):
        return event("scan", (
            branch == "direct", ["local", "local"],
            [("provider", "first"), ("provider", "second")],
        ))

    def methods(*_args, **_kwargs):
        return event("methods", ["Worker.run", "Worker.run"])

    def threading(*_args, **_kwargs):
        return event("threading", branch == "threading")

    def export_method(call, *_args):
        return event("method:" + call, (
            "provider", "Remote", None,
            {"name": call, "may_park": branch == "method_" + call},
        ))

    def export_function(_exports, target):
        return event("function:" + target[1], branch == "function_" + target[1])

    monkeypatch.setattr(analysis, "_scan_function_effects", scan)
    monkeypatch.setattr(analysis, "_local_method_call_keys", methods)
    monkeypatch.setattr(analysis, "_function_has_threading_suspension", threading)
    monkeypatch.setattr(analysis, "_vthread_proven_export_method_call_target", export_method)
    monkeypatch.setattr(analysis, "_export_target_is_may_park", export_function)

    def run():
        # Omitting the new argument exercises the helper's unchanged default.
        options = {"summary_only": True} if summary_only else {}
        analysis._add_local_callable_effect(
            "function:entry", fd, None, {"local"}, {}, {}, {}, {}, exports,
            {}, {}, object(), graph, effects, None,
            {id(fd): ([], None, ["first", "second"], {})}, {},
            effect_summary=summary, summary_module_name="unit", **options,
        )

    return run, events, graph, effects, summary, old_edges, old_seeds


@pytest.mark.parametrize("branch", [
    "direct", "threading", "method_first", "method_second",
    "function_first", "function_second", "quiet",
])
def test_summary_only_keeps_analysis_order_and_every_early_return(monkeypatch, branch):
    outcomes = []
    for summary_only in (False, True):
        with monkeypatch.context() as patch:
            run, events, graph, effects, summary, old_edges, old_seeds = (
                _callable_fixture(patch, summary_only, branch)
            )
            run()
        assert old_edges == ["f:unit:entry", "stale", "unrelated", "kept"]
        assert summary["edges"] is not old_edges
        assert summary["seeds"] is old_seeds
        assert summary["publish"] == ["f:unit:entry"]
        assert summary["edges"][:6] == [
            "unrelated", "kept", "f:unit:entry", "f:unit:local",
            "f:unit:entry", "m:unit:Worker:run",
        ]
        if summary_only:
            assert graph == {}
            assert effects == set()
        else:
            assert graph == {"function:entry": ["function:local", "method:Worker.run"]}
            assert effects == (set() if branch == "quiet" else {"function:entry"})
        outcomes.append((events, summary))
    assert outcomes[0] == outcomes[1]
    events, summary = outcomes[0]
    expected_events = ["scan", "methods"]
    if branch != "direct":
        expected_events.append("threading")
    if branch in ("direct", "threading"):
        assert summary["seeds"] == ["f:unit:entry"]
        assert len(summary["edges"]) == 6
    else:
        assert summary["seeds"] == []
        expected_events.append("method:first")
        if branch != "method_first":
            expected_events.append("method:second")
            if branch != "method_second":
                expected_events.append("function:first")
                if branch != "function_first":
                    expected_events.append("function:second")
        expected_targets = [
            ("method:first", "m:provider:Remote:first"),
            ("method:second", "m:provider:Remote:second"),
            ("function:first", "f:provider:first"),
            ("function:second", "f:provider:second"),
        ]
        assert summary["edges"][6:] == [
            value for name, target in expected_targets if name in expected_events
            for value in ("f:unit:entry", target)
        ]
    assert events == expected_events


@pytest.mark.parametrize("failure_at", [
    "scan", "methods", "threading", "method:first", "function:first",
])
@pytest.mark.parametrize("error_type", [ValueError, MemoryError])
def test_summary_only_preserves_analysis_failure_and_partial_summary(
    monkeypatch, failure_at, error_type,
):
    failure = error_type("analysis failed")
    outcomes = []
    for summary_only in (False, True):
        with monkeypatch.context() as patch:
            run, events, graph, effects, summary, old_edges, old_seeds = (
                _callable_fixture(patch, summary_only, "quiet", failure_at, failure)
            )
            with pytest.raises(error_type) as raised:
                run()
        assert raised.value is failure
        assert events[-1] == failure_at
        assert old_edges == ["f:unit:entry", "stale", "unrelated", "kept"]
        assert summary["seeds"] is old_seeds
        if summary_only:
            assert graph == {}
            assert effects == set()
        outcomes.append((events, summary, summary["edges"] is old_edges))
    assert outcomes[0] == outcomes[1]


@pytest.mark.integration
def test_summary_owner_program_matches_same_source_cpython(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    """Owned parser/analyzer execution, not GC pressure or Stage2 qualification."""
    analyzed_source, exports = _chain_fixture(32)
    program = (
        "import sys\n"
        "from pcc.frontends.python.py_lift import parse_and_lift\n"
        "from pcc.frontends.python.codegen.vthread_effect_analysis import (\n"
        "    _compute_local_vthread_effects,\n"
        "    build_closed_world_vthread_effect_summary,\n"
        "    write_closed_world_vthread_effect_summary,\n"
        ")\n"
        f"SOURCE = {analyzed_source!r}\n"
        f"EXPORTS = {exports!r}\n"
        "def main():\n"
        "    module = parse_and_lift(SOURCE, 'unit.py', 'unit')\n"
        "    summary = build_closed_world_vthread_effect_summary(module, 'unit', EXPORTS)\n"
        "    full = {'schema': 'pcc.vthread.effect-summary.v1', 'module_name': 'unit',\n"
        "            'seeds': [], 'edges': [], 'publish': []}\n"
        "    result = _compute_local_vthread_effects(\n"
        "        module, EXPORTS, effect_summary=full, summary_module_name='unit')\n"
        "    if summary != full:\n"
        "        raise RuntimeError('summary collection differs from full local analysis')\n"
        "    write_closed_world_vthread_effect_summary(sys.argv[1], summary)\n"
        "    print('|'.join(sorted(result[1])))\n"
        "    print('|'.join(sorted(result[3])))\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    source = tmp_path / "summary_owners.py"
    executable = tmp_path / "summary_owners"
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
    assert reference.stdout.splitlines() == [
        "|".join(sorted(f"f{index}" for index in range(33))), "Worker.run",
    ]
    expected = analysis.build_closed_world_vthread_effect_summary(
        parse_and_lift(analyzed_source, "unit.py", "unit"), "unit", exports,
    )
    assert analysis.read_closed_world_vthread_effect_summary(str(reference_path)) == expected
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

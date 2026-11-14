"""Only an exclusively owned fresh summary may skip first-caller resets."""

from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pcc.frontends.python.codegen import vthread_effect_analysis as analysis
from pcc.frontends.python.py_lift import parse_and_lift


SOURCE = """import pcc.virtual_thread as vt
def first_alias():
    vt.yield_now()
def observer():
    return first_alias()
def second_alias():
    return quiet()
def repeated():
    return leaf()
def repeated():
    return quiet()
def leaf():
    vt.yield_now()
def quiet():
    return 0
class First:
    def run(self):
        vt.yield_now()
class Second:
    def run(self):
        return quiet()
"""


def _function(name):
    return {"kind": "function", "owning_module": "unit", "export_name": name}


def _exports():
    shared = _function("shared")
    shared_class = {
        "kind": "class", "owning_module": "unit", "export_name": "Shared",
        "methods": [{"name": "run", "may_park": False}],
    }
    return {"unit": {
        "first_alias": shared,
        "observer": _function("observer"),
        "second_alias": shared,
        "repeated": _function("repeated"),
        "leaf": _function("leaf"),
        "quiet": _function("quiet"),
        "First": shared_class,
        "Second": shared_class,
    }}


def _empty_summary():
    return {
        "schema": "pcc.vthread.effect-summary.v1", "module_name": "unit",
        "seeds": [], "edges": [], "publish": [],
    }


def _expected_summary():
    return {
        "schema": "pcc.vthread.effect-summary.v1", "module_name": "unit",
        "seeds": ["f:unit:leaf"],
        "edges": [
            "f:unit:observer", "f:unit:shared",
            "f:unit:shared", "f:unit:quiet",
            "f:unit:repeated", "f:unit:quiet",
            "m:unit:Shared:run", "f:unit:quiet",
        ],
        "publish": [
            "f:unit:shared", "f:unit:observer", "f:unit:repeated",
            "f:unit:leaf", "f:unit:quiet", "m:unit:Shared:run",
        ],
    }


def test_fresh_builder_resets_only_redefinitions_and_canonical_aliases(
    monkeypatch, tmp_path,
):
    module = parse_and_lift(SOURCE, "unit.py", "unit")
    exports = _exports()
    before = deepcopy(exports)
    original = analysis._vthread_summary_reset_caller
    resets = []

    def observed(summary, caller):
        resets.append(caller)
        return original(summary, caller)

    monkeypatch.setattr(analysis, "_vthread_summary_reset_caller", observed)
    summary = analysis.build_closed_world_vthread_effect_summary(module, "unit", exports)
    assert resets == ["f:unit:shared", "f:unit:repeated", "m:unit:Shared:run"]
    assert summary == _expected_summary()
    assert exports == before
    assert exports["unit"]["first_alias"] is exports["unit"]["second_alias"]
    assert exports["unit"]["First"] is exports["unit"]["Second"]
    resets.clear()
    full = _empty_summary()
    result = analysis._compute_local_vthread_effects(
        module, exports, effect_summary=full, summary_module_name="unit",
        summary_only=True,
    )
    assert result is None
    assert len(resets) == 9
    assert full == summary
    first = tmp_path / "fresh.wire"
    second = tmp_path / "full.wire"
    analysis.write_closed_world_vthread_effect_summary(str(first), summary)
    analysis.write_closed_world_vthread_effect_summary(str(second), full)
    assert first.read_bytes() == second.read_bytes()


@pytest.mark.parametrize("callers", [16, 64, 256])
def test_first_caller_resets_do_not_walk_or_copy_prior_edges(monkeypatch, callers):
    # This checks reset invocation/input size, not elapsed time or RSS.
    # Publish membership and same-caller edge scans remain unchanged.
    source = "import pcc.virtual_thread as vt\n" + "\n".join(
        f"def f{index}():\n    return leaf()\n" for index in range(callers)
    ) + "\ndef leaf():\n    vt.yield_now()\n"
    module = parse_and_lift(source, "unit.py", "unit")
    exports = {"unit": {
        name: _function(name) for name in [*(f"f{i}" for i in range(callers)), "leaf"]
    }}
    original = analysis._vthread_summary_reset_caller
    edge_slots = []

    def counted(summary, caller):
        edge_slots.append(len(summary["edges"]))
        return original(summary, caller)

    monkeypatch.setattr(analysis, "_vthread_summary_reset_caller", counted)
    actual = analysis.build_closed_world_vthread_effect_summary(module, "unit", exports)
    assert edge_slots == []
    full = _empty_summary()
    analysis._compute_local_vthread_effects(
        module, exports, effect_summary=full, summary_module_name="unit",
        summary_only=True,
    )
    assert full == actual
    assert edge_slots == [2 * index for index in range(callers + 1)]
    assert sum(edge_slots) == callers * (callers + 1)


@pytest.mark.parametrize("summary_only", [False, True])
def test_supplied_nonempty_summary_keeps_full_reset_and_external_aliases(
    monkeypatch, summary_only,
):
    module = parse_and_lift(SOURCE, "unit.py", "unit")
    summary = _empty_summary()
    seeds = ["f:unit:shared", "existing", "f:unit:shared"]
    edges = ["f:unit:shared", "old", "existing", "kept"]
    publish = ["existing"]
    summary.update(seeds=seeds, edges=edges, publish=publish)
    original = analysis._vthread_summary_reset_caller
    resets = []

    def observed(current, caller):
        resets.append(caller)
        return original(current, caller)

    monkeypatch.setattr(analysis, "_vthread_summary_reset_caller", observed)
    result = analysis._compute_local_vthread_effects(
        module, _exports(), effect_summary=summary, summary_module_name="unit",
        summary_only=summary_only,
    )
    assert (result is None) is summary_only
    assert len(resets) == 9
    expected = _expected_summary()
    expected["seeds"].insert(0, "existing")
    expected["edges"][0:0] = ["existing", "kept"]
    expected["publish"].insert(0, "existing")
    assert summary == expected
    assert summary["seeds"] is seeds
    assert summary["publish"] is publish
    assert summary["edges"] is not edges
    assert edges == ["f:unit:shared", "old", "existing", "kept"]


@pytest.mark.parametrize("summary_only", [False, True])
def test_supplied_empty_summary_still_replaces_aliased_edge_list(summary_only):
    module = parse_and_lift("def lone():\n    return 0\n", "unit.py", "unit")
    summary = _empty_summary()
    edges = summary["edges"]
    analysis._compute_local_vthread_effects(
        module, {"unit": {"lone": _function("lone")}}, effect_summary=summary,
        summary_module_name="unit", summary_only=summary_only,
    )
    assert summary["edges"] == edges == []
    assert summary["edges"] is not edges
    assert summary["publish"] == ["f:unit:lone"]


@pytest.mark.parametrize("failed_append", [1, 3, 6])
@pytest.mark.parametrize("error_type", [MemoryError, ValueError])
def test_fresh_publication_failure_preserves_error_and_partial_values(
    monkeypatch, failed_append, error_type,
):
    module = parse_and_lift(SOURCE, "unit.py", "unit")
    original = analysis._compute_local_vthread_effects
    failure = error_type("publish failed")

    class FailingPublish(list):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def append(self, value):
            self.calls += 1
            if self.calls == failed_append:
                raise failure
            super().append(value)

    outcomes = []
    for fresh in (False, True):
        captured = []

        def collect(*args, **kwargs):
            summary = kwargs["effect_summary"]
            summary["publish"] = FailingPublish()
            captured.append(summary)
            kwargs["fresh_summary"] = fresh
            return original(*args, **kwargs)

        with monkeypatch.context() as fault:
            fault.setattr(analysis, "_compute_local_vthread_effects", collect)
            with pytest.raises(error_type) as raised:
                analysis.build_closed_world_vthread_effect_summary(module, "unit", _exports())
        assert raised.value is failure
        assert captured[0]["publish"].calls == failed_append
        outcomes.append(captured[0])
    # A fresh summary is private until success. Its internal list identities
    # may differ, but all mutations before this exception retain their order.
    assert outcomes[0] == outcomes[1]


def test_fresh_repeat_reset_failure_keeps_pre_failure_summary(monkeypatch):
    module = parse_and_lift(SOURCE, "unit.py", "unit")
    original_compute = analysis._compute_local_vthread_effects
    original_reset = analysis._vthread_summary_reset_caller
    failure = RuntimeError("repeat reset failed")
    outcomes = []
    for fresh in (False, True):
        captured = []

        def collect(*args, **kwargs):
            captured.append(kwargs["effect_summary"])
            kwargs["fresh_summary"] = fresh
            return original_compute(*args, **kwargs)

        def reset(summary, caller):
            if caller == "f:unit:shared" and caller in summary["publish"]:
                raise failure
            return original_reset(summary, caller)

        with monkeypatch.context() as fault:
            fault.setattr(analysis, "_compute_local_vthread_effects", collect)
            fault.setattr(analysis, "_vthread_summary_reset_caller", reset)
            with pytest.raises(RuntimeError) as raised:
                analysis.build_closed_world_vthread_effect_summary(module, "unit", _exports())
        assert raised.value is failure
        outcomes.append(captured[0])
    assert outcomes[0] == outcomes[1]
    assert outcomes[0]["seeds"] == ["f:unit:shared"]
    assert outcomes[0]["edges"] == ["f:unit:observer", "f:unit:shared"]
    assert outcomes[0]["publish"] == ["f:unit:shared", "f:unit:observer"]


@pytest.mark.integration
def test_fresh_reset_program_matches_same_source_cpython(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    """Owned parser/analyzer execution, not GC pressure or Stage2 qualification."""
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
    source = tmp_path / "fresh_reset.py"
    executable = tmp_path / "fresh_reset"
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

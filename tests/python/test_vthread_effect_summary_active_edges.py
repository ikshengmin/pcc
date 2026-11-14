"""Stable in-place edge retirement preserves the full-scan fixed point."""

from copy import deepcopy
from inspect import getsource
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pcc.backend.self_backend_value_arena import CompilerIntArena
from pcc.frontends.python.codegen import vthread_effect_analysis as analysis
from pcc.frontends.python.codegen.vthread_effect_analysis import (
    _VTHREAD_EFFECT_PARKS,
    _VTHREAD_EFFECT_PUBLISH,
    _vthread_effect_function_key,
    _vthread_effect_method_key,
    _vthread_effect_node_id,
    read_closed_world_vthread_effect_summary,
)


def _fixture(names, seeds, pairs):
    exports = {"unit": {
        name: {"kind": "function", "owning_module": "unit",
               "export_name": name, "may_park": False}
        for name in names
    }}
    exports["unit"]["unseen"] = {"kind": "function", "may_park": False}
    summary = {
        "schema": "pcc.vthread.effect-summary.v1", "module_name": "unit",
        "seeds": ["f:unit:" + name for name in seeds],
        "publish": ["f:unit:" + name for name in names],
        "edges": ["f:unit:" + name for pair in pairs for name in pair],
    }
    return exports, summary


def _chain_fixture(length, reverse):
    names = [f"f{index}" for index in range(length + 1)]
    pairs = [(f"f{index}", f"f{index + 1}") for index in range(length)]
    if reverse:
        pairs.reverse()
    return _fixture(names, [f"f{length}"], pairs)


def _shape_fixture(shape):
    pairs_by_shape = {
        "quiet_cycle": [("a", "b"), ("b", "a")],
        "quiet_self": [("a", "a")],
        "seeded_cycle": [("a", "b"), ("b", "a")],
        "seeded_self": [("a", "a")],
        "preseeded_head": [("a", "a"), ("b", "c"), ("c", "b")],
        "progress_after_seeded_head": [("a", "a"), ("b", "a"),
                                       ("c", "d"), ("d", "c")],
        "moving_tail_chain": [("a", "b"), ("b", "c"),
                              ("c", "seed"), ("d", "d")],
        "duplicates": [("a", "seed"), ("a", "seed"), ("a", "seed"),
                       ("b", "a"), ("a", "a")],
        "mixed": [("a", "seed"), ("b", "c"), ("d", "a"), ("c", "b")],
    }
    seeds = []
    if shape in ("seeded_cycle", "seeded_self", "preseeded_head",
                 "progress_after_seeded_head"):
        seeds = ["a"]
    elif shape in ("duplicates", "mixed", "moving_tail_chain"):
        seeds = ["seed"]
    return _fixture(["seed", "a", "b", "c", "d"], seeds, pairs_by_shape[shape])


def _write_summary(tmp_path, summary):
    path = tmp_path / "summary.wire"
    analysis.write_closed_world_vthread_effect_summary(str(path), summary)
    return [str(path)]


def _observe_run(monkeypatch, annotator, paths, exports, record,
                 *, fail_write=0, failure=None):
    arena_type = CompilerIntArena
    node_id_function = analysis._vthread_effect_node_id
    record.update(arenas=[], closed=[], edge_reads=0, edge_writes=0, state_reads=0)

    class ObservedArena(arena_type):
        def __init__(self):
            super().__init__()
            self.owner_index = len(record["arenas"])
            self.saved_values = None
            self.last_read = -1
            record["arenas"].append(self)

        def get_unchecked(self, index):
            assert 0 <= index < len(self)
            if self.owner_index == 1:
                record["edge_reads"] += 1
                self.last_read = index
            else:
                record["state_reads"] += 1
            return super().get_unchecked(index)

        def set_unchecked(self, index, value):
            assert 0 <= index < len(self)
            if self.owner_index == 1:
                # Both words were read, and a moved pair writes strictly
                # behind them. An unread source pair may never be clobbered.
                assert index <= self.last_read - 2
                record["edge_writes"] += 1
                if record["edge_writes"] == fail_write:
                    raise failure
            return super().set_unchecked(index, value)

        def close(self):
            self.saved_values = self.diagnostic_values()
            record["closed"].append(self.owner_index)
            super().close()

    def node_id(node_ids, effects, key):
        record["nodes"] = node_ids
        return node_id_function(node_ids, effects, key)

    with monkeypatch.context() as patch:
        # The frozen oracle has this test module's globals; production has
        # the analyzer's globals. Both keep the same real arena operations.
        patch.setitem(annotator.__globals__, "CompilerIntArena", ObservedArena)
        patch.setitem(annotator.__globals__, "_vthread_effect_node_id", node_id)
        return annotator(paths, exports)


def _compare_runs(monkeypatch, paths, exports):
    reference = deepcopy(exports)
    old = {}
    new = {}
    expected = _observe_run(monkeypatch, _legacy_annotate, paths, reference, old)
    actual = _observe_run(
        monkeypatch, analysis.annotate_closed_world_vthread_effect_summaries,
        paths, exports, new,
    )
    assert actual == expected
    assert exports == reference
    assert new.get("nodes", {}) == old.get("nodes", {})
    for record in (old, new):
        assert len(record["arenas"]) == 2
        assert record["closed"] == [0, 1]
        assert all(len(arena) == 0 for arena in record["arenas"])
    assert new["arenas"][0].saved_values == old["arenas"][0].saved_values
    assert len(new["arenas"][1].saved_values) == len(old["arenas"][1].saved_values)
    assert new["state_reads"] <= old["state_reads"]
    return actual, old, new


@pytest.mark.parametrize("length", [16, 64, 256])
@pytest.mark.parametrize("reverse", [False, True])
def test_retired_chain_edges_are_not_rescanned(
    tmp_path, monkeypatch, length, reverse,
):
    # Exact method-call counts test the mechanism. They are neither elapsed
    # time nor native instruction/allocation/RSS measurements.
    exports, summary = _chain_fixture(length, reverse)
    paths = _write_summary(tmp_path, summary)
    count, old, new = _compare_runs(monkeypatch, paths, exports)
    assert count == (1, length + 1, length)
    old_visits = 2 * length if reverse else length * (length + 1)
    new_visits = length if reverse else length * (length + 1) // 2
    assert old["edge_reads"] == 2 * old_visits
    assert new["edge_reads"] == 2 * new_visits
    assert new["edge_writes"] == 0
    assert all(exports["unit"][f"f{index}"]["may_park"] is True
               for index in range(length + 1))
    assert exports["unit"]["unseen"]["may_park"] is False


@pytest.mark.parametrize("shape", [
    "quiet_cycle", "quiet_self", "seeded_cycle", "seeded_self",
    "preseeded_head", "progress_after_seeded_head", "moving_tail_chain",
    "duplicates", "mixed",
])
def test_active_prefix_keeps_cycles_duplicates_order_and_input_edge_counts(
    tmp_path, monkeypatch, shape,
):
    exports, summary = _shape_fixture(shape)
    paths = _write_summary(tmp_path, summary)
    count, old, new = _compare_runs(monkeypatch, paths, exports)
    assert count == (1, 5, len(summary["edges"]) // 2)
    expected_parked = {
        "quiet_cycle": set(), "quiet_self": set(),
        "seeded_cycle": {"a", "b"}, "seeded_self": {"a"},
        "preseeded_head": {"a"},
        "progress_after_seeded_head": {"a", "b"},
        "moving_tail_chain": {"seed", "a", "b", "c"},
        "duplicates": {"seed", "a", "b"}, "mixed": {"seed", "a", "d"},
    }[shape]
    assert {name for name, info in exports["unit"].items() if info["may_park"]} == expected_parked
    if shape in ("quiet_cycle", "quiet_self", "preseeded_head"):
        assert new["edge_reads"] == old["edge_reads"] == len(summary["edges"])
        assert new["state_reads"] == old["state_reads"]
        assert new["edge_writes"] == 0
        assert new["arenas"][1].saved_values == old["arenas"][1].saved_values
    if shape == "mixed":
        nodes = new["nodes"]
        assert new["arenas"][1].saved_values[:4] == [
            nodes["f:unit:b"], nodes["f:unit:c"],
            nodes["f:unit:c"], nodes["f:unit:b"],
        ]
        assert new["edge_reads"] == 12
        assert old["edge_reads"] == 16
        assert new["edge_writes"] == 4
    if shape == "progress_after_seeded_head":
        nodes = new["nodes"]
        assert new["arenas"][1].saved_values[:6] == [
            nodes["f:unit:a"], nodes["f:unit:a"],
            nodes["f:unit:c"], nodes["f:unit:d"],
            nodes["f:unit:d"], nodes["f:unit:c"],
        ]
        assert new["edge_reads"] == 14
        assert old["edge_reads"] == 16
        assert new["edge_writes"] == 4
    if shape == "moving_tail_chain":
        node = new["nodes"]["f:unit:d"]
        assert new["arenas"][1].saved_values[:2] == [node, node]
        assert new["edge_reads"] == 20
        assert old["edge_reads"] == 32
        assert new["edge_writes"] == 6


def test_empty_edge_arena_keeps_empty_result_and_metadata(monkeypatch):
    exports = {"unit": {"untouched": {"kind": "function", "may_park": False}}}
    before = deepcopy(exports)
    count, old, new = _compare_runs(monkeypatch, [], exports)
    assert count == (0, 0, 0)
    assert exports == before
    assert new["edge_reads"] == old["edge_reads"] == new["edge_writes"] == 0


@pytest.mark.parametrize("fail_write", [1, 2])
@pytest.mark.parametrize("error_type", [MemoryError, ValueError])
def test_partial_pair_move_propagates_error_without_publishing_metadata(
    tmp_path, monkeypatch, fail_write, error_type,
):
    exports, summary = _shape_fixture("mixed")
    paths = _write_summary(tmp_path, summary)
    before = deepcopy(exports)
    failure = error_type("edge move failed")
    record = {}
    with pytest.raises(error_type) as raised:
        _observe_run(
            monkeypatch, analysis.annotate_closed_world_vthread_effect_summaries,
            paths, exports, record, fail_write=fail_write, failure=failure,
        )
    assert raised.value is failure
    assert record["edge_writes"] == fail_write
    assert record["closed"] == [0, 1]
    assert all(len(arena) == 0 for arena in record["arenas"])
    assert exports == before


@pytest.mark.integration
def test_active_edges_native_program_matches_preimage_and_cpython(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    """Real merger execution, not forced-GC, Stage2/3 or performance evidence."""
    cases = [
        _chain_fixture(8, False), _shape_fixture("mixed"),
        _shape_fixture("progress_after_seeded_head"),
        _shape_fixture("moving_tail_chain"),
    ]
    program = (
        "import sys\n"
        "from pcc.backend.self_backend_value_arena import CompilerIntArena\n"
        "from pcc.frontends.python.codegen.vthread_effect_analysis import (\n"
        "    annotate_closed_world_vthread_effect_summaries,\n"
        "    write_closed_world_vthread_effect_summary,\n"
        "    read_closed_world_vthread_effect_summary,\n"
        "    _VTHREAD_EFFECT_PARKS, _VTHREAD_EFFECT_PUBLISH,\n"
        "    _vthread_effect_function_key, _vthread_effect_method_key,\n"
        "    _vthread_effect_node_id,\n"
        ")\n"
        + getsource(_legacy_annotate)
        + f"\nCASES = {cases!r}\n"
        + f"REFERENCES = {cases!r}\n"
        + "def main():\n"
        + "    for index in range(len(CASES)):\n"
        + "        exports = CASES[index][0]\n"
        + "        summary = CASES[index][1]\n"
        + "        reference = REFERENCES[index][0]\n"
        + "        path = sys.argv[1] + str(index) + '.wire'\n"
        + "        write_closed_world_vthread_effect_summary(path, summary)\n"
        + "        actual = annotate_closed_world_vthread_effect_summaries([path], exports)\n"
        + "        expected = _legacy_annotate([path], reference)\n"
        + "        if actual != expected or exports != reference:\n"
        + "            raise RuntimeError('active edge solver differs from full scan')\n"
        + "        print(actual[0], actual[1], actual[2])\n"
        + "    print(CASES[0][0]['unit']['f0']['may_park'])\n"
        + "    mixed = CASES[1][0]['unit']\n"
        + "    print(mixed['a']['may_park'], mixed['b']['may_park'], mixed['d']['may_park'])\n"
        + "if __name__ == '__main__':\n"
        + "    main()\n"
    )
    source = tmp_path / "active_edges.py"
    executable = tmp_path / "active_edges"
    source.write_text(program, encoding="utf-8")
    repo = Path(__file__).resolve().parents[2]
    reference_env = dict(os.environ)
    reference_env["PYTHONPATH"] = str(repo) + os.pathsep + reference_env.get("PYTHONPATH", "")
    reference = subprocess.run(
        [sys.executable, str(source), str(tmp_path / "reference_")],
        cwd=tmp_path, env=reference_env, capture_output=True, text=True, timeout=30,
    )
    assert reference.returncode == 0, reference.stdout + reference.stderr
    assert reference.stderr == ""
    assert reference.stdout == "1 9 8\n1 5 4\n1 5 4\n1 5 4\nTrue\nTrue False True\n"
    expected_wires = [(tmp_path / f"reference_{index}.wire").read_bytes()
                      for index in range(len(cases))]
    python_program_compiler(
        str(source), str(executable), backend="self",
        runtime_archive=str(pcc_runtime_archive), ir_scaffold_mode="on",
    )
    for collector in ("0", "1", "2", "3", "4"):
        prefix = tmp_path / f"native_{collector}_"
        result = subprocess.run(
            [str(executable), str(prefix)], cwd=tmp_path,
            env={**os.environ, "PCC_GC_BACKEND": collector},
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, (collector, result.stdout, result.stderr)
        assert result.stderr == "", collector
        assert result.stdout == reference.stdout, collector
        for index in range(len(cases)):
            assert Path(str(prefix) + str(index) + ".wire").read_bytes() == expected_wires[index]


# Frozen d26fb49 implementation; only the function name differs.
def _legacy_annotate(
    summary_paths: list[str],
    native_exports: dict,
) -> tuple[int, int, int]:
    """Merge module wires and solve their dense-ID fixed point in the parent.

    Each existing effect slot also records whether its key must be published.
    Keep that flag separate from the parking bit: a published quiet node must
    not seed propagation, and an unpublished parking node must not rewrite an
    export.  This needs no second dictionary owning the same canonical keys.
    """
    node_ids: dict[str, int] = {}
    effects = CompilerIntArena()
    edge_pairs = CompilerIntArena()
    try:
        for module_name, exports in native_exports.items():
            for export_name, info in exports.items():
                if not isinstance(info, dict):
                    continue
                if info.get("kind") == "function":
                    if bool(info.get("may_park", False)):
                        key = _vthread_effect_function_key(
                            module_name, export_name, native_exports
                        )
                        node_id = _vthread_effect_node_id(node_ids, effects, key)
                        effects.set_unchecked(node_id, _VTHREAD_EFFECT_PARKS)
                    continue
                if info.get("kind") != "class":
                    continue
                for method_info in info.get("methods", ()):
                    if not isinstance(method_info, dict) or not bool(
                        method_info.get("may_park", False)
                    ):
                        continue
                    key = _vthread_effect_method_key(
                        module_name,
                        export_name,
                        str(method_info.get("name", "")),
                        native_exports,
                    )
                    node_id = _vthread_effect_node_id(node_ids, effects, key)
                    effects.set_unchecked(node_id, _VTHREAD_EFFECT_PARKS)

        for path in summary_paths:
            summary = read_closed_world_vthread_effect_summary(path)
            for key in summary["seeds"]:
                node_id = _vthread_effect_node_id(node_ids, effects, key)
                effects.set_unchecked(
                    node_id, effects.get_unchecked(node_id) | _VTHREAD_EFFECT_PARKS
                )
            for key in summary["publish"]:
                node_id = _vthread_effect_node_id(node_ids, effects, key)
                effects.set_unchecked(
                    node_id, effects.get_unchecked(node_id) | _VTHREAD_EFFECT_PUBLISH
                )
            edges = summary["edges"]
            index = 0
            while index < len(edges):
                edge_pairs.append2(
                    _vthread_effect_node_id(node_ids, effects, edges[index]),
                    _vthread_effect_node_id(
                        node_ids, effects, edges[index + 1]
                    ),
                )
                index += 2

            # Needed keys and edge IDs now have their node/arena owners.
            # Drop only these local aliases, without mutating the wire lists,
            # before reading the next payload or entering the fixed point.
            edges = None
            summary = None

        changed = True
        while changed:
            changed = False
            index = 0
            while index < len(edge_pairs):
                caller = edge_pairs.get_unchecked(index)
                callee = edge_pairs.get_unchecked(index + 1)
                if effects.get_unchecked(callee) & _VTHREAD_EFFECT_PARKS:
                    caller_state = effects.get_unchecked(caller)
                    if not (caller_state & _VTHREAD_EFFECT_PARKS):
                        effects.set_unchecked(
                            caller, caller_state | _VTHREAD_EFFECT_PARKS
                        )
                        changed = True
                index += 2

        for module_name, exports in native_exports.items():
            for export_name, info in exports.items():
                if not isinstance(info, dict):
                    continue
                if info.get("kind") == "function":
                    key = _vthread_effect_function_key(
                        module_name, export_name, native_exports
                    )
                    node_id = node_ids.get(key, -1)
                    if node_id >= 0:
                        state = effects.get_unchecked(node_id)
                        if state & _VTHREAD_EFFECT_PUBLISH:
                            info["may_park"] = bool(state & _VTHREAD_EFFECT_PARKS)
                    continue
                if info.get("kind") != "class":
                    continue
                for method_info in info.get("methods", ()):
                    if not isinstance(method_info, dict):
                        continue
                    key = _vthread_effect_method_key(
                        module_name,
                        export_name,
                        str(method_info.get("name", "")),
                        native_exports,
                    )
                    node_id = node_ids.get(key, -1)
                    if node_id >= 0:
                        state = effects.get_unchecked(node_id)
                        if state & _VTHREAD_EFFECT_PUBLISH:
                            method_info["may_park"] = bool(
                                state & _VTHREAD_EFFECT_PARKS
                            )
        return len(summary_paths), len(effects), len(edge_pairs) // 2
    finally:
        effects.close()
        edge_pairs.close()

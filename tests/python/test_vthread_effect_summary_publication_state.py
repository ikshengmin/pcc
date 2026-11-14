"""Dense summary states retain publication independently of parking effects."""

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
    _vthread_effect_function_key,
    _vthread_effect_method_key,
    _vthread_effect_node_id,
    read_closed_world_vthread_effect_summary,
)


def _function(module, name, **metadata):
    return {"kind": "function", "owning_module": module, "export_name": name, **metadata}


def _fixture(quiet_seed):
    exports = {
        "unit": {
            name: _function("unit", name, may_park=False)
            for name in ("entry", "middle", "quiet", "unpublished", "cycle_a", "cycle_b")
        },
        "provider": {"leaf": _function("provider", "leaf", may_park=True)},
    }
    exports["unit"].update({
        "preexisting": _function("unit", "preexisting", may_park=True),
        "seed_only": _function("unit", "seed_only"),
        "quiet_unpublished": _function("unit", "quiet_unpublished"),
        "unseen": _function("unit", "unseen"),
        "constant": 17,
        "value": {"kind": "value"},
        "Worker": {
            "kind": "class", "owning_module": "unit", "export_name": "Worker",
            "methods": [{"name": "run", "may_park": False},
                        {"name": "quiet", "may_park": False},
                        {"name": "unseen"},
                        {"name": "unpublished", "may_park": False},
                        {"name": "preexisting", "may_park": True}, "opaque"],
        },
    })
    # Both shared metadata identity and canonical aliasing must survive.
    exports["facade"] = {
        "entry_alias": exports["unit"]["entry"],
        "WorkerAlias": exports["unit"]["Worker"],
    }
    summaries = [{
        "schema": "pcc.vthread.effect-summary.v1", "module_name": "first",
        "seeds": ["f:unit:seed_only", "f:unit:seed_only"],
        "publish": [
            "f:unit:entry", "f:unit:middle", "f:unit:quiet",
            "m:unit:Worker:run", "m:unit:Worker:quiet", "f:unit:entry",
            "f:ghost:published", "f:unit:cycle_a", "f:unit:cycle_b",
        ],
        "edges": [
            "f:unit:entry", "f:unit:middle",
            "f:unit:middle", "f:provider:leaf",
            "f:unit:unpublished", "f:provider:leaf",
            "m:unit:Worker:run", "f:unit:entry",
            "m:unit:Worker:quiet", "f:unit:quiet",
            "f:unit:cycle_a", "f:unit:cycle_b",
            "f:unit:cycle_b", "f:unit:cycle_a",
            "f:unit:quiet_unpublished", "f:unit:cycle_a",
            "m:unit:Worker:unpublished", "f:provider:leaf",
        ],
    }, {
        "schema": "pcc.vthread.effect-summary.v1", "module_name": "second",
        "seeds": ["f:unit:quiet", "f:unit:quiet"] if quiet_seed else [],
        "publish": ["f:provider:leaf", "f:provider:leaf"], "edges": [],
    }]
    return exports, summaries


def _write_summaries(tmp_path, summaries):
    paths = []
    for index, summary in enumerate(summaries):
        path = tmp_path / f"summary_{index}.wire"
        analysis.write_closed_world_vthread_effect_summary(str(path), summary)
        paths.append(str(path))
    return paths


@pytest.mark.parametrize("quiet_seed", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_publication_state_preserves_flags_order_aliases_and_two_arenas(
    tmp_path, monkeypatch, quiet_seed, reverse,
):
    exports, summaries = _fixture(quiet_seed)
    if reverse:
        summaries.reverse()
    paths = _write_summaries(tmp_path, summaries)
    reference = deepcopy(exports)
    expected_count = _legacy_annotate(paths, reference)
    arenas = []
    dictionaries = []
    original_node_id = analysis._vthread_effect_node_id

    class RecordedArena(CompilerIntArena):
        def __init__(self):
            super().__init__()
            self.saved_values = None
            self.close_calls = 0
            arenas.append(self)

        def get_unchecked(self, index):
            assert 0 <= index < len(self)
            return super().get_unchecked(index)

        def set_unchecked(self, index, value):
            assert 0 <= index < len(self)
            return super().set_unchecked(index, value)

        def close(self):
            self.saved_values = self.diagnostic_values()
            self.close_calls += 1
            super().close()

    def record_node_id(node_ids, effects, key):
        dictionaries.append(node_ids)
        return original_node_id(node_ids, effects, key)

    monkeypatch.setattr(analysis, "CompilerIntArena", RecordedArena)
    monkeypatch.setattr(analysis, "_vthread_effect_node_id", record_node_id)
    actual_count = analysis.annotate_closed_world_vthread_effect_summaries(paths, exports)
    assert actual_count == expected_count == (2, 15, 9)
    assert exports == reference
    assert exports["facade"]["entry_alias"] is exports["unit"]["entry"]
    assert exports["facade"]["WorkerAlias"] is exports["unit"]["Worker"]
    assert exports["unit"]["entry"]["may_park"] is True
    assert exports["unit"]["quiet"]["may_park"] is quiet_seed
    assert exports["unit"]["unpublished"]["may_park"] is False
    assert exports["unit"]["cycle_a"]["may_park"] is False
    assert exports["unit"]["cycle_b"]["may_park"] is False
    assert exports["unit"]["preexisting"]["may_park"] is True
    for name in ("seed_only", "quiet_unpublished", "unseen"):
        assert "may_park" not in exports["unit"][name]
    assert exports["unit"]["Worker"]["methods"][:3] == [
        {"name": "run", "may_park": True},
        {"name": "quiet", "may_park": quiet_seed}, {"name": "unseen"},
    ]
    assert exports["unit"]["Worker"]["methods"][3:5] == [
        {"name": "unpublished", "may_park": False},
        {"name": "preexisting", "may_park": True},
    ]
    assert len(arenas) == 2
    assert all(arena.close_calls == 1 and len(arena) == 0 for arena in arenas)
    assert all(mapping is dictionaries[0] for mapping in dictionaries)
    state_by_key = {key: arenas[0].saved_values[index]
                    for key, index in dictionaries[0].items()}
    assert state_by_key == {
        "f:provider:leaf": 3, "f:unit:preexisting": 1, "f:unit:seed_only": 1,
        "f:unit:entry": 3, "f:unit:middle": 3,
        "f:unit:quiet": 3 if quiet_seed else 2,
        "m:unit:Worker:run": 3, "m:unit:Worker:quiet": 3 if quiet_seed else 2,
        "f:ghost:published": 2, "f:unit:cycle_a": 2, "f:unit:cycle_b": 2,
        "f:unit:unpublished": 1, "f:unit:quiet_unpublished": 0,
        "m:unit:Worker:unpublished": 1, "m:unit:Worker:preexisting": 1,
    }
    assert len(arenas[1].saved_values) == 18


@pytest.mark.parametrize("length", [16, 64, 256])
def test_publication_flags_survive_long_chain_propagation(tmp_path, length):
    names = [f"f{index}" for index in range(length + 1)]
    exports = {"chain": {name: _function("chain", name) for name in names}}
    summary = {
        "schema": "pcc.vthread.effect-summary.v1", "module_name": "chain",
        "seeds": [f"f:chain:f{length}"],
        "publish": [f"f:chain:{name}" for name in names],
        "edges": [value for index in range(length)
                  for value in (f"f:chain:f{index}", f"f:chain:f{index + 1}")],
    }
    paths = _write_summaries(tmp_path, [summary])
    reference = deepcopy(exports)
    count = analysis.annotate_closed_world_vthread_effect_summaries(paths, exports)
    assert count == _legacy_annotate(paths, reference) == (1, length + 1, length)
    assert exports == reference
    assert all(exports["chain"][name]["may_park"] is True for name in names)


def test_no_summaries_keeps_unpublished_export_metadata():
    exports, _summaries = _fixture(False)
    before = deepcopy(exports)
    assert analysis.annotate_closed_world_vthread_effect_summaries([], exports) == (0, 3, 0)
    assert exports == before


@pytest.mark.parametrize("failure_at", ["read_first", "read_second", "publish"])
@pytest.mark.parametrize("error_type", [ValueError, MemoryError])
def test_publication_state_preserves_failure_identity_and_closes_arenas(
    tmp_path, monkeypatch, failure_at, error_type,
):
    exports, summaries = _fixture(True)
    paths = _write_summaries(tmp_path, summaries)
    before = deepcopy(exports)
    failure = error_type("summary merge failed")
    arenas = []
    closed = []
    reads = []
    reader = analysis.read_closed_world_vthread_effect_summary

    class RecordedArena(CompilerIntArena):
        def __init__(self):
            super().__init__()
            self.owner_index = len(arenas)
            arenas.append(self)

        def close(self):
            closed.append(self.owner_index)
            super().close()

    class FailingInfo(dict):
        def __setitem__(self, key, value):
            if key == "may_park":
                raise failure
            super().__setitem__(key, value)

    def read(path):
        reads.append(path)
        if (failure_at == "read_first" and len(reads) == 1
                or failure_at == "read_second" and len(reads) == 2):
            raise failure
        return reader(path)

    if failure_at == "publish":
        bad = FailingInfo(exports["unit"]["entry"])
        exports["unit"]["entry"] = bad
        exports["facade"]["entry_alias"] = bad
    monkeypatch.setattr(analysis, "CompilerIntArena", RecordedArena)
    monkeypatch.setattr(analysis, "read_closed_world_vthread_effect_summary", read)
    with pytest.raises(error_type) as raised:
        analysis.annotate_closed_world_vthread_effect_summaries(paths, exports)
    assert raised.value is failure
    assert exports == before
    assert exports["facade"]["entry_alias"] is exports["unit"]["entry"]
    assert closed == [0, 1]
    assert len(arenas) == 2 and all(len(arena) == 0 for arena in arenas)
    assert len(reads) == (1 if failure_at == "read_first" else 2)


@pytest.mark.integration
def test_publication_state_native_program_matches_cpython_and_preimage(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    """Execute the real wire merger, not a parser shortcut or a memory probe."""
    exports, summaries = _fixture(True)
    program = (
        "import sys\n"
        "from pcc.backend.self_backend_value_arena import CompilerIntArena\n"
        "from pcc.frontends.python.codegen.vthread_effect_analysis import (\n"
        "    annotate_closed_world_vthread_effect_summaries,\n"
        "    write_closed_world_vthread_effect_summary,\n"
        "    read_closed_world_vthread_effect_summary,\n"
        "    _vthread_effect_function_key, _vthread_effect_method_key,\n"
        "    _vthread_effect_node_id,\n"
        ")\n"
        + getsource(_legacy_annotate)
        + f"\nSUMMARIES = {summaries!r}\n"
        + "def main():\n"
        + f"    exports = {exports!r}\n"
        + f"    reference = {exports!r}\n"
        + "    exports['facade']['entry_alias'] = exports['unit']['entry']\n"
        + "    reference['facade']['entry_alias'] = reference['unit']['entry']\n"
        + "    exports['facade']['WorkerAlias'] = exports['unit']['Worker']\n"
        + "    reference['facade']['WorkerAlias'] = reference['unit']['Worker']\n"
        + "    paths = []\n"
        + "    index = 0\n"
        + "    for summary in SUMMARIES:\n"
        + "        path = sys.argv[1] + str(index) + '.wire'\n"
        + "        write_closed_world_vthread_effect_summary(path, summary)\n"
        + "        paths.append(path)\n"
        + "        index += 1\n"
        + "    result = annotate_closed_world_vthread_effect_summaries(paths, exports)\n"
        + "    expected = _legacy_annotate(paths, reference)\n"
        + "    if result != expected or exports != reference:\n"
        + "        raise RuntimeError('dense state merger differs from the preimage')\n"
        + "    if exports['facade']['entry_alias'] is not exports['unit']['entry']:\n"
        + "        raise RuntimeError('export metadata alias was replaced')\n"
        + "    if result != (2, 15, 9):\n"
        + "        raise RuntimeError('summary counts differ')\n"
        + "    if not exports['unit']['entry']['may_park']:\n"
        + "        raise RuntimeError('published caller lost its parking bit')\n"
        + "    if exports['unit']['cycle_a']['may_park']:\n"
        + "        raise RuntimeError('publication seeded a quiet cycle')\n"
        + "    if exports['unit']['unpublished']['may_park']:\n"
        + "        raise RuntimeError('unpublished metadata was changed')\n"
        + "    if exports['unit']['Worker']['methods'][3]['may_park']:\n"
        + "        raise RuntimeError('unpublished method metadata was changed')\n"
        + "    if not exports['unit']['Worker']['methods'][1]['may_park']:\n"
        + "        raise RuntimeError('method publication bit was lost')\n"
        + "    print(result[0], result[1], result[2])\n"
        + "    print(exports['unit']['quiet']['may_park'])\n"
        + "if __name__ == '__main__':\n"
        + "    main()\n"
    )
    source = tmp_path / "publication_state.py"
    executable = tmp_path / "publication_state"
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
    assert reference.stdout == "2 15 9\nTrue\n"
    expected_wires = [(tmp_path / f"reference_{index}.wire").read_bytes() for index in range(2)]
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
        for index in range(2):
            assert Path(str(prefix) + str(index) + ".wire").read_bytes() == expected_wires[index]


# Frozen 8c3c85c implementation below; only its function name differs.
# This is an executable reference for the change, not another production path.
def _legacy_annotate(
    summary_paths: list[str],
    native_exports: dict,
) -> tuple[int, int, int]:
    """Merge module wires and solve their dense-ID fixed point in the parent."""
    node_ids: dict[str, int] = {}
    publish_keys: dict[str, bool] = {}
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
                        effects.set_unchecked(node_id, 1)
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
                    effects.set_unchecked(node_id, 1)

        for path in summary_paths:
            summary = read_closed_world_vthread_effect_summary(path)
            for key in summary["seeds"]:
                node_id = _vthread_effect_node_id(node_ids, effects, key)
                effects.set_unchecked(node_id, 1)
            for key in summary["publish"]:
                publish_keys[key] = True
                _vthread_effect_node_id(node_ids, effects, key)
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

        changed = True
        while changed:
            changed = False
            index = 0
            while index < len(edge_pairs):
                caller = edge_pairs.get_unchecked(index)
                callee = edge_pairs.get_unchecked(index + 1)
                if effects.get_unchecked(callee) and not effects.get_unchecked(
                    caller
                ):
                    effects.set_unchecked(caller, 1)
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
                    if publish_keys.get(key, False):
                        info["may_park"] = bool(
                            effects.get_unchecked(node_ids[key])
                        )
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
                    if publish_keys.get(key, False):
                        method_info["may_park"] = bool(
                            effects.get_unchecked(node_ids[key])
                        )
        return len(summary_paths), len(effects), len(edge_pairs) // 2
    finally:
        effects.close()
        edge_pairs.close()

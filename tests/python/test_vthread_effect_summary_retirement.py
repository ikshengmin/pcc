"""Consumed wire containers stop being local owners before the next phase."""

from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
import weakref

import pytest

from pcc.frontends.python.codegen import vthread_effect_analysis as analysis


class _ObservedSummary(dict):
    pass


class _ObservedList(list):
    pass


def _fixture(count):
    exports = {
        "unit": {f"f{index}": {"kind": "function", "may_park": False}
                 for index in range(count)},
        "provider": {"leaf": {"kind": "function", "may_park": True}},
    }
    exports["unit"]["quiet"] = {"kind": "function", "may_park": False}
    summaries = []
    for index in range(count):
        caller = f"f:unit:f{index}"
        callee = f"f:unit:f{index + 1}" if index + 1 < count else "f:provider:leaf"
        summaries.append({
            "schema": "pcc.vthread.effect-summary.v1", "module_name": f"part_{index}",
            "seeds": [], "publish": [caller], "edges": [caller, callee],
        })
    return exports, summaries


def _write_summaries(tmp_path, summaries):
    paths = []
    for index, summary in enumerate(summaries):
        path = tmp_path / f"summary_{index}.wire"
        analysis.write_closed_world_vthread_effect_summary(str(path), summary)
        paths.append(str(path))
    return paths


def _observe_payload(payload, references):
    result = _ObservedSummary(payload)
    for name in ("seeds", "edges", "publish"):
        result[name] = _ObservedList(payload[name])
    references.append((weakref.ref(result), *(weakref.ref(result[name])
                       for name in ("seeds", "edges", "publish"))))
    return result


def _assert_retired(references):
    assert all(reference() is None for group in references for reference in group)


@pytest.mark.parametrize("count", [0, 1, 3, 16])
def test_payload_owners_retire_before_next_read_and_first_solver_access(
    tmp_path, monkeypatch, count,
):
    # Weak references observe only the CPython ownership boundary. This is
    # neither a native-GC collection assertion nor a process-memory measure.
    exports, summaries = _fixture(count)
    paths = _write_summaries(tmp_path, summaries)
    reader = analysis.read_closed_world_vthread_effect_summary
    arena_type = analysis.CompilerIntArena
    references = []
    reads = []
    arenas = []
    solver_access = []

    def read(path):
        _assert_retired(references)
        reads.append(path)
        return _observe_payload(reader(path), references)

    class ObservedArena(arena_type):
        def __init__(self):
            super().__init__()
            self.owner_index = len(arenas)
            self.close_calls = 0
            arenas.append(self)

        def __len__(self):
            # Ingestion appends without querying edge_pairs.__len__. Its
            # first length query starts fixed-point processing, even for an
            # empty edge arena. effects has owner_index == 0.
            if self.owner_index == 1 and not solver_access:
                _assert_retired(references)
                solver_access.append(True)
            return super().__len__()

        def close(self):
            self.close_calls += 1
            super().close()

    monkeypatch.setattr(analysis, "read_closed_world_vthread_effect_summary", read)
    monkeypatch.setattr(analysis, "CompilerIntArena", ObservedArena)
    assert analysis.annotate_closed_world_vthread_effect_summaries(paths, exports) == (
        count, count + 1, count,
    )
    assert reads == paths
    assert len(references) == count
    _assert_retired(references)
    assert solver_access == [True]
    assert len(arenas) == 2 and all(arena.close_calls == 1 for arena in arenas)
    assert all(exports["unit"][f"f{index}"]["may_park"] is True for index in range(count))
    assert exports["unit"]["quiet"]["may_park"] is False


def test_retiring_local_aliases_does_not_clear_borrowed_payload(monkeypatch):
    exports, summaries = _fixture(1)
    borrowed = summaries[0]
    # Exercise repeated input identity and a seeds/publish alias. Dropping
    # locals must never mutate either the container or any borrowed list.
    borrowed["seeds"] = borrowed["publish"]
    before = deepcopy(borrowed)
    edges_alias = borrowed["edges"]
    shared_alias = borrowed["seeds"]

    def read(_path):
        assert borrowed == before
        return borrowed

    monkeypatch.setattr(analysis, "read_closed_world_vthread_effect_summary", read)
    assert analysis.annotate_closed_world_vthread_effect_summaries(
        ["first", "same_again"], exports,
    ) == (2, 2, 2)
    assert borrowed == before
    assert borrowed["edges"] is edges_alias
    assert borrowed["seeds"] is borrowed["publish"] is shared_alias
    assert exports["unit"]["f0"]["may_park"] is True


@pytest.mark.parametrize("failure_at", ["read_first", "read_second", "append"])
@pytest.mark.parametrize("error_type", [ValueError, MemoryError])
def test_retirement_keeps_error_identity_borrowed_values_and_arena_cleanup(
    tmp_path, monkeypatch, failure_at, error_type,
):
    exports, summaries = _fixture(2)
    paths = _write_summaries(tmp_path, summaries)
    before = deepcopy(exports)
    borrowed = deepcopy(summaries[0])
    borrowed_before = deepcopy(borrowed)
    failure = error_type("summary consumption failed")
    reader = analysis.read_closed_world_vthread_effect_summary
    arena_type = analysis.CompilerIntArena
    references = []
    reads = []
    arenas = []
    closed = []

    def read(path):
        _assert_retired(references)
        reads.append(path)
        if (failure_at == "read_first" and len(reads) == 1
                or failure_at == "read_second" and len(reads) == 2):
            raise failure
        if failure_at == "append":
            return borrowed
        return _observe_payload(reader(path), references)

    class FailingArena(arena_type):
        def __init__(self):
            super().__init__()
            self.owner_index = len(arenas)
            arenas.append(self)

        def append2(self, first, second):
            if failure_at == "append":
                raise failure
            super().append2(first, second)

        def close(self):
            closed.append(self.owner_index)
            super().close()

    monkeypatch.setattr(analysis, "read_closed_world_vthread_effect_summary", read)
    monkeypatch.setattr(analysis, "CompilerIntArena", FailingArena)
    with pytest.raises(error_type) as raised:
        analysis.annotate_closed_world_vthread_effect_summaries(paths, exports)
    assert raised.value is failure
    assert exports == before
    assert borrowed == borrowed_before
    assert closed == [0, 1]
    assert len(arenas) == 2 and all(len(arena) == 0 for arena in arenas)
    assert len(reads) == (2 if failure_at == "read_second" else 1)
    _assert_retired(references)


@pytest.mark.integration
def test_retirement_native_wire_merger_matches_same_source_cpython(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    """Real native wire ingestion, not a native liveness or memory assertion."""
    exports, summaries = _fixture(4)
    program = (
        "import sys\n"
        "from pcc.frontends.python.codegen.vthread_effect_analysis import (\n"
        "    annotate_closed_world_vthread_effect_summaries,\n"
        "    write_closed_world_vthread_effect_summary,\n"
        ")\n"
        f"EXPORTS = {exports!r}\n"
        f"SUMMARIES = {summaries!r}\n"
        "def main():\n"
        "    paths = []\n"
        "    index = 0\n"
        "    for summary in SUMMARIES:\n"
        "        path = sys.argv[1] + str(index) + '.wire'\n"
        "        write_closed_world_vthread_effect_summary(path, summary)\n"
        "        paths.append(path)\n"
        "        index += 1\n"
        "    result = annotate_closed_world_vthread_effect_summaries(paths, EXPORTS)\n"
        "    if result != (4, 5, 4):\n"
        "        raise RuntimeError('summary counts changed')\n"
        "    for index in range(4):\n"
        "        if not EXPORTS['unit']['f' + str(index)]['may_park']:\n"
        "            raise RuntimeError('retirement lost a needed edge or seed')\n"
        "    if EXPORTS['unit']['quiet']['may_park']:\n"
        "        raise RuntimeError('unpublished metadata changed')\n"
        "    print(result[0], result[1], result[2])\n"
        "    print(EXPORTS['unit']['f0']['may_park'])\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    source = tmp_path / "summary_retirement.py"
    executable = tmp_path / "summary_retirement"
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
    assert reference.stdout == "4 5 4\nTrue\n"
    expected_wires = [(tmp_path / f"reference_{index}.wire").read_bytes() for index in range(4)]
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
        for index in range(4):
            assert Path(str(prefix) + str(index) + ".wire").read_bytes() == expected_wires[index]

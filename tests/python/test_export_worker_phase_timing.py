"""Disjoint optional trace boundaries for the actual export worker."""
from __future__ import annotations

import re

import pytest

from pcc.frontends.python import pipeline
from pcc.frontends.python import pipeline_frontend_worker_execution as execution
from pcc.frontends.python import pipeline_profile


def _manifest(tmp_path, *, ast=True):
    out = tmp_path / "exports"
    out.mkdir(exist_ok=True)
    ast_dir = tmp_path / "ast"
    ast_dir.mkdir(exist_ok=True)
    return {
        "result_path": str(tmp_path / "worker.tsv"),
        "ir_dir": str(out),
        "ast_dir": str(ast_dir) if ast else "",
        "src_paths": [str(tmp_path / "a.py"), str(tmp_path / "b.py")],
        "module_names": ["pkg.a", "pkg.b"],
        "assigned_indices": [0, 1],
    }


def _callbacks(events, *, owned, fail_at=None):
    parsed = [object(), object()]
    exports = {"pkg.a": {"x": {"kind": "function"}}, "pkg.b": {}}
    deps = (("pkg.a", ("pkg.b",)), ("pkg.b", ()))
    uses = (("pkg.a", "x"),)
    edges = [("pkg.b", "pkg.a", "x", "x", False)]

    def call(name):
        events.append(name)
        if fail_at == name:
            raise ValueError("original export failure")

    def context(srcs, names, **kwargs):
        call("context")
        assert names == ["pkg.a", "pkg.b"]
        assert len(srcs) == 2 and kwargs == {
            "profile": None, "lift_indices": None, "merge_exports": False,
            "allow_local_int_abi_proofs": False,
        }
        return parsed, exports, {}

    def ast(path, module):
        call("ast_write")
        assert module is parsed[0 if path.endswith("module_0.json") else 1]

    def reexport(modules, names, srcs, known):
        call("reexport_scan")
        assert modules is parsed and names == known
        return edges

    def owned_metadata(modules, names, srcs, known, actual_exports):
        call("owned_metadata")
        assert modules is parsed and actual_exports is exports and names == known
        return (deps, uses) if owned else None

    def dependencies(modules, names, srcs, known):
        call("dependencies")
        assert modules is parsed and names == known
        return deps

    def mark(modules, names, srcs, actual_exports, *, known_module_names):
        call("function_objects")
        assert modules is parsed and actual_exports is exports and names == known_module_names
        return uses

    def wire(path, actual, derived, *, function_object_uses):
        call("native_wire")
        assert actual is exports and derived == {} and function_object_uses is uses

    def reexport_wire(path, actual, *, module_dependencies):
        call("reexport_wire")
        assert actual is edges and module_dependencies is deps

    return dict(
        build_closed_world_context=context,
        write_ast_wire=ast,
        closed_world_reexport_edges=reexport,
        closed_world_module_dependencies=dependencies,
        mark_function_object_exports=mark,
        write_native_exports_wire=wire,
        write_reexport_edges_wire=reexport_wire,
        owned_export_metadata=owned_metadata,
    )


def _phases(text):
    return [(name, int(start), int(end), int(elapsed))
            for name, start, end, elapsed in re.findall(
                r"pcc pipeline phase pid=\d+ name=(\S+) start_ms=(\d+) "
                r"end_ms=(\d+) elapsed_ms=(\d+)", text)]


@pytest.mark.parametrize("owned, ast", [(True, True), (False, True), (False, False)])
def test_export_worker_nonoverlapping_trace(monkeypatch, capsys, tmp_path, owned, ast):
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "1")
    ticks = iter(range(100, 1000, 5))
    monkeypatch.setattr(pipeline_profile, "profile_now_ms", lambda: next(ticks))
    calls = []
    manifest = _manifest(tmp_path, ast=ast)
    assert execution._run_export_worker(
        manifest, worker_timing_enabled=lambda: True,
        **_callbacks(calls, owned=owned)) == 0
    expected = ["ast_write"] if ast else []
    expected += ["reexport_scan", "owned_metadata"]
    if not owned:
        expected += ["dependencies", "function_objects"]
    expected += ["native_wire", "reexport_wire"]
    assert calls == ["context"] + (["ast_write"] if ast else []) + expected
    phases = _phases(capsys.readouterr().err)
    assert [name for name, *_ in phases] == ["export_worker_" + name for name in expected]
    assert all(end > start and elapsed == end - start for _, start, end, elapsed in phases)
    assert all(a[2] <= b[1] for a, b in zip(phases, phases[1:]))
    assert len((tmp_path / "worker.tsv").read_text().strip().split("\t")) == 4


def test_export_worker_disabled_does_not_touch_trace(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("PCC_PY_FRONTEND_WORKER_TIMING", raising=False)
    def unexpected(*args, **kwargs):
        raise AssertionError("disabled export trace invoked")
    monkeypatch.setattr(pipeline_profile, "profile_begin", unexpected)
    monkeypatch.setattr(pipeline_profile, "profile_end", unexpected)
    monkeypatch.setattr(pipeline_profile, "profile_now_ms", unexpected)
    monkeypatch.setattr(execution.time, "monotonic", unexpected)
    calls = []
    assert execution._run_export_worker(
        _manifest(tmp_path), worker_timing_enabled=lambda: False,
        **_callbacks(calls, owned=False)) == 0
    assert calls == ["context", "ast_write", "ast_write", "reexport_scan",
                     "owned_metadata", "dependencies", "function_objects",
                     "native_wire", "reexport_wire"]
    assert capsys.readouterr() == ("", "")
    assert len((tmp_path / "worker.tsv").read_text().strip().split("\t")) == 3


def test_export_worker_failed_phase_keeps_original_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "1")
    ticks = iter(range(100, 1000, 5))
    monkeypatch.setattr(pipeline_profile, "profile_now_ms", lambda: next(ticks))
    calls = []
    with pytest.raises(ValueError, match="original export failure"):
        execution._run_export_worker(
            _manifest(tmp_path), worker_timing_enabled=lambda: True,
            **_callbacks(calls, owned=False, fail_at="dependencies"))
    assert [p[0] for p in _phases(capsys.readouterr().err)] == [
        "export_worker_ast_write", "export_worker_reexport_scan", "export_worker_owned_metadata"]
    assert not (tmp_path / "worker.tsv").exists()


def test_export_worker_optional_clock_failure_preserves_output(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "1")
    def unavailable():
        raise OSError("diagnostic clock unavailable")
    monkeypatch.setattr(pipeline_profile, "profile_now_ms", unavailable)
    assert execution._run_export_worker(
        _manifest(tmp_path), worker_timing_enabled=lambda: True,
        **_callbacks([], owned=False)) == 0
    assert capsys.readouterr().err == ""
    assert len((tmp_path / "worker.tsv").read_text().strip().split("\t")) == 4


def test_actual_owned_batch_export_wire_unchanged(monkeypatch, tmp_path, capsys):
    manifest = _manifest(tmp_path)
    (tmp_path / "a.py").write_text("def f():\n    return 42\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("from pkg.a import f\nx = f\n", encoding="utf-8")
    monkeypatch.delenv("PCC_STAGE1_CHECKPOINT_STAGE", raising=False)
    monkeypatch.delenv("PCC_PY_FRONTEND_WORKER_TIMING", raising=False)
    assert pipeline._run_python_multi_export_worker(manifest) == 0
    row0 = (tmp_path / "worker.tsv").read_text().strip().split("\t")
    snapshots = {path: path.read_bytes() for directory in ("ast", "exports")
                 for path in (tmp_path / directory).glob("*.json")}
    assert len(snapshots) == 4 and len(row0) == 3 and capsys.readouterr().err == ""
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "1")
    assert pipeline._run_python_multi_export_worker(manifest) == 0
    row1 = (tmp_path / "worker.tsv").read_text().strip().split("\t")
    assert row1[:3] == row0 and len(row1) == 4 and row1[3].isdigit()
    assert all(path.read_bytes() == before for path, before in snapshots.items())
    phases = [p for p in _phases(capsys.readouterr().err)
              if p[0].startswith("export_worker_")]
    assert [p[0] for p in phases] == [
        "export_worker_ast_write", "export_worker_reexport_scan",
        "export_worker_owned_metadata", "export_worker_dependencies",
        "export_worker_function_objects", "export_worker_native_wire",
        "export_worker_reexport_wire"]

"""Per-root preload deltas computed in worker processes build the serial index."""

import ast
import inspect
import json
import subprocess
import sys
import textwrap

import pytest

from pcc.frontends.python import pipeline, preload_delta_worker, type_infer
from pcc.frontends.python.pipeline_exports import _write_native_exports_wire


def _cls(name, owner, fields):
    return {
        "kind": "class", "class_name": name, "owning_module": owner,
        "field_names": tuple(field for field, _ in fields),
        "field_types": tuple(fields), "methods": (), "base_names": (),
    }


def _exports(module_count=20):
    # Removing either Shared owner makes the other one unique, so those two
    # roots add descriptors the global preload lacks; every m* root drops
    # its own unique class.
    exports = {
        "dup_a": {"Shared": _cls("Shared", "dup_a", (("x", ("int", 64, True)),))},
        "dup_b": {"Shared": _cls("Shared", "dup_b", (("y", ("str",)),))},
    }
    for index in range(module_count):
        name = "m%02d" % index
        exports[name] = {
            "C%d" % index: _cls("C%d" % index, name, (("v", ("int", 64, True)),)),
        }
    exports["plain"] = {}
    return exports


@pytest.fixture
def host_workers(monkeypatch):
    monkeypatch.setattr(
        pipeline, "_python_frontend_worker_command_prefix",
        lambda: [sys.executable, "-m", "pcc"],
    )
    calls = []
    original = pipeline._run_python_frontend_worker_commands

    def observe(commands, **kwargs):
        calls.append((list(commands), kwargs))
        return original(commands, **kwargs)

    monkeypatch.setattr(pipeline, "_run_python_frontend_worker_commands", observe)
    return calls


def test_worker_deltas_build_the_serial_index_byte_for_byte(
    monkeypatch, host_workers, tmp_path,
):
    exports = _exports()
    serial = type_infer.build_unique_external_class_preload_index(exports)
    # New descriptors get ids in root order: dup_a's before dup_b's.
    assert len(serial["types"]) == 22
    assert serial["roots"]["dup_a"] == ((), (("Shared", 20), ("dup_b.Shared", 20)))
    assert serial["roots"]["dup_b"] == ((), (("Shared", 21), ("dup_a.Shared", 21)))
    assert serial["roots"]["m07"] == (("C7", "m07.C7"), ())
    assert serial["roots"]["plain"] == ((), ())

    monkeypatch.setenv("PCC_PRELOAD_DELTA_JOBS", "3")
    parallel = pipeline._build_unique_external_class_preload_index(
        exports, str(tmp_path),
    )
    assert len(host_workers) == 1
    commands, kwargs = host_workers[0]
    assert len(commands) == 3 and kwargs == {"max_parallel": 3}
    assert all(preload_delta_worker.WORKER_ARG in command for command in commands)
    assert all(str(tmp_path / "preload_deltas") in command for command in commands)
    assert parallel == serial
    assert json.dumps(parallel) == json.dumps(serial)
    assert list(tmp_path.iterdir()) == []


def test_worker_result_missing_a_root_fails_closed(monkeypatch, host_workers, tmp_path):
    original = preload_delta_worker.read_result

    def drop_dup_b(out_path):
        deltas = original(out_path)
        deltas.pop("dup_b", None)
        return deltas

    monkeypatch.setenv("PCC_PRELOAD_DELTA_JOBS", "2")
    monkeypatch.setattr(preload_delta_worker, "read_result", drop_dup_b)
    with pytest.raises(pipeline.PyPipelineError, match="omitted root dup_b"):
        pipeline._build_unique_external_class_preload_index(_exports(), str(tmp_path))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("jobs,module_count,work_dir", [
    ("1", 20, True), ("4", 8, True), ("4", 20, False),
])
def test_one_job_few_roots_or_no_work_dir_stay_in_the_coordinator(
    monkeypatch, host_workers, tmp_path, jobs, module_count, work_dir,
):
    exports = _exports(module_count)
    monkeypatch.setenv("PCC_PRELOAD_DELTA_JOBS", jobs)
    result = pipeline._build_unique_external_class_preload_index(
        exports, str(tmp_path) if work_dir else "",
    )
    assert host_workers == []
    assert result == type_infer.build_unique_external_class_preload_index(exports)


def test_worker_cli_round_trip_and_unknown_root(tmp_path):
    exports = _exports()
    exports_path = tmp_path / "exports.json"
    _write_native_exports_wire(str(exports_path), exports, {})
    roots_path = tmp_path / "roots.txt"
    roots_path.write_text("dup_b\nm03\n", encoding="utf-8")
    out_path = tmp_path / "deltas.json"
    command = [sys.executable, "-m", "pcc", preload_delta_worker.WORKER_ARG]
    completed = subprocess.run(
        command + [str(exports_path), str(roots_path), str(out_path)],
        capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    global_by_key = type_infer.preload_global_by_key(
        type_infer.build_unique_external_class_preload(exports)
    )
    assert preload_delta_worker.read_result(str(out_path)) == {
        root: type_infer.preload_root_delta(exports, root, global_by_key)
        for root in ("dup_b", "m03")
    }
    assert not (tmp_path / "deltas.json.partial").exists()

    roots_path.write_text("not_exported\n", encoding="utf-8")
    failed = subprocess.run(
        command + [str(exports_path), str(roots_path), str(tmp_path / "bad.json")],
        capture_output=True, text=True,
    )
    assert failed.returncode != 0
    assert not (tmp_path / "bad.json").exists()

    usage = subprocess.run(command + [str(exports_path)], capture_output=True, text=True)
    assert usage.returncode == 2
    assert "requires exports, roots and output paths" in usage.stderr


def test_worker_path_avoids_modules_pcc1_compiles_as_cpython():
    # pcc1 links tempfile and shutil as CPython modules; a function calling
    # them compiles to a no-libpython stub that raises in the native Stage2
    # coordinator, and only a full self-host run reaches it.
    for function in (
        pipeline._build_unique_external_class_preload_index,
        pipeline._preload_deltas_in_workers,
        preload_delta_worker.run,
        preload_delta_worker.read_result,
    ):
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        names = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        }
        assert not names & {"tempfile", "shutil"}, function.__name__

"""A codegen worker reuses its Darwin object when the emitter input is unchanged.

The worker's self backend is about 90% of a Stage1 codegen worker. Its object
depends on the captured indexed kernel, the emitter sources, the target and
the PCC_ settings; pcc/frontends/python/direct_object_cache.py keys the
object on those four, so a second compile of unchanged modules reruns only
the frontend. Replaying three Stage1 workers: array_core 8.5 -> 1.6 s,
py_ast 25.2 -> 3.0 s, objects byte-identical.
"""
from __future__ import annotations

import os
import sys
import textwrap

from pcc.frontends.python import direct_object_cache
from tests.python.process_timeout import run_process_group_timeout


def test_cache_is_off_without_the_bootstrap_identity(monkeypatch, tmp_path):
    monkeypatch.delenv("PCC_SELF_BACKEND_OBJECT_CACHE_IDENTITY", raising=False)
    monkeypatch.setenv("PCC_SELF_BACKEND_OBJECT_CACHE_DIR", str(tmp_path))
    assert direct_object_cache.cache_path(object(), "arm64-apple-darwin", str(tmp_path), 0) == ""
    monkeypatch.setenv("PCC_SELF_BACKEND_OBJECT_CACHE_IDENTITY", "emitter")
    monkeypatch.setenv("PCC_SELF_BACKEND_OBJECT_CACHE", "off")
    assert direct_object_cache.cache_path(object(), "arm64-apple-darwin", str(tmp_path), 0) == ""


def test_settings_that_steer_only_scheduling_stay_out_of_the_key(monkeypatch):
    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", "6")
    monkeypatch.setenv("PCC_WORKER_RESOURCE_TOKEN", "attempt")
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "0")
    rows = direct_object_cache._keyed_settings()
    assert "PCC_SELF_CALLEE_SAVED_REGISTERS=0" in rows
    assert not any(row.startswith(("PCC_PY_FRONTEND_JOBS=", "PCC_WORKER_")) for row in rows)


def test_store_and_read_round_trip(tmp_path):
    path = str(tmp_path / "ab" / "key.pco")
    assert direct_object_cache.read(path) is None
    direct_object_cache.store(path, b"object bytes")
    assert direct_object_cache.read(path) == b"object bytes"
    assert sorted(os.listdir(tmp_path / "ab")) == ["key.pco"]


FILES = {
    "helper.py": """
        class Box:
            def __init__(self, value):
                self.value = value

            def doubled(self):
                return self.value * 2


        def make(value):
            return Box(value)
    """,
    "main.py": """
        import helper

        total = 0
        for value in range(5):
            total += helper.make(value).doubled()
        print(total)
    """,
}


def test_second_compile_reuses_every_worker_object(tmp_path, monkeypatch, pcc_runtime_archive):
    from pcc.driver.bootstrap_cache_identity import bootstrap_object_cache_identity
    from pcc.frontends.python.pipeline import compile_python_multi

    for name, value in {
        "PCC_DIRECT_INDEXED_KERNEL_CAPTURE": "1", "PCC_DIRECT_INDEXED_KERNEL_EMIT": "1",
        "PCC_DIRECT_INLINE_ERROR_EDGE_CAPTURE": "1",
        "PCC_DIRECT_INDEXED_KERNEL_REQUIRE_ZERO_FALLBACK": "1",
        "PCC_DIRECT_INDEXED_KERNEL_FUSE_USES": "1",
        "PCC_DIRECT_INDEXED_KERNEL_RELEASE_FRONTEND": "1",
        "PCC_PY_FRONTEND_JOBS": "2",
        "PCC_SELF_BACKEND_OBJECT_CACHE_IDENTITY": bootstrap_object_cache_identity(),
        "PCC_SELF_BACKEND_OBJECT_CACHE_DIR": str(tmp_path / "cache"),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("PCC_SELF_BACKEND_OBJECT_CACHE", raising=False)
    paths = []
    for name, source in FILES.items():
        path = tmp_path / name
        path.write_text(textwrap.dedent(source).lstrip())
        paths.append(str(path))
    reference = run_process_group_timeout(
        [sys.executable, "-E", "-B", str(tmp_path / "main.py")], timeout=30,
    )
    assert reference.returncode == 0, reference.stderr

    def compile_once(name):
        binary = tmp_path / name
        compile_python_multi(paths, str(binary), entry_module="main", backend="self",
                             ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
        ran = run_process_group_timeout([str(binary)], timeout=30)
        assert (ran.returncode, ran.stderr) == (0, ""), ran.stderr[-2000:]
        assert ran.stdout == reference.stdout
        cached = sorted(
            os.path.join(root, entry)
            for root, _dirs, entries in os.walk(tmp_path / "cache")
            for entry in entries if entry.endswith(".pco")
        )
        return cached, {path: os.stat(path).st_mtime_ns for path in cached}

    first, first_times = compile_once("first")
    if sys.platform != "darwin":
        # Only the Darwin object route consults the cache.
        assert first == []
        return
    assert len(first) >= 2, first
    second, second_times = compile_once("second")
    # The second compile found every object: nothing new, nothing rewritten.
    assert second == first and second_times == first_times

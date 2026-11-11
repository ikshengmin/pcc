"""Execute admitted managed imports in all five collectors.

Host pcc0/self/static Linux execution, not pcc1 or Gateway/QPS acceptance.
A current owned default threads-atomic cache is required, never provisioned.
These checks do not infer nursery activation from a compiler admission flag.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES = Path(__file__).with_name("fixtures") / "gc3_closed_young_modules"
_TARGET = "x86_64-unknown-linux-gnu"
_EXPECTED = b"7099\n2468-root\n2468-root\n"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.pcc_gate(probe=lambda: sys.platform.startswith("linux")
                        and platform.machine().lower() in ("x86_64", "amd64")),
]


def _no_runtime_build(*args, **kwargs):
    raise AssertionError("module admission test requires the current prebuilt owned runtime")


def test_real_managed_module_imports_and_sibling_globals(tmp_path, monkeypatch, record_property):
    from pcc.backend import elf_x86_64
    from pcc.frontends.python import owned_runtime_build, pipeline
    from tests.runtime_fixture_provenance import _verified_test_runtime_archive

    reference = subprocess.run([sys.executable, "-I", "-c",
        "import runpy,sys;sys.path.insert(0,sys.argv[1]);runpy.run_path(sys.argv[2],run_name='__main__')",
        str(_FIXTURES), str(_FIXTURES / "entry.py")], capture_output=True, timeout=20)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, _EXPECTED, b"")
    runtime_root = _ROOT / "pcc/runtime"
    archive = runtime_root / "build_owned" / _TARGET / "threads-atomic/libpy_runtime_pcc_py.a"
    assert archive.is_file(), "requires current default owned threads-atomic runtime cache"
    for key in (
        "PCC_RUNTIME_ARCHIVE", "PCC_RUNTIME_DIR", "PCC_DEFER_SELF_LINK_PLAN",
        "PCC_STAGE1_CHECKPOINT_DIR", "PCC_DEFER_FRONTEND_CODEGEN_PLAN",
        "PCC_INDEXED_HANDOFF_REQUEST", "PCC_DIRECT_INDEXED_SIDECAR",
        "PCC_HOST_INDEXED_PROCESS_SPLIT", "PCC_REFCOUNT_KIND",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PCC_RUNTIME_BUILD", "owned")
    monkeypatch.setenv("PCC_SELF_LINK", "pcc")
    monkeypatch.setenv("PCC_WITH_THREADS", "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    monkeypatch.setenv("PCC_TEST_NO_NATIVE_PROVISIONING", "1")
    monkeypatch.setenv("PCC_NO_AUTO_PCC1", "1")
    monkeypatch.setattr(owned_runtime_build, "build_runtime_archive", _no_runtime_build)
    verified, _ = _verified_test_runtime_archive(archive, runtime_root=runtime_root)
    runtime_hash = hashlib.sha256(verified.read_bytes()).hexdigest()
    record_property("runtime_sha256", runtime_hash)
    original_link = pipeline._link_with_self_backend_ir_texts
    links = []

    def link(texts, *args, **kwargs):
        calls = [line for text in texts for line in text.splitlines()
                 if "call " in line and "@pcc_gc_backend3_enable_closed_young_domain(" in line]
        links.append(len(calls))
        return original_link(texts, *args, **kwargs)

    monkeypatch.setattr(pipeline, "_link_with_self_backend_ir_texts", link)
    for frontend_jobs in (1, 2):
        monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", str(frontend_jobs))
        binary = tmp_path / ("managed_modules_" + str(frontend_jobs))
        profile = {}
        pipeline.compile_python_multi(
            [str(_FIXTURES / "entry.py"), str(_FIXTURES / "young_values.py")],
            str(binary), module_names=["entry", "young_values"], entry_module="entry",
            backend="self", target_triple=(_TARGET if frontend_jobs == 1 else None),
            ir_scaffold_mode="on", profile=profile,
        )
        image = binary.read_bytes()
        elf_x86_64.parse_static_executable(image)
        record_property("binary_" + str(frontend_jobs), hashlib.sha256(image).hexdigest())
        assert profile["counters"]["gc3_closed_young_domain_admitted"] == 1
        if frontend_jobs == 2:
            assert profile["counters"]["gc3_closed_young_cache_bypasses"] == 1
            assert profile["counters"]["multi_frontend_chunks"] > 0
            assert profile["counters"]["multi_frontend_ast_wire_enabled"] == 1
        record_property("frontend_" + str(frontend_jobs), json.dumps(profile["counters"], sort_keys=True))
        for backend in range(5):
            for alloc_max in (16, 128):
                env = {key: value for key, value in os.environ.items()
                       if not key.startswith("PCC_GC_") and key not in (
                           "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH")}
                env.update(PATH="/nonexistent", PCC_GC_BACKEND=str(backend),
                           PCC_GC_MINOR_HEAP_SIZE="4096", PCC_GC_MINOR_ALLOC_MAX=str(alloc_max))
                result = subprocess.run([str(binary)], env=env, capture_output=True, timeout=20)
                case = (frontend_jobs, backend, alloc_max)
                assert (result.returncode, result.stdout, result.stderr) == (0, _EXPECTED, b""), case
    assert links == [1, 1]
    assert hashlib.sha256(verified.read_bytes()).hexdigest() == runtime_hash

"""Execute the compiler-admitted closed scalar domain, not an injected proof.

This is host-pcc0/self-backend, static Linux x86-64 acceptance, not native-pcc1,
QPS, concurrent-minor or general young-object reclamation evidence. The source
and request admission exclusions are in test_gc3_closed_young_domain.py.

Prerequisite: a current default owned threads-atomic runtime cache, including
its provenance and C-API inventory. This test never builds or installs one.
Run with the usual 90-second/1-GiB process-tree guard and pytest -x -m integration;
retain the repository's default six workers and distribution mode.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import select
import subprocess
import sys
import time

import pytest


_ROOT = Path(__file__).resolve().parents[2]
_SOURCE = Path(__file__).with_name("fixtures") / "gc3_closed_young_activation.py"
_TARGET = "x86_64-unknown-linux-gnu"
_EXPECTED = b"7099\nREADY\n"
_CHILD_TIMEOUT = 20
_LOADER_ENV = (
    "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH",
)
_COUNTER_WIDTHS = {
    "pcc_gc_backend_selected": 4,
    "pcc_gc_backend3_closed_young_domain": 4,
    "pcc_gc_backend3_young_cut_commits": 8,
    "pcc_gc_backend3_young_cut_freed": 8,
    "pcc_gc_minor_alloc_max": 4,
    "pcc_gc_minor_arena_refills": 4,
    "pcc_gc_minor_arena_bumps": 4,
    "pcc_gc_tracing_finish_commits": 8,
}


def _linux_x86_64_proc_available():
    if not (
        sys.platform.startswith("linux")
        and platform.machine().lower() in ("x86_64", "amd64")
        and sys.implementation.name == "cpython"
        and Path("/dev/stdin").exists()
    ):
        return False
    try:
        with open("/proc/self/mem", "rb", buffering=0):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.pcc_gate(probe=_linux_x86_64_proc_available),
]


@pytest.fixture(scope="module")
def cpython_reference():
    result = subprocess.run(
        [sys.executable, "-I", str(_SOURCE)], input=b"\n",
        capture_output=True, timeout=_CHILD_TIMEOUT,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == b""
    assert result.stdout == _EXPECTED
    return result.stdout


def test_closed_young_workload_cpython_reference(cpython_reference):
    assert cpython_reference == _EXPECTED


def _forbid_compile_subprocess_or_build(*args, **kwargs):
    raise AssertionError("closed-domain compilation must reuse the owned cache in process")


def _compile_admitted_program(tmp_path, monkeypatch):
    from pcc.backend import elf_x86_64 as elf
    from pcc.frontends.python import owned_runtime_build, pipeline
    from tests.runtime_fixture_provenance import _verified_test_runtime_archive

    runtime_root = _ROOT / "pcc" / "runtime"
    archive = runtime_root / "build_owned" / _TARGET / "threads-atomic" / "libpy_runtime_pcc_py.a"
    output = tmp_path / "closed_young_activation"
    observed_links = []
    observed_runtimes = []
    profile = {}

    def observe(frame, event, result):
        if event != "return":
            return
        if frame.f_code is pipeline._ensure_runtime.__code__ and result is not None:
            observed_runtimes.append(Path(result).resolve())
        if frame.f_code is elf._link_static_executable_image.__code__ and result is not None:
            # Read the unchanged linker's resolved symbols, never inject a
            # proof, an object, a constructor, a telemetry import or a write.
            addresses = {}
            for name, width in _COUNTER_WIDTHS.items():
                address, symbol = frame.f_locals["resolve_symbol"](
                    *frame.f_locals["definitions"][name],
                )
                assert address > 0 and symbol.size == width, name
                addresses[name] = address
            observed_links.append((hashlib.sha256(result).hexdigest(), addresses))

    with monkeypatch.context() as compile_env:
        # Shared test fixtures often select an explicit archive. Such an
        # override denies the production proof, so use the real default cache.
        for name in (
            "PCC_RUNTIME_ARCHIVE", "PCC_RUNTIME_DIR", "PCC_DEFER_SELF_LINK_PLAN",
            "PCC_REFCOUNT_KIND", "PCC_RUNTIME_GC_STRESS_EVERY",
        ):
            compile_env.delenv(name, raising=False)
        compile_env.setenv("PCC_RUNTIME_BUILD", "owned")
        compile_env.setenv("PCC_SELF_LINK", "pcc")
        compile_env.setenv("PCC_WITH_THREADS", "1")
        compile_env.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
        compile_env.setenv("PCC_TEST_NO_NATIVE_PROVISIONING", "1")
        compile_env.setenv("PCC_NO_AUTO_PCC1", "1")
        compile_env.setattr(subprocess, "Popen", _forbid_compile_subprocess_or_build)
        compile_env.setattr(
            owned_runtime_build, "build_runtime_archive", _forbid_compile_subprocess_or_build,
        )
        if not archive.is_file():
            pytest.fail("requires a prebuilt, current default owned threads-atomic runtime cache")
        verified, _manifest = _verified_test_runtime_archive(archive, runtime_root=runtime_root)
        archive_sha256 = hashlib.sha256(verified.read_bytes()).hexdigest()
        previous_profile = sys.getprofile()
        try:
            sys.setprofile(observe)
            pipeline.compile_python(
                str(_SOURCE), str(output), backend="self", target_triple=_TARGET,
                profile=profile,
            )
        finally:
            sys.setprofile(previous_profile)
        assert hashlib.sha256(verified.read_bytes()).hexdigest() == archive_sha256

    assert observed_runtimes == [verified]
    assert len(observed_links) == 1
    binary_sha256, addresses = observed_links[0]
    image = output.read_bytes()
    assert hashlib.sha256(image).hexdigest() == binary_sha256
    elf.parse_static_executable(image)  # ET_EXEC, no dynamic loader/table.
    assert profile["counters"]["auto_files"] == 1
    assert profile["counters"]["gc3_closed_young_domain_admitted"] == 1
    phases = {event["name"] for event in profile["events"]}
    assert {
        "parse_and_lift", "type_infer", "codegen_layer1", "python_ir_pass_pipeline",
        "native_capability_gate", "ensure_runtime", "link_self_pcc",
        "compile_python_total",
    } <= phases
    return output, binary_sha256, addresses


def _run_native(binary, binary_sha256, addresses, *, backend, revoked, alloc_max):
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == binary_sha256
    environment = {
        name: value for name, value in os.environ.items()
        if not name.startswith("PCC_GC_") and name not in _LOADER_ENV
    }
    environment.update(
        PATH="/nonexistent", PCC_GC_BACKEND=str(backend), PCC_GC_MINOR_HEAP_SIZE="4096",
    )
    if alloc_max is not None:
        environment["PCC_GC_MINOR_ALLOC_MAX"] = str(alloc_max)
    if revoked:
        # Presence, even with no library named, permanently revokes admission.
        environment["LD_LIBRARY_PATH"] = ""
    deadline = time.monotonic() + _CHILD_TIMEOUT
    child = subprocess.Popen(
        [str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=environment, bufsize=0,
    )
    output = b""
    try:
        while b"READY\n" not in output:
            remaining = deadline - time.monotonic()
            assert remaining > 0, "native child did not reach READY within 20 seconds"
            readable, _, _ = select.select([child.stdout], [], [], remaining)
            assert readable, "native child did not reach READY within 20 seconds"
            part = os.read(child.stdout.fileno(), 4096)
            if not part:
                tail, error = child.communicate(timeout=max(0.001, deadline - time.monotonic()))
                pytest.fail(f"native child exited before READY: {child.returncode}, {output + tail!r}, {error!r}")
            output += part
            assert len(output) <= 4096, "unexpected native output before READY"
        assert output == _EXPECTED
        # Bind captured static addresses to this exact live executable. Only
        # this test's own child is opened, read-only, while blocked on stdin.
        proc = Path("/proc") / str(child.pid)
        assert hashlib.sha256((proc / "exe").read_bytes()).hexdigest() == binary_sha256
        counters = {}
        with (proc / "mem").open("rb", buffering=0) as memory:
            for name, width in _COUNTER_WIDTHS.items():
                memory.seek(addresses[name])
                value = memory.read(width)
                assert len(value) == width, f"short native counter read: {name}"
                counters[name] = int.from_bytes(value, "little", signed=True)
        tail, error = child.communicate(b"\n", timeout=max(0.001, deadline - time.monotonic()))
        output += tail
        assert child.returncode == 0, error
        assert error == b""
        return output, counters
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=2)


def test_compiler_admitted_young_cut_activation(
    tmp_path, monkeypatch, cpython_reference, record_property,
):
    binary, binary_sha256, addresses = _compile_admitted_program(tmp_path, monkeypatch)
    record_property("binary_sha256", binary_sha256)
    # One compile and one integration node preserve ordinary xdist scheduling.
    # The last two rows leave the max unset to execute the unchanged default 16.
    cases = [(backend, revoked, 128) for backend in range(5) for revoked in (False, True)]
    cases += [(3, revoked, None) for revoked in (False, True)]
    for backend, revoked, alloc_max in cases:
        output, counters = _run_native(
            binary, binary_sha256, addresses,
            backend=backend, revoked=revoked, alloc_max=alloc_max,
        )
        case = f"GC{backend}, revoked={revoked}, alloc_max={alloc_max}"
        record_property(case, json.dumps(counters, sort_keys=True))
        assert output == cpython_reference, case
        assert counters["pcc_gc_backend_selected"] == backend, case
        assert counters["pcc_gc_backend3_closed_young_domain"] == (2 if revoked else 1), case
        assert counters["pcc_gc_minor_alloc_max"] == (16 if alloc_max is None else alloc_max), case
        assert counters["pcc_gc_tracing_finish_commits"] == 0, case
        if backend == 3 and alloc_max == 128:
            assert counters["pcc_gc_minor_arena_refills"] > 1, case
            assert counters["pcc_gc_minor_arena_bumps"] > 0, case
        if backend == 3 and not revoked and alloc_max == 128:
            assert counters["pcc_gc_backend3_young_cut_commits"] > 0, case
            assert counters["pcc_gc_backend3_young_cut_freed"] > 0, case
        else:
            assert counters["pcc_gc_backend3_young_cut_commits"] == 0, case
            assert counters["pcc_gc_backend3_young_cut_freed"] == 0, case

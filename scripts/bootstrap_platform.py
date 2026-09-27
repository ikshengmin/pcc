"""Portable, source-identified five-GC self-host qualification driver.

Runs a full host -> pcc1 -> pcc2 -> pcc3 chain for every selected GC. Unlike
the Darwin reference harness this entry needs neither Bash nor Mach-O tools.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.platform_process_watchdog import run


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@contextlib.contextmanager
def performance_lock():
    path = ROOT / "build" / ".pcc-performance.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            if stream.read(1) == b"":
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _source_snapshot():
    """Hash the complete compiler/runtime/harness inputs, not a cache namespace.

    Git supplies tracked and non-ignored new inputs, including data files and
    Makefile inventories. Generated objects/caches are deliberately excluded
    by the repository ignore rules. The frontend cache's source hash is too
    narrow here: it explicitly omits runtime, C and linker implementations.
    """
    paths = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--",
         "pcc", "scripts", "src", "include", "utils", "hatch_build.py",
         "pyproject.toml", "uv.lock", ".python-version"], cwd=ROOT,
    )
    names = sorted({os.fsdecode(name) for name in paths.split(b"\0") if name})
    digest = hashlib.sha256()
    for name in names:
        relative = name.encode("utf-8", errors="surrogateescape")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        path = ROOT / name
        if not path.is_file():
            # A tracked deletion is meaningful even in diagnostic dirty mode.
            digest.update(b"missing\0")
        else:
            digest.update(b"file\0")
            digest.update(bytes.fromhex(_digest(path)))
    return {"source_sha256": digest.hexdigest(), "source_file_count": len(names),
            "source_scope": "compiler-runtime-headers-build-harness-v1"}


def source_identity(allow_dirty=False):
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=ROOT, text=True)
    if dirty and not allow_dirty:
        raise RuntimeError("qualification requires a clean source commit; --allow-dirty is diagnostic only")
    return {"commit": head, **_source_snapshot(), "clean_commit": not bool(dirty)}


def normalized_image(data):
    if data.startswith(b"\x7fELF"):
        return data  # Owned static ELF has no variable signature/build-id.
    if not data.startswith(b"MZ") or len(data) < 64:
        raise RuntimeError("bootstrap output is not a native ELF/PE image")
    output = bytearray(data)
    pe = struct.unpack_from("<I", output, 60)[0]
    if pe + 24 + 240 > len(output) or output[pe:pe + 4] != b"PE\0\0":
        raise RuntimeError("malformed PE bootstrap artifact")
    output[pe + 8:pe + 12] = b"\0" * 4  # COFF timestamp
    output[pe + 24 + 64:pe + 24 + 68] = b"\0" * 4  # PE checksum
    return bytes(output)


def dependency_receipt(path):
    data = Path(path).read_bytes()
    if data.startswith(b"\x7fELF"):
        from pcc.backend.elf_x86_64 import parse_static_executable
        shape = parse_static_executable(data)
        return {"format": "ELF", "no_dynamic_dependencies": True,
                "proof_scope": "ELF headers; does not identify statically linked source ownership", **shape}
    pe = struct.unpack_from("<I", data, 60)[0]
    count = struct.unpack_from("<H", data, pe + 6)[0]
    optional_size = struct.unpack_from("<H", data, pe + 20)[0]
    optional = pe + 24
    if struct.unpack_from("<H", data, optional)[0] != 0x20B:
        raise RuntimeError("expected PE32+")
    ranges = []
    for index in range(count):
        section = optional + optional_size + index * 40
        virtual_size, rva, raw_size, raw = struct.unpack_from("<IIII", data, section + 8)
        ranges.append((rva, raw_size, raw))
    def offset(rva):
        for start, size, raw in ranges:
            if start <= rva < start + size:
                return raw + rva - start
        raise RuntimeError("PE RVA outside file-backed sections")
    import_rva, import_size = struct.unpack_from("<II", data, optional + 112 + 8)
    libraries = []
    if import_rva:
        for index in range(import_size // 20):
            at = offset(import_rva + index * 20)
            descriptor = struct.unpack_from("<IIIII", data, at)
            if descriptor == (0, 0, 0, 0, 0):
                break
            name_at = offset(descriptor[3])
            end = data.find(b"\0", name_at)
            if end < 0:
                raise RuntimeError("unterminated PE import name")
            libraries.append(data[name_at:end].decode("ascii").lower())
    allowed = {"kernel32.dll", "ntdll.dll", "ws2_32.dll", "shell32.dll", "psapi.dll", "bcrypt.dll"}
    if set(libraries) - allowed:
        raise RuntimeError("non-system compiler dependencies: " + repr(libraries))
    return {"format": "PE32+", "system_dlls": sorted(libraries),
            "imports_only_system_dlls": True,
            "proof_scope": "PE import table; does not identify statically linked source ownership"}


def _stage_environment(gc_backend):
    """Qualification always completes compilation in the supervised process."""
    environment = os.environ.copy()
    environment.pop("LC_ALL", None)
    environment.update({
        "PCC_GC_BACKEND": str(gc_backend), "PCC_SELF_LINK": "pcc", "PCC_SELF_OBJ": "pcc",
        "PCC_IR_TO_OBJ_EMITTER": "pcc", "PCC_RUNTIME_CC": "pcc", "PCC_RUNTIME_HIGH": "py",
        "PCC_RUNTIME_DIR": str(ROOT / "pcc" / "py_runtime"),
        "PCC_PY_FRONTEND_IR_CACHE": "0", "PCC_SELF_BACKEND_OBJECT_CACHE": "0",
        "PCC_PYTHON_IR_PASSES": "off", "PCC_PY_FRONTEND_JOBS": "1",
        "PCC_SELF_BACKEND_JOBS": "1", "PCC_DEFER_FRONTEND_CODEGEN": "0",
        "PCC_DEFER_FRONTEND_CODEGEN_PLAN": "", "PCC_DEFER_FRONTEND_OUTPUT": "",
        "PCC_DEFER_SELF_LINK_PLAN": "", "PCC_DIRECT_INDEXED_KERNEL_EMIT": "1",
    })
    return environment


def run_chain(gc_backend, out_dir, *, stage_limit=3, timeout=2400,
              rss_limit=17179869184, allow_dirty=False):
    from pcc.py_frontend.pipeline_targets import host_target_triple
    from pcc.backend.self_backend_targets import resolve_self_backend_target
    target = host_target_triple()
    identity = resolve_self_backend_target(target).identity
    if identity not in ("self-aarch64-linux-v0", "self-x86_64-linux-v0", "self-x86_64-windows-v0"):
        raise RuntimeError("use the existing Darwin reference gate on this host")
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    # Reusing an output directory must not expose an older success receipt
    # after this attempt fails before it can publish its own result.
    for name in ("receipt.json", "failure.json", "progress.json"):
        (out_dir / name).unlink(missing_ok=True)
    source = source_identity(allow_dirty)
    environment = _stage_environment(gc_backend)
    suffix = ".exe" if os.name == "nt" else ""
    records = []
    compiler = [sys.executable, "-m", "pcc"]
    canary = out_dir / "canary.py"
    canary.write_text(
        "from pcc.unsafe import gc_backend_current\n"
        "class Box:\n"
        "    def __init__(self, value: int):\n        self.value = value\n"
        "def run() -> int:\n"
        "    box = Box(2 ** 80)\n"
        "    items = [box]\n"
        "    try:\n        raise ValueError('probe')\n"
        "    except ValueError:\n        return items[0].value + 42\n"
        "print(gc_backend_current())\nprint(run())\n", encoding="utf-8")
    with performance_lock():
        try:
            runtime = out_dir / "runtime" / "libpy_runtime_pcc_py.a"
            runtime_env = environment.copy()
            runtime_env.pop("PCC_RUNTIME_ARCHIVE", None)
            # The runtime builder locally masks executable/direct emission
            # flags for each library-IR request, restoring them afterward.
            run([sys.executable, "-m", "pcc.py_frontend.owned_runtime_build", "--target", target,
                 "--output", str(runtime)], cwd=ROOT, env=runtime_env,
                log_path=out_dir / "runtime-build.log", timeout=timeout,
                rss_limit=rss_limit)
            runtime_identity = {"path": str(runtime), "sha256": _digest(runtime),
                                "provenance_sha256": _digest(str(runtime) + ".provenance.json")}
            environment["PCC_RUNTIME_ARCHIVE"] = str(runtime)
            for stage in range(1, stage_limit + 1):
                if source_identity(allow_dirty) != source:
                    raise RuntimeError("source changed before stage " + str(stage))
                if _digest(runtime) != runtime_identity["sha256"]:
                    raise RuntimeError("runtime archive changed before stage " + str(stage))
                compiler_digest = _digest(compiler[0])
                output = out_dir / ("pcc" + str(stage) + suffix)
                stage_env = environment.copy()
                if stage > 1:
                    stage_env["PCC_HOST_PYTHON"] = str(out_dir / "forbidden-host-python")
                command = compiler + ["--backend", "self", "--python-libpython", "off",
                                      "--ir-scaffold=on", str(ROOT / "pcc" / "__main__.py"), "-o", str(output)]
                print(f"GC{gc_backend} stage{stage}: {' '.join(command)}", flush=True)
                record = run(command, cwd=ROOT, env=stage_env, log_path=out_dir / f"stage{stage}.log",
                             timeout=timeout, rss_limit=rss_limit, native=stage > 1)
                if source_identity(allow_dirty) != source:
                    raise RuntimeError("source changed during stage " + str(stage))
                record.update({"stage": stage, "sha256": _digest(output),
                               "input_compiler_sha256": compiler_digest,
                               "dependencies": dependency_receipt(output)})
                if _digest(compiler[0]) != compiler_digest:
                    raise RuntimeError("input compiler changed during stage " + str(stage))
                compiler = [str(output)]
                executable = out_dir / (f"canary{stage}" + suffix)
                smoke_env = environment.copy()
                smoke_env["PCC_HOST_PYTHON"] = str(out_dir / "forbidden-host-python")
                canary_compile = run(
                    compiler + [str(canary), "-o", str(executable), "--backend", "self", "--python-libpython", "off"],
                    cwd=ROOT, env=smoke_env, log_path=out_dir / f"canary{stage}-compile.log",
                    timeout=timeout, rss_limit=rss_limit, native=True)
                log = out_dir / f"canary{stage}-run.log"
                canary_run = run([str(executable)], cwd=out_dir, env=smoke_env, log_path=log,
                                 timeout=60, rss_limit=rss_limit, native=True)
                expected = f"{gc_backend}\n{2 ** 80 + 42}\n"
                if log.read_text(encoding="utf-8").replace("\r\n", "\n") != expected:
                    raise RuntimeError("native GC/function/object/integer/exception canary failed")
                if source_identity(allow_dirty) != source:
                    raise RuntimeError("source changed during native canary " + str(stage))
                if _digest(runtime) != runtime_identity["sha256"] or _digest(
                    str(runtime) + ".provenance.json"
                ) != runtime_identity["provenance_sha256"]:
                    raise RuntimeError("runtime archive/provenance changed during stage " + str(stage))
                if _digest(output) != record["sha256"]:
                    raise RuntimeError("stage compiler changed during native canary " + str(stage))
                record["canary"] = {"source_sha256": _digest(canary),
                                    "executable_sha256": _digest(executable),
                                    "dependencies": dependency_receipt(executable),
                                    "compile": canary_compile, "run": canary_run,
                                    "output_sha256": _digest(log)}
                records.append(record)
                (out_dir / "progress.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
            fixed = False
            if stage_limit == 3:
                second = normalized_image((out_dir / ("pcc2" + suffix)).read_bytes())
                third = normalized_image((out_dir / ("pcc3" + suffix)).read_bytes())
                fixed = second == third
                if not fixed:
                    raise RuntimeError("pcc2/pcc3 normalized images differ; classify drift before qualification")
            receipt = {"schema": "pcc.platform-bootstrap.v1", "target": target, "gc": str(gc_backend),
                       "source": source, "runtime": runtime_identity,
                       "stages": records, "fixed_point": fixed,
                       "qualification_scope": "source-stable self-host fixed point and native GC/object/integer/exception canary",
                       "execution_owner_evidence": {
                           "method": "process-tree executable sampling and artifact dependency headers",
                           "sampling_interval_seconds": 0.2,
                           "exhaustive_subprocess_trace": False,
                           "limitation": "processes that start and exit between samples may not be observed",
                       },
                       "qualified": fixed and source["clean_commit"]}
            (out_dir / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
            return receipt
        except BaseException as exc:
            (out_dir / "failure.json").write_text(json.dumps({"source": source, "stages": records,
                                                              "error": str(exc)}, indent=2), encoding="utf-8")
            raise


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--gc", default="all", choices=("all", "0", "1", "2", "3", "4"))
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--stage", type=int, default=3, choices=(1, 2, 3))
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--rss-limit", type=int, default=17179869184)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.rss_limit <= 0:
        parser.error("timeout and RSS limit must be positive")
    backends = ("0", "1", "2", "3", "4") if args.gc == "all" else (args.gc,)
    for backend in backends:
        run_chain(backend, Path(args.out_dir) / ("gc" + backend), stage_limit=args.stage,
                  timeout=args.timeout, rss_limit=args.rss_limit, allow_dirty=args.allow_dirty)


if __name__ == "__main__":
    main()

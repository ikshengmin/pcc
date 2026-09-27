"""Build a target runtime in process using pcc's frontend, emitter and ar.

The checked-in module inventory remains the Makefile's two module variables;
Make is not executed. Explicit target selection reaches both frontend ABI
lowering and object emission. Existing provenance receipts remain authoritative.
"""

import os
import json
from pathlib import Path
from pcc.backend.self_backend_target_match import target_os_name


# Runtime construction requests library IR, even when its caller is building
# an executable through the direct or deferred frontend routes.
_RUNTIME_IR_OUTPUT_ENV = (
    "PCC_DIRECT_INDEXED_KERNEL_CAPTURE",
    "PCC_DIRECT_INDEXED_KERNEL_EMIT",
    "PCC_DIRECT_INDEXED_KERNEL_REQUIRE_ZERO_FALLBACK",
    "PCC_DIRECT_INDEXED_KERNEL_FUSE_USES",
    "PCC_DIRECT_INDEXED_KERNEL_RELEASE_FRONTEND",
    "PCC_DIRECT_INDEXED_KERNEL_VALIDATE",
    "PCC_DIRECT_INDEXED_SIDECAR",
    "PCC_TEXT_INDEXED_KERNEL_EMIT",
    "PCC_DEFER_FRONTEND_CODEGEN",
    "PCC_DEFER_FRONTEND_CODEGEN_PLAN",
    "PCC_DEFER_FRONTEND_OUTPUT",
    "PCC_DEFER_SELF_LINK_PLAN",
)


def runtime_build_config() -> dict:
    threads = str(os.environ.get("PCC_WITH_THREADS", "")).strip().lower() in (
        "1", "true", "yes", "on",
    )
    refcount = str(os.environ.get("PCC_REFCOUNT_KIND", "") or "").strip().lower() or "atomic"
    if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in refcount):
        raise ValueError("invalid refcount configuration for runtime cache")
    return {"threads": threads, "refcount": refcount}


# The Makefile compiles this module without the runtime pass list and with
# automatic safepoint polls off; the owned builder mirrors both settings.
_THREAD_KERNEL_MODULE = "freestanding_thread_kernel_pthread"


def runtime_ir_passes(runtime_dir: str) -> str:
    """The Makefile's PCC_RUNTIME_IR_PASSES; like its ``?=``, the environment wins."""
    explicit = str(os.environ.get("PCC_RUNTIME_IR_PASSES", "") or "").strip()
    if explicit:
        return explicit
    with open(os.path.join(runtime_dir, "Makefile"), encoding="utf-8") as stream:
        for line in stream:
            if line.startswith("PCC_RUNTIME_IR_PASSES ?="):
                return line.split("=", 1)[1].strip()
    raise ValueError("runtime Makefile does not define PCC_RUNTIME_IR_PASSES")


def runtime_modules(runtime_dir: str, target: str, threads: bool = False) -> list[str]:
    names = []
    with open(os.path.join(runtime_dir, "Makefile"), encoding="utf-8") as stream:
        for line in stream:
            if line.startswith(("PY_MODULES =", "PY_MODULES +=", "FREESTANDING_PY_MODULES =", "FREESTANDING_PY_MODULES +=")):
                for name in line.split("=", 1)[1].split():
                    if name not in names:
                        names.append(name)
    # The only conditionally selected entries in these two inventories are
    # the native thread kernel variants. Preserve all managed/GC modules.
    names = [name for name in names if name not in ("freestanding_thread_kernel", "freestanding_thread_kernel_pthread")]
    names.append("freestanding_thread_kernel_pthread" if threads else "freestanding_thread_kernel")
    if target_os_name(target) == "win32":
        names.extend(("freestanding_windows", "freestanding_windows_process", "freestanding_windows_start", "freestanding_windows_socket", "freestanding_windows_system", "freestanding_windows_file_lock", "freestanding_windows_time", "freestanding_time_format", "freestanding_windows_directory", "freestanding_windows_signal", "freestanding_signal_set"))
        if threads:
            names.append("freestanding_windows_threads")
    elif target_os_name(target) == "linux":
        if threads:
            names.append("freestanding_linux_threads")
        names.append("freestanding_c_linux_start")
        names.extend(("freestanding_linux_libc", "freestanding_linux_time", "freestanding_time_format", "freestanding_posix_timezone", "freestanding_linux_directory", "freestanding_linux_signal", "freestanding_linux_static_loader", "freestanding_signal_set"))
        if target.startswith(("arm64-", "aarch64-")):
            names.append("freestanding_linux_aarch64")
    return names


def _compile_runtime_module(name, source, ir_path, target):
    from .pipeline import compile_python

    # The kernel implements pcc_thread_safepoint itself. Match the Makefile's
    # per-module setting: instrumenting its entry or helpers would recurse
    # back into the kernel before it has initialized its synchronization.
    suppress_polls = name == _THREAD_KERNEL_MODULE
    names = list(_RUNTIME_IR_OUTPUT_ENV)
    if suppress_polls:
        names.append("PCC_WITH_THREADS")
    previous = {key: os.environ.get(key) for key in names}
    try:
        for key in _RUNTIME_IR_OUTPUT_ENV:
            os.environ.pop(key, None)
        if suppress_polls:
            os.environ["PCC_WITH_THREADS"] = "0"
        compile_python(source, ir_path, emit_llvm_only=True, python_library=True,
                       libpython_mode="off", backend="self", target_triple=target)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def build_runtime_archive(runtime_dir: str, archive: str, target: str) -> None:
    from .pipeline_runtime_archive import _acquire_runtime_build_lock, _remove_runtime_build_lock
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.ar_writer import write_archive, _defined_symbols
    from pcc.native_ir.driver import optimize_ir
    from pcc.tools.runtime_archive_provenance import (
        write_pcc_python_receipt, assemble_runtime_archive_manifest,
    )
    if target_os_name(target) not in ("linux", "win32"):
        raise ValueError("owned platform runtime builder target is unsupported: " + target)
    config = runtime_build_config()
    threads = config["threads"]
    passes = runtime_ir_passes(runtime_dir)
    output_dir = archive + ".objects"
    os.makedirs(output_dir, exist_ok=True)
    lock = os.path.join(runtime_dir, ".pcc-runtime-build.lock")
    _acquire_runtime_build_lock(lock)
    try:
        members = []
        objects = []
        capi = set()
        for name in runtime_modules(runtime_dir, target, threads):
            source = os.path.join(runtime_dir, "py", name + ".py")
            ir_path = os.path.join(output_dir, name + ".ll")
            object_path = os.path.join(output_dir, name + ".o")
            _compile_runtime_module(name, source, ir_path, target)
            with open(ir_path, encoding="utf-8") as stream:
                ir_text = stream.read()
            # As in the Makefile rule, the object and its receipt describe the
            # optimized IR: precise stack maps expect promoted frame-slot
            # locals, and unoptimized runtime code is not what Darwin ships.
            if name != _THREAD_KERNEL_MODULE:
                ir_text = optimize_ir(ir_text, passes)
                with open(ir_path, "w", encoding="utf-8") as stream:
                    stream.write(ir_text)
            data = emit_owned_object(ir_text, target)
            with open(object_path, "wb") as stream:
                stream.write(data)
            write_pcc_python_receipt(object_path=Path(object_path), ir_path=Path(ir_path),
                                     source_path=Path(source), runtime_root=Path(runtime_dir),
                                     target_triple=target, object_emitter="pcc", object_bytes=data,
                                     runtime_build_config=config)
            members.append((name + ".o", data))
            objects.append(Path(object_path))
            _kind, definitions = _defined_symbols(data)
            for symbol in definitions:
                if symbol.startswith(("Py", "_Py")):
                    capi.add(symbol)
        temporary = archive + ".tmp"
        with open(temporary, "wb") as stream:
            stream.write(write_archive(members))
        capi_path = temporary + ".capi_syms"
        with open(capi_path, "w", encoding="ascii") as stream:
            stream.write("\n".join(sorted(capi)) + "\n")
        assemble_runtime_archive_manifest(Path(temporary), objects, runtime_root=Path(runtime_dir),
                                           capi_inventory_path=Path(capi_path))
        os.replace(capi_path, archive + ".capi_syms")
        os.replace(temporary, archive)
        # Publish the completed receipt after its payloads, as the Makefile's
        # archive builder does. Interrupted publication cannot bless old data.
        os.replace(temporary + ".provenance.json", archive + ".provenance.json")
    finally:
        _remove_runtime_build_lock(lock, os.path.join(lock, "owner"))


def _manifest_matches_config(receipt: dict, runtime_dir: str, target: str, config: dict) -> bool:
    if not isinstance(receipt, dict) or receipt.get("target_triple") != target:
        return False
    members = receipt.get("members")
    if not isinstance(members, list) or not members:
        return False
    expected = {name + ".o" for name in runtime_modules(runtime_dir, target, config["threads"])}
    if len(members) != len(expected):
        return False
    actual = set()
    for member in members:
        if not isinstance(member, dict) or member.get("runtime_build_config") != config:
            return False
        actual.add(member.get("member"))
    return actual == expected


def ensure_target_runtime(runtime_dir: str, target: str, *, packaged_archive: str = "",
                          wheel_matches=None, explicit_archive: str | None = None) -> str:
    """Reuse only a source/codegen/target/configuration-matched owned archive."""
    from pcc.backend.self_backend_targets import resolve_self_backend_target
    from pcc.tools.runtime_archive_provenance import (
        verify_runtime_archive_manifest, manifest_is_stale_for_current_codegen,
    )
    resolve_self_backend_target(target)
    if target_os_name(target) not in ("linux", "win32"):
        raise ValueError("owned platform runtime builder target is unsupported: " + target)
    override = str(os.environ.get("PCC_RUNTIME_DIR", "") or "").strip()
    if override:
        runtime_dir = os.path.abspath(override)
        if packaged_archive:
            packaged_archive = os.path.join(runtime_dir, os.path.basename(packaged_archive))
    if not os.path.isdir(runtime_dir):
        raise ValueError("runtime directory not found: " + runtime_dir)
    config = runtime_build_config()
    threads = config["threads"]
    refcount = config["refcount"]
    output = os.path.join(runtime_dir, "build_owned", target,
                          ("threads-" if threads else "single-") + refcount,
                          "libpy_runtime_pcc_py.a")
    explicit = (str(os.environ.get("PCC_RUNTIME_ARCHIVE", "") or "").strip()
                if explicit_archive is None else str(explicit_archive).strip())
    if explicit_archive is not None and not explicit:
        raise ValueError("explicit runtime archive path is empty")
    candidates = [os.path.abspath(explicit)] if explicit else [packaged_archive, output]
    for candidate in candidates:
        if not candidate:
            continue
        if not os.path.isfile(candidate):
            if explicit:
                raise ValueError("explicit runtime archive is missing: " + candidate)
            continue
        try:
            # A wheel's stamp binds archive, manifest and inventory bytes.
            # Its source/codegen need not match the consumer's checkout, but
            # target and build configuration must still match the request.
            installed = wheel_matches is not None and wheel_matches(candidate)
            if installed:
                with open(candidate + ".provenance.json", encoding="utf-8") as stream:
                    receipt = json.loads(stream.read())
            else:
                receipt = verify_runtime_archive_manifest(Path(candidate), runtime_root=Path(runtime_dir))
            if (_manifest_matches_config(receipt, runtime_dir, target, config)
                    and (installed or not manifest_is_stale_for_current_codegen(receipt))):
                return candidate
        except (ValueError, OSError, KeyError, TypeError):
            if explicit:
                raise
        if explicit:
            raise ValueError("explicit runtime archive does not match the target/source configuration")
    os.makedirs(os.path.dirname(output), exist_ok=True)
    build_runtime_archive(runtime_dir, output, target)
    receipt = verify_runtime_archive_manifest(Path(output), runtime_root=Path(runtime_dir))
    if not _manifest_matches_config(receipt, runtime_dir, target, config):
        raise ValueError("owned runtime build produced a mismatched target/configuration")
    return output


def main(argv=None):
    import argparse
    from .pipeline_targets import host_target_triple
    parser = argparse.ArgumentParser(description="Build pcc's owned target runtime")
    parser.add_argument("--target", default=host_target_triple())
    parser.add_argument("--output", required=True)
    parser.add_argument("--runtime-dir", default=str(Path(__file__).resolve().parents[1] / "py_runtime"))
    args = parser.parse_args(argv)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    build_runtime_archive(args.runtime_dir, os.path.abspath(args.output), args.target)


if __name__ == "__main__":
    main()

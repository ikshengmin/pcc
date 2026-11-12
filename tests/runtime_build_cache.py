"""Per-pytest-process cache for immutable native runtime build artifacts.

Probe sources and executables remain in each test's ``tmp_path``.  Only a
builder's copied runtime directory/archive is reused when its explicit
arguments and build-relevant environment are identical.  A failed builder is
never cached.

A failed source-addressed runtime build (``cached_pcc_python_runtime``)
reports its compiler and linker diagnostics first and keeps the full Make
log in a file. A compiler diagnostic (``error: PCC-...``) is a property of
the cache key's sources and compiler, so it is recorded for the rest of the
pytest run: every other dependent test, in every xdist worker, fails with
the same diagnosis at once instead of repeating a minutes-long build. The
next run builds again; a failure without a compiler diagnostic is never
recorded.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache, wraps
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Callable, ParamSpec, TypeVar

from pcc.backend.precise_stackmap import precise_stackmap_tables_enabled
from pcc.backend.self_backend_cache_identity import (
    self_backend_emitter_source_identity,
)
from pcc.frontends.python.pipeline_runtime_archive import target_id, write_target_stamp
from pcc.tools.runtime_archive_provenance import (
    ProvenanceError,
    capi_inventory_path_for_archive,
    codegen_checksum,
    manifest_path_for_archive,
    verify_runtime_archive_manifest,
)
from tests.native_provisioning import require_native_provisioning_allowed

P = ParamSpec("P")
T = TypeVar("T")

_BUILD_ENV_KEYS = (
    "CC",
    "CFLAGS",
    "CPPFLAGS",
    "LDFLAGS",
    "PCC_BACKEND",
    "PCC_GC_BACKEND",
    "PCC_REFCOUNT_KIND",
    "PCC_WITH_THREADS",
    "PCC_WITH_LIBPYTHON",
)
_PCC_PY_ARCHIVE_ENV_KEYS = (
    "CC",
    "CFLAGS",
    "CPPFLAGS",
    "LDFLAGS",
    "PCC_REFCOUNT_KIND",
    "PCC_IR_TO_OBJ_EMITTER",
)

_REPO_ROOT = Path(__file__).absolute().parents[1]
_RUNTIME_DIR = _REPO_ROOT / "pcc" / "runtime"
_PCC_RUNTIME_CACHE_MARKER_SCHEMA = "pcc.runtime-build-cache.v4"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _pcc_runtime_cache_marker_value(*, key: str, archive: Path) -> dict[str, str]:
    manifest = manifest_path_for_archive(archive)
    capi_inventory = capi_inventory_path_for_archive(archive)
    target = Path(str(archive) + ".target")
    return {
        "schema": _PCC_RUNTIME_CACHE_MARKER_SCHEMA,
        "key": key,
        "archive_sha256": _sha256_file(archive),
        "manifest_sha256": _sha256_file(manifest),
        "capi_inventory_sha256": _sha256_file(capi_inventory),
        "target_sha256": _sha256_file(target),
    }


def _pcc_runtime_cache_is_complete(
    *,
    runtime: Path,
    archive: Path,
    marker: Path,
    key: str,
) -> bool:
    manifest = manifest_path_for_archive(archive)
    capi_inventory = capi_inventory_path_for_archive(archive)
    target = Path(str(archive) + ".target")
    if (
        not archive.is_file()
        or not manifest.is_file()
        or not capi_inventory.is_file()
        or not target.is_file()
        or not marker.is_file()
    ):
        return False
    try:
        marker_value = json.loads(marker.read_text(encoding="utf-8"))
        verified = verify_runtime_archive_manifest(archive, runtime_root=runtime)
        manifest_target = verified.get("target_triple")
        if not isinstance(manifest_target, str):
            return False
        expected_target = target_id(manifest_target) + "\n"
        if target.read_text(encoding="utf-8") != expected_target:
            return False
        expected = _pcc_runtime_cache_marker_value(key=key, archive=archive)
        if marker_value != expected:
            return False
    except (OSError, UnicodeError, json.JSONDecodeError, ProvenanceError):
        return False
    return True


def _write_pcc_runtime_cache_marker(
    marker: Path,
    *,
    key: str,
    archive: Path,
) -> None:
    marker.write_text(
        json.dumps(
            _pcc_runtime_cache_marker_value(key=key, archive=archive),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


@lru_cache(maxsize=1)
def self_host_source_key() -> str:
    """Fingerprint inputs that can change the self-host compiler stages."""

    digest = hashlib.sha256()
    digest.update(b"pcc.self-host-test-artifact.v2\0")
    digest.update(str(_REPO_ROOT.resolve()).encode("utf-8"))
    digest.update(b"\0")
    digest.update(sys.version.encode("utf-8"))
    digest.update(b"\0")
    for name in (
        "CC",
        "CFLAGS",
        "CPPFLAGS",
        "LDFLAGS",
        "MACOSX_DEPLOYMENT_TARGET",
        "PCC_GC_BACKEND",
        "PCC_HOST_PYTHON",
        "PCC_PYTHON_CONFIG",
        "PCC_REFCOUNT_KIND",
        "PCC_WITH_THREADS",
        "PCC_SELF_TARGET_PASSES",
        "PCC_SELF_TARGET_PASS_TRANSPORT",
    ):
        digest.update(name.encode("utf-8"))
        digest.update(b"=")
        digest.update(str(os.environ.get(name, "")).encode("utf-8"))
        digest.update(b"\0")
    explicit_runtime = str(os.environ.get("PCC_RUNTIME_ARCHIVE", "")).strip()
    digest.update(b"PCC_RUNTIME_ARCHIVE\0")
    digest.update(explicit_runtime.encode("utf-8"))
    digest.update(b"\0")
    if explicit_runtime:
        archive = Path(explicit_runtime).expanduser().resolve()
        for label, artifact in (
            ("archive", archive),
            ("manifest", manifest_path_for_archive(archive)),
            ("capi_inventory", capi_inventory_path_for_archive(archive)),
        ):
            digest.update(label.encode("utf-8"))
            digest.update(b"\0")
            try:
                digest.update(artifact.read_bytes())
            except OSError:
                digest.update(b"<missing>")
            digest.update(b"\0")
    cc = os.environ.get("CC", "cc")
    try:
        toolchain = subprocess.check_output(
            [cc, "--version"],
            stderr=subprocess.STDOUT,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        toolchain = b"<unknown-toolchain>"
    digest.update(toolchain)
    digest.update(b"\0")
    files = []
    for path in (_REPO_ROOT / "pcc").rglob("*"):
        if not path.is_file():
            continue
        if any(part.startswith(".") or part == "__pycache__" for part in path.parts):
            continue
        if path.suffix not in {".c", ".h", ".py"} and path.name != "Makefile":
            continue
        files.append(path)
    for path in sorted(files):
        relative = path.relative_to(_REPO_ROOT).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(path.read_bytes())
    return digest.hexdigest()


@lru_cache(maxsize=1)
def self_backend_object_cache_key() -> str:
    """Fingerprint only the implementation behind IR-shard object emission."""

    return self_backend_emitter_source_identity(_REPO_ROOT)


def cached_self_host_oracle_dir() -> Path:
    """Return a source-addressed directory for immutable pcc1/pcc2/pcc3."""

    root = Path.home() / ".cache" / "pcc" / "test-artifacts" / "self-host-oracle"
    root.mkdir(parents=True, exist_ok=True)
    artifact_dir = root / self_host_source_key()[:24]
    artifact_dir.mkdir(parents=True, exist_ok=True)
    return artifact_dir


def self_host_object_cache_dir() -> Path:
    """Return the persistent compiled self-backend object-cache directory."""

    path = Path.home() / ".cache" / "pcc" / "test-artifacts" / "self-host-objects"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_runtime_build(
    builder: Callable[P, T],
) -> Callable[P, T]:
    """Cache a runtime builder while excluding its first ``tmp_path`` arg."""

    cache: dict[tuple[object, ...], T] = {}

    @wraps(builder)
    def wrapped(tmp_path, *args, **kwargs):
        env_key = tuple((name, os.environ.get(name)) for name in _BUILD_ENV_KEYS)
        key = (
            args,
            tuple(sorted(kwargs.items())),
            env_key,
        )
        if key in cache:
            return cache[key]
        require_native_provisioning_allowed()
        artifact = builder(tmp_path, *args, **kwargs)
        cache[key] = artifact
        return artifact

    return wrapped


def _selected_runtime_source(runtime_source: Path | None) -> Path:
    if runtime_source is None:
        return _RUNTIME_DIR
    selected = Path(runtime_source).expanduser().resolve()
    if not selected.is_dir():
        raise NotADirectoryError(f"runtime source directory does not exist: {selected}")
    return selected


def _pcc_runtime_source_key(
    pcc_bin: Path, *, runtime_source: Path | None = None,
) -> str:
    digest = hashlib.sha256()
    digest.update(sys.version.encode("utf-8"))
    digest.update(os.path.realpath(pcc_bin).encode("utf-8"))
    # PCC_BACKEND/PCC_GC_BACKEND do not affect ``--python-library
    # --emit-llvm`` output. Including them would rebuild this identical
    # archive for every meta-matrix label.
    for name in _PCC_PY_ARCHIVE_ENV_KEYS:
        digest.update(name.encode("utf-8"))
        digest.update(str(os.environ.get(name, "")).encode("utf-8"))
    roots = (
        _RUNTIME_DIR,
        _REPO_ROOT / "pcc" / "backend",
        _REPO_ROOT / "pcc" / "frontends" / "c" / "codegen",
        _REPO_ROOT / "pcc" / "frontends" / "c" / "evaluator",
        _REPO_ROOT / "pcc" / "ir",
        _REPO_ROOT / "pcc" / "frontends" / "c" / "parse",
        _REPO_ROOT / "pcc" / "frontends" / "python",
        _REPO_ROOT / "pcc" / "tools",
    )
    selected_runtime = _selected_runtime_source(runtime_source)
    files = []
    for root in roots:
        source_root = selected_runtime if root == _RUNTIME_DIR else root
        for source_path in source_root.rglob("*"):
            if not source_path.is_file():
                continue
            # Override location and snapshot permissions are not build inputs.
            # Preserve the original logical paths and ordering in the digest.
            path = root / source_path.relative_to(source_root)
            if any(
                part.startswith(".") or part == "__pycache__" for part in path.parts
            ):
                continue
            if path.suffix not in {".c", ".h", ".py"} and path.name != "Makefile":
                continue
            files.append((path, source_path))
    for path, source_path in sorted(files):
        digest.update(path.relative_to(_REPO_ROOT).as_posix().encode("utf-8"))
        digest.update(source_path.read_bytes())
    return digest.hexdigest()[:24]


def _pcc_runtime_cache_key(
    pcc_bin: Path, *, variant: str, runtime_source: Path | None = None,
) -> str:
    """Bind the immutable cache directory to its publication format."""

    digest = hashlib.sha256()
    digest.update(_PCC_RUNTIME_CACHE_MARKER_SCHEMA.encode("utf-8"))
    digest.update(b"\0")
    source_key = (
        _pcc_runtime_source_key(pcc_bin)
        if runtime_source is None
        else _pcc_runtime_source_key(pcc_bin, runtime_source=runtime_source)
    )
    digest.update(source_key.encode("ascii"))
    # Use the same compiler identity as runtime provenance admission. The
    # partial source key alone omits provider/driver changes and can select an
    # archive that the explicit-runtime fixture correctly rejects as stale.
    digest.update(b"\0codegen\0")
    digest.update(codegen_checksum().encode("ascii"))
    # A table-enabled runtime emits different objects from the same compiler.
    if precise_stackmap_tables_enabled():
        digest.update(b"\0precise-stackmap-tables\0")
    return digest.hexdigest()[:24] + "-" + variant


_PCC_RUNTIME_BUILD_FAILURE_SCHEMA = "pcc.runtime-build-failure.v1"
# Lines of a failed build worth reading: compiler/linker/Make errors and
# Python failures, not the hundreds of commands Make echoes.
_BUILD_DIAGNOSTIC = re.compile(
    r"\berror\b|\*\*\*|undefined|can't open|no such file|not found|traceback",
    re.IGNORECASE,
)
_COMPILER_DIAGNOSTIC = re.compile(r"\berror: PCC-[A-Z0-9-]+")
_BUILD_DIAGNOSTIC_LINES = 40


class RuntimeBuildFailed(AssertionError):
    """A runtime archive build failed; the message leads with its diagnostics."""


def runtime_build_diagnostics(output: str) -> list[str]:
    """The diagnostic lines of a failed build, in order, without repeats."""
    picked: list[str] = []
    for line in output.splitlines():
        text = line.strip()
        if not text or text in picked:
            continue
        if _BUILD_DIAGNOSTIC.search(text) or text.startswith("note:"):
            picked.append(text)
            if len(picked) == _BUILD_DIAGNOSTIC_LINES:
                break
    return picked


def _build_run_identity() -> str:
    """One pytest run: xdist gives all of its workers one test-run id."""
    run = os.environ.get("PYTEST_XDIST_TESTRUNUID", "")
    return run if run else "pid-" + str(os.getpid())


def _runtime_build_failure_paths(cache_root: Path, key: str) -> tuple[Path, Path]:
    return cache_root / (key + ".failed.json"), cache_root / (key + ".failed.log")


def _runtime_build_failure_message(
    headline: str, diagnostics: list[str], log: Path,
) -> str:
    return "\n".join(
        [headline, *(diagnostics or ["(no diagnostic line; see the log)"]),
         "full build log: " + str(log)]
    )


def _record_runtime_build_failure(cache_root: Path, key: str, output: str) -> str:
    """Keep ``output`` as the key's build log and return the failure message;
    a compiler diagnostic is recorded for the rest of this run."""
    record, log = _runtime_build_failure_paths(cache_root, key)
    log.write_text(output, encoding="utf-8")
    diagnostics = runtime_build_diagnostics(output)
    if _COMPILER_DIAGNOSTIC.search(output):
        record.write_text(
            json.dumps(
                {
                    "schema": _PCC_RUNTIME_BUILD_FAILURE_SCHEMA,
                    "key": key,
                    "run": _build_run_identity(),
                    "diagnostics": diagnostics,
                    "log": str(log),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    else:
        record.unlink(missing_ok=True)
    return _runtime_build_failure_message(
        "runtime archive build failed (cache key " + key + "):", diagnostics, log,
    )


def _recorded_runtime_build_failure(cache_root: Path, key: str) -> str | None:
    """This run's recorded compiler failure for ``key``, as a message."""
    record, _log = _runtime_build_failure_paths(cache_root, key)
    try:
        value = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(value, dict)
        or value.get("schema") != _PCC_RUNTIME_BUILD_FAILURE_SCHEMA
        or value.get("key") != key
        or value.get("run") != _build_run_identity()
    ):
        return None
    return _runtime_build_failure_message(
        "runtime archive build failed earlier in this run (cache key " + key
        + "), not rebuilt:",
        [str(line) for line in value.get("diagnostics", [])],
        Path(str(value.get("log", ""))),
    )


def _clear_runtime_build_failure(cache_root: Path, key: str) -> None:
    for path in _runtime_build_failure_paths(cache_root, key):
        path.unlink(missing_ok=True)


def _make_runtime_staging_writable(runtime: Path) -> None:
    """Change only the copied tree; frozen source snapshots stay read-only."""
    runtime.chmod(stat.S_IMODE(runtime.stat().st_mode) | stat.S_IRWXU)
    for directory, directories, files in os.walk(runtime):
        for name in directories:
            path = Path(directory) / name
            path.chmod(stat.S_IMODE(path.stat().st_mode) | stat.S_IRWXU)
        for name in files:
            path = Path(directory) / name
            path.chmod(stat.S_IMODE(path.stat().st_mode) | stat.S_IRUSR | stat.S_IWUSR)


def _cached_pcc_python_runtime(
    *, threaded: bool, runtime_source: Path | None = None,
) -> Path:
    selected_runtime = _selected_runtime_source(runtime_source)
    pcc_bin = _REPO_ROOT / ".venv" / "bin" / "pcc"
    # Every runtime is threaded: both public helpers share one build.
    if not threaded:
        raise ValueError("the single-thread runtime was removed")
    variant = "pcc-py"
    key = _pcc_runtime_cache_key(pcc_bin, variant=variant, runtime_source=runtime_source)
    selected_cache = os.environ.get("PCC_TEST_RUNTIME_CACHE_ROOT", "")
    cache_root = (
        Path(selected_cache) if selected_cache
        else Path.home() / ".cache" / "pcc" / "test-artifacts" / "runtime-builds"
    )
    if selected_cache and not cache_root.is_absolute():
        raise ValueError("PCC_TEST_RUNTIME_CACHE_ROOT must be absolute")
    cache_root.mkdir(parents=True, exist_ok=True)
    runtime = cache_root / key
    marker = runtime / (".pcc-" + variant + "-complete")
    archive = runtime / "libpy_runtime_pcc_py.a"
    lock_path = cache_root / (key + ".lock")

    with lock_path.open("a", encoding="utf-8") as lock_file:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - POSIX test environment
            fcntl = None
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        staging_root: Path | None = None
        try:
            if _pcc_runtime_cache_is_complete(
                runtime=runtime,
                archive=archive,
                marker=marker,
                key=key,
            ):
                return runtime
            recorded = _recorded_runtime_build_failure(cache_root, key)
            if recorded is not None:
                raise RuntimeBuildFailed(recorded)
            require_native_provisioning_allowed()
            if runtime.exists():
                shutil.rmtree(runtime)
            staging_root = Path(tempfile.mkdtemp(prefix=key + ".", dir=str(cache_root)))
            work_runtime = staging_root / "py_runtime"
            shutil.copytree(
                selected_runtime,
                work_runtime,
                ignore=shutil.ignore_patterns(
                    "_native",
                    "__pycache__",
                    "build",
                    "build_*",
                    "*.a",
                    "*.a.target",
                    "*.a.provenance.json",
                    "*.a.capi_syms",
                ),
            )
            _make_runtime_staging_writable(work_runtime)
            # Module rules are independent (.py -> .ll -> .o); the archive
            # recipe holds its own lock, so a bounded -j is safe and turns a
            # ~15 minute serial cold build into a few minutes.
            command = [
                "make",
                "-C",
                str(work_runtime),
                "-j" + str(min(8, os.cpu_count() or 1)),
                f"PCC={pcc_bin}",
                f"PYTHON={sys.executable}",
                f"PCC_REPO_ROOT={_REPO_ROOT}",
            ]
            command.append("PCC_WITH_THREADS=1")
            command.append("libpy_runtime_pcc_py.a")
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=900,
            )
            if result.returncode != 0:
                raise RuntimeBuildFailed(
                    _record_runtime_build_failure(
                        cache_root, key, result.stdout + result.stderr
                    )
                )
            work_archive = work_runtime / archive.name
            verified = verify_runtime_archive_manifest(
                work_archive,
                runtime_root=work_runtime,
            )
            manifest_target = verified.get("target_triple")
            if not isinstance(manifest_target, str):
                raise ProvenanceError(
                    "runtime archive manifest target triple is missing"
                )
            expected_target = target_id(manifest_target)
            write_target_stamp(str(work_archive), expected_target)
            work_target = Path(str(work_archive) + ".target")
            if (
                not work_target.is_file()
                or work_target.read_text(encoding="utf-8")
                != expected_target + "\n"
            ):
                raise OSError("failed to publish runtime target stamp")
            _write_pcc_runtime_cache_marker(
                work_runtime / marker.name,
                key=key,
                archive=work_archive,
            )
            os.replace(work_runtime, runtime)
            _clear_runtime_build_failure(cache_root, key)
            return runtime
        finally:
            if staging_root is not None:
                shutil.rmtree(staging_root, ignore_errors=True)
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def cached_pcc_python_runtime(*, runtime_source: Path | None = None) -> Path:
    """Build/reuse runtime sources with the compiler's effective thread mode."""

    from pcc.frontends.python.owned_runtime_build import runtime_build_config

    return _cached_pcc_python_runtime(
        threaded=runtime_build_config()["threads"], runtime_source=runtime_source,
    )


def cached_threaded_pcc_python_runtime(*, runtime_source: Path | None = None) -> Path:
    """Build/reuse one immutable threaded pcc-Python runtime source tree."""

    return _cached_pcc_python_runtime(threaded=True, runtime_source=runtime_source)

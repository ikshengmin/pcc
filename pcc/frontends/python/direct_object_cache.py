"""Reuse a codegen worker's native object when its emitter input is unchanged.

A Stage1 codegen worker spends about 90% of its time in the self backend,
after the frontend has captured the module's indexed kernel. The backend's
output is a function of that kernel, the emitter sources, the target and the
PCC_ settings, so an object emitted once for the same four can be reused:
after an edit, most modules' kernels are unchanged and only the frontend
reruns for them.

The cache is active only in CPython workers (native pcc workers are not yet
qualified for it) and where the bootstrap names the emitter identity
(PCC_SELF_BACKEND_OBJECT_CACHE_IDENTITY, pcc/backend/self_backend_cache_
identity.py) and PCC_SELF_BACKEND_OBJECT_CACHE is not off. The kernel is keyed
by its own codec bytes (encode_indexed_module_file, the cross-process handoff
format, so it holds everything the backend reads). Settings that only steer
scheduling, paths, timing or cache bookkeeping are left out of the key; any
other PCC_ variable is in it, so an unknown setting can only cost a miss.
"""

from __future__ import annotations

import hashlib
import os

_SCHEMA = b"pcc.direct-object-cache.v1\0"
_IDENTITY_ENV = "PCC_SELF_BACKEND_OBJECT_CACHE_IDENTITY"
_ENABLED_ENV = "PCC_SELF_BACKEND_OBJECT_CACHE"
_DIR_ENV = "PCC_SELF_BACKEND_OBJECT_CACHE_DIR"
_OFF = ("0", "false", "no", "off", "disable", "disabled")
# Settings that cannot change the emitted bytes.
_UNKEYED_PREFIXES = (
    "PCC_WORKER_", "PCC_BOOTSTRAP_", "PCC_STAGE1_CHECKPOINT_",
    "PCC_SELF_BACKEND_OBJECT_CACHE", "PCC_PY_FRONTEND_IR_CACHE",
    # scripts/pcc_perf.py's sampler and measurement home.
    "PCC_COMPILE_PROFILE_", "PCC_PERF_HOME",
)
_UNKEYED = frozenset((
    "PCC_PY_FRONTEND_JOBS", "PCC_SELF_BACKEND_JOBS", "PCC_MACHO_LINK_JOBS",
    "PCC_PY_FRONTEND_WORKER_TIMING", "PCC_NO_AUTO_PCC1", "PCC_PACKAGE_SITE",
    "PCC_LOG",
))


def _keyed_settings() -> list:
    rows = []
    for name in sorted(os.environ):
        if not name.startswith("PCC_") or name in _UNKEYED:
            continue
        if any(name.startswith(prefix) for prefix in _UNKEYED_PREFIXES):
            continue
        rows.append(name + "=" + str(os.environ.get(name, "")))
    return rows


def cache_path(module, target: str, work_dir: str, index: int) -> str:
    """The cache file for MODULE's object, or "" when the cache is off."""
    import sys as execution_runtime

    if execution_runtime.implementation.name != "cpython":
        # Host Stage1 workers only, until native workers are qualified.
        return ""
    identity = str(os.environ.get(_IDENTITY_ENV, "") or "").strip()
    enabled = str(os.environ.get(_ENABLED_ENV, "") or "").strip().lower()
    if not identity or enabled in _OFF:
        return ""
    directory = str(os.environ.get(_DIR_ENV, "") or "").strip()
    if not directory:
        return ""
    from pcc.backend.self_backend_indexed_codec import encode_indexed_module_file

    encoded_path = os.path.join(work_dir, "module_" + str(index) + ".cache-key.pidx")
    try:
        encode_indexed_module_file(encoded_path, module)
        digest = hashlib.sha256()
        digest.update(_SCHEMA)
        digest.update(identity.encode("utf-8") + b"\0")
        digest.update(str(target).encode("utf-8") + b"\0")
        for row in _keyed_settings():
            digest.update(row.encode("utf-8") + b"\0")
        with open(encoded_path, "rb") as stream:
            while True:
                chunk = stream.read(1 << 20)
                if not chunk:
                    break
                digest.update(chunk)
    finally:
        if os.path.exists(encoded_path):
            os.unlink(encoded_path)
    key = digest.hexdigest()
    return os.path.join(os.path.expanduser(directory), "direct", key[:2], key + ".pco")


def read(path: str):
    """The cached object bytes, or None."""
    if not path:
        return None
    try:
        with open(path, "rb") as stream:
            return stream.read()
    except OSError:
        return None


def store(path: str, data: bytes) -> None:
    """Publish DATA at PATH atomically; a failed store only costs a miss."""
    if not path:
        return
    temporary = path + ".tmp." + str(os.getpid())
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(temporary, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    except OSError:
        if os.path.exists(temporary):
            os.unlink(temporary)

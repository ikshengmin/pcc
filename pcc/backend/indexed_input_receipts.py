"""Opt-in observation of the concrete indexed object actually sent to emission.

Every public entry is best effort. A diagnostic failure never substitutes for a
compiler failure, and leaves incomplete evidence which mandatory consumers reject.
No entry is called or imported by the normal no-dump path. Claims are exclusive:
this protocol has no retry/overwrite policy and does not accept cached inputs.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

from pcc.extern import c_int64, c_obj, c_rawptr, extern
from pcc.unsafe import (
    directory_close, directory_error, directory_next, directory_open,
    load_i8, ptr_is_null, stack_alloc, store_i8, strlen,
)


_receipt_entry_name = extern("py_str_new", (c_rawptr, c_int64), c_obj)


LIMIT = 2 * 1024 * 1024
INVENTORY_LIMIT = 128
_DIRECTORY_PATH_SCALAR_LIMIT = 8191
_DIRECTORY_PATH_SCRATCH_BYTES = 32765


# Fixed fields only. The first successful exclusive publisher wins, which is
# not necessarily the first exception in wall-clock order across processes.
_FAILURE_SCHEMA = "pcc.indexed-input-first-recorded-failure.v1"
_FAILURE_LIMIT = 1024
_FAILURE_STAGES = (
    "invocation", "manifest", "worker-context", "sidecar-publish",
    "sidecar-context", "sidecar-retire", "invalidate", "capture-identity",
    "capture-target", "capture-lock", "claim", "remaining-input-budget",
    "snapshot-destination", "codec-preflight", "codec-shape", "codec-header",
    "codec-wire-size", "codec-write", "snapshot-hash", "snapshot-publish",
    "snapshot-header-read", "output-identity", "output-lock",
    "remaining-output-budget", "output-stat", "output-destination",
    "output-copy-open", "output-copy", "output-rehash", "output-publish",
    "emission-json", "link-invocation", "module-count", "emission-json-read",
    "claim-read", "input-identity", "input-size", "input-hash", "retained-hash",
    "inventory", "link-recheck", "binary-hash", "link-json", "unlock",
)
_FAILURE_REASONS = {
    "indexed module function has no published indexed kernel": "missing_kernel",
    "indexed module requires complete value scalar columns": "incomplete_value_columns",
    "indexed module requires complete definition positions": "incomplete_definition_positions",
    "indexed module sidecar must be written before stack preparation": "prepared_slots",
    "indexed module sidecar contains prepared value state": "prepared_values",
    "unsupported diagnostic cold payload": "unsupported_cold",
    "bounded indexed debug capture is not supported": "unsupported_debug_capture",
    "indexed module mixes arena storage projections": "mixed_arena_storage",
    "indexed module header exceeds the size limit": "header_limit",
    "indexed receipt aggregate bytes exceed limit": "aggregate_limit",
    "indexed receipt output exceeds limit or has wrong kind": "output_limit_or_kind",
    "indexed receipt diagnostic lock is busy": "lock_busy",
    "short indexed receipt output write": "short_output_write",
    "short indexed receipt metadata write": "short_metadata_write",
    "indexed receipt output grew": "output_grew",
    "indexed receipt output changed": "output_changed",
    "indexed receipt snapshot destination already exists": "destination_exists",
    "indexed receipt output destination already exists": "destination_exists",
    "indexed receipt metadata destination already exists": "destination_exists",
    "indexed receipt link inventory is partial or mixed": "inventory_mismatch",
}


def _failure_state(stage):
    return {"invocation": "", "index": -1, "stage": stage,
            "remaining_bytes": -1, "observed_bytes": -1,
            "copied_bytes": -1, "inventory_count": -1}


def _failure_index(state, value):
    if type(value) is int and 0 <= value <= 30:
        state["index"] = value


def _failure_payload(state, error):
    # Never format exceptions: their text/args may contain paths or secrets,
    # and user exception __str__/__repr__ must not run in this handler.
    if not isinstance(state, dict) or state.get("stage") not in _FAILURE_STAGES:
        raise ValueError("invalid indexed failure stage")
    token = state.get("invocation")
    if type(token) is not str or (token and not _token(token)):
        raise ValueError("invalid indexed failure token")
    index = state.get("index")
    if type(index) is not int or not -1 <= index <= 30:
        raise ValueError("invalid indexed failure index")
    exception_class = "other_exception"
    if isinstance(error, FileExistsError):
        exception_class = "file_exists"
    elif isinstance(error, UnicodeError):
        exception_class = "unicode_error"
    elif isinstance(error, ValueError):
        exception_class = "value_error"
    elif isinstance(error, KeyError):
        exception_class = "key_error"
    elif isinstance(error, TypeError):
        exception_class = "type_error"
    elif isinstance(error, OSError):
        exception_class = "os_error"
    elif isinstance(error, MemoryError):
        exception_class = "memory_error"
    reason = "unknown"
    if isinstance(error, Exception) and len(error.args) == 1:
        message = error.args[0]
        if type(message) is str and len(message) <= 128:
            reason = _FAILURE_REASONS.get(message, "unknown")
            if message == "indexed module diagnostic exceeds the size limit":
                if state["stage"] == "codec-preflight":
                    reason = "conservative_limit"
                elif state["stage"] == "codec-wire-size":
                    reason = "wire_limit"
    value = {"schema": _FAILURE_SCHEMA, "invocation": token, "index": index,
             "stage": state["stage"], "exception_class": exception_class,
             "reason": reason, "overflow": False}
    for key in ("remaining_bytes", "observed_bytes", "copied_bytes", "inventory_count"):
        count = state.get(key)
        if type(count) is not int or count < -1:
            raise ValueError("invalid indexed failure counter")
        if count > 9223372036854775807:
            count = -1
            value["overflow"] = True
        value[key] = count
    raw = (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")
    if len(raw) > _FAILURE_LIMIT:
        raise ValueError("indexed failure exceeds limit")
    return raw


def _poison(state=None, error=None):
    # Reporting failure must never prevent the original exclusive poison
    # attempt. Existing/truncated/legacy/symlink markers are never repaired.
    raw = b"incomplete indexed capture\n"
    try:
        raw = _failure_payload(state, error)
    except Exception:
        pass
    try:
        directory = str(os.environ.get("PCC_DEBUG_SELF_IR_DUMP_DIR", "") or "").strip()
        if directory:
            with open(os.path.join(directory, "indexed-capture-failed"), "xb") as stream:
                stream.write(raw)
    except Exception:
        pass


def _unlock(path, state=None):
    if path:
        try:
            os.unlink(path)
        except Exception as error:
            try:
                if state is not None:
                    state["stage"] = "unlock"
            except Exception:
                pass
            _poison(state, error)


def _token(value):
    return isinstance(value, str) and len(value) == 32 and all(
        character in "0123456789abcdef" for character in value
    )


def _hash(path, max_bytes=0):
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(min(65536, max_bytes - size + 1) if max_bytes else 65536)
            if not chunk:
                break
            size += len(chunk)
            if max_bytes and size > max_bytes:
                raise ValueError("indexed receipt hashed file exceeds limit")
            digest.update(chunk)
    return digest.hexdigest(), size


def _read_json(path):
    size = os.path.getsize(path)
    if size > LIMIT or os.path.islink(path):
        raise ValueError("invalid indexed receipt metadata size or kind")
    with open(path, "rb") as stream:
        raw = stream.read(size + 1)
    if len(raw) != size:
        raise ValueError("indexed receipt metadata changed during read")
    return json.loads(raw.decode("utf-8"))


def _native_entries(directory):
    """Bounded scan over the already-owned platform directory intrinsics.

    No managed pointer is borrowed across directory calls: the path is copied
    into a bounded raw stack buffer, and entry bytes belong to the open stream.
    Each c_obj name is immediately adopted by an ordinary managed local/list.
    """
    # Four UTF-8 bytes per scalar, plus NUL. This bounds scratch BEFORE encode.
    if len(directory) > _DIRECTORY_PATH_SCALAR_LIMIT or "\0" in directory:
        raise ValueError("indexed receipt directory path exceeds diagnostic bound")
    encoded = directory.encode("utf-8")
    # stack_alloc requires a compile-time size, not a bounded runtime size.
    raw = stack_alloc(_DIRECTORY_PATH_SCRATCH_BYTES)
    for index in range(len(encoded)):
        store_i8(raw, index, encoded[index])
    store_i8(raw, len(encoded), 0)
    result = []
    name_bytes = 0
    failed = True
    stream = directory_open(raw)
    if ptr_is_null(stream):
        raise OSError("indexed receipt directory could not be opened")
    try:
        while True:
            entry = directory_next(stream)
            if ptr_is_null(entry):
                if directory_error(stream) < 0:
                    raise OSError("indexed receipt directory scan failed")
                break
            if load_i8(entry, 0) == 46:
                if load_i8(entry, 1) == 0 or (load_i8(entry, 1) == 46 and load_i8(entry, 2) == 0):
                    continue
            if len(result) >= INVENTORY_LIMIT:
                raise ValueError("indexed receipt inventory exceeds limit")
            size = strlen(entry)
            name_bytes += size
            if size > 4096 or name_bytes > LIMIT:
                raise ValueError("indexed receipt entry names exceed limit")
            # All protocol filenames are ASCII. Reject unknown non-ASCII
            # entries instead of inventing a filesystem decoding convention.
            for index in range(size):
                if load_i8(entry, index) < 0 or load_i8(entry, index) > 127:
                    raise ValueError("indexed receipt entry name is not ASCII")
            name = _receipt_entry_name(entry, size)
            if ptr_is_null(name):
                raise MemoryError("indexed receipt entry name allocation failed")
            result.append(name)
        failed = False
    finally:
        status = directory_close(stream)
        if status < 0 and not failed:
            raise OSError("indexed receipt directory close failed")
    return result


def _entries(directory):
    if sys.implementation.name == "pcc":
        return _native_entries(directory)
    result = []
    name_bytes = 0
    # Host-only dynamic lookup avoids claiming an absent owned os.scandir
    # provider. Native execution uses the bounded directory intrinsics above.
    with getattr(os, "scandir")(directory) as entries:
        for entry in entries:
            if len(result) >= INVENTORY_LIMIT:
                raise ValueError("indexed receipt inventory exceeds limit")
            name_bytes += len(entry.name.encode("utf-8"))
            if name_bytes > LIMIT:
                raise ValueError("indexed receipt entry names exceed limit")
            result.append(entry.name)
    return result


def _write_json(path, value, *, dump_inventory=True):
    # Metadata is built from bounded coordinator rows, but path/name strings
    # are still caller input. Bound before JSON escaping allocates a buffer.
    budget = [0]

    def charge(item):
        budget[0] += 32
        if isinstance(item, str):
            budget[0] += len(item) * 12
        elif isinstance(item, dict):
            for key, value in item.items():
                charge(key)
                charge(value)
        elif isinstance(item, (list, tuple)):
            for value in item:
                charge(value)
        elif isinstance(item, int):
            budget[0] += item.bit_length() + 1
        if budget[0] > LIMIT:
            raise ValueError("indexed receipt metadata exceeds limit")

    charge(value)
    raw = (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")
    if len(raw) > LIMIT:
        raise ValueError("indexed receipt metadata exceeds limit")
    # Reject an occupied final name before the inventory follows its target.
    # This is only an early rejection: keep the serialized check below too.
    if os.path.exists(path) or os.path.islink(path):
        raise ValueError("indexed receipt metadata destination already exists")
    directory = os.path.dirname(path)
    if dump_inventory:
        names = _entries(directory)
        total = len(raw)
        for name in names:
            if name.endswith(".json") or name.endswith(".json.partial"):
                total += os.path.getsize(os.path.join(directory, name))
        if total > LIMIT or len(names) >= INVENTORY_LIMIT:
            raise ValueError("indexed receipt aggregate metadata exceeds limit")
    # All publishers of this destination must acquire this fixed exclusive
    # temporary FIRST and retain it through replace. Therefore the final-path
    # check below is serialized with every other protocol publisher, including
    # after an earlier publisher has completed. No random temporary names.
    # A partial write remains invalid evidence and no retry repairs it.
    temporary = path + ".partial"
    with open(temporary, "xb") as stream:
        if stream.write(raw) != len(raw):
            raise ValueError("short indexed receipt metadata write")
    if os.path.exists(path) or os.path.islink(path):
        raise ValueError("indexed receipt metadata destination already exists")
    os.replace(temporary, path)


def _directory():
    directory = str(os.environ.get("PCC_DEBUG_SELF_IR_DUMP_DIR", "") or "").strip()
    invocation = str(os.environ.get("PCC_DEBUG_SELF_IR_INVOCATION", "") or "")
    if not directory or not _token(invocation):
        raise ValueError("indexed receipt requires a launcher invocation token")
    return os.path.abspath(directory), invocation


def _invocation(directory, token):
    value = _read_json(os.path.join(directory, "invocation.json"))
    if value.get("schema") != "pcc.indexed-input-invocation.v1" or value.get("invocation") != token:
        raise ValueError("indexed receipt invocation differs")
    return value


def begin_invocation(src_paths, module_names):
    """Bind coordinator-global indices, not worker-local enumeration."""
    state = None
    try:
        state = _failure_state("invocation")
        directory, token = _directory()
        state["invocation"] = token
        if (len(src_paths) != len(module_names) or not module_names
                or len(set(module_names)) != len(module_names)
                or 4 * len(module_names) + 2 > INVENTORY_LIMIT):
            raise ValueError("indexed receipt module inventory exceeds limit")
        if _entries(directory):
            raise ValueError("indexed receipt directory is not fresh")
        rows = []
        for index, name in enumerate(module_names):
            digest, _size = _hash(src_paths[index])
            rows.append({"index": index, "module": name, "source_sha256": digest})
        value = {"schema": "pcc.indexed-input-invocation.v1", "invocation": token,
                 "modules": rows, "count": len(rows)}
        _write_json(os.path.join(directory, "invocation.json"), value)
        return value
    except Exception as error:
        _poison(state, error)
        return None


def attach_manifest(manifest_path, invocation, assigned_indices):
    """Companion lives with its owner's temporary worker manifest."""
    if invocation is None:
        return
    state = None
    try:
        state = _failure_state("manifest")
        directory, token = _directory()
        state["invocation"] = token
        if invocation["invocation"] != token:
            raise ValueError("indexed receipt invocation changed")
        manifest_sha256, _size = _hash(manifest_path, LIMIT)
        rows = []
        for index in assigned_indices:
            row = dict(invocation["modules"][index])
            row["attempt"] = hashlib.sha256((token + ":" + str(index)).encode("utf-8")).hexdigest()[:32]
            rows.append(row)
        _write_json(manifest_path + ".receipt.json", {
            "schema": "pcc.indexed-input-worker.v1", "invocation": token,
            "directory": directory, "manifest_sha256": manifest_sha256, "modules": rows,
        }, dump_inventory=False)
    except Exception as error:
        _poison(state, error)


def worker_context(manifest_path, index, module_name, source_path):
    state = None
    try:
        state = _failure_state("worker-context")
        directory, token = _directory()
        state["invocation"] = token
        context = _read_json(manifest_path + ".receipt.json")
        digest, _size = _hash(manifest_path, LIMIT)
        if (context.get("schema") != "pcc.indexed-input-worker.v1"
                or context.get("invocation") != token or context.get("directory") != directory
                or context.get("manifest_sha256") != digest):
            raise ValueError("indexed receipt worker identity differs")
        selected = [row for row in context["modules"] if row["index"] == index]
        if len(selected) != 1 or selected[0]["module"] != module_name:
            raise ValueError("indexed receipt global module index differs")
        row = selected[0]
        digest, _size = _hash(source_path)
        if row["source_sha256"] != digest or not _token(row["attempt"]):
            raise ValueError("indexed receipt source or attempt differs")
        expected = _invocation(directory, token)["modules"][index]
        if any(row[key] != expected[key] for key in ("index", "module", "source_sha256")):
            raise ValueError("indexed receipt coordinator identity differs")
        return {**row, "invocation": token, "directory": directory}
    except Exception as error:
        _poison(state, error)
        return None


def publish_sidecar_request(sidecar_path, context, passes):
    """Only context is handed off here; this is NOT an emission receipt."""
    if context is None:
        return
    state = None
    try:
        state = _failure_state("sidecar-publish")
        if _token(context.get("invocation")):
            state["invocation"] = context["invocation"]
        if os.path.getsize(sidecar_path) > LIMIT:
            raise ValueError("indexed receipt sidecar exceeds limit")
        digest, size = _hash(sidecar_path, LIMIT)
        _write_json(sidecar_path + ".receipt.json", {
            "schema": "pcc.indexed-input-sidecar.v1", "context": context,
            "sidecar_sha256": digest, "sidecar_bytes": size, "passes": list(passes),
        }, dump_inventory=False)
        companion_digest, _size = _hash(sidecar_path + ".receipt.json", LIMIT)
        _write_json(sidecar_path + ".receipt.owner", {
            "schema": "pcc.indexed-input-sidecar-owner.v1",
            "invocation": context["invocation"], "companion_sha256": companion_digest,
        }, dump_inventory=False)
    except Exception as error:
        _poison(state, error)


def sidecar_context(sidecar_path):
    state = None
    try:
        state = _failure_state("sidecar-context")
        request = _read_json(sidecar_path + ".receipt.json")
        directory, token = _directory()
        state["invocation"] = token
        owner = _read_json(sidecar_path + ".receipt.owner")
        companion_digest, _size = _hash(sidecar_path + ".receipt.json", LIMIT)
        if (owner.get("schema") != "pcc.indexed-input-sidecar-owner.v1"
                or owner.get("invocation") != token
                or owner.get("companion_sha256") != companion_digest):
            raise ValueError("indexed receipt sidecar owner differs")
        context = request["context"]
        if request["sidecar_bytes"] > LIMIT or os.path.getsize(sidecar_path) != request["sidecar_bytes"]:
            raise ValueError("indexed receipt sidecar size differs")
        digest, size = _hash(sidecar_path, LIMIT)
        if (request["schema"] != "pcc.indexed-input-sidecar.v1"
                or context["invocation"] != token or context["directory"] != directory
                or request["sidecar_sha256"] != digest or request["sidecar_bytes"] != size):
            raise ValueError("indexed receipt sidecar differs")
        expected = _invocation(directory, token)["modules"][context["index"]]
        if any(context[key] != expected[key] for key in ("index", "module", "source_sha256")):
            raise ValueError("indexed receipt sidecar coordinator differs")
        return context, request["passes"]
    except Exception as error:
        _poison(state, error)
        return None, []


def _lock(directory):
    path = os.path.join(directory, ".capture-lock")
    # Diagnostic serialization only. Never widen a worker's process limits.
    for _attempt in range(1000):
        try:
            with open(path, "xb"):
                pass
            return path
        except FileExistsError:
            time.sleep(0.001)
    raise ValueError("indexed receipt diagnostic lock is busy")


def _remaining(directory, prefix, suffix):
    used = 0
    for name in _entries(directory):
        if name.startswith(prefix) and (name.endswith(suffix) or name.endswith(suffix + ".partial")):
            used += os.path.getsize(os.path.join(directory, name))
    if used >= LIMIT:
        raise ValueError("indexed receipt aggregate bytes exceed limit")
    return LIMIT - used


def begin_capture(module, context, passes):
    """Capture exactly this post-pass, concrete-target pre-stackprep object."""
    if context is None:
        return None
    lock = ""
    state = None
    try:
        state = _failure_state("capture-identity")
        directory, token = _directory()
        state["invocation"] = token
        if context["directory"] != directory or context["invocation"] != token:
            raise ValueError("indexed receipt capture identity differs")
        state["stage"] = "capture-target"
        if module.triple == "unknown-unknown-unknown":
            raise ValueError("indexed receipt target is not concrete")
        index = context["index"]
        state["stage"] = "capture-lock"
        lock = _lock(directory)
        state["stage"] = "claim"
        _write_json(os.path.join(directory, "module_" + str(index) + ".claim.json"), {
            "schema": "pcc.indexed-input-claim.v1", "invocation": token,
            "index": index, "module": context["module"], "attempt": context["attempt"],
        })
        # A failed/colliding claim has not assigned this capture a module.
        # Consumers bind any known index back to that exclusive claim.
        _failure_index(state, index)
        state["stage"] = "remaining-input-budget"
        remaining = _remaining(directory, "self_backend_input_", ".pidx")
        state["remaining_bytes"] = remaining
        from .self_backend_indexed_codec import encode_indexed_module_file

        name = "self_backend_input_" + str(index) + ".pidx"
        path = os.path.join(directory, name)
        temporary = path + ".partial"
        state["stage"] = "snapshot-destination"
        if (os.path.exists(path) or os.path.islink(path)
                or os.path.exists(temporary) or os.path.islink(temporary)):
            raise ValueError("indexed receipt snapshot destination already exists")
        # The capture lock and exclusive claim own this fixed temporary.
        # max_bytes checks raw arena/cold budgets before their materialization.
        encode_indexed_module_file(temporary, module, max_bytes=remaining, diagnostic=state, compact=True)
        state["stage"] = "snapshot-hash"
        digest, size = _hash(temporary, remaining)
        state["observed_bytes"] = size
        if size > remaining or os.path.exists(path) or os.path.islink(path):
            raise ValueError("indexed receipt input size or destination differs")
        state["stage"] = "snapshot-publish"
        os.replace(temporary, path)
        state["stage"] = "snapshot-header-read"
        with open(path, "rb") as stream:
            stream.readline(32)
            header_size = int(stream.readline(32))
            if header_size > size:
                raise ValueError("indexed receipt header size differs")
            representation = json.loads(stream.read(header_size).decode("utf-8"))["schema"]
        return {"schema": "pcc.indexed-executable-input.v1", "invocation": token,
                "index": index, "module": context["module"], "attempt": context["attempt"],
                "target": module.triple, "passes": list(passes), "representation": representation,
                "input": {"name": name, "bytes": size, "sha256": digest}}
    except Exception as error:
        _poison(state, error)
        return None
    finally:
        _unlock(lock, state)


def finish_capture(capture, output_path, kind):
    """A successful emitter, never a frontend sidecar, completes one receipt."""
    if capture is None:
        return
    lock = ""
    state = None
    try:
        state = _failure_state("output-identity")
        directory, token = _directory()
        state["invocation"] = token
        if capture["invocation"] != token or kind not in ("PCO", "ASM"):
            raise ValueError("indexed receipt output identity differs")
        _failure_index(state, capture.get("index"))
        state["stage"] = "output-lock"
        lock = _lock(directory)
        state["stage"] = "remaining-output-budget"
        remaining = _remaining(directory, "self_backend_output_", ".pco")
        remaining -= sum(os.path.getsize(os.path.join(directory, name))
                         for name in _entries(directory)
                         if name.startswith("self_backend_output_")
                         and (name.endswith(".s") or name.endswith(".s.partial")))
        # Negative capacity is not an unsigned byte count. Keep it explicitly
        # unknown in the optional record; the original size check still fails.
        state["remaining_bytes"] = remaining if remaining >= 0 else -1
        state["stage"] = "output-stat"
        size = os.path.getsize(output_path)
        state["observed_bytes"] = size
        if size > remaining or os.path.islink(output_path):
            raise ValueError("indexed receipt output exceeds limit or has wrong kind")
        name = "self_backend_output_" + str(capture["index"]) + (".pco" if kind == "PCO" else ".s")
        destination = os.path.join(directory, name)
        temporary = destination + ".partial"
        state["stage"] = "output-destination"
        if os.path.exists(destination) or os.path.islink(destination):
            raise ValueError("indexed receipt output destination already exists")
        digest = hashlib.sha256()
        copied = 0
        state["copied_bytes"] = copied
        state["stage"] = "output-copy-open"
        with open(output_path, "rb") as reader, open(temporary, "xb") as writer:
            state["stage"] = "output-copy"
            while True:
                chunk = reader.read(min(65536, size - copied + 1))
                if not chunk:
                    break
                copied += len(chunk)
                state["copied_bytes"] = copied
                if copied > size:
                    raise ValueError("indexed receipt output grew")
                digest.update(chunk)
                if writer.write(chunk) != len(chunk):
                    raise ValueError("short indexed receipt output write")
        state["stage"] = "output-rehash"
        if copied != size or _hash(output_path, LIMIT) != (digest.hexdigest(), size):
            raise ValueError("indexed receipt output changed")
        state["stage"] = "output-publish"
        # The capture lock serializes every retained-output publisher.
        if os.path.exists(destination) or os.path.islink(destination):
            raise ValueError("indexed receipt output destination already exists")
        os.replace(temporary, destination)
        capture = dict(capture)
        capture["output"] = {"name": name, "kind": kind, "sha256": digest.hexdigest(), "bytes": size}
        capture["status"] = "EMITTED"
        state["stage"] = "emission-json"
        _write_json(os.path.join(directory, "self_backend_input_" + str(capture["index"]) + ".json"), capture)
    except Exception as error:
        _poison(state, error)
    finally:
        _unlock(lock, state)


def prepare_link(artifacts):
    """Match the actual ordered linker arguments before invoking the linker."""
    state = None
    try:
        state = _failure_state("link-invocation")
        directory, token = _directory()
        state["invocation"] = token
        invocation = _invocation(directory, token)
        state["stage"] = "module-count"
        if len(artifacts) != invocation["count"]:
            raise ValueError("indexed receipt link module count differs")
        rows = []
        for index, (module, kind, path) in enumerate(artifacts):
            state["index"] = -1
            state["observed_bytes"] = -1
            _failure_index(state, index)
            state["stage"] = "emission-json-read"
            expected = invocation["modules"][index]
            receipt = _read_json(os.path.join(directory, "self_backend_input_" + str(index) + ".json"))
            state["stage"] = "claim-read"
            claim = _read_json(os.path.join(directory, "module_" + str(index) + ".claim.json"))
            state["stage"] = "input-identity"
            if (expected["module"] != module or receipt["module"] != module
                    or receipt["invocation"] != token or receipt["index"] != index
                    or receipt["status"] != "EMITTED" or receipt["output"]["kind"] != kind
                    or any(claim[key] != receipt[key] for key in ("invocation", "index", "module", "attempt"))):
                raise ValueError("indexed receipt link input identity differs")
            state["stage"] = "input-size"
            retained_path = os.path.join(directory, receipt["output"]["name"])
            if (receipt["output"]["bytes"] > LIMIT
                    or os.path.getsize(path) != receipt["output"]["bytes"]
                    or os.path.getsize(retained_path) != receipt["output"]["bytes"]):
                raise ValueError("indexed receipt linker input size changed")
            state["stage"] = "input-hash"
            digest, size = _hash(path, LIMIT)
            state["observed_bytes"] = size
            if (digest, size) != (receipt["output"]["sha256"], receipt["output"]["bytes"]):
                raise ValueError("indexed receipt linker input changed")
            state["stage"] = "retained-hash"
            if _hash(os.path.join(directory, receipt["output"]["name"]), LIMIT) != (digest, size):
                raise ValueError("indexed receipt retained output differs")
            rows.append({"index": index, "module": module, "attempt": receipt["attempt"],
                         "input_sha256": receipt["input"]["sha256"], "output_sha256": digest,
                         "output_name": receipt["output"]["name"], "kind": kind})
        state["index"] = -1
        state["observed_bytes"] = -1
        state["stage"] = "inventory"
        inventory_count = len(_entries(directory))
        state["inventory_count"] = inventory_count
        if inventory_count != 4 * len(rows) + 1:
            raise ValueError("indexed receipt link inventory is partial or mixed")
        return {"schema": "pcc.indexed-input-link.v1", "invocation": token, "modules": rows}
    except Exception as error:
        _poison(state, error)
        return None


def finish_link(link, artifacts, binary):
    if link is None:
        return
    state = None
    try:
        state = _failure_state("link-recheck")
        directory, token = _directory()
        state["invocation"] = token
        # Recheck inputs after linking, not merely an earlier snapshot.
        if prepare_link(artifacts) != link or link["invocation"] != token:
            raise ValueError("indexed receipt linker inputs changed")
        state["stage"] = "binary-hash"
        digest, size = _hash(binary)
        state["observed_bytes"] = size
        completed = dict(link)
        completed["binary_sha256"] = digest
        completed["binary_bytes"] = size
        state["stage"] = "link-json"
        _write_json(os.path.join(directory, "link.json"), completed)
    except Exception as error:
        _poison(state, error)


def invalidate_capture():
    """Persist a failed identity attempt so retry cannot erase the failure."""
    state = None
    try:
        state = _failure_state("invalidate")
        _directory_path, token = _directory()
        state["invocation"] = token
    except Exception:
        pass
    _poison(state)


def retire_sidecar_request(sidecar_path):
    """Remove only a companion acquired by this successful invocation."""
    state = None
    try:
        state = _failure_state("sidecar-retire")
        directory, token = _directory()
        state["invocation"] = token
        if os.path.exists(os.path.join(directory, "indexed-capture-failed")):
            return
        path = sidecar_path + ".receipt.json"
        owner_path = sidecar_path + ".receipt.owner"
        if not os.path.isfile(owner_path) or os.path.islink(owner_path):
            return
        owner = _read_json(owner_path)
        request = _read_json(path)
        digest, _size = _hash(path, LIMIT)
        context = request["context"]
        expected = _invocation(directory, token)["modules"][context["index"]]
        if (owner.get("schema") != "pcc.indexed-input-sidecar-owner.v1"
                or owner.get("invocation") != token or owner.get("companion_sha256") != digest
                or request.get("schema") != "pcc.indexed-input-sidecar.v1"
                or context.get("invocation") != token or context.get("directory") != directory
                or any(context[key] != expected[key] for key in ("index", "module", "source_sha256"))):
            raise ValueError("indexed receipt sidecar retirement owner differs")
        os.unlink(path)
        os.unlink(owner_path)
    except Exception as error:
        _poison(state, error)

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


def _poison():
    # A later successful retry must not turn a failed diagnostic attempt into
    # apparently complete evidence. This extra entry is intentionally invalid.
    try:
        directory = str(os.environ.get("PCC_DEBUG_SELF_IR_DUMP_DIR", "") or "").strip()
        if directory:
            with open(os.path.join(directory, "indexed-capture-failed"), "xb") as stream:
                stream.write(b"incomplete indexed capture\n")
    except Exception:
        pass


def _unlock(path):
    if path:
        try:
            os.unlink(path)
        except Exception:
            _poison()


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
    try:
        directory, token = _directory()
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
    except Exception:
        _poison()
        return None


def attach_manifest(manifest_path, invocation, assigned_indices):
    """Companion lives with its owner's temporary worker manifest."""
    if invocation is None:
        return
    try:
        directory, token = _directory()
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
    except Exception:
        _poison()


def worker_context(manifest_path, index, module_name, source_path):
    try:
        directory, token = _directory()
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
    except Exception:
        _poison()
        return None


def publish_sidecar_request(sidecar_path, context, passes):
    """Only context is handed off here; this is NOT an emission receipt."""
    if context is None:
        return
    try:
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
    except Exception:
        _poison()


def sidecar_context(sidecar_path):
    try:
        request = _read_json(sidecar_path + ".receipt.json")
        directory, token = _directory()
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
    except Exception:
        _poison()
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
    try:
        directory, token = _directory()
        if context["directory"] != directory or context["invocation"] != token:
            raise ValueError("indexed receipt capture identity differs")
        if module.triple == "unknown-unknown-unknown":
            raise ValueError("indexed receipt target is not concrete")
        index = context["index"]
        lock = _lock(directory)
        _write_json(os.path.join(directory, "module_" + str(index) + ".claim.json"), {
            "schema": "pcc.indexed-input-claim.v1", "invocation": token,
            "index": index, "module": context["module"], "attempt": context["attempt"],
        })
        remaining = _remaining(directory, "self_backend_input_", ".pidx")
        from .self_backend_indexed_codec import encode_indexed_module_file

        name = "self_backend_input_" + str(index) + ".pidx"
        path = os.path.join(directory, name)
        temporary = path + ".partial"
        if (os.path.exists(path) or os.path.islink(path)
                or os.path.exists(temporary) or os.path.islink(temporary)):
            raise ValueError("indexed receipt snapshot destination already exists")
        # The capture lock and exclusive claim own this fixed temporary.
        # max_bytes checks raw arena/cold budgets before their materialization.
        encode_indexed_module_file(temporary, module, max_bytes=remaining)
        digest, size = _hash(temporary, remaining)
        if size > remaining or os.path.exists(path) or os.path.islink(path):
            raise ValueError("indexed receipt input size or destination differs")
        os.replace(temporary, path)
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
    except Exception:
        _poison()
        return None
    finally:
        _unlock(lock)


def finish_capture(capture, output_path, kind):
    """A successful emitter, never a frontend sidecar, completes one receipt."""
    if capture is None:
        return
    lock = ""
    try:
        directory, token = _directory()
        if capture["invocation"] != token or kind not in ("PCO", "ASM"):
            raise ValueError("indexed receipt output identity differs")
        lock = _lock(directory)
        remaining = _remaining(directory, "self_backend_output_", ".pco")
        remaining -= sum(os.path.getsize(os.path.join(directory, name))
                         for name in _entries(directory)
                         if name.startswith("self_backend_output_")
                         and (name.endswith(".s") or name.endswith(".s.partial")))
        size = os.path.getsize(output_path)
        if size > remaining or os.path.islink(output_path):
            raise ValueError("indexed receipt output exceeds limit or has wrong kind")
        name = "self_backend_output_" + str(capture["index"]) + (".pco" if kind == "PCO" else ".s")
        destination = os.path.join(directory, name)
        temporary = destination + ".partial"
        if os.path.exists(destination) or os.path.islink(destination):
            raise ValueError("indexed receipt output destination already exists")
        digest = hashlib.sha256()
        copied = 0
        with open(output_path, "rb") as reader, open(temporary, "xb") as writer:
            while True:
                chunk = reader.read(min(65536, size - copied + 1))
                if not chunk:
                    break
                copied += len(chunk)
                if copied > size:
                    raise ValueError("indexed receipt output grew")
                digest.update(chunk)
                if writer.write(chunk) != len(chunk):
                    raise ValueError("short indexed receipt output write")
        if copied != size or _hash(output_path, LIMIT) != (digest.hexdigest(), size):
            raise ValueError("indexed receipt output changed")
        # The capture lock serializes every retained-output publisher.
        if os.path.exists(destination) or os.path.islink(destination):
            raise ValueError("indexed receipt output destination already exists")
        os.replace(temporary, destination)
        capture = dict(capture)
        capture["output"] = {"name": name, "kind": kind, "sha256": digest.hexdigest(), "bytes": size}
        capture["status"] = "EMITTED"
        _write_json(os.path.join(directory, "self_backend_input_" + str(capture["index"]) + ".json"), capture)
    except Exception:
        _poison()
    finally:
        _unlock(lock)


def prepare_link(artifacts):
    """Match the actual ordered linker arguments before invoking the linker."""
    try:
        directory, token = _directory()
        invocation = _invocation(directory, token)
        if len(artifacts) != invocation["count"]:
            raise ValueError("indexed receipt link module count differs")
        rows = []
        for index, (module, kind, path) in enumerate(artifacts):
            expected = invocation["modules"][index]
            receipt = _read_json(os.path.join(directory, "self_backend_input_" + str(index) + ".json"))
            claim = _read_json(os.path.join(directory, "module_" + str(index) + ".claim.json"))
            if (expected["module"] != module or receipt["module"] != module
                    or receipt["invocation"] != token or receipt["index"] != index
                    or receipt["status"] != "EMITTED" or receipt["output"]["kind"] != kind
                    or any(claim[key] != receipt[key] for key in ("invocation", "index", "module", "attempt"))):
                raise ValueError("indexed receipt link input identity differs")
            retained_path = os.path.join(directory, receipt["output"]["name"])
            if (receipt["output"]["bytes"] > LIMIT
                    or os.path.getsize(path) != receipt["output"]["bytes"]
                    or os.path.getsize(retained_path) != receipt["output"]["bytes"]):
                raise ValueError("indexed receipt linker input size changed")
            digest, size = _hash(path, LIMIT)
            if (digest, size) != (receipt["output"]["sha256"], receipt["output"]["bytes"]):
                raise ValueError("indexed receipt linker input changed")
            if _hash(os.path.join(directory, receipt["output"]["name"]), LIMIT) != (digest, size):
                raise ValueError("indexed receipt retained output differs")
            rows.append({"index": index, "module": module, "attempt": receipt["attempt"],
                         "input_sha256": receipt["input"]["sha256"], "output_sha256": digest,
                         "output_name": receipt["output"]["name"], "kind": kind})
        if len(_entries(directory)) != 4 * len(rows) + 1:
            raise ValueError("indexed receipt link inventory is partial or mixed")
        return {"schema": "pcc.indexed-input-link.v1", "invocation": token, "modules": rows}
    except Exception:
        _poison()
        return None


def finish_link(link, artifacts, binary):
    if link is None:
        return
    try:
        directory, token = _directory()
        # Recheck inputs after linking, not merely an earlier snapshot.
        if prepare_link(artifacts) != link or link["invocation"] != token:
            raise ValueError("indexed receipt linker inputs changed")
        digest, size = _hash(binary)
        completed = dict(link)
        completed["binary_sha256"] = digest
        completed["binary_bytes"] = size
        _write_json(os.path.join(directory, "link.json"), completed)
    except Exception:
        _poison()


def invalidate_capture():
    """Persist a failed identity attempt so retry cannot erase the failure."""
    _poison()


def retire_sidecar_request(sidecar_path):
    """Remove only a companion acquired by this successful invocation."""
    try:
        directory, token = _directory()
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
    except Exception:
        _poison()

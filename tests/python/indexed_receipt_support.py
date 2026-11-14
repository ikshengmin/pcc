"""Bounded, independently replayable evidence for actual indexed emission.

The records are diagnostics, never a substitute for native/reference execution.
This consumer reads the captured inputs and outputs, not the compiler's live IR.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


LIMIT = 2 * 1024 * 1024
CODEC_SCHEMAS = tuple("pcc.self-backend.indexed-module.v" + str(v) for v in (1, 2, 3))


def unique_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            assert key not in result, "MALFORMED: duplicate receipt key"
            result[key] = value
        return result
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as failure:
        raise AssertionError("MALFORMED: indexed receipt JSON") from failure


def inventory_dump(dump):
    paths = []
    with os.scandir(dump) as entries:
        for entry in entries:
            assert len(paths) < 128, "OVERSIZE: observed IR module inventory"
            paths.append(Path(entry.path))
    assert paths, "MISSING: executable compiler produced no self-backend IR receipt"
    assert all(path.is_file() and not path.is_symlink() for path in paths), (
        "MALFORMED: dump file kind"
    )
    return {path.name: path for path in paths}


def _digest_file(path, expected_size):
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(min(65536, expected_size - size + 1)):
            size += len(chunk)
            assert size <= expected_size, "IDENTITY_CHANGED: emitted output grew"
            digest.update(chunk)
    assert size == expected_size, "IDENTITY_CHANGED: emitted output shrank"
    return digest.hexdigest()


def _hex(value, size=64):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{" + str(size) + "}", value)


def _index(value, expected):
    assert type(value) is int and value == expected, "MALFORMED: global module index"


def codec_header(raw):
    assert raw.startswith(b"PCCIDXMOD1\n"), "MALFORMED: indexed module magic"
    end = raw.find(b"\n", 11, 48)
    assert end >= 0 and re.fullmatch(b"[0-9]+", raw[11:end]), "MALFORMED: indexed header size"
    size = int(raw[11:end])
    assert 0 < size <= len(raw) - end - 1, "MALFORMED: indexed header bounds"
    header = unique_json(raw[end + 1:end + 1 + size])
    assert isinstance(header, dict) and header.get("schema") in CODEC_SCHEMAS, (
        "MALFORMED: unknown indexed module representation"
    )
    return header


@contextmanager
def decoded_module(raw, limit=LIMIT):
    assert isinstance(raw, bytes) and len(raw) <= limit, "OVERSIZE: indexed module bytes"
    codec_header(raw)
    from pcc.backend.self_backend_indexed_codec import decode_indexed_module_file

    with tempfile.TemporaryDirectory(prefix="pcc-indexed-evidence-") as directory:
        path = Path(directory) / "input.pidx"
        path.write_bytes(raw)
        try:
            module = decode_indexed_module_file(str(path), max_bytes=limit)
        except Exception as failure:
            raise AssertionError("MALFORMED: indexed module decode") from failure
        try:
            yield module
        finally:
            for function in module.functions:
                if function.indexed_kernel is not None:
                    function.indexed_kernel.close_native_tables()


def _instruction_operands(kind, data):
    # These are operand positions in diagnostic projections of the actual typed
    # records. Never search call_texts, value-name tables, types or metadata.
    positions = {
        "store": (1, 3), "store_atomic": (1, 3), "load": (3,), "load_atomic": (3,),
        "atomicrmw": (3, 5), "cmpxchg": (3, 5, 6), "va_arg": (2,),
        "binop": (3, 4), "fbinop": (3, 4), "icmp": (3, 4), "fcmp": (3, 4),
        "fneg": (2,), "cast": (3,), "select": (2, 3, 4), "freeze": (2,),
        "insertelement": (2, 4, 5), "extractelement": (2, 3),
        "shufflevector": (2, 3, 5), "extractvalue": (2,), "insertvalue": (2, 4),
    }
    if kind in positions:
        return tuple(data[index] for index in positions[kind])
    if kind == "syscall6":
        return tuple(data[1])
    if kind == "gep":
        return (data[3], *(item[1] for item in data[4]))
    if kind == "call":
        return ((data[2],) if data[3] else ()) + tuple(item[1] for item in data[4])
    return ()


def _has_local_operand_use(module, symbol):
    from pcc.backend.self_backend_ir import PARSED_INSTRUCTION_KINDS
    from pcc.backend.self_backend_kernel import get_indexed_function_kernel

    for function in module.functions:
        kernel = get_indexed_function_kernel(function)
        for block_id in range(len(kernel.block_names)):
            for instruction_index in range(kernel.instruction_count(block_id)):
                kind = PARSED_INSTRUCTION_KINDS[kernel.instruction_kind_id(block_id, instruction_index)]
                data = kernel.instruction_data(block_id, instruction_index)
                if symbol in _instruction_operands(kind, data):
                    return True
            span = kernel.block_phi_fact(block_id)
            for phi_index in range(span.second):
                if any(incoming.value == symbol for incoming in
                       kernel.diagnostic_phi(block_id, phi_index).incoming):
                    return True
            terminator = kernel.diagnostic_terminator(block_id)
            if terminator.kind == "ret" and terminator.data[1] == symbol:
                return True
    return False


def select_indexed_program_ir(raw_modules, marker):
    assert raw_modules and all(isinstance(raw, bytes) for raw in raw_modules), (
        "MALFORMED: mixed executable IR representations"
    )
    assert len(raw_modules) * 4 + 2 <= 128, "OVERSIZE: indexed module inventory"
    assert sum(map(len, raw_modules)) <= LIMIT, "OVERSIZE: total observed IR bytes"
    selected = []
    wanted = (marker.encode("utf-8"), marker.encode("utf-8") + b"\0")
    for index, raw in enumerate(raw_modules):
        with decoded_module(raw) as module:
            matches = []
            for global_ in module.globals_:
                if not global_.is_constant:
                    continue
                payload = re.fullmatch(r'c"((?:\\[0-9a-fA-F]{2})*)"', global_.initializer)
                if payload is None:
                    continue
                literal = bytes.fromhex(payload[1].replace("\\", ""))
                if literal not in wanted:
                    continue
                type_ = global_.type
                assert (type_.kind == "array" and type_.elem is not None
                        and type_.elem.kind == "int" and type_.elem.width == 8
                        and type_.count == len(literal)), "INVALID: marker array length differs"
                assert sum(other.name == global_.name for other in module.globals_) == 1, (
                    "AMBIGUOUS: marker local definition"
                )
                assert _has_local_operand_use(module, "@" + global_.name), (
                    "MISSING: marker has no local function use"
                )
                matches.append(global_.name)
            assert len(matches) <= 1, "AMBIGUOUS: program IR marker"
            if matches:
                selected.append(index)
    assert selected, "MISSING: program IR marker"
    assert len(selected) == 1, "AMBIGUOUS: program IR marker"
    return tuple(selected)


def _file_format(name):
    if name.endswith(".pidx"):
        return "pcc-indexed-module"
    if name.endswith(".pco"):
        return "pcc-owned-object"
    if name.endswith(".s"):
        return "assembly-text"
    return "json"


def indexed_evidence_shell(record, *, module_limit=LIMIT, total_limit=LIMIT,
                           json_limit=LIMIT, selected_required=True):
    """Complete success metadata, without any raw payload or decoded module.

    SHA256 placeholders have exactly the final width. A three-digit selected
    index conservatively covers the bounded module inventory. The same shell
    constructs the actual retained record and its pre-read size projection.
    """
    evidence = {
        "schema": "pcc.observed-executable-indexed-input.v1",
        "status": "INDEXED_INPUTS_RETAINED",
        "compiler_completed": True,
        "producer_completeness": "LINK_BOUND",
        "all_modules_captured": "LINK_BOUND",
        "qualification": "NOT_ASSESSED",
        **{key: record[key] for key in (
            "run_id", "compiler_fixture_parameter", "compiler_mode", "backend",
            "source_sha256", "runtime_archive", "runtime_sha256", "binary_path",
        )},
        "representation": "indexed-codec",
        "scope": "same-invocation indexed emitter inputs, emitted outputs and linked binary; semantic and native checks remain separate",
        "limits_bytes": {"module": module_limit, "total_observed_ir": total_limit,
                         "json_with_metadata": json_limit, "output_module": module_limit,
                         "total_emitted_output": total_limit},
        "modules": [],
        "observed_identity": {key: record[key] for key in ("source_sha256", "runtime_sha256")},
        "binary_sha256": "0" * 64,
    }
    if selected_required:
        evidence["selected_module_indices"] = [128]
    return evidence


def read_indexed_inputs(dump, record, binary, read_bounded, *,
                        module_limit=LIMIT, total_limit=LIMIT, json_limit=LIMIT,
                        expected_target=None, expected_passes=(), selected_required=True):
    """Recompute complete invocation/input/output/link identities from raw files.

    Inventory and size all files before the first read or decode. The independent
    outer gate calls this again and re-runs semantic predicates; PASS is not input.
    """
    files = inventory_dump(dump)
    assert "invocation.json" in files and "link.json" in files, "MISSING: indexed completion manifest"
    sizes = {name: path.stat().st_size for name, path in files.items()}
    json_names = [name for name in files if name.endswith(".json")]
    input_names = [name for name in files if name.endswith(".pidx")]
    output_names = [name for name in files if re.fullmatch(r"self_backend_output_[0-9]+\.(pco|s)", name)]
    assert all(0 <= sizes[name] <= module_limit for name in input_names), "OVERSIZE: observed IR module bytes"
    assert sum(sizes[name] for name in input_names) <= total_limit, "OVERSIZE: total observed IR bytes"
    assert all(0 <= sizes[name] <= json_limit for name in json_names), "OVERSIZE: indexed JSON bytes"
    assert sum(sizes[name] for name in json_names) <= json_limit, "OVERSIZE: total indexed JSON bytes"
    assert all(0 <= sizes[name] <= module_limit for name in output_names), "OVERSIZE: emitted output bytes"
    assert sum(sizes[name] for name in output_names) <= total_limit, "OVERSIZE: total emitted output bytes"
    assert set(files) == set(json_names + input_names + output_names), "MALFORMED: mixed or unknown dump files"
    # Base64 length is known from stat sizes, so reject a guaranteed oversize
    # retained JSON artifact before reading any payload or constructing strings.
    envelope = {"schema": "pcc.indexed-input-retention.v1", "invocation": record.get("indexed_input_invocation"),
                "files": [{"name": name, "bytes": sizes[name], "sha256": "0" * 64,
                           "encoding": "base64", "format": _file_format(name), "base64": ""}
                          for name in sorted(files)]}
    encoded_size = len(json.dumps(envelope).encode("utf-8")) + sum(
        4 * ((sizes[name] + 2) // 3) for name in files
    )
    assert encoded_size <= json_limit, "OVERSIZE: retained indexed JSON bytes"
    wrapper = indexed_evidence_shell(
        record, module_limit=module_limit, total_limit=total_limit,
        json_limit=json_limit, selected_required=selected_required,
    )
    wrapper["retention"] = envelope
    wrapper["observed_dump_sha256"] = {name: "0" * 64 for name in sorted(files)}
    wrapper_size = len(json.dumps(wrapper).encode("utf-8")) + 1 + sum(
        4 * ((sizes[name] + 2) // 3) for name in files
    )
    assert wrapper_size <= json_limit, "OVERSIZE: JSON bytes including metadata and escaping"
    raw_files, hashes = {}, {}
    for name in sorted(files):
        raw_files[name], hashes[name] = read_bounded(files[name], sizes[name])
    documents = {name: unique_json(raw_files[name]) for name in json_names}
    assert all(isinstance(value, dict) for value in documents.values()), "MALFORMED: indexed JSON document"
    invocation, link = documents["invocation.json"], documents["link.json"]
    token = record.get("indexed_input_invocation")
    assert _hex(token, 32), "MALFORMED: missing invocation request"
    assert invocation.get("schema") == "pcc.indexed-input-invocation.v1", "MALFORMED: invocation schema"
    assert invocation.get("invocation") == token, "IDENTITY_CHANGED: indexed invocation"
    rows = invocation.get("modules")
    assert isinstance(rows, list) and rows, "MISSING: indexed modules"
    _index(invocation.get("count"), len(rows))
    assert len(rows) * 4 + 2 <= 128, "OVERSIZE: indexed module inventory"
    assert link.get("schema") == "pcc.indexed-input-link.v1", "MALFORMED: link schema"
    assert link.get("invocation") == token, "IDENTITY_CHANGED: link invocation"
    assert binary.is_file() and not binary.is_symlink(), "MISSING: compiler did not emit an executable"
    binary_size = binary.stat().st_size
    assert type(link.get("binary_bytes")) is int and link["binary_bytes"] == binary_size, (
        "IDENTITY_CHANGED: linked executable size"
    )
    assert link.get("binary_sha256") == _digest_file(binary, binary_size), "IDENTITY_CHANGED: linked executable"
    expected_names = {"invocation.json", "link.json"}
    expected_links, modules, attempts, module_names = [], [], set(), set()
    for index, row in enumerate(rows):
        assert isinstance(row, dict), "MALFORMED: invocation module"
        _index(row.get("index"), index)
        module_name = row.get("module")
        assert isinstance(module_name, str) and module_name and module_name not in module_names, (
            "AMBIGUOUS: indexed module name"
        )
        module_names.add(module_name)
        assert _hex(row.get("source_sha256")), "MALFORMED: indexed source hash"
        claim_name = f"module_{index}.claim.json"
        input_name, receipt_name = f"self_backend_input_{index}.pidx", f"self_backend_input_{index}.json"
        assert all(name in documents for name in (claim_name, receipt_name)), "MISSING: indexed module receipt"
        claim, emission = documents[claim_name], documents[receipt_name]
        assert claim.get("schema") == "pcc.indexed-input-claim.v1", "MALFORMED: claim schema"
        assert emission.get("schema") == "pcc.indexed-executable-input.v1", "MALFORMED: emission schema"
        attempt = claim.get("attempt")
        assert _hex(attempt, 32) and attempt not in attempts, "AMBIGUOUS: indexed attempt"
        assert attempt == hashlib.sha256((token + ":" + str(index)).encode("utf-8")).hexdigest()[:32], (
            "IDENTITY_CHANGED: assigned indexed attempt"
        )
        attempts.add(attempt)
        for document in (claim, emission):
            assert document.get("invocation") == token, "IDENTITY_CHANGED: module invocation"
            _index(document.get("index"), index)
            assert document.get("module") == module_name and document.get("attempt") == attempt, (
                "IDENTITY_CHANGED: module claim"
            )
        assert emission.get("status") == "EMITTED", "MISSING: incomplete indexed emission"
        assert isinstance(emission.get("passes"), list) and all(
            isinstance(value, str) for value in emission["passes"]
        ), "MALFORMED: indexed pass configuration"
        assert emission["passes"] == list(expected_passes), "IDENTITY_CHANGED: indexed pass configuration"
        assert input_name in raw_files, "MISSING: indexed input"
        assert isinstance(emission.get("input"), dict) and type(emission["input"].get("bytes")) is int, (
            "MALFORMED: indexed input size"
        )
        assert emission.get("input") == {"name": input_name, "bytes": sizes[input_name],
                                          "sha256": hashes[input_name]}, "IDENTITY_CHANGED: indexed input"
        header = codec_header(raw_files[input_name])
        assert emission.get("representation") == header["schema"], "MALFORMED: representation mismatch"
        target = emission.get("target")
        assert (isinstance(target, str) and target and target != "unknown-unknown-unknown"
                and target == header.get("triple")), "IDENTITY_CHANGED: indexed target"
        if expected_target is not None:
            assert target == expected_target, "IDENTITY_CHANGED: runtime target"
        output = emission.get("output")
        assert isinstance(output, dict) and output.get("kind") in ("PCO", "ASM"), "MALFORMED: indexed output"
        kind = output["kind"]
        output_name = f"self_backend_output_{index}." + ("pco" if kind == "PCO" else "s")
        assert output_name in raw_files, "MISSING: emitted output"
        assert type(output.get("bytes")) is int, "MALFORMED: indexed output size"
        assert output == {"name": output_name, "kind": kind, "bytes": sizes[output_name],
                          "sha256": hashes[output_name]}, "IDENTITY_CHANGED: emitted output"
        expected_names.update((claim_name, receipt_name, input_name, output_name))
        expected_links.append({"index": index, "module": module_name, "attempt": attempt,
                               "input_sha256": hashes[input_name], "output_sha256": hashes[output_name],
                               "output_name": output_name, "kind": kind})
        modules.append(raw_files[input_name])
    assert set(files) == expected_names, "MALFORMED: partial, stale or mixed indexed inventory"
    assert link.get("modules") == expected_links, "IDENTITY_CHANGED: linked module inventory"
    assert len({hashlib.sha256(raw).hexdigest() for raw in modules}) == len(modules), (
        "AMBIGUOUS: duplicate executable IR module"
    )
    assert record["source_sha256"] in [row["source_sha256"] for row in rows], "IDENTITY_CHANGED: invocation source"
    # A bounded JSON envelope carries every input/output byte through existing
    # JSON-only artifact selection. Do not silently truncate to make it fit.
    envelope = {"schema": "pcc.indexed-input-retention.v1", "invocation": token,
                "files": [{"name": name, "bytes": sizes[name], "sha256": hashes[name],
                           "encoding": "base64", "format": _file_format(name),
                           "base64": base64.b64encode(raw_files[name]).decode("ascii")}
                          for name in sorted(files)]}
    assert len(json.dumps(envelope).encode("utf-8")) <= json_limit, "OVERSIZE: retained indexed JSON bytes"
    return {"raw_modules": modules, "inventory": hashes, "retention": envelope,
            "invocation": invocation, "link": link}


def validate_indexed_modules(raw_modules, limit=LIMIT):
    assert raw_modules and len(raw_modules) * 4 + 2 <= 128, "OVERSIZE: indexed module inventory"
    assert all(isinstance(raw, bytes) and len(raw) <= limit for raw in raw_modules), "OVERSIZE: indexed module bytes"
    assert sum(map(len, raw_modules)) <= limit, "OVERSIZE: total observed IR bytes"
    for raw in raw_modules:
        with decoded_module(raw, limit) as module:
            assert module.triple == codec_header(raw)["triple"], "IDENTITY_CHANGED: decoded target"


def has_indexed_marker_definition(raw_modules, marker):
    wanted = (marker.encode("utf-8"), marker.encode("utf-8") + b"\0")
    for raw in raw_modules:
        with decoded_module(raw) as module:
            for global_ in module.globals_:
                payload = re.fullmatch(r'c"((?:\\[0-9a-fA-F]{2})*)"', global_.initializer)
                if global_.is_constant and payload is not None:
                    if bytes.fromhex(payload[1].replace("\\", "")) in wanted:
                        return True
    return False

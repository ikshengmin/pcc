"""Offline attribution of an independently admitted native worker CPU PC sample."""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import ci_stage1_function_map as transport
from scripts import ci_stage1_bundle as bundle

LAUNCH_SCHEMA = "pcc.worker-cpu-pc-launch.v1"
SAMPLE_SCHEMA = "pcc.worker-cpu-pc.v1"
REPORT_SCHEMA = "pcc.worker-cpu-pc-report.v1"
MANIFEST_SCHEMA = "pcc.frontends.python.codegen_worker.v4"
PHASE = "frontend_generate"
PERIOD_NS = 2_000_000
CAPACITY = 8192
MAX_SAMPLE_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_INPUT_BYTES = 256 * 1024 * 1024
MAX_RETAINED_ALIAS_ROWS = 64
_HEADER_KEYS = {"schema", "module", "index", "pid", "phase", "period_ns", "capacity", "count", "dropped",
                "compiler_image_sha256", "source_commit", "source_sha256", "manifest_sha256", "status", "phase_complete"}
_LAUNCH_KEYS = {"schema", "status", "producer", "source", "compiler", "map_binding_sha256", "argv", "pid", "module",
                "index", "phase", "manifest", "inputs", "sample", "terminal"}
_FILE_KEYS = {"path", "sha256", "bytes"}
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_PC = re.compile(rb"[1-9][0-9]{0,19}\Z")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _keys(value, names, label):
    _require(type(value) is dict and set(value) == names, "invalid " + label + " record")


def _uint(value):
    return type(value) is int and 0 <= value < 1 << 64


def _path(value):
    return (type(value) is str and os.path.isabs(value) and os.path.normpath(value) == value
            and not any(char in value for char in ("\0", "\r", "\n", "\t")))


def _text(value):
    return type(value) is str and bool(value) and not any(char in value for char in ("\0", "\r", "\n", "\t"))


def _number(value, label):
    _require(type(value) is str and _DECIMAL.fullmatch(value) is not None and len(value) <= 20, "invalid " + label)
    result = int(value)
    _require(_uint(result), "invalid " + label)
    return result


def _record(value, extra=()):
    _keys(value, _FILE_KEYS | set(extra), "file identity")
    _require(_path(value["path"]) and bundle.valid_digest(value["sha256"])
             and type(value["bytes"]) is int and value["bytes"] > 0, "invalid file identity")


def _read_bound(path, record, limit, *, extra=(), keep=False, prefix=0):
    """Rehash actual supplied bytes; logical producer path may be relocated."""
    _record(record, extra)
    path = Path(path)
    stream, info = transport._checked_open(path, limit)
    with stream:
        _require(info.st_size == record["bytes"], "bound file size differs")
        digest = hashlib.sha256()
        data = bytearray()
        total = 0
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            digest.update(chunk)
            if keep:
                data.extend(chunk)
            elif len(data) < prefix:
                data.extend(chunk[:prefix - len(data)])
        transport._unchanged(path, stream, info)
    _require(total == record["bytes"] and digest.hexdigest() == record["sha256"], "bound file SHA/size differs")
    return bytes(data)


def _manifest(data, index, module):
    """Strict finite admission of the real ordinary V4 wire, no target execution."""
    text = data.decode("utf-8")
    _require(text.endswith("\n") and "\r" not in text and "\0" not in text, "incomplete worker manifest")
    lines = text[:-1].split("\n")
    _require(len(lines) >= 14 and lines[0] == MANIFEST_SCHEMA and lines[4] == "codegen",
             "CPU sample requires ordinary V4 codegen manifest")
    _require(all(_path(lines[i]) for i in (1, 2, 3, 5)) and _text(lines[6]) and lines[7] == "on" and lines[8] in ("0", "1"),
             "invalid V4 worker manifest paths or mode")
    position = 9
    siblings = _number(lines[position], "sibling count")
    position += 1
    _require(siblings <= len(lines) - position, "truncated worker sibling list")
    _require(all(_text(name) for name in lines[position:position + siblings]), "invalid sibling name")
    position += siblings
    _require(position < len(lines), "truncated worker module count")
    count = _number(lines[position], "module count")
    position += 1
    _require(0 < count <= len(lines) - position and 0 <= index < count, "invalid worker module count/index")
    names = set()
    source = ""
    for ordinal in range(count):
        fields = lines[position].split("\t")
        position += 1
        _require(len(fields) == 3 and fields[0] == str(ordinal) and _text(fields[1]) and _path(fields[2])
                 and fields[1] not in names, "invalid V4 worker source graph entry")
        names.add(fields[1])
        if ordinal == index:
            _require(fields[1] == module, "worker selected module differs")
            source = fields[2]
    _require(lines[position:] == ["1", str(index)] and bool(source), "CPU sample requires exact singleton worker assignment")
    return {"result": lines[1], "ir_dir": lines[2], "exports": lines[3], "ast": os.path.join(lines[5], f"module_{index}.json"),
            "source": source, "artifact": os.path.join(lines[2], f"module_{index}.direct.pco"),
            "ir": os.path.join(lines[2], f"module_{index}.ll")}


def _result(data, paths, index, module):
    text = data.decode("utf-8")
    _require(text.endswith("\n") and "\r" not in text and "\n" not in text[:-1], "worker terminal result must be one complete row")
    fields = text[:-1].split("\t")
    _require(len(fields) in (9, 12) and fields[:5] == ["OK", str(index), module, "0", "0"]
             and fields[6] == paths["ir"] and fields[-2:] == ["PCO", paths["artifact"]], "worker terminal PCO result differs")
    _number(fields[5], "IR byte count")
    if len(fields) == 12:
        for field in fields[7:10]: _number(field, "worker phase time")


def _guard(data, argv, pid, manifest_path):
    guard = transport._json(data)
    _require(type(guard) is dict and guard.get("schema") == "pcc.process_tree_sample.v1"
             and guard.get("status") == "COMPLETE" and type(guard.get("returncode")) is int and guard["returncode"] == 0,
             "actual worker process-tree guard did not complete successfully")
    command = guard.get("command")
    _require(type(command) is list and command[-3:] == argv, "guard command differs from actual worker launch")
    largest = guard.get("largest_process_observed")
    # Initial singleton owner requires observed exact native PID. A missing
    # process observation is not filled in from a header or wall-clock match.
    _require(type(largest) is dict and type(largest.get("pid")) is int and largest["pid"] == pid
             and largest.get("command") == " ".join(argv)
             and type(largest.get("manifest_paths")) is list and manifest_path in largest["manifest_paths"],
             "guard lacks exact observed worker PID/argv/manifest")


def _samples(data, launch, binding):
    _require(data.endswith(b"\n"), "sample file is incomplete")
    lines = data[:-1].split(b"\n")
    header = transport._json(lines[0])
    _keys(header, _HEADER_KEYS, "worker sample header")
    _require(header["schema"] == SAMPLE_SCHEMA and header["status"] == "ok" and header["phase_complete"] is True
             and header["phase"] == PHASE and header["module"] == launch["module"]
             and type(header["index"]) is int and header["index"] == launch["index"]
             and type(header["pid"]) is int and header["pid"] == launch["pid"]
             and type(header["period_ns"]) is int and header["period_ns"] == PERIOD_NS
             and type(header["capacity"]) is int and header["capacity"] == CAPACITY,
             "worker sample phase or identity differs")
    _require(header["compiler_image_sha256"] == binding["compiler_sha256"]
             and header["source_commit"] == binding["source"]["commit"]
             and header["source_sha256"] == binding["source"]["source_sha256"]
             and header["manifest_sha256"] == launch["manifest"]["sha256"], "worker sample image/source/manifest identity differs")
    count, dropped = header["count"], header["dropped"]
    _require(_uint(count) and count <= CAPACITY and _uint(dropped) and (not dropped or count == CAPACITY)
             and type(launch["sample"]["count"]) is int and launch["sample"]["count"] == count
             and type(launch["sample"]["dropped"]) is int and launch["sample"]["dropped"] == dropped
             and len(lines) == count + 1, "worker sample count/drop differs")
    pcs = []
    for line in lines[1:]:
        _require(_PC.fullmatch(line) is not None, "sample PC is not canonical positive decimal")
        pc = int(line)
        _require(0 < pc < 1 << 64, "sample PC is outside u64")
        pcs.append(pc)
    return pcs, dropped


def _map_rows(path, binding):
    """Reread only exact already validated map bytes; no new symbol inference."""
    path = Path(path)
    stream, info = transport._checked_open(path, transport.MAX_MAP_BYTES)
    with stream:
        _require(info.st_size == binding["map_bytes"], "verified function map size changed")
        digest = hashlib.sha256()
        count = -1
        total = 0
        while line := stream.readline(transport.MAX_ROW_BYTES + 1):
            _require(len(line) <= transport.MAX_ROW_BYTES and line.endswith(b"\n"), "verified function map row changed")
            digest.update(line); total += len(line); count += 1
            if count:
                yield transport._json(line)
        transport._unchanged(path, stream, info)
    _require(total == binding["map_bytes"] and count == binding["function_count"]
             and digest.hexdigest() == binding["map_sha256"], "verified function map changed during interpretation")


def _attribute(image_path, map_path, binding, pcs):
    frequency = Counter(pcs)
    ordered = sorted(frequency)
    # Difference counts over sampled addresses avoid a quadratic overlap
    # expansion. A second bounded pass retains exact candidate identities only
    # for PC groups with at most the explicit alias display bound.
    diff = [0] * (len(ordered) + 1)
    for row in _map_rows(map_path, binding):
        first = bisect_left(ordered, row["start"])
        last = bisect_left(ordered, row["end"])
        diff[first] += 1; diff[last] -= 1
    counts_by_pc = {}
    active = 0
    for index, pc in enumerate(ordered):
        active += diff[index]; counts_by_pc[pc] = active
    retained = [pc for pc in ordered if 0 < counts_by_pc[pc] <= MAX_RETAINED_ALIAS_ROWS]
    candidates = {pc: [] for pc in retained}
    if retained:
        for row in _map_rows(map_path, binding):
            first = bisect_left(retained, row["start"])
            last = bisect_left(retained, row["end"])
            for index in range(first, last): candidates[retained[index]].append(row)
    image_sha, image_bytes, rx = transport._image_identity(image_path)
    _require(image_sha == binding["compiler_sha256"] and image_bytes == binding["compiler_bytes"], "verified compiler changed during interpretation")
    aggregates = {}
    ambiguous = []
    unknown = []
    totals = {"unique": 0, "ambiguous": 0, "unmapped_rx": 0, "outside_rx": 0}
    for pc in ordered:
        count, n = counts_by_pc[pc], frequency[pc]
        rows = candidates.get(pc, [])
        if count == 1:
            _require(len(rows) == 1, "verified map unique range changed")
            row = rows[0]
            key = (row["object_index"], row["symbol_index"])
            aggregate = aggregates.setdefault(key, {"function": row, "samples": 0})
            aggregate["samples"] += n; totals["unique"] += n
        elif count > 1:
            _require(len(rows) == count if count <= MAX_RETAINED_ALIAS_ROWS else not rows, "verified map alias ranges changed")
            ambiguous.append({"pc": pc, "samples": n, "candidate_count": count, "candidates": rows})
            totals["ambiguous"] += n
        else:
            kind = "unmapped_rx" if any(addr <= pc < addr + size for _, addr, size in rx) else "outside_rx"
            totals[kind] += n
            unknown.append({"pc": pc, "samples": n, "kind": kind})
    _require(sum(totals.values()) == len(pcs), "sample attribution accounting differs")
    return totals, sorted(aggregates.values(), key=lambda row: (-row["samples"], row["function"]["object_index"], row["function"]["symbol_index"])), ambiguous, unknown


def build_report(*, image_path, map_path, binding_path, stage_receipt_path, shared_receipt_path, bundle_manifest_path, lease,
                 launch_path, manifest_path, ast_path, exports_path, source_path, sample_path, guard_path, result_path, artifact_path):
    """Admit a complete source/worker/image-bound sample before attribution."""
    binding = transport.validate_transport(image_path, map_path, binding_path, stage_receipt_path, shared_receipt_path, bundle_manifest_path, lease)
    binding_value, binding_sha, _ = transport._read_receipt(binding_path)
    _require(binding_value == binding, "verified map binding changed")
    launch, launch_sha, _ = transport._read_receipt(launch_path)
    _keys(launch, _LAUNCH_KEYS, "independent worker launch")
    _require(launch["schema"] == LAUNCH_SCHEMA and launch["status"] == "ok" and launch["producer"] == binding["producer"] == lease
             and launch["source"] == binding["source"] and launch["map_binding_sha256"] == binding_sha
             and launch["phase"] == PHASE and _text(launch["module"]) and _uint(launch["index"]) and _uint(launch["pid"]) and launch["pid"] > 0,
             "independent worker launch identity or terminal status differs")
    compiler = launch["compiler"]
    _record(compiler, ("post_sha256",))
    _require(compiler["sha256"] == compiler["post_sha256"] == binding["compiler_sha256"]
             and compiler["bytes"] == binding["compiler_bytes"], "actual launched compiler differs from verified Stage1 ELF")
    _record(launch["manifest"])
    argv = launch["argv"]
    _require(argv == [compiler["path"], "--pcc-python-multi-codegen-worker", launch["manifest"]["path"]],
             "independent actual worker argv differs")
    manifest_data = _read_bound(manifest_path, launch["manifest"], MAX_MANIFEST_BYTES, keep=True)
    paths = _manifest(manifest_data, launch["index"], launch["module"])
    inputs = launch["inputs"]
    _keys(inputs, {"ast", "exports", "source"}, "worker inputs")
    for key, actual_path in (("ast", ast_path), ("exports", exports_path), ("source", source_path)):
        _record(inputs[key]); _require(inputs[key]["path"] == paths[key], "worker input path differs from manifest")
        _read_bound(actual_path, inputs[key], MAX_INPUT_BYTES)
    terminal = launch["terminal"]
    _keys(terminal, {"returncode", "guard", "result", "artifact"}, "worker terminal")
    _require(type(terminal["returncode"]) is int and terminal["returncode"] == 0, "worker terminal returncode is not success")
    guard_data = _read_bound(guard_path, terminal["guard"], transport.MAX_RECEIPT_BYTES, keep=True)
    _guard(guard_data, argv, launch["pid"], launch["manifest"]["path"])
    _record(terminal["result"])
    _require(terminal["result"]["path"] == paths["result"], "worker terminal result path differs")
    result_data = _read_bound(result_path, terminal["result"], MAX_MANIFEST_BYTES, keep=True)
    _result(result_data, paths, launch["index"], launch["module"])
    artifact = terminal["artifact"]
    _record(artifact, ("kind",))
    _require(artifact["kind"] == "PCO" and artifact["path"] == paths["artifact"], "worker terminal object path/kind differs")
    prefix = _read_bound(artifact_path, artifact, bundle.MAX_FILE, extra=("kind",), prefix=64)
    _require(len(prefix) == 64 and prefix[:7] == b"\x7fELF\x02\x01\x01"
             and struct.unpack_from("<HHI", prefix, 16) == (1, 62, 1), "actual terminal PCO is not x64 ELF relocatable")
    sample = launch["sample"]
    _record(sample, ("count", "dropped"))
    expected_path = os.path.join(paths["ir_dir"], f"worker-cpu-pc-{launch['index']}-{launch['pid']}.samples")
    _require(sample["path"] == expected_path, "worker sample path differs or is partial")
    data = _read_bound(sample_path, sample, MAX_SAMPLE_BYTES, extra=("count", "dropped"), keep=True)
    pcs, dropped = _samples(data, launch, binding)
    totals, unique, ambiguous, unknown = _attribute(image_path, map_path, binding, pcs)
    return {"schema": REPORT_SCHEMA, "status": "ok" if pcs else "no_samples", "producer": lease, "source": binding["source"],
            "compiler_sha256": binding["compiler_sha256"], "map_binding_sha256": binding_sha, "map_sha256": binding["map_sha256"],
            "launch_sha256": launch_sha, "manifest_sha256": launch["manifest"]["sha256"],
            "input_sha256": {key: value["sha256"] for key, value in inputs.items()},
            "sample_sha256": sample["sha256"], "guard_sha256": terminal["guard"]["sha256"],
            "result_sha256": terminal["result"]["sha256"], "artifact_sha256": artifact["sha256"],
            "module": launch["module"], "index": launch["index"], "pid": launch["pid"], "phase": PHASE,
            "period_ns": PERIOD_NS, "capacity": CAPACITY, "capture_censored": bool(dropped),
            "counts": {"stored": len(pcs), "dropped": dropped, **totals},
            "unique_functions": unique, "ambiguous_pcs": ambiguous, "unknown_pcs": unknown,
            "scope": "Captured CPU-PC sample counts only; not exact exclusive CPU time or IPC. Dropped samples censor the captured prefix."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    paths = ("image", "map", "binding", "stage_receipt", "shared_receipt", "bundle_manifest", "launch", "manifest", "ast", "exports", "source", "sample", "guard", "result", "artifact")
    for name in paths: parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args(argv)
    lease = {"repository": args.repository, "workflow": ".github/workflows/pcc1-package-parity.yml",
             "run_id": args.run_id, "run_attempt": args.run_attempt, "sha": args.commit}
    report = build_report(lease=lease, **{name + "_path": getattr(args, name) for name in paths})
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Same-invocation indexed input diagnostics, not an alternate compiler route."""

import builtins
import hashlib
import json
from pathlib import Path

import pytest

from pcc.backend import indexed_input_receipts as receipts
from pcc.backend.self_backend_indexed_codec import (
    decode_indexed_module_file,
    encode_indexed_module_file,
)
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.backend.self_backend_parse import parse_self_backend_module


TARGET = "x86_64-unknown-linux-gnu"


def module(value=42, target=TARGET):
    parsed = parse_self_backend_module(
        'target triple = "' + target + '"\n'
        'define i64 @answer() {\nentry:\n  ret i64 ' + str(value) + '\n}\n'
    )
    for function in parsed.functions:
        get_indexed_function_kernel(function)
    return parsed


def setup(tmp_path, monkeypatch, count=1):
    dump = tmp_path / "dump"
    dump.mkdir()
    monkeypatch.setenv("PCC_DEBUG_SELF_IR_DUMP_DIR", str(dump))
    monkeypatch.setenv("PCC_DEBUG_SELF_IR_INVOCATION", "a" * 32)
    sources = []
    names = []
    for index in range(count):
        source = tmp_path / ("source_" + str(index) + ".py")
        source.write_text("value = " + str(index) + "\n")
        sources.append(str(source))
        names.append("module_" + str(index))
    invocation = receipts.begin_invocation(sources, names)
    assert invocation is not None
    contexts = []
    for index in range(count):
        manifest = tmp_path / ("worker_" + str(index) + ".manifest")
        manifest.write_text("coordinator global assignment " + str(index))
        receipts.attach_manifest(str(manifest), invocation, [index])
        context = receipts.worker_context(str(manifest), index, names[index], sources[index])
        assert context is not None
        contexts.append(context)
    return dump, sources, names, contexts


def test_global_indices_keep_two_workers_and_link_actual_outputs(tmp_path, monkeypatch):
    dump, sources, names, contexts = setup(tmp_path, monkeypatch, 2)
    artifacts = []
    # Complete the second worker first: identity must not be completion order.
    for index in (1, 0):
        capture = receipts.begin_capture(module(40 + index), contexts[index], ["mem2reg"])
        assert capture is not None
        output = tmp_path / ("output_" + str(index) + ".pco")
        output.write_bytes(b"output" + bytes([index]))
        receipts.finish_capture(capture, str(output), "PCO")
        artifacts.append((names[index], "PCO", str(output)))
    artifacts.reverse()
    link = receipts.prepare_link(artifacts)
    assert link is not None
    assert [row["index"] for row in link["modules"]] == [0, 1]
    binary = tmp_path / "program"
    binary.write_bytes(b"final executable")
    receipts.finish_link(link, artifacts, str(binary))
    final = json.loads((dump / "link.json").read_text())
    assert final["binary_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert len(list(dump.iterdir())) == 10
    for index in range(2):
        receipt = json.loads((dump / ("self_backend_input_" + str(index) + ".json")).read_text())
        assert receipt["module"] == names[index]
        assert receipt["target"] == TARGET and receipt["passes"] == ["mem2reg"]
        assert (dump / receipt["output"]["name"]).read_bytes() == Path(artifacts[index][2]).read_bytes()
        assert receipt["input"]["sha256"] == hashlib.sha256((dump / receipt["input"]["name"]).read_bytes()).hexdigest()


def test_retry_cannot_overwrite_or_hide_first_attempt(tmp_path, monkeypatch):
    dump, _sources, _names, contexts = setup(tmp_path, monkeypatch)
    first = receipts.begin_capture(module(1), contexts[0], [])
    assert first is not None
    snapshot = (dump / first["input"]["name"]).read_bytes()
    assert receipts.begin_capture(module(2), contexts[0], []) is None
    assert (dump / first["input"]["name"]).read_bytes() == snapshot
    assert (dump / "indexed-capture-failed").exists()
    output = tmp_path / "object"
    output.write_bytes(b"object")
    receipts.finish_capture(first, str(output), "PCO")
    assert receipts.prepare_link([("module_0", "PCO", str(output))]) is None


def test_emission_failure_has_no_completed_receipt(tmp_path, monkeypatch):
    dump, _sources, _names, contexts = setup(tmp_path, monkeypatch)
    capture = receipts.begin_capture(module(), contexts[0], [])
    assert capture is not None
    receipts.finish_capture(capture, str(tmp_path / "missing-output"), "PCO")
    assert not (dump / "self_backend_input_0.json").exists()
    assert not (dump / "link.json").exists()


def test_link_rechecks_changed_output_and_order(tmp_path, monkeypatch):
    dump, _sources, names, contexts = setup(tmp_path, monkeypatch)
    capture = receipts.begin_capture(module(), contexts[0], [])
    output = tmp_path / "object"
    output.write_bytes(b"emitted")
    receipts.finish_capture(capture, str(output), "PCO")
    artifacts = [(names[0], "PCO", str(output))]
    link = receipts.prepare_link(artifacts)
    assert link is not None
    output.write_bytes(b"changed")
    binary = tmp_path / "binary"
    binary.write_bytes(b"binary")
    receipts.finish_link(link, artifacts, str(binary))
    assert not (dump / "link.json").exists()


def test_encoder_bound_precedes_wire_materialization(tmp_path, monkeypatch):
    from pcc.backend import self_backend_indexed_codec as codec

    def forbidden(*args):
        raise AssertionError("wire materialized before bound")

    parsed = module()
    monkeypatch.setattr(codec, "_function_to_wire", forbidden)
    with pytest.raises(ValueError, match="diagnostic exceeds"):
        codec.encode_indexed_module_file(str(tmp_path / "too-large"), parsed, max_bytes=16)
    assert not (tmp_path / "too-large").exists()


def test_decoder_bound_precedes_open_and_decode(tmp_path, monkeypatch):
    path = tmp_path / "oversize"
    with path.open("wb") as stream:
        stream.truncate(receipts.LIMIT + 1)
    original_open = builtins.open

    def forbidden(value, *args, **kwargs):
        assert str(value) != str(path), "oversize file was opened"
        return original_open(value, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", forbidden)
    with pytest.raises(ValueError, match="diagnostic exceeds"):
        decode_indexed_module_file(str(path), max_bytes=receipts.LIMIT)


@pytest.mark.parametrize("tail", [b"x", b"\0" * 8])
def test_decoder_rejects_trailing_bytes(tmp_path, tail):
    path = tmp_path / "input.pidx"
    encode_indexed_module_file(str(path), module(), max_bytes=receipts.LIMIT)
    path.write_bytes(path.read_bytes() + tail)
    with pytest.raises(ValueError, match="size is inconsistent"):
        decode_indexed_module_file(str(path), max_bytes=receipts.LIMIT)


def test_source_and_manifest_drift_fail_closed(tmp_path, monkeypatch):
    dump, sources, names, _contexts = setup(tmp_path, monkeypatch)
    Path(sources[0]).write_text("changed source")
    assert receipts.worker_context(str(tmp_path / "worker_0.manifest"), 0, names[0], sources[0]) is None
    assert (dump / "indexed-capture-failed").exists()


def _worker(tmp_path, monkeypatch, target, passes, *, dump_enabled=True, sidecar=False):
    from pcc.frontends.python import pipeline
    from pcc.frontends.python.codegen.layer1 import L1CodeGen

    source = tmp_path / "probe.py"
    source.write_text("def choose(flag):\n    if flag:\n        value = 40\n    else:\n        value = 41\n    return value + 1\nprint(choose(True))\n")
    generate = L1CodeGen._generate_initial_module

    def generate_target(self):
        self._target_triple = target
        return generate(self)

    monkeypatch.setattr(L1CodeGen, "_generate_initial_module", generate_target)
    for key in ("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "PCC_DIRECT_INDEXED_KERNEL_EMIT",
                "PCC_DIRECT_INDEXED_KERNEL_FUSE_USES", "PCC_DIRECT_INDEXED_NATIVE_OBJECT",
                "PCC_DIRECT_INDEXED_KERNEL_REQUIRE_ZERO_FALLBACK"):
        monkeypatch.setenv(key, "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_RELEASE_FRONTEND", "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_VALIDATE", "0")
    monkeypatch.setenv("PCC_TEXT_INDEXED_KERNEL_EMIT", "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_SIDECAR", "1" if sidecar else "0")
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", passes)
    monkeypatch.setenv("PCC_SELF_TARGET_PASSES", "off")
    manifest = tmp_path / "worker.manifest"
    result = tmp_path / "result.tsv"
    pipeline._write_python_frontend_worker_manifest(
        str(manifest), str(result), str(tmp_path), "", "", [str(source)],
        ["probe"], [0], entry_module="probe", sibling_inits=(),
        ir_scaffold_mode="on", verbose=False,
    )
    dump = tmp_path / "dump"
    if dump_enabled:
        dump.mkdir()
        monkeypatch.setenv("PCC_DEBUG_SELF_IR_DUMP_DIR", str(dump))
        monkeypatch.setenv("PCC_DEBUG_SELF_IR_INVOCATION", "c" * 32)
        invocation = receipts.begin_invocation([str(source)], ["probe"])
        receipts.attach_manifest(str(manifest), invocation, [0])
    else:
        monkeypatch.delenv("PCC_DEBUG_SELF_IR_DUMP_DIR", raising=False)
        monkeypatch.delenv("PCC_DEBUG_SELF_IR_INVOCATION", raising=False)
    return pipeline, manifest, result, dump


@pytest.mark.parametrize("target", [TARGET, "x86_64-pc-windows-msvc"])
@pytest.mark.parametrize("passes", ["off", "default"])
def test_real_worker_captures_exact_postpass_emitter_object(tmp_path, monkeypatch, target, passes):
    from pcc.backend import target_objects

    pipeline, manifest, result, dump = _worker(tmp_path, monkeypatch, target, passes)
    seen = []
    original_capture = receipts.begin_capture
    original_emit = target_objects.emit_x86_64_streamed_module

    def capture(parsed, context, pass_names):
        seen.append((id(parsed), tuple(pass_names)))
        return original_capture(parsed, context, pass_names)

    def emit(parsed, **kwargs):
        assert seen and seen[-1][0] == id(parsed)
        assert parsed.triple == target
        return original_emit(parsed, **kwargs)

    monkeypatch.setattr(receipts, "begin_capture", capture)
    monkeypatch.setattr(target_objects, "emit_x86_64_streamed_module", emit)
    assert pipeline.run_python_multi_codegen_worker(str(manifest)) == 0, result.read_text()
    receipt = json.loads((dump / "self_backend_input_0.json").read_text())
    assert receipt["target"] == target
    assert bool(receipt["passes"]) == (passes != "off")
    assert len(seen) == 1 and receipt["passes"] == list(seen[0][1])
    text = (tmp_path / "module_0.ll").read_text()
    assert (text == "") if passes == "off" else bool(text)
    parsed = decode_indexed_module_file(str(dump / receipt["input"]["name"]), max_bytes=receipts.LIMIT)
    assert parsed.triple == target
    assert (dump / receipt["output"]["name"]).read_bytes() == (tmp_path / "module_0.direct.pco").read_bytes()


def test_no_dump_worker_does_not_call_receipt_or_codec(tmp_path, monkeypatch):
    from pcc.backend import self_backend_indexed_codec as codec

    pipeline, manifest, result, _dump = _worker(tmp_path, monkeypatch, TARGET, "off", dump_enabled=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("disabled diagnostics traversed indexed input")

    monkeypatch.setattr(receipts, "begin_capture", forbidden)
    monkeypatch.setattr(codec, "encode_indexed_module_file", forbidden)
    assert pipeline.run_python_multi_codegen_worker(str(manifest)) == 0, result.read_text()


def test_sidecar_receipt_captures_decoded_consumed_module(tmp_path, monkeypatch):
    from pcc.backend import self_backend_indexed_emit as emitter

    pipeline, manifest, result, dump = _worker(tmp_path, monkeypatch, TARGET, "off", sidecar=True)
    assert pipeline.run_python_multi_codegen_worker(str(manifest)) == 0, result.read_text()
    assert not list(dump.glob("*.pidx")), "frontend sidecar is not completed emitter evidence"
    sidecar = tmp_path / "module_0.direct.pidx"
    output = tmp_path / "consumed.pco"
    decoded = []
    decode = emitter.decode_indexed_module_file
    capture = receipts.begin_capture

    def observe_decode(path, **kwargs):
        parsed = decode(path, **kwargs)
        decoded.append(parsed)
        return parsed

    def observe_capture(parsed, context, passes):
        assert parsed is decoded[-1]
        return capture(parsed, context, passes)

    monkeypatch.setattr(emitter, "decode_indexed_module_file", observe_decode)
    monkeypatch.setattr(receipts, "begin_capture", observe_capture)
    emitter.emit_indexed_module_file(str(sidecar), str(output), "PCO", optimize=True)
    assert len(decoded) == 1
    receipt = json.loads((dump / "self_backend_input_0.json").read_text())
    assert (dump / receipt["output"]["name"]).read_bytes() == output.read_bytes()


def test_owner_companions_do_not_inventory_unrelated_files(tmp_path, monkeypatch):
    dump, sources, names, contexts = setup(tmp_path, monkeypatch)
    owner = tmp_path / "large-owner"
    owner.mkdir()
    for index in range(140):
        (owner / (str(index) + ".json")).write_text("unrelated AST metadata")
    manifest = owner / "worker.manifest"
    manifest.write_text("global assignment")
    invocation = json.loads((dump / "invocation.json").read_text())
    receipts.attach_manifest(str(manifest), invocation, [0])
    assert receipts.worker_context(str(manifest), 0, names[0], sources[0]) is not None
    sidecar = owner / "input.pidx"
    encode_indexed_module_file(str(sidecar), module())
    receipts.publish_sidecar_request(str(sidecar), contexts[0], [])
    assert receipts.sidecar_context(str(sidecar))[0] == contexts[0]
    assert not (dump / "indexed-capture-failed").exists()


def test_fixed_publication_reservation_rejects_concurrent_writer(tmp_path, monkeypatch):
    path = tmp_path / "receipt.json"
    replace = receipts.os.replace
    rejected = []

    def competing_replace(source, destination):
        # Interleave a second compliant writer precisely after the first
        # writer checked final-path absence but before atomic publication.
        with pytest.raises(FileExistsError):
            receipts._write_json(str(path), {"writer": 2})
        rejected.append(True)
        return replace(source, destination)

    monkeypatch.setattr(receipts.os, "replace", competing_replace)
    receipts._write_json(str(path), {"writer": 1})
    assert rejected == [True]
    assert json.loads(path.read_text()) == {"writer": 1}
    with pytest.raises(ValueError, match="already exists"):
        receipts._write_json(str(path), {"writer": 3})
    assert json.loads(path.read_text()) == {"writer": 1}


def test_metadata_rejects_dangling_final_symlink(tmp_path):
    path = tmp_path / "receipt.json"
    try:
        path.symlink_to(tmp_path / "missing")
    except (OSError, NotImplementedError):
        pytest.skip("host cannot create test symlinks")
    with pytest.raises(ValueError, match="already exists"):
        receipts._write_json(str(path), {"writer": 1})
    assert path.is_symlink()


def test_failed_capture_does_not_replace_emitter_exception(tmp_path, monkeypatch):
    from pcc.backend import target_objects

    pipeline, manifest, result, dump = _worker(tmp_path, monkeypatch, TARGET, "off")

    def emitter_failure(*args, **kwargs):
        raise ValueError("original emission failure")

    monkeypatch.setattr(target_objects, "emit_x86_64_streamed_module", emitter_failure)
    assert pipeline.run_python_multi_codegen_worker(str(manifest)) == 1
    assert "original emission failure" in result.read_text()
    assert not (dump / "self_backend_input_0.json").exists()
    assert not (dump / "link.json").exists()


def test_diagnostic_failure_does_not_stop_compiler(tmp_path, monkeypatch):
    from pcc.backend import self_backend_indexed_codec as codec

    pipeline, manifest, result, dump = _worker(tmp_path, monkeypatch, TARGET, "off")

    def diagnostic_failure(*args, **kwargs):
        raise OSError("diagnostic disk full")

    monkeypatch.setattr(codec, "encode_indexed_module_file", diagnostic_failure)
    assert pipeline.run_python_multi_codegen_worker(str(manifest)) == 0, result.read_text()
    assert (tmp_path / "module_0.direct.pco").is_file()
    assert (dump / "indexed-capture-failed").exists()
    assert not (dump / "self_backend_input_0.json").exists()


def test_snapshot_rejects_stale_partial_without_overwrite(tmp_path, monkeypatch):
    dump, _sources, _names, contexts = setup(tmp_path, monkeypatch)
    partial = dump / "self_backend_input_0.pidx.partial"
    partial.write_bytes(b"stale partial")
    assert receipts.begin_capture(module(), contexts[0], []) is None
    assert partial.read_bytes() == b"stale partial"
    assert (dump / "indexed-capture-failed").exists()


def test_failed_partial_still_consumes_aggregate_budget(tmp_path, monkeypatch):
    dump, _sources, _names, contexts = setup(tmp_path, monkeypatch, 2)
    partial = dump / "self_backend_input_1.pidx.partial"
    with partial.open("wb") as stream:
        stream.truncate(receipts.LIMIT)
    assert receipts.begin_capture(module(), contexts[0], []) is None
    assert not (dump / "self_backend_input_0.pidx").exists()
    assert (dump / "indexed-capture-failed").exists()


def test_sidecar_collision_is_not_retired(tmp_path, monkeypatch):
    dump, _sources, _names, contexts = setup(tmp_path, monkeypatch)
    sidecar = tmp_path / "input.pidx"
    encode_indexed_module_file(str(sidecar), module())
    companion = Path(str(sidecar) + ".receipt.json")
    companion.write_bytes(b"unrelated pre-existing companion")
    receipts.publish_sidecar_request(str(sidecar), contexts[0], [])
    receipts.retire_sidecar_request(str(sidecar))
    assert companion.read_bytes() == b"unrelated pre-existing companion"
    assert not Path(str(sidecar) + ".receipt.owner").exists()
    assert (dump / "indexed-capture-failed").exists()


def test_successful_sidecar_owner_can_retire_its_companion(tmp_path, monkeypatch):
    _dump, _sources, _names, contexts = setup(tmp_path, monkeypatch)
    sidecar = tmp_path / "input.pidx"
    encode_indexed_module_file(str(sidecar), module())
    receipts.publish_sidecar_request(str(sidecar), contexts[0], [])
    assert receipts.sidecar_context(str(sidecar))[0] == contexts[0]
    receipts.retire_sidecar_request(str(sidecar))
    assert not Path(str(sidecar) + ".receipt.json").exists()
    assert not Path(str(sidecar) + ".receipt.owner").exists()


def test_changed_valid_sidecar_context_poison_survives_retry(tmp_path, monkeypatch):
    from pcc.backend import self_backend_indexed_emit as emitter

    pipeline, manifest, result, dump = _worker(tmp_path, monkeypatch, TARGET, "off", sidecar=True)
    assert pipeline.run_python_multi_codegen_worker(str(manifest)) == 0, result.read_text()
    sidecar = tmp_path / "module_0.direct.pidx"
    output = tmp_path / "consumed.pco"
    original_context = receipts.sidecar_context
    reads = []

    def changed_context(path):
        context, passes = original_context(path)
        assert context is not None
        reads.append(True)
        return context, (["mem2reg"] if len(reads) == 2 else passes)

    monkeypatch.setattr(receipts, "sidecar_context", changed_context)
    emitter.emit_indexed_module_file(str(sidecar), str(output), "PCO", optimize=True)
    assert output.exists(), "diagnostic mismatch must not replace compiler outcome"
    assert (dump / "indexed-capture-failed").exists()
    monkeypatch.setattr(receipts, "sidecar_context", original_context)
    emitter.emit_indexed_module_file(str(sidecar), str(output), "PCO", optimize=True)
    assert (dump / "indexed-capture-failed").exists()
    assert receipts.prepare_link([("probe", "PCO", str(output))]) is None


def test_fresh_no_dump_worker_never_imports_receipt_module(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    _pipeline, manifest, _result, _dump = _worker(tmp_path, monkeypatch, TARGET, "off", dump_enabled=False)
    script = """import sys
from pcc.frontends.python import pipeline
assert 'pcc.backend.indexed_input_receipts' not in sys.modules
assert pipeline.run_python_multi_codegen_worker(sys.argv[1]) == 0
assert 'pcc.backend.indexed_input_receipts' not in sys.modules
print('NO_DUMP_IMPORT_OK')
"""
    result = subprocess.run([sys.executable, "-c", script, str(manifest)],
                            env=dict(os.environ), capture_output=True, text=True, timeout=90)
    assert (result.returncode, result.stdout, result.stderr) == (0, "NO_DUMP_IMPORT_OK\n", "")


def _mock_native_scan(monkeypatch, names, *, scan_error=0, close_error=0):
    entries = iter([name + b"\0" for name in names])
    closed = []
    monkeypatch.setattr(receipts, "stack_alloc", lambda count: bytearray(count))
    monkeypatch.setattr(receipts, "store_i8", lambda raw, index, value: raw.__setitem__(index, value))
    monkeypatch.setattr(receipts, "load_i8", lambda raw, index: raw[index])
    monkeypatch.setattr(receipts, "ptr_is_null", lambda value: value is None)
    monkeypatch.setattr(receipts, "strlen", lambda raw: len(raw) - 1)
    monkeypatch.setattr(receipts, "directory_open", lambda path: object())
    monkeypatch.setattr(receipts, "directory_next", lambda stream: next(entries, None))
    monkeypatch.setattr(receipts, "directory_error", lambda stream: scan_error)
    monkeypatch.setattr(receipts, "_receipt_entry_name", lambda raw, size: raw[:size].decode("ascii"))

    def close(stream):
        closed.append(stream)
        return close_error

    monkeypatch.setattr(receipts, "directory_close", close)
    return closed


def test_native_scanner_stops_before_allocating_entry_129(monkeypatch):
    names = [str(index).encode() for index in range(129)]
    closed = _mock_native_scan(monkeypatch, names)
    published = []

    def name(raw, size):
        published.append(True)
        return raw[:size].decode()

    monkeypatch.setattr(receipts, "_receipt_entry_name", name)
    with pytest.raises(ValueError, match="inventory exceeds"):
        receipts._native_entries("directory")
    assert len(published) == 128 and len(closed) == 1


def test_native_scanner_uses_literal_bounded_scratch(monkeypatch):
    import ast

    tree = ast.parse(Path(receipts.__file__).read_text())
    scanner = next(node for node in tree.body
                   if isinstance(node, ast.FunctionDef) and node.name == "_native_entries")
    call = next(node for node in ast.walk(scanner)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "stack_alloc")
    assert len(call.args) == 1 and isinstance(call.args[0], ast.Name)
    binding = next(node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == call.args[0].id
                           for target in node.targets))
    assert isinstance(binding.value, ast.Constant) and binding.value.value == 32765
    closed = _mock_native_scan(monkeypatch, [])
    allocations = []

    def scratch(count):
        allocations.append(count)
        return bytearray(count)

    monkeypatch.setattr(receipts, "stack_alloc", scratch)
    assert receipts._native_entries("directory") == []
    assert allocations == [32765] and len(closed) == 1
    with pytest.raises(ValueError, match="directory path exceeds"):
        receipts._native_entries("x" * 8192)
    assert allocations == [32765]


@pytest.mark.parametrize("scan_error,close_error,expected", [
    (-5, -5, "scan failed"), (0, -5, "close failed"),
])
def test_native_scanner_closes_and_preserves_first_error(monkeypatch, scan_error, close_error, expected):
    closed = _mock_native_scan(monkeypatch, [b".", b"..", b"entry"],
                               scan_error=scan_error, close_error=close_error)
    with pytest.raises(OSError, match=expected):
        receipts._native_entries("directory")
    assert len(closed) == 1


def test_native_scanner_closes_after_name_allocation_failure(monkeypatch):
    closed = _mock_native_scan(monkeypatch, [b"entry"], close_error=-5)

    def failure(raw, size):
        raise MemoryError("original allocation failure")

    monkeypatch.setattr(receipts, "_receipt_entry_name", failure)
    with pytest.raises(MemoryError, match="original allocation failure"):
        receipts._native_entries("directory")
    assert len(closed) == 1



def _first_failure(dump, stage, index=0):
    raw = (dump / "indexed-capture-failed").read_bytes()
    assert len(raw) <= 1024
    value = json.loads(raw)
    assert value["schema"] == "pcc.indexed-input-first-recorded-failure.v1"
    assert (value["invocation"], value["index"], value["stage"]) == ("a" * 32, index, stage)
    assert b"private-source" not in raw
    return value


def _raise_diagnostic(*args, **kwargs):
    raise OSError("private-source-path-and-secret:" + "x" * 8192)


@pytest.mark.parametrize("stage", (
    "capture-identity", "capture-target", "capture-lock", "claim",
    "remaining-input-budget", "snapshot-destination", "codec-preflight",
    "codec-shape", "codec-header", "codec-wire-size", "codec-write",
    "snapshot-hash", "snapshot-publish", "snapshot-header-read", "unlock",
))
def test_capture_first_failure_reports_real_operation(tmp_path, monkeypatch, stage):
    from pcc.backend import self_backend_indexed_codec as codec
    dump, _sources, _names, contexts = setup(tmp_path, monkeypatch)
    context, parsed = dict(contexts[0]), module()
    if stage == "capture-identity":
        context["invocation"] = "b" * 32
    elif stage == "capture-target":
        parsed = module(target="unknown-unknown-unknown")
    elif stage in ("capture-lock", "claim", "remaining-input-budget", "snapshot-hash"):
        name = {"capture-lock": "_lock", "claim": "_write_json",
                "remaining-input-budget": "_remaining", "snapshot-hash": "_hash"}[stage]
        monkeypatch.setattr(receipts, name, _raise_diagnostic)
    elif stage == "snapshot-destination":
        (dump / "self_backend_input_0.pidx.partial").write_bytes(b"first owner")
    elif stage in ("codec-preflight", "codec-wire-size"):
        monkeypatch.setattr(receipts, "_remaining", lambda *args: 16)
        if stage == "codec-wire-size":
            monkeypatch.setattr(codec, "_bounded_capture_budget", lambda *args: None)
    elif stage == "codec-shape":
        monkeypatch.setattr(codec, "_function_to_wire", _raise_diagnostic)
    elif stage == "codec-header":
        original = json.dumps
        def dumps(value, *args, **kwargs):
            if isinstance(value, dict) and value.get("schema", "").startswith("pcc.self-backend.indexed-module."):
                _raise_diagnostic()
            return original(value, *args, **kwargs)
        monkeypatch.setattr(codec.json, "dumps", dumps)
    elif stage == "codec-write":
        monkeypatch.setattr(codec, "open", _raise_diagnostic, raising=False)
    elif stage == "snapshot-publish":
        original = receipts.os.replace
        def replace(source, destination):
            if str(destination).endswith(".pidx"):
                _raise_diagnostic()
            return original(source, destination)
        monkeypatch.setattr(receipts.os, "replace", replace)
    elif stage == "snapshot-header-read":
        def open_(path, mode="r", *args, **kwargs):
            if str(path).endswith(".pidx") and mode == "rb":
                _raise_diagnostic()
            return builtins.open(path, mode, *args, **kwargs)
        monkeypatch.setattr(receipts, "open", open_, raising=False)
    elif stage == "unlock":
        monkeypatch.setattr(receipts.os, "unlink", _raise_diagnostic)
    capture = receipts.begin_capture(parsed, context, [])
    assert (capture is not None) == (stage == "unlock")
    index = -1 if stage in ("capture-identity", "capture-target", "capture-lock", "claim") else 0
    value = _first_failure(dump, stage, index)
    if stage in ("codec-preflight", "codec-wire-size"):
        assert value["remaining_bytes"] == 16 and value["observed_bytes"] > 16
        assert value["reason"] == ("conservative_limit" if stage == "codec-preflight" else "wire_limit")
    first = (dump / "indexed-capture-failed").read_bytes()
    assert receipts.prepare_link([]) is None
    assert (dump / "indexed-capture-failed").read_bytes() == first
    assert not (dump / "self_backend_input_0.json").exists()


@pytest.mark.parametrize("stage", (
    "output-identity", "output-lock", "remaining-output-budget", "output-stat",
    "output-destination", "output-copy-open", "output-copy", "output-rehash",
    "output-publish", "emission-json",
))
def test_output_first_failure_reports_real_operation(tmp_path, monkeypatch, stage):
    dump, _sources, _names, contexts = setup(tmp_path, monkeypatch)
    capture = receipts.begin_capture(module(), contexts[0], [])
    assert capture is not None
    output = tmp_path / "object.pco"
    output.write_bytes(b"retained-output")
    kind = "PCO"
    if stage == "output-identity":
        kind = "INVALID"
    elif stage in ("output-lock", "remaining-output-budget", "output-rehash", "emission-json"):
        name = {"output-lock": "_lock", "remaining-output-budget": "_remaining",
                "output-rehash": "_hash", "emission-json": "_write_json"}[stage]
        monkeypatch.setattr(receipts, name, _raise_diagnostic)
    elif stage == "output-stat":
        original = receipts.os.path.getsize
        def getsize(path):
            if path == str(output):
                _raise_diagnostic()
            return original(path)
        monkeypatch.setattr(receipts.os.path, "getsize", getsize)
    elif stage == "output-destination":
        (dump / "self_backend_output_0.pco").write_bytes(b"prior-owner")
    elif stage in ("output-copy-open", "output-copy"):
        class Writer:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def write(self, data):
                _raise_diagnostic()
        def open_(path, mode="r", *args, **kwargs):
            if str(path).endswith(".pco.partial"):
                if stage == "output-copy-open":
                    _raise_diagnostic()
                return Writer()
            return builtins.open(path, mode, *args, **kwargs)
        monkeypatch.setattr(receipts, "open", open_, raising=False)
    elif stage == "output-publish":
        monkeypatch.setattr(receipts.os, "replace", _raise_diagnostic)
    assert receipts.finish_capture(capture, str(output), kind) is None
    value = _first_failure(dump, stage, -1 if stage == "output-identity" else 0)
    if stage in ("output-copy-open", "output-copy", "output-rehash", "output-publish", "emission-json"):
        assert value["observed_bytes"] == len(b"retained-output")
        assert value["copied_bytes"] == (0 if stage == "output-copy-open" else len(b"retained-output"))
    assert not (dump / "self_backend_input_0.json").exists()


@pytest.mark.parametrize("stage", (
    "link-invocation", "module-count", "emission-json-read", "claim-read",
    "input-identity", "input-size", "input-hash", "retained-hash", "inventory",
    "link-recheck", "binary-hash", "link-json",
))
def test_link_first_failure_reports_real_operation(tmp_path, monkeypatch, stage):
    dump, _sources, names, contexts = setup(tmp_path, monkeypatch)
    capture = receipts.begin_capture(module(), contexts[0], [])
    output = tmp_path / "object.pco"
    output.write_bytes(b"retained-output")
    receipts.finish_capture(capture, str(output), "PCO")
    artifacts = [(names[0], "PCO", str(output))]
    link = receipts.prepare_link(artifacts)
    assert link is not None
    binary = tmp_path / "binary"
    binary.write_bytes(b"binary")
    if stage == "link-invocation":
        monkeypatch.setattr(receipts, "_invocation", _raise_diagnostic)
    elif stage == "module-count":
        artifacts = []
    elif stage in ("emission-json-read", "claim-read"):
        original = receipts._read_json
        def read_json(path):
            suffix = "self_backend_input_0.json" if stage == "emission-json-read" else "module_0.claim.json"
            if str(path).endswith(suffix):
                _raise_diagnostic()
            return original(path)
        monkeypatch.setattr(receipts, "_read_json", read_json)
    elif stage == "input-identity":
        artifacts = [("wrong-module", "PCO", str(output))]
    elif stage == "input-size":
        output.write_bytes(b"changed")
    elif stage in ("input-hash", "retained-hash", "binary-hash"):
        original = receipts._hash
        target = str(output) if stage == "input-hash" else (
            str(binary) if stage == "binary-hash" else str(dump / "self_backend_output_0.pco"))
        def hash_(path, *args):
            if path == target:
                _raise_diagnostic()
            return original(path, *args)
        monkeypatch.setattr(receipts, "_hash", hash_)
    elif stage == "inventory":
        (dump / "unknown-entry").write_bytes(b"")
    elif stage == "link-recheck":
        monkeypatch.setattr(receipts, "prepare_link", lambda *args: None)
    elif stage == "link-json":
        monkeypatch.setattr(receipts, "_write_json", _raise_diagnostic)
    if stage in ("link-recheck", "binary-hash", "link-json"):
        assert receipts.finish_link(link, artifacts, str(binary)) is None
    else:
        assert receipts.prepare_link(artifacts) is None
    index = -1 if stage in ("link-invocation", "module-count", "inventory", "link-recheck", "binary-hash", "link-json") else 0
    value = _first_failure(dump, stage, index)
    if stage == "inventory":
        assert value["inventory_count"] == 6 and value["reason"] == "inventory_mismatch"
    assert not (dump / "link.json").exists()


@pytest.mark.parametrize("exception,classification", (
    (ValueError("secret"), "value_error"), (KeyError("secret"), "key_error"),
    (TypeError("secret"), "type_error"), (OSError("secret"), "os_error"),
    (FileExistsError("secret"), "file_exists"), (MemoryError("secret"), "memory_error"),
    (UnicodeError("secret"), "unicode_error"), (RuntimeError("secret"), "other_exception"),
))
def test_first_failure_has_fixed_exception_class(exception, classification):
    value = json.loads(receipts._failure_payload(receipts._failure_state("invocation"), exception))
    assert value["exception_class"] == classification
    assert value["reason"] == "unknown" and "secret" not in json.dumps(value)


def test_first_failure_never_formats_private_exception_or_extra_state():
    class PrivateError(Exception):
        def __str__(self):
            raise AssertionError("exception formatted")
        def __repr__(self):
            raise AssertionError("exception represented")
    state = receipts._failure_state("invocation")
    state["private"] = "private-source-and-secret" * 10000
    state["observed_bytes"] = 1 << 10000
    raw = receipts._failure_payload(state, PrivateError("secret" * 100000))
    value = json.loads(raw)
    assert len(raw) <= 1024 and b"private" not in raw and b"secret" not in raw
    assert value["overflow"] is True and value["observed_bytes"] == -1


@pytest.mark.parametrize("key,value", (
    ("stage", "private-stage"), ("invocation", "private-token"), ("index", True),
    ("index", 31), ("remaining_bytes", -2), ("observed_bytes", "private-number"),
    ("copied_bytes", True),
))
def test_invalid_diagnostic_state_falls_back_to_original_poison(tmp_path, monkeypatch, key, value):
    dump, _sources, _names, _contexts = setup(tmp_path, monkeypatch)
    state = receipts._failure_state("invocation")
    state[key] = value
    receipts._poison(state, ValueError("private"))
    assert (dump / "indexed-capture-failed").read_bytes() == b"incomplete indexed capture\n"


@pytest.mark.parametrize("prior", (b"", b"{", b"incomplete indexed capture\n", b'{"unknown":1}'))
def test_first_failure_never_repairs_existing_marker(tmp_path, monkeypatch, prior):
    dump, _sources, _names, _contexts = setup(tmp_path, monkeypatch)
    marker = dump / "indexed-capture-failed"
    marker.write_bytes(prior)
    receipts._poison(receipts._failure_state("invocation"), ValueError("later"))
    assert marker.read_bytes() == prior


def test_first_failure_does_not_follow_marker_symlink(tmp_path, monkeypatch):
    dump, _sources, _names, _contexts = setup(tmp_path, monkeypatch)
    destination = tmp_path / "private"
    destination.write_bytes(b"unchanged")
    marker = dump / "indexed-capture-failed"
    try:
        marker.symlink_to(destination)
    except OSError:
        pytest.skip("host cannot create symlinks")
    receipts._poison(receipts._failure_state("invocation"), ValueError("later"))
    assert marker.is_symlink() and destination.read_bytes() == b"unchanged"


def test_first_failure_is_exclusive_not_chronological(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    dump, _sources, _names, _contexts = setup(tmp_path, monkeypatch)
    barrier = Barrier(2)
    states = [receipts._failure_state(stage) for stage in ("invocation", "manifest")]
    earlier_error = ValueError("raised earlier is not publication ordering")
    for state in states:
        state["invocation"] = "a" * 32
    def publish(state):
        barrier.wait(timeout=5)
        receipts._poison(state, ValueError("private"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(publish, state) for state in states]
        for future in futures:
            future.result(timeout=5)
    marker = dump / "indexed-capture-failed"
    first = marker.read_bytes()
    assert first in [receipts._failure_payload(state, ValueError("private")) for state in states]
    receipts._poison(receipts._failure_state("invalidate"), earlier_error)
    assert marker.read_bytes() == first


@pytest.mark.parametrize("failure", ("allocation", "serialization", "short-write", "close"))
def test_diagnostic_failure_keeps_original_poison_attempt(tmp_path, monkeypatch, failure):
    dump, _sources, _names, contexts = setup(tmp_path, monkeypatch)
    marker = dump / "indexed-capture-failed"
    if failure == "allocation":
        monkeypatch.setattr(receipts, "_failure_state", _raise_diagnostic)
        assert receipts.begin_capture(module(), contexts[0], []) is None
        assert marker.read_bytes() == b"incomplete indexed capture\n"
        return
    if failure == "serialization":
        monkeypatch.setattr(receipts, "_failure_payload", _raise_diagnostic)
        receipts._poison()
        assert marker.read_bytes() == b"incomplete indexed capture\n"
        return
    calls = []
    class Stream:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
            if failure == "close":
                _raise_diagnostic()
        def write(self, raw):
            return self.stream.write(raw[:3] if failure == "short-write" else raw)
    def open_(path, mode="r", *args, **kwargs):
        if Path(path) == marker:
            calls.append(mode)
            return Stream(builtins.open(path, mode, *args, **kwargs))
        return builtins.open(path, mode, *args, **kwargs)
    monkeypatch.setattr(receipts, "open", open_, raising=False)
    receipts._poison(receipts._failure_state("invocation"), ValueError("private"))
    first = marker.read_bytes()
    assert first and (len(first) == 3 if failure == "short-write" else len(first) <= 1024)
    receipts._poison(receipts._failure_state("manifest"), ValueError("later"))
    assert calls == ["xb", "xb"] and marker.read_bytes() == first


def test_first_capture_failure_survives_unlock_failure(tmp_path, monkeypatch):
    dump, _sources, _names, contexts = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(receipts, "_remaining", _raise_diagnostic)
    monkeypatch.setattr(receipts.os, "unlink", _raise_diagnostic)
    assert receipts.begin_capture(module(), contexts[0], []) is None
    _first_failure(dump, "remaining-input-budget")


def test_negative_output_capacity_keeps_budget_failure_diagnosis(tmp_path, monkeypatch):
    dump, _sources, _names, contexts = setup(tmp_path, monkeypatch)
    capture = receipts.begin_capture(module(), contexts[0], [])
    with (dump / "self_backend_output_1.s.partial").open("wb") as stream:
        stream.truncate(receipts.LIMIT + 2)
    output = tmp_path / "object.pco"
    output.write_bytes(b"output")
    receipts.finish_capture(capture, str(output), "PCO")
    value = _first_failure(dump, "output-stat")
    assert value["remaining_bytes"] == -1
    assert value["observed_bytes"] == 6 and value["reason"] == "output_limit_or_kind"
    assert not (dump / "self_backend_input_0.json").exists()


def test_link_module_failure_never_uses_previous_module_byte_count(tmp_path, monkeypatch):
    dump, _sources, names, contexts = setup(tmp_path, monkeypatch, count=2)
    outputs = []
    for index in range(2):
        capture = receipts.begin_capture(module(index), contexts[index], [])
        output = tmp_path / ("object" + str(index))
        output.write_bytes(b"first-output-size" if index == 0 else b"second")
        receipts.finish_capture(capture, str(output), "PCO")
        outputs.append((names[index], "PCO", str(output)))
    original = receipts._read_json
    def read_json(path):
        if str(path).endswith("self_backend_input_1.json"):
            _raise_diagnostic()
        return original(path)
    monkeypatch.setattr(receipts, "_read_json", read_json)
    assert receipts.prepare_link(outputs) is None
    assert _first_failure(dump, "emission-json-read", 1)["observed_bytes"] == -1


@pytest.mark.parametrize("message,reason", tuple(receipts._FAILURE_REASONS.items()))
def test_first_failure_maps_only_fixed_internal_reason(message, reason):
    raw = receipts._failure_payload(receipts._failure_state("codec-shape"), ValueError(message))
    assert json.loads(raw)["reason"] == reason


@pytest.mark.parametrize("entry,stage", (
    ("begin_invocation", "invocation"), ("attach_manifest", "manifest"),
    ("worker_context", "worker-context"), ("publish_sidecar_request", "sidecar-publish"),
    ("sidecar_context", "sidecar-context"), ("retire_sidecar_request", "sidecar-retire"),
))
def test_public_receipt_failure_keeps_entry_stage(tmp_path, monkeypatch, entry, stage):
    dump, sources, names, contexts = setup(tmp_path, monkeypatch)
    calls = {
        "begin_invocation": lambda: receipts.begin_invocation(sources, names),
        "attach_manifest": lambda: receipts.attach_manifest(str(tmp_path / "missing"), {"invocation": "a" * 32}, [0]),
        "worker_context": lambda: receipts.worker_context(str(tmp_path / "missing"), 0, names[0], sources[0]),
        "publish_sidecar_request": lambda: receipts.publish_sidecar_request(str(tmp_path / "missing"), contexts[0], []),
        "sidecar_context": lambda: receipts.sidecar_context(str(tmp_path / "missing")),
        "retire_sidecar_request": lambda: receipts.retire_sidecar_request(str(tmp_path / "missing")),
    }
    if entry == "retire_sidecar_request":
        monkeypatch.setattr(receipts.os.path, "isfile", lambda path: True)
    calls[entry]()
    value = json.loads((dump / "indexed-capture-failed").read_bytes())
    assert value["stage"] == stage and value["index"] == -1
    # sidecar_context reads its request before validating the invocation.
    assert value["invocation"] == ("" if entry == "sidecar_context" else "a" * 32)

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

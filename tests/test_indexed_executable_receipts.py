"""Host-only indexed receipt controls; these do not establish native acceptance."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from scripts import ci_native_object_regressions as gate
from tests.python import indexed_receipt_support as indexed
from tests.python import owned_regression_support as support
from tests.test_ci_native_object_regressions import _elf_image_fixture, _program_ir


TOKEN = "1234567890abcdef" * 2
TARGET = "x86_64-unknown-linux-gnu"
MARKER = "OBJECT_TYPE_IDENTITY_OK"


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _encode(tmp_path, text=None, mutate=None):
    from pcc.backend.self_backend_indexed_codec import encode_indexed_module_file
    from pcc.backend.self_backend_kernel import get_indexed_function_kernel
    from pcc.backend.self_backend_parse import parse_self_backend_module

    module = parse_self_backend_module('target triple = "' + TARGET + '"\n' +
                                       (_program_ir() if text is None else text))
    for function in module.functions:
        get_indexed_function_kernel(function)
    if mutate is not None:
        module = mutate(module)
    path = tmp_path / "encoded.pidx"
    encode_indexed_module_file(str(path), module, max_bytes=indexed.LIMIT)
    try:
        return path.read_bytes()
    finally:
        path.unlink()
        for function in module.functions:
            function.indexed_kernel.close_native_tables()


def _write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _publish(dump, raw_modules, source, binary, token=TOKEN):
    """Independent protocol fixture: production helpers cannot bless their own output."""
    rows, links = [], []
    for index, raw in enumerate(raw_modules):
        name = "program" if index == len(raw_modules) - 1 else "dependency" + str(index)
        attempt = _sha((token + ":" + str(index)).encode())[:32]
        rows.append({"index": index, "module": name, "source_sha256": gate.sha(source)})
        claim = {"schema": "pcc.indexed-input-claim.v1", "invocation": token,
                 "index": index, "module": name, "attempt": attempt}
        _write_json(dump / f"module_{index}.claim.json", claim)
        input_name, output_name = f"self_backend_input_{index}.pidx", f"self_backend_output_{index}.pco"
        (dump / input_name).write_bytes(raw)
        output = b"host-only emitted-output identity fixture; never linked:" + str(index).encode()
        (dump / output_name).write_bytes(output)
        emission = {**claim, "schema": "pcc.indexed-executable-input.v1", "status": "EMITTED",
                    "target": TARGET, "passes": [], "representation": indexed.codec_header(raw)["schema"],
                    "input": {"name": input_name, "bytes": len(raw), "sha256": _sha(raw)},
                    "output": {"name": output_name, "bytes": len(output), "sha256": _sha(output), "kind": "PCO"}}
        _write_json(dump / f"self_backend_input_{index}.json", emission)
        links.append({"index": index, "module": name, "attempt": attempt,
                      "input_sha256": _sha(raw), "output_sha256": _sha(output),
                      "output_name": output_name, "kind": "PCO"})
    _write_json(dump / "invocation.json", {"schema": "pcc.indexed-input-invocation.v1",
                "invocation": token, "count": len(rows), "modules": rows})
    _write_json(dump / "link.json", {"schema": "pcc.indexed-input-link.v1", "invocation": token,
                "modules": links, "binary_sha256": gate.sha(binary), "binary_bytes": binary.stat().st_size})


def _inputs(tmp_path):
    source, binary, archive = (tmp_path / name for name in ("program.py", "program.out", "runtime.a"))
    source.write_bytes(b"source identity fixture\n")
    binary.write_bytes(_elf_image_fixture())
    archive.write_bytes(b"runtime identity fixture")
    dump = tmp_path / "executable-ir"
    dump.mkdir()
    raw = _encode(tmp_path)
    _publish(dump, [raw], source, binary)
    record = {"run_id": "host-control-run", "indexed_input_invocation": TOKEN,
              "compiler_fixture_parameter": "pcc0", "compiler_mode": "pcc0", "backend": "self",
              "source_sha256": gate.sha(source), "binary_path": str(binary),
              "runtime_archive": str(archive), "runtime_sha256": gate.sha(archive)}
    return dump, record, source, binary, archive


def _select(modules):
    return support.select_program_ir(modules, MARKER)


def _record(args, **kwargs):
    support._record_observed_ir_dump(*args[:1], _select, *args[1:], completed=True, **kwargs)
    args[1]["binary_sha256"] = gate.sha(args[3])
    return json.loads((args[2].parent / "executable-ir-dump.json").read_text())


def test_indexed_retention_is_raw_bounded_and_outer_gate_replays_it(tmp_path):
    args = _inputs(tmp_path)
    evidence = _record(args)
    assert evidence["status"] == "INDEXED_INPUTS_RETAINED"
    assert evidence["modules"] == [] and evidence["selected_module_indices"] == [0]
    assert evidence["qualification"] == "NOT_ASSESSED"
    assert evidence["all_modules_captured"] == evidence["producer_completeness"] == "LINK_BOUND"
    assert len((tmp_path / "executable-ir-dump.json").read_bytes()) <= indexed.LIMIT
    assert len(evidence["retention"]["files"]) == 6
    for row in evidence["retention"]["files"]:
        raw = base64.b64decode(row["base64"], validate=True)
        assert raw == (args[0] / row["name"]).read_bytes()
        assert row["bytes"] == len(raw) and row["sha256"] == _sha(raw)
    assert gate.verify_indexed_ir(tmp_path, args[1], "object-identity", TARGET) == evidence


@pytest.mark.parametrize("mutation", (
    "missing", "unused", "foreign", "intern-only", "prefix", "array-length",
    "duplicate-definition", "ambiguous-module", "ambiguous-local", "mixed",
))
def test_indexed_marker_requires_exact_unique_real_operand_use(tmp_path, mutation):
    text = _program_ir()
    mutate = None
    if mutation == "missing":
        text = _program_ir("other")
    elif mutation in ("unused", "intern-only"):
        text = text.replace("ret ptr @message", "ret ptr null")
        if mutation == "intern-only":
            def mutate(module):
                module.functions[0].indexed_kernel.call_texts.append("@message")
                return module
    elif mutation == "prefix":
        text = text.replace("ret ptr @message", "ret ptr @message-other")
    elif mutation == "array-length":
        text = text.replace("[24 x i8]", "[25 x i8]")
    elif mutation == "duplicate-definition":
        def mutate(module):
            from dataclasses import replace
            return replace(module, globals_=module.globals_ + (module.globals_[1],))
    elif mutation == "ambiguous-local":
        text = text + text.splitlines()[1].replace("@message", "@second") + "\n"
        text = text.replace("ret ptr @message", "call void @use(ptr @second)\n  ret ptr @message")
    if mutation == "foreign":
        modules = [_encode(tmp_path, text.split("define", 1)[0]),
                   _encode(tmp_path, "define" + text.split("define", 1)[1])]
    else:
        modules = [_encode(tmp_path, text, mutate)]
    if mutation == "ambiguous-module":
        modules *= 2
    elif mutation == "mixed":
        modules.append(text)
    with pytest.raises(AssertionError, match="MISSING|AMBIGUOUS|INVALID|MALFORMED"):
        _select(modules)


@pytest.mark.parametrize("mutation", (
    "missing-invocation", "missing-claim", "missing-input", "missing-output", "missing-link",
    "mixed", "poison", "partial", "unknown-schema", "unknown-codec", "unknown-output-kind",
    "duplicate-json", "stale-token", "stale-attempt", "index", "boolean-index", "module",
    "source-hash", "input-hash", "output-hash", "link-input", "link-output", "link-order",
    "binary-hash", "binary-size", "binary-absent", "target", "passes", "input-eof",
))
def test_indexed_receipts_fail_closed_on_partial_or_mismatched_evidence(tmp_path, mutation):
    args = _inputs(tmp_path)
    dump, record, source, binary, archive = args
    if mutation.startswith("missing-"):
        name = {"invocation": "invocation.json", "claim": "module_0.claim.json",
                "input": "self_backend_input_0.pidx", "output": "self_backend_output_0.pco",
                "link": "link.json"}[mutation.removeprefix("missing-")]
        (dump / name).unlink()
    elif mutation in ("mixed", "poison", "partial"):
        name = {"mixed": "self_backend_input_0.ll", "poison": "indexed-capture-failed",
                "partial": "self_backend_input_0.pidx.partial"}[mutation]
        (dump / name).write_bytes(b"unexpected")
    elif mutation == "binary-absent":
        binary.unlink()
    elif mutation == "stale-token":
        record["indexed_input_invocation"] = "f" * 32
    else:
        name = "self_backend_input_0.json"
        if mutation in ("source-hash", "index", "boolean-index", "module"):
            name = "invocation.json"
        elif mutation.startswith("link-") or mutation.startswith("binary-"):
            name = "link.json"
        value = json.loads((dump / name).read_text())
        if mutation == "unknown-schema":
            value["schema"] += "-unknown"
        elif mutation == "unknown-codec":
            value["representation"] += "-unknown"
        elif mutation == "unknown-output-kind":
            value["output"]["kind"] = "LLVM"
        elif mutation == "stale-attempt":
            value["attempt"] = "e" * 32
        elif mutation == "index":
            value["modules"][0]["index"] = 1
        elif mutation == "boolean-index":
            value["modules"][0]["index"] = False
        elif mutation == "module":
            value["modules"][0]["module"] = "foreign"
        elif mutation == "source-hash":
            value["modules"][0]["source_sha256"] = "f" * 64
        elif mutation == "input-hash":
            value["input"]["sha256"] = "f" * 64
        elif mutation == "output-hash":
            value["output"]["sha256"] = "f" * 64
        elif mutation in ("link-input", "link-output"):
            value["modules"][0][mutation.split("-")[1] + "_sha256"] = "f" * 64
        elif mutation == "link-order":
            value["modules"] *= 2
        elif mutation == "binary-hash":
            value["binary_sha256"] = "f" * 64
        elif mutation == "binary-size":
            value["binary_bytes"] += 1
        elif mutation == "target":
            value["target"] = "aarch64-unknown-linux-gnu"
        elif mutation == "passes":
            value["passes"] = "off"
        elif mutation == "input-eof":
            path = dump / "self_backend_input_0.pidx"
            path.write_bytes(path.read_bytes() + b"trailing")
            value["input"].update(bytes=path.stat().st_size, sha256=gate.sha(path))
            link = json.loads((dump / "link.json").read_text())
            link["modules"][0]["input_sha256"] = gate.sha(path)
            _write_json(dump / "link.json", link)
        _write_json(dump / name, value)
        if mutation == "duplicate-json":
            (dump / name).write_text((dump / name).read_text().replace("{", '{"schema":"ignored",', 1))
    with pytest.raises((AssertionError, KeyError, ValueError)):
        _record(args)
    evidence = json.loads((tmp_path / "executable-ir-dump.json").read_text())
    assert evidence["modules"] == [] and "retention" not in evidence
    assert evidence["status"] != "INDEXED_INPUTS_RETAINED"


@pytest.mark.parametrize("kind", ("input", "output", "json", "input-total", "output-total"))
def test_indexed_limits_fail_before_any_reader_or_decoder(tmp_path, monkeypatch, kind):
    args = _inputs(tmp_path)
    dump = args[0]
    name = {"input": "self_backend_input_0.pidx", "input-total": "self_backend_input_0.pidx",
            "output": "self_backend_output_0.pco", "output-total": "self_backend_output_0.pco",
            "json": "invocation.json"}[kind]
    with (dump / name).open("wb") as stream:
        stream.truncate(indexed.LIMIT + 1 if "total" not in kind else indexed.LIMIT)
    if "total" in kind:
        second = name.replace("_0.", "_1.")
        (dump / second).write_bytes(b"x")

    def forbidden(*args, **kwargs):
        pytest.fail("oversize input reached a reader or decoder")

    monkeypatch.setattr(support, "_read_bounded_dump", forbidden)
    monkeypatch.setattr(indexed, "decoded_module", forbidden)
    with pytest.raises(AssertionError, match="OVERSIZE"):
        _record(args)


@pytest.mark.parametrize("grow", (False, True))
@pytest.mark.parametrize("name", ("self_backend_input_0.pidx", "self_backend_output_0.pco", "invocation.json"))
def test_indexed_exact_inventory_eof_precedes_decode(tmp_path, monkeypatch, grow, name):
    args = _inputs(tmp_path)
    original = support._read_bounded_dump

    def changing(path, size):
        if path.name == name:
            raw = path.read_bytes()
            path.write_bytes(raw + b"x" if grow else raw[:-1])
        return original(path, size)

    monkeypatch.setattr(support, "_read_bounded_dump", changing)
    with pytest.raises(AssertionError, match="IDENTITY_CHANGED"):
        _record(args)


@pytest.mark.parametrize("selection", (None, (), (True,), (1,), (0, 1)))
def test_indexed_selection_requires_exactly_one_valid_index(tmp_path, selection):
    args = _inputs(tmp_path)
    with pytest.raises(AssertionError, match="MALFORMED"):
        support._record_observed_ir_dump(args[0], lambda modules: selection, *args[1:], completed=True)


@pytest.mark.parametrize("identity", ("source", "runtime"))
def test_indexed_source_and_runtime_drift_are_never_evidence(tmp_path, identity):
    args = _inputs(tmp_path)
    args[2 if identity == "source" else 4].write_bytes(b"changed")
    with pytest.raises(AssertionError, match="IDENTITY_CHANGED"):
        _record(args)


@pytest.mark.parametrize("mutation", ("payload", "retention-removed", "selected-index", "helper-pass", "raw-output", "runtime-target"))
def test_outer_gate_recomputes_raw_evidence_instead_of_trusting_helper_pass(tmp_path, mutation):
    args = _inputs(tmp_path)
    evidence = _record(args)
    path = tmp_path / "executable-ir-dump.json"
    target = TARGET
    if mutation == "payload":
        evidence["retention"]["files"][0]["base64"] = base64.b64encode(b"other").decode()
    elif mutation == "retention-removed":
        evidence.pop("retention")
    elif mutation == "selected-index":
        evidence["selected_module_indices"] = [1]
    elif mutation == "helper-pass":
        evidence["status"] = args[1]["executable_ir_dump_evidence"]["status"] = "PASS"
    elif mutation == "raw-output":
        (args[0] / "self_backend_output_0.pco").write_bytes(b"changed output")
    else:
        target = "aarch64-unknown-linux-gnu"
    _write_json(path, evidence)
    args[1]["executable_ir_dump_evidence"]["sha256"] = gate.sha(path)
    with pytest.raises((AssertionError, KeyError)):
        gate.verify_indexed_ir(tmp_path, args[1], "object-identity", target)


@pytest.mark.parametrize("failure", (None, "before", "after", "diagnostic-write", "native"))
def test_indexed_capture_keeps_one_compile_and_original_failure(tmp_path, monkeypatch, failure):
    archive = tmp_path / "runtime.a"
    archive.write_bytes(b"runtime fixture")
    raw = _encode(tmp_path)
    calls, executions = [], []
    original = ValueError("original compiler error")

    def compiler(source, binary, **options):
        calls.append(options)
        dump = Path(os.environ["PCC_DEBUG_SELF_IR_DUMP_DIR"])
        token = os.environ["PCC_DEBUG_SELF_IR_INVOCATION"]
        assert len(token) == 32 and not list(dump.iterdir())
        assert options == {"backend": "self", "ir_scaffold_mode": "on", "runtime_archive": str(archive)}
        if failure == "before":
            raise original
        Path(binary).write_bytes(_elf_image_fixture())
        _publish(dump, [raw], Path(source), Path(binary), token)
        if failure in ("after", "diagnostic-write"):
            raise original

    def execute(command, *, env, timeout):
        backend = env.get("PCC_GC_BACKEND")
        executions.append(backend)
        assert timeout == 10
        return subprocess.CompletedProcess(command, 1 if failure == "native" and backend == "1" else 0,
                                           stdout="ok\n", stderr="")

    monkeypatch.setattr(support, "run_process_group_timeout", execute)
    if failure == "diagnostic-write":
        write = Path.write_bytes
        def fail_write(path, payload):
            if path.name == "executable-ir-dump.json":
                raise OSError("diagnostic write failed")
            return write(path, payload)
        monkeypatch.setattr(Path, "write_bytes", fail_write)
    before = os.environ.get("PCC_DEBUG_SELF_IR_INVOCATION")
    capfd = SimpleNamespace(readouterr=lambda: SimpleNamespace(out="", err=""))
    args = ("original source\n", "ok\n", tmp_path, compiler, "pcc0", archive, capfd)
    if failure in ("before", "after", "diagnostic-write"):
        with pytest.raises(ValueError) as caught:
            support.assert_owned_program(*args, emitted_ir_check=_select, retain_dump_evidence=True)
        assert caught.value is original
    elif failure == "native":
        with pytest.raises(AssertionError):
            support.assert_owned_program(*args, emitted_ir_check=_select, retain_dump_evidence=True)
    else:
        support.assert_owned_program(*args, emitted_ir_check=_select, retain_dump_evidence=True)
    assert len(calls) == 1 and os.environ.get("PCC_DEBUG_SELF_IR_INVOCATION") == before
    record = json.loads((tmp_path / "ownership-regression.json").read_text())
    assert executions == ([None] if failure in ("before", "after", "diagnostic-write")
                          else [None, "0", "1"] if failure == "native"
                          else [None, "0", "1", "2", "3", "4"])
    if failure in ("before", "after", "diagnostic-write"):
        assert record["error"] == "ValueError: original compiler error"
    else:
        assert record["executable_ir_dump_evidence"]["status"] == "INDEXED_INPUTS_RETAINED"


def test_existing_two_root_callback_dispatches_bytes_without_changing_text_contract(monkeypatch):
    from tests.python import two_root_indexed_lifecycle_support as typed
    from tests.python import two_root_lifecycle_support as legacy

    calls = []
    monkeypatch.setattr(typed, "assert_indexed_compiler_two_root_error_edge", calls.append)
    raw = [b"typed dispatch fixture; not decoded by this isolated test"]
    assert legacy.assert_compiler_two_root_error_edge(raw) is None
    assert calls == [raw]
    with pytest.raises(AssertionError, match="mixed"):
        legacy.assert_compiler_two_root_error_edge([raw[0], "text"])


def test_indexed_multi_module_global_indices_and_selection_are_not_worker_local(tmp_path):
    args = _inputs(tmp_path)
    for path in args[0].iterdir():
        path.unlink()
    unrelated = _encode(tmp_path, _program_ir("unrelated"))
    selected = _encode(tmp_path)
    _publish(args[0], [unrelated, selected], args[2], args[3])
    evidence = _record(args)
    assert evidence["selected_module_indices"] == [1]
    assert len(evidence["retention"]["files"]) == 10
    assert gate.verify_indexed_ir(tmp_path, args[1], "object-identity", TARGET) == evidence


@pytest.mark.parametrize("which", ("raw-growth", "duplicate-module", "claim-collision"))
def test_indexed_multi_module_partial_and_duplicate_controls(tmp_path, which):
    args = _inputs(tmp_path)
    for path in args[0].iterdir():
        path.unlink()
    raw = _encode(tmp_path)
    second = raw if which == "duplicate-module" else _encode(tmp_path, _program_ir("other"))
    _publish(args[0], [second, raw], args[2], args[3])
    if which == "raw-growth":
        with (args[0] / "self_backend_input_0.pidx").open("ab") as stream:
            stream.write(b"tail")
    elif which == "claim-collision":
        path = args[0] / "module_1.claim.json"
        value = json.loads(path.read_text())
        value["attempt"] = json.loads((args[0] / "module_0.claim.json").read_text())["attempt"]
        _write_json(path, value)
    with pytest.raises(AssertionError):
        _record(args)


def test_indexed_json_base64_expansion_is_bounded_before_payload_reads(tmp_path, monkeypatch):
    args = _inputs(tmp_path)
    with (args[0] / "self_backend_output_0.pco").open("wb") as stream:
        stream.truncate(indexed.LIMIT * 3 // 4)

    def forbidden(*args, **kwargs):
        pytest.fail("oversize encoded envelope reached a payload reader")

    monkeypatch.setattr(support, "_read_bounded_dump", forbidden)
    with pytest.raises(AssertionError, match="OVERSIZE: retained indexed JSON"):
        _record(args)


def test_indexed_final_json_metadata_budget_precedes_payload_reads_and_base64(tmp_path, monkeypatch):
    args = _inputs(tmp_path)
    snapshot = indexed.read_indexed_inputs(args[0], args[1], args[3], support._read_bounded_dump)
    envelope_bytes = len(json.dumps(snapshot["retention"]).encode())
    monkeypatch.setattr(support, "_IR_JSON_LIMIT", envelope_bytes + 50)

    def forbidden(*args, **kwargs):
        pytest.fail("oversize final JSON reached a payload reader, base64 encoder or decoder")

    monkeypatch.setattr(support, "_read_bounded_dump", forbidden)
    monkeypatch.setattr(indexed.base64, "b64encode", forbidden)
    monkeypatch.setattr(indexed, "validate_indexed_modules", forbidden)
    with pytest.raises(AssertionError, match="OVERSIZE: JSON bytes"):
        _record(args)
    evidence = json.loads((tmp_path / "executable-ir-dump.json").read_text())
    assert evidence["status"] == "OVERSIZE" and "retention" not in evidence


def test_indexed_poison_cannot_be_hidden_by_forged_pass_receipts(tmp_path):
    args = _inputs(tmp_path)
    _record(args)
    args[1]["status"] = "PASS"
    (args[0] / "indexed-capture-failed").write_bytes(b"conflicting publisher")
    with pytest.raises(AssertionError, match="MALFORMED"):
        gate.verify_indexed_ir(tmp_path, args[1], "object-identity", TARGET)


@pytest.mark.parametrize("inline", (False, True))
def test_two_root_outer_gate_repeats_real_typed_proof(tmp_path, monkeypatch, inline):
    from tests.python.test_two_root_indexed_lifecycle_support import _module, _raw
    from tests.python.two_root_lifecycle_support import assert_compiler_two_root_error_edge

    args = _inputs(tmp_path)
    for path in args[0].iterdir():
        path.unlink()
    marker = _encode(tmp_path, _program_ir("TWO_ROOT_CLEANUP_EXCEPTION_OK"))
    roots = _raw(tmp_path, _module(monkeypatch, inline=inline))
    _publish(args[0], [marker, roots], args[2], args[3])
    support._record_observed_ir_dump(args[0], assert_compiler_two_root_error_edge,
                                     *args[1:], completed=True, selected_required=False)
    args[1]["binary_sha256"] = gate.sha(args[3])
    evidence = gate.verify_indexed_ir(tmp_path, args[1], "two-root-cleanup", TARGET)
    assert evidence["status"] == "INDEXED_INPUTS_RETAINED"
    assert "selected_module_indices" not in evidence


def test_outer_two_root_rejects_corruption_even_when_all_hashes_are_rebound(tmp_path, monkeypatch):
    from tests.python.test_two_root_indexed_lifecycle_support import _module, _raw
    from tests.python.two_root_lifecycle_support import assert_compiler_two_root_error_edge

    args = _inputs(tmp_path)
    for path in args[0].iterdir():
        path.unlink()
    marker = _encode(tmp_path, _program_ir("TWO_ROOT_CLEANUP_EXCEPTION_OK"))
    roots = _raw(tmp_path, _module(monkeypatch))
    _publish(args[0], [marker, roots], args[2], args[3])
    support._record_observed_ir_dump(args[0], assert_compiler_two_root_error_edge,
                                     *args[1:], completed=True, selected_required=False)
    args[1]["binary_sha256"] = gate.sha(args[3])
    evidence_path = tmp_path / "executable-ir-dump.json"
    evidence = json.loads(evidence_path.read_text())
    # A helper's prior status, matching inventories and self-consistent hashes
    # cannot certify the changed root protocol. The outer predicate must run.
    wrong = _raw(tmp_path, _module(monkeypatch, fault="map_count"), "wrong")
    _publish(args[0], [marker, wrong], args[2], args[3])
    captured = indexed.read_indexed_inputs(args[0], args[1], args[3], support._read_bounded_dump)
    evidence["retention"] = captured["retention"]
    evidence["observed_dump_sha256"] = captured["inventory"]
    args[1]["observed_ir_dump_sha256"] = args[1]["executable_input_ir"] = captured["inventory"]
    _write_json(evidence_path, evidence)
    args[1]["executable_ir_dump_evidence"]["sha256"] = gate.sha(evidence_path)
    with pytest.raises((AssertionError, ValueError)):
        gate.verify_indexed_ir(tmp_path, args[1], "two-root-cleanup", TARGET)


@pytest.mark.parametrize("count,size", ((32, 1), (2, indexed.LIMIT // 2 + 1)))
def test_typed_marker_direct_calls_bound_count_and_aggregate_before_decode(monkeypatch, count, size):
    def forbidden(*args, **kwargs):
        pytest.fail("oversize direct marker call reached decoder")
    monkeypatch.setattr(indexed, "decoded_module", forbidden)
    with pytest.raises(AssertionError, match="OVERSIZE"):
        indexed.select_indexed_program_ir([b"x" * size] * count, MARKER)


@pytest.mark.parametrize("module_index", (0, 1))
@pytest.mark.parametrize("passes", (["mem2reg"], ["off"], ["sroa", "mem2reg"]))
def test_passes_off_is_exact_for_every_module_even_with_rebound_hashes(tmp_path, module_index, passes):
    args = _inputs(tmp_path)
    for path in args[0].iterdir():
        path.unlink()
    unrelated = _encode(tmp_path, _program_ir("unrelated"))
    selected = _encode(tmp_path)
    _publish(args[0], [unrelated, selected], args[2], args[3])
    evidence = _record(args)
    changed_name = f"self_backend_input_{module_index}.json"
    changed = args[0] / changed_name
    emission = json.loads(changed.read_text())
    emission["passes"] = passes
    _write_json(changed, emission)
    raw = changed.read_bytes()
    digest = _sha(raw)
    # Rebind every affected raw hash, byte count, base64 payload and enclosing
    # evidence digest. A well-typed list is still not the gate's passes-off []
    # contract, independently of syntactic validity or internally matching data.
    evidence["observed_dump_sha256"][changed_name] = digest
    args[1]["observed_ir_dump_sha256"][changed_name] = digest
    args[1]["executable_input_ir"][changed_name] = digest
    row = next(row for row in evidence["retention"]["files"] if row["name"] == changed_name)
    row.update(bytes=len(raw), sha256=digest, base64=base64.b64encode(raw).decode("ascii"))
    evidence_path = tmp_path / "executable-ir-dump.json"
    _write_json(evidence_path, evidence)
    args[1]["executable_ir_dump_evidence"]["sha256"] = gate.sha(evidence_path)
    with pytest.raises(AssertionError, match="IDENTITY_CHANGED: indexed pass configuration"):
        gate.verify_indexed_ir(tmp_path, args[1], "object-identity", TARGET)
    with pytest.raises(AssertionError, match="IDENTITY_CHANGED: indexed pass configuration"):
        _record(args)


@pytest.mark.parametrize("label", ("two-root-cleanup", "two-root-tracing"))
@pytest.mark.parametrize("representation", ("garbage", "indexed-codec-v2"))
def test_two_root_unknown_representation_cannot_fall_through_to_legacy(tmp_path, monkeypatch, label, representation):
    from tests.python import two_root_lifecycle_support as legacy

    def forbidden(*args):
        pytest.fail("unknown representation reached legacy semantic verifier")

    monkeypatch.setattr(legacy, "assert_compiler_two_root_error_edge", forbidden)
    with pytest.raises(AssertionError, match="unknown executable input representation"):
        gate.verify_two_root_ir(tmp_path, {"executable_input_representation": representation}, label)


@pytest.mark.parametrize("label", ("two-root-cleanup", "two-root-tracing"))
@pytest.mark.parametrize("representation", (None, ""))
def test_two_root_legacy_dispatch_accepts_only_absent_or_empty_tag(tmp_path, monkeypatch, label, representation):
    from tests.python import two_root_lifecycle_support as legacy

    marker = ("TWO_ROOT_CLEANUP_EXCEPTION_OK" if label == "two-root-cleanup"
              else "TWO_ROOT_TRACING_CLEANUP_OK")
    dump = tmp_path / "executable-ir"
    dump.mkdir()
    path = dump / "self_backend_input_0.ll"
    text = _program_ir(marker)
    path.write_text(text)
    record = {"executable_input_ir": {path.name: gate.sha(path)}}
    if representation is not None:
        record["executable_input_representation"] = representation
    calls = []
    monkeypatch.setattr(legacy, "assert_compiler_two_root_error_edge", calls.append)
    gate.verify_two_root_ir(tmp_path, record, label)
    assert calls == [[text]]

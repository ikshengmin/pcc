"""Execute the receipt protocol through owned APIs, including bounded scans.

Synthetic object/final payloads exercise protocol binding only. Real emitter
identity and semantics remain the direct-worker and native ownership gates.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.python.owned_regression_support import explicit_owned_runtime
from tests.python.indexed_receipt_support import read_first_recorded_failure
from tests.python.process_timeout import run_process_group_timeout


PROBE = '''import os
import sys
from pcc.backend import indexed_input_receipts as receipts
from pcc.backend.self_backend_ir import GlobalDef, ParsedModule, TypeDesc
from pcc.backend.self_backend_indexed_codec import decode_indexed_module_file


def main():
    assert sys.implementation.name == "pcc"
    root = sys.argv[1]
    if sys.argv[2] == "inventory":
        refused = False
        try:
            receipts._entries(root)
        except ValueError as error:
            assert "inventory exceeds" in str(error)
            refused = True
        assert refused
        print("INDEXED_INVENTORY_BOUND_OK")
        return
    dump = os.path.join(root, "dump")
    source = os.path.join(root, "source.py")
    manifest = os.path.join(root, "worker.manifest")
    invocation = receipts.begin_invocation([source], ["probe"])
    assert invocation is not None
    receipts.attach_manifest(manifest, invocation, [0])
    context = receipts.worker_context(manifest, 0, "probe", source)
    assert context is not None
    # Verify exact EEXIST class, bounded retry and retention of the owner.
    lock = receipts._lock(dump)
    exists = False
    try:
        with open(lock, "xb"):
            pass
    except FileExistsError:
        exists = True
    assert exists
    blocked = False
    try:
        receipts._lock(dump)
    except ValueError as error:
        assert "diagnostic lock is busy" in str(error)
        blocked = True
    assert blocked and os.path.isfile(lock)
    receipts._unlock(lock)
    assert not os.path.exists(lock)
    target = "x86_64-pc-windows-msvc" if sys.platform == "win32" else "x86_64-unknown-linux-gnu"
    literal = GlobalDef("receipt.literal", TypeDesc("int", 32), "1", True, True)
    parsed = ParsedModule(target, (literal,), (), None)
    if sys.argv[2] == "budget":
        # A real conservative codec rejection after the exclusive claim. The
        # original 2 MiB bound is unchanged; there is no emitted object/link.
        with open(os.path.join(dump, "self_backend_input_1.pidx.partial"), "xb") as stream:
            assert stream.write(b"x" * (receipts.LIMIT - 16)) == receipts.LIMIT - 16
        assert receipts.begin_capture(parsed, context, []) is None
        marker = os.path.join(dump, "indexed-capture-failed")
        assert os.path.getsize(marker) <= 1024
        failure = receipts._read_json(marker)
        assert failure["stage"] == "codec-preflight"
        assert failure["reason"] == "conservative_limit"
        assert failure["index"] == 0 and failure["remaining_bytes"] == 16
        assert failure["observed_bytes"] == 4096
        assert not os.path.exists(os.path.join(dump, "self_backend_input_0.json"))
        assert receipts.prepare_link([("probe", "PCO", "missing-output")]) is None
        assert receipts._read_json(marker) == failure
        print("INDEXED_NATIVE_FAILURE_OK")
        return
    capture = receipts.begin_capture(parsed, context, [])
    assert capture is not None
    decoded = decode_indexed_module_file(os.path.join(dump, capture["input"]["name"]), max_bytes=receipts.LIMIT)
    assert decoded.triple == target and decoded.globals_[0].initializer == "1"
    output = os.path.join(root, "synthetic-object")
    with open(output, "xb") as stream:
        assert stream.write(b"synthetic emitted object") == 24
    receipts.finish_capture(capture, output, "PCO")
    artifacts = [("probe", "PCO", output)]
    link = receipts.prepare_link(artifacts)
    assert link is not None
    final = os.path.join(root, "synthetic-final")
    with open(final, "xb") as stream:
        assert stream.write(b"synthetic linked image") == 22
    receipts.finish_link(link, artifacts, final)
    completed = receipts._read_json(os.path.join(dump, "link.json"))
    assert completed["invocation"] == context["invocation"]
    assert len(receipts._entries(dump)) == 6
    # Exclusive metadata collisions must preserve the first bytes and poison.
    assert receipts.begin_capture(parsed, context, []) is None
    assert os.path.isfile(os.path.join(dump, "indexed-capture-failed"))
    marker = os.path.join(dump, "indexed-capture-failed")
    assert os.path.getsize(marker) <= 1024
    first = receipts._read_json(marker)
    assert first["stage"] == "claim" and first["index"] == -1
    assert first["exception_class"] == "value_error"
    assert first["reason"] == "destination_exists"
    assert first["invocation"] == context["invocation"]
    assert receipts.prepare_link(artifacts) is None
    assert receipts._read_json(marker) == first
    print("INDEXED_NATIVE_PROTOCOL_OK")

main()
'''


@pytest.mark.integration
@pytest.mark.parametrize("python_program_compiler", ("pcc0", "pcc1"), indirect=True)
def test_indexed_receipts_execute_owned_protocol_five_gc(
    tmp_path, python_program_compiler, explicit_owned_runtime,
):
    from scripts.owned_image_linkage import inspect_owned_image

    source = tmp_path / "native_protocol.py"
    source.write_text(PROBE)
    binary = tmp_path / "native_protocol.out"
    python_program_compiler(str(source), str(binary), backend="self", ir_scaffold_mode="on",
                            runtime_archive=str(explicit_owned_runtime))
    assert inspect_owned_image(binary.read_bytes())["libpython"] == "off"
    for backend in range(5):
        root = tmp_path / ("gc" + str(backend))
        root.mkdir()
        dump = root / "dump"
        dump.mkdir()
        (root / "source.py").write_text("value = 42\n")
        (root / "worker.manifest").write_text("native protocol assignment\n")
        environment = dict(os.environ, PCC_GC_BACKEND=str(backend), PATH="",
                           PCC_HOST_PYTHON="/nonexistent/host-python",
                           PCC_HOST_PCC="/nonexistent/host-pcc",
                           PCC_DEBUG_SELF_IR_DUMP_DIR=str(dump),
                           PCC_DEBUG_SELF_IR_INVOCATION=(str(backend) * 32))
        result = run_process_group_timeout([str(binary), str(root), "protocol"],
                                          env=environment, timeout=10)
        assert (result.returncode, result.stdout, result.stderr) == (0, "INDEXED_NATIVE_PROTOCOL_OK\n", "")
        receipt = json.loads((dump / "link.json").read_text())
        assert receipt["binary_bytes"] == len(b"synthetic linked image")
        record = {"indexed_input_invocation": str(backend) * 32,
                  "source_sha256": hashlib.sha256((root / "source.py").read_bytes()).hexdigest()}
        assert read_first_recorded_failure(dump, record)["stage"] == "claim"
        failure_root = root / "failure"
        failure_root.mkdir()
        failure_dump = failure_root / "dump"
        failure_dump.mkdir()
        (failure_root / "source.py").write_bytes((root / "source.py").read_bytes())
        (failure_root / "worker.manifest").write_text("native failed capture assignment\n")
        failure_environment = dict(environment, PCC_DEBUG_SELF_IR_DUMP_DIR=str(failure_dump))
        result = run_process_group_timeout([str(binary), str(failure_root), "budget"],
                                          env=failure_environment, timeout=10)
        assert (result.returncode, result.stdout, result.stderr) == (0, "INDEXED_NATIVE_FAILURE_OK\n", "")
        failure = read_first_recorded_failure(failure_dump, record)
        assert failure["stage"] == "codec-preflight" and failure["index"] == 0
        assert failure["remaining_bytes"] == 16 and failure["observed_bytes"] == 4096
        bounded = root / "oversize"
        bounded.mkdir()
        for index in range(129):
            (bounded / str(index)).write_bytes(b"")
        result = run_process_group_timeout([str(binary), str(bounded), "inventory"],
                                          env=environment, timeout=10)
        assert (result.returncode, result.stdout, result.stderr) == (0, "INDEXED_INVENTORY_BOUND_OK\n", "")

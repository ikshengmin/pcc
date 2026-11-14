"""Execute the receipt protocol through owned APIs, including bounded scans.

Synthetic object/final payloads exercise protocol binding only. Real emitter
identity and semantics remain the direct-worker and native ownership gates.
"""
import json
import os
from pathlib import Path

import pytest

from tests.python.owned_regression_support import explicit_owned_runtime
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
    assert receipts.prepare_link(artifacts) is None
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
        bounded = root / "oversize"
        bounded.mkdir()
        for index in range(129):
            (bounded / str(index)).write_bytes(b"")
        result = run_process_group_timeout([str(binary), str(bounded), "inventory"],
                                          env=environment, timeout=10)
        assert (result.returncode, result.stdout, result.stderr) == (0, "INDEXED_INVENTORY_BOUND_OK\n", "")

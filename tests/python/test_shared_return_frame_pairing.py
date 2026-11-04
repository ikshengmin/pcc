"""Shared-return frames select their ABI before the shared exit is emitted.

Specification: docs/compiler-contract.md, shared GC slot/root ownership.
The exit block is constructed before its incoming return arms. A local LIFO
enter must never join a legacy leave, including on a persistent carrier whose
normal unregister checks that every activation-private root has been removed.
"""
import os
from pathlib import Path
import re
import subprocess

import pytest


SOURCES = (
    '''def choose(k: int):
    return {"value": k}
''',
    '''def choose(k: int):
    if k == 0:
        return {"value": 1}
    if k == 1:
        return {"value": 2}
    return {"value": 3}
''',
    '''def choose(k: int):
    if k < 0:
        raise ValueError("negative")
    try:
        if k == 0:
            return {"value": 1}
    finally:
        unused = [k]
    return {"value": k}
''',
    '''def choose(k: int):
    if k < 0:
        return None
    if k == 0:
        return {"value": 1}
    later = [k, str(k)]
    if k == 1:
        raise ValueError("one")
    return {"value": later}
''',
)


def _body(source, mapped):
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.type_infer import infer_module

    module = infer_module(parse_and_lift(source, "pairing.py", "pairing"))
    codegen = L1CodeGen(module, ir_scaffold_mode="on")
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    codegen._precise_stack_frames_mapped = mapped
    text = str(codegen.generate(module))
    match = re.search(r"^define [^\n]*@user_pairing_choose\([^\n]*\).*?^}",
                      text, re.M | re.S)
    assert match
    return match.group(0)


def _assert_balanced_shared_paths(body, slot):
    """Every reachable return has left exactly the LIFO scope it entered."""
    split = re.split(r"^([\w.]+):\n", body, flags=re.M)
    blocks = dict(zip(split[1::2], split[2::2]))
    pending = [(split[1], False)]
    seen = set()
    while pending:
        label, active = pending.pop()
        if (label, active) in seen:
            continue
        seen.add((label, active))
        block = blocks[label]
        for operation in re.findall(
            r"@pcc_gc_frame_(enter|leave)_lifo[\w]*\([^\n]*ptr "
            + re.escape(slot) + r"\)", block,
        ):
            assert active == (operation == "leave"), (slot, label, operation, active)
            active = operation == "enter"
        if re.search(r"^\s*ret ", block, re.M):
            assert not active, (slot, label, "return with live shared frame")
        for target in re.findall(r"label %([\w.]+)", block):
            pending.append((target, active))


@pytest.mark.parametrize("mapped", (False, True))
@pytest.mark.parametrize("source", SOURCES, ids=(
    "object", "early-multiple", "exception-finally", "unrooted-late-root-error",
))
def test_shared_return_lifo_abi_is_paired(source, mapped):
    body = _body(source, mapped)
    slots = set(re.findall(r"(%[\w.]+) = alloca ptr", body))
    slots = {slot for slot in slots if slot.startswith("%ret.shared.")}
    assert slots, "regression must execute shared-return lowering"
    rooted = 0
    for slot in slots:
        enters = re.findall(
            r"@(pcc_gc_frame_enter_lifo[\w]*)\(ptr [^,]+, ptr "
            + re.escape(slot) + r"\)", body,
        )
        leaves = re.findall(
            r"@(pcc_gc_frame_leave_lifo[\w]*)\(ptr " + re.escape(slot) + r"\)", body,
        )
        if not enters:
            assert not leaves, (slot, "unrooted return has a LIFO leave", leaves)
            continue
        rooted += 1
        suffix = "_local_mapped" if mapped else "_local"
        assert set(enters) == {"pcc_gc_frame_enter_lifo" + suffix}
        assert leaves == ["pcc_gc_frame_leave_lifo" + suffix], (slot, enters, leaves)
        _assert_balanced_shared_paths(body, slot)
        assert not re.search(
            r"@pcc_gc_frame_enter(?:_local)?(?:_mapped)?\(ptr [^,]+, ptr "
            + re.escape(slot) + r"\)", body,
        ), "a shared return must not become an unconditional entry root"
        # Rooted returns must reload after all ordinary cleanup, then clear
        # and leave the same slot. Relocating collectors can update it.
        tails = re.findall(
            r"ret\.shared\.tail[^\n]*:\n(.*?^\s*ret [^\n]+)",
            body, re.M | re.S,
        )
        tails = [tail for tail in tails if "@" + leaves[0] + "(ptr " + slot + ")" in tail]
        assert len(tails) == 1, (slot, tails)
        tail = tails[0]
        reload = tail.index("@pcc_gc_load_ptr(ptr null, ptr " + slot + ")")
        clear = tail.index("@pcc_gc_store_root(ptr " + slot + ", ptr null)")
        leave = tail.index("@" + leaves[0] + "(ptr " + slot + ")")
        assert reload < clear < leave < tail.index("ret ptr")
        # Only the rooted predecessors enter this lifetime. Error and
        # unrooted exits must not acquire a matching leave just because the
        # shared tail was constructed early.
        blocks = re.split(r"^([\w.]+):\n", body, flags=re.M)
        for label, block in zip(blocks[1::2], blocks[2::2]):
            if label.startswith(("err.", "activation.failure")):
                assert not re.search(
                    r"@pcc_gc_frame_(?:enter|leave)[\w]*\([^\n]*ptr "
                    + re.escape(slot) + r"\)", block,
                ), (slot, label)
    assert rooted


def test_shared_object_return_unregisters_carrier_on_five_collectors(
    tmp_path: Path, threaded_pcc_runtime_archive,
):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "shared_return_carrier.py"
    source.write_text('''import pcc.virtual_thread as vt
from pcc.extern import c_int64, extern

idle_sleep_ns = extern("pcc_platform_sleep_ns", (c_int64,), c_int64)

def make_result(value: int):
    return {"answer": value}

def job():
    vt.sleep_current(0)
    result = make_result(7)
    if result["answer"] != 7:
        raise RuntimeError("wrong shared-return value")
    return 7

def main():
    if vt.carrier_pool_start(1) != 1:
        raise RuntimeError("carrier start failed")
    thread = vt.spawn(job)
    while vt.outcome(thread) == vt.OUTCOME_PENDING:
        if idle_sleep_ns(1000000) < 0:
            raise RuntimeError("wait failed")
    failure = vt.exception(thread)
    if failure is not None:
        raise failure
    if vt.outcome(thread) != vt.OUTCOME_RETURNED or vt.result(thread) != 7:
        raise RuntimeError("wrong task outcome")
    if vt.carrier_pool_stop() != 1:
        raise RuntimeError("carrier stop failed")
    print("shared-return-carrier-ok")

main()
''', encoding="utf-8")
    binary = tmp_path / "shared_return_carrier"
    compile_python(str(source), str(binary), backend="self", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run(
            [str(binary)], env=dict(os.environ, PCC_GC_BACKEND=str(backend)),
            capture_output=True, text=True, timeout=60,
        )
        assert ran.returncode == 0, f"GC{backend}: {ran.returncode}: {ran.stdout}{ran.stderr}"
        assert ran.stdout == "shared-return-carrier-ok\n", f"GC{backend}: {ran.stdout}"

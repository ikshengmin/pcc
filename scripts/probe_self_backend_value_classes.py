"""Classify every scalar SSA definition of a module by why it cannot live in a caller-saved register."""
import sys, collections
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from pcc.backend import self_backend_aarch64_darwin_regalloc as ra
from pcc.backend import self_backend_aarch64_darwin as be
from pcc.backend.self_backend_kernel import TYPE_KIND_INT, TYPE_KIND_PTR
from pcc.backend.self_backend_ir import PARSED_INSTRUCTION_KIND_CALL, PARSED_INSTRUCTION_KIND_ALLOCA

stats = collections.Counter()
orig = ra.allocate_aarch64_block_registers
def classify(func):
    k = ra.get_indexed_function_kernel(func)
    if func.is_vararg:
        return orig(func)
    block_base, last_use, barriers, block_safe = ra._function_level_facts(k)
    phi_inputs = set()
    for b in range(len(k.block_names)):
        pf = k.block_phi_fact(b)
        for pi in range(pf.second):
            phi = k.phi_record(pf.first + pi)
            stats["PHI value"] += 1
            for ii in range(phi.fourth):
                inc = k.phi_incoming(phi.third + ii)
                if inc.first >= 0: phi_inputs.add(inc.first)
    kind_at = {}
    for b in range(len(k.block_names)):
        bf = k.block_fact(b)
        for i in range(bf.second):
            kind_at[block_base[b] + i] = k.instruction_metadata_by_id(bf.first + i).first
    bset = set(barriers)
    for b in range(len(k.block_names)):
        bf = k.block_fact(b)
        for i in range(bf.second):
            iid = bf.first + i
            dest = k.instruction_fact_by_id(iid).first
            if dest < 0: continue
            kind = k.instruction_metadata_by_id(iid).first
            th = k.type_header(k.value_type_id(dest))
            if th.first == TYPE_KIND_PTR: ty = "ptr"
            elif th.first == TYPE_KIND_INT: ty = "i%d" % th.second
            else:
                stats["non-scalar def"] += 1; continue
            if kind == PARSED_INSTRUCTION_KIND_ALLOCA:
                stats["alloca"] += 1; continue
            stats["scalar def"] += 1
            start = block_base[b] + i
            end = last_use.get(dest)
            if dest in phi_inputs:
                stats["  PHI input"] += 1; continue
            if end is None:
                stats["  no non-PHI use"] += 1; continue
            crossing = [p for p in barriers if start < p < end]
            ends_at_call = kind_at.get(end) == PARSED_INSTRUCTION_KIND_CALL
            if kind == PARSED_INSTRUCTION_KIND_CALL:
                stats["  call result" + (" -> only into a call" if ends_at_call and not crossing else (" crossing a call" if crossing else " (no call until last use)"))] += 1
                continue
            if crossing:
                stats["  crosses a call, %s" % ("ptr" if ty == "ptr" else "int")] += 1; continue
            if ends_at_call:
                stats["  last use is a call operand (no call between)"] += 1; continue
            stats["  eligible (no call in interval)"] += 1
    return orig(func)
ra.allocate_aarch64_block_registers = classify
import pcc.backend.self_backend_aarch64_darwin_prologue as pro
pro.allocate_aarch64_block_registers = classify
for path in sys.argv[1:]:
    stats.clear()
    be.emit_aarch64_darwin_asm(open(path).read(), optimize=True)
    print(path.split("/")[-1])
    for kk, v in sorted(stats.items(), key=lambda kv: (-kv[1] if not kv[0].startswith("  ") else 0, kv[0])):
        print(f"   {kk:52} {v:6d}")

"""fused-protocol: drop calls the fused collector answers without work.

The fused collector is GC0 (reference counting with the cycle collector;
docs/status.md, "One collector"). It never moves an object and owes no
store barrier, so these calls, emitted for the relocating and tracing
collectors, return at once under it:

* pcc_gc_pin / pcc_gc_unpin: nothing reads the pin (pcc_gc_pins_inert);
* pcc_gc_foreign_lease_acquire / pcc_gc_borrowed_lease_acquire answer 0
  and pcc_gc_foreign_lease_release answers 0 for a 0 token;
* pcc_gc_note_slot_write_barrier / pcc_gc_note_write_barrier return.

pcc_gc_load_ptr and pcc_gc_resolve_owned_ptr stay: the backend's precise
stack-map analysis reads a value's root provenance from those calls.

Each is removed or replaced by its answer. No block is split, so the
control-flow graph and every phi are unchanged. A program compiled this way
runs only on the fused collector.
"""
from __future__ import annotations

import re

_VALUE = r"(?:null|%[\w.$-]+|@[\w.$-]+)"
_DROP = re.compile(
    r"^\s*call void \((?:ptr|ptr, ptr|ptr, ptr, ptr)\) "
    r"@(?:pcc_gc_pin|pcc_gc_unpin|pcc_gc_note_slot_write_barrier|pcc_gc_note_write_barrier)\("
    rf"ptr {_VALUE}(?:, ptr {_VALUE}){{0,2}}\)\s*$"
)
_ZERO = re.compile(
    r"^(\s*)(%[\w.$-]+) = call i64 \((?:ptr|ptr, i64)\) "
    r"@(?:pcc_gc_foreign_lease_acquire|pcc_gc_borrowed_lease_acquire|pcc_gc_foreign_lease_release)\("
    rf"ptr {_VALUE}(?:, i64 (?:-?\d+|%[\w.$-]+))?\)\s*$"
)
_LOAD = re.compile(
    rf"^(\s*)(%[\w.$-]+) = call ptr \(ptr, ptr\) @pcc_gc_load_ptr\(ptr {_VALUE}, ptr (%[\w.$-]+|@[\w.$-]+)\)\s*$"
)
_RESOLVE = re.compile(
    rf"^(\s*)(%[\w.$-]+) = call ptr \(ptr\) @pcc_gc_resolve_owned_ptr\(ptr (%[\w.$-]+|@[\w.$-]+)\)\s*$"
)


def fused_protocol_text(text: str) -> tuple[str, bool]:
    """Apply the pass to one module's IR text; returns (text, changed)."""
    if "@pcc_gc_" not in text:
        return text, False
    output = []
    changed = False
    inside = False
    for line in text.split("\n"):
        if not inside:
            inside = line.startswith("define ") and line.rstrip().endswith("{")
            output.append(line)
            continue
        if line == "}":
            inside = False
            output.append(line)
            continue
        if "@pcc_gc_" in line:
            if _DROP.match(line):
                changed = True
                continue
            match = _ZERO.match(line)
            if match:
                output.append(f"{match.group(1)}{match.group(2)} = add i64 0, 0")
                changed = True
                continue
        output.append(line)
    return ("\n".join(output), True) if changed else (text, False)

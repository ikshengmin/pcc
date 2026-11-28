"""An atomic access whose address folded to null is still emitted.

``load atomic i64, ptr null`` is valid IR (undefined only if executed):
constant folding reaches it in dead code once an address chain folds, as
instsimplify's zero-GEP fold exposed in py_func's inlined signature error
path. A plain load of null was accepted; the atomic forms were refused
with "malformed atomic load". The address is materialized like any other
constant pointer.
"""
from __future__ import annotations

import re

import pytest

from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


@pytest.mark.parametrize("instruction, mnemonic", [
    ("%v = load atomic i64, ptr null monotonic, align 8", r"ldr x\d+, \[x9\]"),
    ("%v = load atomic i64, ptr null acquire, align 8", r"ldapr x\d+, \[x9\]"),
    ("%v = load atomic i64, ptr null seq_cst, align 8", r"ldar x\d+, \[x9\]"),
    ("store atomic i64 7, ptr null release, align 8\n  %v = add i64 0, 1", r"stlr x\d+, \[x9\]"),
    ("%v = atomicrmw add ptr null, i64 1 monotonic, align 8", r"ldadd"),
    ("%pair = cmpxchg ptr null, i64 1, i64 2 acq_rel monotonic\n"
     "  %v = extractvalue { i64, i1 } %pair, 0", r"casal"),
])
@pytest.mark.parametrize("optimize", (False, True))
def test_atomics_on_a_null_address_are_emitted(instruction, mnemonic, optimize):
    text = ('target triple = "arm64-apple-darwin"\n'
            "define i64 @probe() {\nentry:\n  " + instruction + "\n  ret i64 %v\n}\n")
    assembly = emit_aarch64_darwin_asm(text, optimize=optimize)
    body = assembly.split("_probe:", 1)[1].split(".section", 1)[0]
    assert re.search(mnemonic, body), body

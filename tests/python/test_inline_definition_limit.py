"""inline-defined-wide raises only the definition size limit.

The runtime archive's helper chains (allocation, release, root stores) pass
through bodies larger than the general 32-instruction definition limit. The
runtime pass list uses inline-defined-wide (96); applications keep
inline-defined. The transformed module is emitted, linked and executed.
"""
from __future__ import annotations

import platform
import subprocess
import sys

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.frontends.python.compiled_owned_passes import (
    OWNED_PASS_NAMES,
    WIDE_DEFINITION_LIMIT,
    run_owned_passes,
)
from pcc.ir.optimization.inline import DEFINITION_LIMIT, inline_module


def _module(steps: int) -> str:
    body = ["define internal i64 @mix(i64 %x) {", "entry:"]
    previous = "%x"
    for index in range(steps):
        body.append(f"  %v{index} = {'add' if index % 2 else 'xor'} i64 {previous}, {index + 3}")
        previous = f"%v{index}"
    body.extend([f"  ret i64 {previous}", "}"])
    return "\n".join([
        'target triple = "arm64-apple-darwin"',
        *body,
        "define i32 @main() {",
        "entry:",
        "  %r = call i64 @mix(i64 5)",
        "  %t = trunc i64 %r to i32",
        "  %m = and i32 %t, 127",
        "  ret i32 %m",
        "}",
    ]) + "\n"


def _expected(steps: int) -> int:
    value = 5
    for index in range(steps):
        value = (value + index + 3) if index % 2 else (value ^ (index + 3))
    return value & 127


def _calls(text: str) -> int:
    main = text.split("define i32 @main()", 1)[1]
    return main.count("call i64 @mix(")


def test_wide_limit_inlines_bodies_the_general_limit_keeps():
    steps = 40
    assert DEFINITION_LIMIT < steps + 1 <= WIDE_DEFINITION_LIMIT
    module = _module(steps)
    general, _ = inline_module(module, include_definitions=True)
    wide, changed = inline_module(module, include_definitions=True,
                                  definition_limit=WIDE_DEFINITION_LIMIT)
    assert _calls(general) == 1
    assert changed and _calls(wide) == 0
    assert "inline-defined-wide" in OWNED_PASS_NAMES
    assert _calls(run_owned_passes(module, ["inline-defined"])) == 1
    assert _calls(run_owned_passes(module, ["inline-defined-wide"])) == 0
    too_large = _module(WIDE_DEFINITION_LIMIT + 4)
    assert _calls(run_owned_passes(too_large, ["inline-defined-wide"])) == 1


@pytest.mark.skipif(sys.platform != "darwin" or platform.machine() != "arm64",
                    reason="native Darwin arm64 execution")
def test_wide_inlined_module_runs(tmp_path):
    steps = 40
    text = run_owned_passes(_module(steps), ["inline-defined-wide"])
    executable = tmp_path / "wide"
    executable.write_bytes(link_executable([emit_owned_object(text, "arm64-apple-darwin")],
                                           entry="_main"))
    executable.chmod(0o755)
    ran = subprocess.run([str(executable)], capture_output=True, timeout=30)
    assert ran.returncode == _expected(steps)

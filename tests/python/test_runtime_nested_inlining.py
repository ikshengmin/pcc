"""The runtime pass list inlines helpers reached through inlined helpers.

The definition inliner clones each callee's body as it was before the pass,
so a call inside an inlined helper is copied into the caller and stays a
call; LLVM's inliner reaches it by working bottom-up over the call graph.
The runtime archive's helper chains (TLS reads, allocator magazines, root
stores) are such nests, so the runtime pass list runs the inliner twice:
the second pass sees helper bodies whose own small callees are inlined.
The transformed module is emitted, linked and executed.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.frontends.python import owned_runtime_build as owned
from pcc.ir.optimization.driver import optimize_ir


ROOT = Path(__file__).resolve().parents[2]
HELPERS = ("leaf", "middle", "outer")

# main -> outer -> middle -> leaf, every helper a small external definition,
# as runtime helpers are.
MODULE = "\n".join([
    'target triple = "arm64-apple-darwin"',
    "define external i64 @leaf(i64 %x) {",
    "entry:",
    "  %a = xor i64 %x, 9",
    "  ret i64 %a",
    "}",
    "define external i64 @middle(i64 %x) {",
    "entry:",
    "  %a = add i64 %x, 4",
    "  %b = call i64 @leaf(i64 %a)",
    "  ret i64 %b",
    "}",
    "define external i64 @outer(i64 %x) {",
    "entry:",
    "  %a = shl i64 %x, 1",
    "  %b = call i64 @middle(i64 %a)",
    "  ret i64 %b",
    "}",
    "define i32 @main() {",
    "entry:",
    "  %r = call i64 @outer(i64 5)",
    "  %t = trunc i64 %r to i32",
    "  %m = and i32 %t, 127",
    "  ret i32 %m",
    "}",
]) + "\n"
EXPECTED = (((5 << 1) + 4) ^ 9) & 127


@pytest.fixture(autouse=True)
def configured_runtime_passes(monkeypatch):
    monkeypatch.delenv("PCC_RUNTIME_IR_PASSES", raising=False)


def _main_calls(text: str) -> list[str]:
    main = text.split("define i32 @main()", 1)[1].split("\n}", 1)[0]
    return [name for name in HELPERS if f"@{name}(" in main]


def _runtime_passes() -> str:
    return owned.runtime_ir_passes(str(ROOT / "pcc/runtime"))


def test_one_inliner_pass_leaves_the_nested_helper_a_call():
    once = optimize_ir(MODULE, "inline-defined-wide")
    assert _main_calls(once) == ["middle"]


def test_the_runtime_pass_list_inlines_the_whole_nest():
    passes = _runtime_passes()
    assert passes.split(",").count("inline-defined-wide") == 2
    assert _main_calls(optimize_ir(MODULE, passes)) == []


# native Darwin arm64 execution
@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
def test_the_inlined_nest_runs(tmp_path):
    text = optimize_ir(MODULE, _runtime_passes())
    executable = tmp_path / "nest"
    executable.write_bytes(link_executable([emit_owned_object(text, "arm64-apple-darwin")],
                                           entry="_main"))
    executable.chmod(0o755)
    ran = subprocess.run([str(executable)], capture_output=True, timeout=30)
    assert ran.returncode == EXPECTED

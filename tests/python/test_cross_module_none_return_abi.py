"""Cross-module declarations must match definitions for every symbol.

An explicit ``-> None`` method encodes as a NoneType descriptor in the
class export schema; the extern-class declaration path lowered it to a
PyObject*-returning declaration while the defining module emitted
``define void``. The linker cannot see the mismatch, so callers rooted
leftover x0 as an owned object — latent under the pcc-Python runtime
(leftover happened to be a heap object) and fatal when x0 held a stack
frame-node address left by pcc_gc_frame_leave (see
docs/investigations/libpy-runtime-pcc-archive-pure-c-chain-crashes.md).

The regression is generic: compile a two-module program and assert that
every ``declare``d symbol's return type matches its ``define``.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

HELPER = """
class Helper:
    def __init__(self) -> None:
        self.n = 0

    def reset(self) -> None:
        xs = [1, 2, 3]
        xs.append(self.n)

    async def drain(self) -> None:
        return None
"""

MAIN = """
from helper_mod import Helper


def run() -> int:
    h = Helper()
    h.reset()
    return 0


run()
"""

def _return_types(ir_text):
    # Reuse the owned header parser so explicit linkage, visibility and
    # return attributes cannot make the regression silently skip a symbol.
    from pcc.backend.self_backend_parse import (
        _iter_function_defs, _parse_function_header, decode_global_name,
    )

    declares, defines, bodies = {}, {}, {}
    for line in ir_text.splitlines():
        if not line.startswith("declare "):
            continue
        # Declarations share the function-header syntax, minus the body.
        _, ret, sym, _ = _parse_function_header("define " + line[len("declare "):] + " {")
        declares.setdefault(decode_global_name(sym), set()).add(ret)
    for header, body in _iter_function_defs(ir_text):
        _, ret, sym, _ = _parse_function_header(header)
        sym = decode_global_name(sym)
        defines.setdefault(sym, set()).add(ret)
        bodies.setdefault(sym, []).append(body)
    return declares, defines, bodies


def _return_mismatches(declares, defines):
    # Combined IR contains one declaration per importing module. A later
    # matching declaration must not overwrite an earlier incompatible one.
    return {
        sym: (declares[sym], defines[sym])
        for sym in declares.keys() & defines.keys()
        if len(defines[sym]) != 1 or declares[sym] != defines[sym]
    }


def _direct_call_return_types(bodies, target):
    pattern = re.compile(r"\bcall\s+(\S+)\s+(?:\([^)]*\)\s+)?@" + re.escape(target) + r"\(")
    result = set()
    for body in bodies:
        for line in body.splitlines():
            if "call " not in line or "@" + target + "(" not in line:
                continue
            match = pattern.search(line)
            assert match is not None, f"unparsed direct call: {line}"
            result.add(match.group(1))
    return result


def test_return_type_check_preserves_explicit_linkage_and_duplicate_declarations():
    ir_text = """declare ptr @helper(ptr)
declare external void @helper(ptr)
declare dso_local ptr @drain(ptr)
define external void @helper(ptr %self) {
entry:
  ret void
}
define internal ptr @drain(ptr %self) {
entry:
  ret ptr %self
}
"""
    declares, defines, _ = _return_types(ir_text)
    assert declares["helper"] == {"void", "ptr"}
    assert defines["helper"] == {"void"}
    assert declares["drain"] == defines["drain"] == {"ptr"}
    assert _return_mismatches(declares, defines) == {"helper": ({"void", "ptr"}, {"void"})}


def _repo_root() -> Path:
    # tests/conftest.py monkeypatches Path.resolve for legacy paths, so
    # fixed parents[N] indexing is unreliable here; walk up to AGENTS.md.
    cur = Path(__file__).resolve().parent
    while cur != cur.parent:
        if (cur / "AGENTS.md").exists():
            return cur
        cur = cur.parent
    raise RuntimeError("AGENTS.md not found above " + __file__)


def test_cross_module_declarations_match_definitions(tmp_path):
    (tmp_path / "helper_mod.py").write_text(HELPER, encoding="utf-8")
    (tmp_path / "main.py").write_text(MAIN, encoding="utf-8")
    out = tmp_path / "toy_ir"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            sys.executable, "scripts/pcc_multi.py",
            "--entry", "main", "--emit-llvm",
            "--out", str(out),
            f"{tmp_path / 'main.py'}=main",
            f"{tmp_path / 'helper_mod.py'}=helper_mod",
        ],
        text=True, capture_output=True, timeout=280, env=env,
        cwd=str(_repo_root()),
    )
    assert build.returncode == 0, build.stderr[-2000:]

    declares, defines, bodies = _return_types(out.read_text(encoding="utf-8"))
    assert defines, "combined IR listed no definitions"
    mismatched = _return_mismatches(declares, defines)
    assert not mismatched, f"declare/define return types diverge: {mismatched}"

    reset = "user_helper_mod_Helper_reset"
    drain = "user_helper_mod_Helper_drain"
    assert declares.get(reset) == defines.get(reset) == {"void"}
    # An async None annotation returns the coroutine handle, not void.
    assert declares.get(drain) == defines.get(drain) == {"ptr"}
    assert _direct_call_return_types(bodies["user_main_run"], reset) == {"void"}
    assert _direct_call_return_types(
        [body for definitions in bodies.values() for body in definitions], reset
    ) == {"void"}

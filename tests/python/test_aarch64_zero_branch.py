"""Zero-only terminal comparisons use CBZ/CBNZ without losing value uses."""
import os
import platform
import re
import subprocess

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm
from tests.python.test_darwin_tls_leaf_protection import emit


def program(kind="i64", predicate="eq", swapped=False, reverse_layout=False, escaped=False):
    zero = "null" if kind == "ptr" else "0"
    nonzero = "@cell" if kind == "ptr" else ("4294967296" if kind == "i64" else "32768" if kind == "i16" else "2147483648")
    args = zero + ", %value" if swapped else "%value, " + zero
    blocks = ["yes:\n  br label %join\n", "no:\n  br label %join\n"]
    if reverse_layout:
        blocks.reverse()
    extra = "  %flag = zext i1 %condition to i64\n" if escaped else ""
    finish = "  %answer = add i64 %result, %flag\n  ret i64 %answer" if escaped else "  ret i64 %result"
    zero_true = predicate == "eq"
    expected0 = (11 if zero_true else 22) + (int(zero_true) if escaped else 0)
    expected1 = (22 if zero_true else 11) + (int(not zero_true) if escaped else 0)
    return ('target triple = "arm64-apple-darwin"\n@cell = global i64 7\n'
            'define i64 @choose(' + kind + ' %value, i64 %left, i64 %right) {\nentry:\n'
            '  %condition = icmp ' + predicate + ' ' + kind + ' ' + args + '\n'
            + extra + '  br i1 %condition, label %yes, label %no\n'
            + ''.join(blocks) + 'join:\n  %result = phi i64 [%left, %yes], [%right, %no]\n'
            + finish + '\n}\ndefine i32 @main() {\nentry:\n'
            '  %a = call i64 @choose(' + kind + ' ' + zero + ', i64 11, i64 22)\n'
            '  %b = call i64 @choose(' + kind + ' ' + nonzero + ', i64 11, i64 22)\n'
            f'  %ok0 = icmp eq i64 %a, {expected0}\n  %ok1 = icmp eq i64 %b, {expected1}\n'
            '  %ok = and i1 %ok0, %ok1\n  %status = select i1 %ok, i32 0, i32 73\n'
            '  ret i32 %status\n}\n')


def body(text):
    return re.search(r"_choose:\n(.*?)(?=\n\.p2align 2|\n\.section|\Z)", text, re.S).group(1)


@pytest.mark.parametrize("kind", ("i32", "i64", "ptr"))
@pytest.mark.parametrize("predicate", ("eq", "ne"))
@pytest.mark.parametrize("swapped", (False, True))
def test_terminal_zero_comparison_has_no_separate_cmp(kind, predicate, swapped, monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    emitted = body(emit_aarch64_darwin_asm(program(kind, predicate, swapped), optimize=True))
    assert re.search(r"\bcbn?z [wx]\d+,", emitted), emitted
    assert not re.search(r"\bcmp\b|\bcset\b", emitted), emitted


@pytest.mark.parametrize("kind,escaped", (("i16", False), ("i64", True)))
def test_narrow_and_escaping_comparisons_keep_the_value_path(kind, escaped):
    emitted = body(emit_aarch64_darwin_asm(program(kind=kind, escaped=escaped), optimize=True))
    assert "cmp " in emitted
    if escaped:
        assert "cset " in emitted


@pytest.mark.skipif(platform.system() != "Darwin" or platform.machine() != "arm64",
                    reason="native Darwin arm64 execution")
@pytest.mark.parametrize("registers", ("0", "1"))
@pytest.mark.parametrize("indexed", (False, True))
@pytest.mark.parametrize("kind,predicate,swapped,layout,escaped", (
    ("i32", "eq", False, False, False),
    ("i64", "ne", True, True, False),
    ("ptr", "eq", True, False, False),
    ("ptr", "ne", False, True, False),
    ("i16", "eq", False, True, False),
    ("i64", "eq", False, False, True),
))
def test_zero_branch_phi_edges_execute(tmp_path, monkeypatch, registers, indexed,
                                       kind, predicate, swapped, layout, escaped):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    data = emit(program(kind, predicate, swapped, layout, escaped), indexed, tmp_path)
    output = tmp_path / "branch"
    output.write_bytes(link_executable([data]))
    output.chmod(0o755)
    run = subprocess.run([str(output)], capture_output=True, text=True, timeout=10,
                         env=dict(os.environ, PATH="/nonexistent"))
    assert (run.returncode, run.stdout, run.stderr) == (0, "", "")


@pytest.mark.parametrize("kind", ("i32", "i64", "ptr"))
def test_zero_branch_emits_owned_aarch64_elf(kind):
    target = "aarch64-unknown-linux-gnu"
    text = program(kind).replace("arm64-apple-darwin", target)
    assert emit_owned_object(text, target).startswith(b"\x7fELF")

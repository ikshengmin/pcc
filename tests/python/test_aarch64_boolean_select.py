"""Constant-arm scalar booleans stay in registers through their consumers."""

import os
import platform
import re
import subprocess

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm
from tests.python.test_darwin_tls_leaf_protection import emit


@pytest.fixture(autouse=True)
def enable_diagnostic_lowering(monkeypatch):
    monkeypatch.setenv("PCC_SELF_BOOLEAN_REGISTER_LOWERING", "1")


def test_default_keeps_accepted_boolean_lowering(monkeypatch):
    monkeypatch.delenv("PCC_SELF_BOOLEAN_REGISTER_LOWERING", raising=False)
    source = program(("true", "%other"))
    default = emit_aarch64_darwin_asm(source, optimize=True)
    monkeypatch.setenv("PCC_SELF_BOOLEAN_REGISTER_LOWERING", "0")
    assert emit_aarch64_darwin_asm(source, optimize=True) == default
    monkeypatch.setenv("PCC_SELF_BOOLEAN_REGISTER_LOWERING", "1")
    assert emit_aarch64_darwin_asm(source, optimize=True) != default


def direct_capture(text):
    from pcc.backend.native_object import NativeObject
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module

    transport = emit_aarch64_darwin_indexed_transport(
        parse_self_backend_module(text), optimize=False, structured_instructions=True,
    )
    sections, undefined = transport.assemble_sections()
    return NativeObject.from_sections(sections, undefined=undefined).to_macho()


ARMS = (("true", "%other"), ("false", "%other"),
        ("%other", "true"), ("%other", "false"),
        ("true", "false"), ("false", "true"),
        ("true", "true"), ("false", "false"))


def program(arms, intervening_call=False, names=False):
    other = "%true" if names else "%other"
    left, right = (x.replace("%other", other) for x in arms)
    call = "  %ignored = call i64 @smash(i64 3, i64 4, i64 5, i64 6, i64 7, i64 8, i64 9, i64 10)\n" if intervening_call else ""
    source = ('target triple = "arm64-apple-darwin"\n@calls = global i64 0\n'
              'define i64 @smash(i64 %a, i64 %b, i64 %c, i64 %d, i64 %e, i64 %f, i64 %g, i64 %h) {\nentry:\n'
              '  %old = load i64, ptr @calls\n  %next = add i64 %old, 1\n  store i64 %next, ptr @calls\n  ret i64 %h\n}\n'
              'define i64 @choose(i1 %condition, i1 ' + other + ') {\nentry:\n'
              '  %selected = select i1 %condition, i1 ' + left + ', i1 ' + right + '\n'
              + call + '  br i1 %selected, label %yes, label %no\n'
              'yes:\n  br label %join\nno:\n  br label %join\njoin:\n'
              '  %answer = phi i64 [11, %yes], [22, %no]\n  ret i64 %answer\n}\n'
              'define i32 @main() {\nentry:\n')
    checks = []
    for index, (condition, value) in enumerate(((0, 0), (0, 1), (1, 0), (1, 1))):
        arm = arms[0] if condition else arms[1]
        selected = value if arm == "%other" else int(arm == "true")
        expected = 11 if selected else 22
        source += f"  %r{index} = call i64 @choose(i1 {condition}, i1 {value})\n  %ok{index} = icmp eq i64 %r{index}, {expected}\n"
        checks.append(f"%ok{index}")
    source += "  %ok01 = and i1 %ok0, %ok1\n  %ok23 = and i1 %ok2, %ok3\n  %ok = and i1 %ok01, %ok23\n"
    source += "  %count = load i64, ptr @calls\n  %count_ok = icmp eq i64 %count, " + ("4" if intervening_call else "0") + "\n"
    source += "  %all = and i1 %ok, %count_ok\n  %status = select i1 %all, i32 0, i32 81\n  ret i32 %status\n}\n"
    return source


@pytest.mark.parametrize("arms", ARMS)
def test_boolean_select_needs_no_csel_or_byte_spill(arms, monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    text = emit_aarch64_darwin_asm(program(arms), optimize=True)
    body = re.search(r"_choose:\n(.*?)(?=\n\.p2align 2|\n\.section|\Z)", text, re.S).group(1)
    assert "csel " not in body
    assert "sturb " not in body, body


@pytest.mark.skipif(platform.system() != "Darwin" or platform.machine() != "arm64",
                    reason="native Darwin arm64 execution")
@pytest.mark.parametrize("arms", ARMS)
@pytest.mark.parametrize("indexed", (False, True))
@pytest.mark.parametrize("lowering", ("0", "1"))
@pytest.mark.parametrize("registers,intervening_call", (("0", False), ("0", True), ("1", False), ("1", True)))
def test_boolean_truth_tables_phi_edges_and_call_clobbers_execute(tmp_path, monkeypatch, arms, indexed, lowering, registers, intervening_call):
    monkeypatch.setenv("PCC_SELF_BOOLEAN_REGISTER_LOWERING", lowering)
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    data = emit(program(arms, intervening_call, names=True), indexed, tmp_path)
    binary = tmp_path / "boolean"
    binary.write_bytes(link_executable([data]))
    binary.chmod(0o755)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10,
                            env=dict(os.environ, PATH="/nonexistent"))
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


@pytest.mark.skipif(platform.system() != "Darwin" or platform.machine() != "arm64",
                    reason="native Darwin arm64 execution")
@pytest.mark.parametrize("operator", ("and", "or", "xor"))
@pytest.mark.parametrize("indexed", (False, True))
def test_materialized_comparisons_and_boolean_logic_execute(tmp_path, monkeypatch, operator, indexed):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    source = ('target triple = "arm64-apple-darwin"\n'
              'define i64 @combine(i64 %a, i64 %b) {\nentry:\n'
              '  %left = icmp eq i64 %a, 0\n  %right = icmp ne i64 %b, 0\n'
              '  %combined = ' + operator + ' i1 %left, %right\n'
              '  %result = zext i1 %combined to i64\n  ret i64 %result\n}\n'
              'define i32 @main() {\nentry:\n')
    for index, (a, b) in enumerate(((0, 0), (0, 9), (-1, 0), (-1, 9))):
        left, right = a == 0, b != 0
        answer = (left and right) if operator == "and" else (left or right) if operator == "or" else left != right
        source += f"  %r{index} = call i64 @combine(i64 {a}, i64 {b})\n  %ok{index} = icmp eq i64 %r{index}, {int(answer)}\n"
    source += '  %a = and i1 %ok0, %ok1\n  %b = and i1 %ok2, %ok3\n  %ok = and i1 %a, %b\n  %status = select i1 %ok, i32 0, i32 82\n  ret i32 %status\n}\n'
    assembly = emit_aarch64_darwin_asm(source, optimize=True)
    body = re.search(r"_combine:\n(.*?)(?=\n\.p2align 2|\n\.section|\Z)", assembly, re.S).group(1)
    assert not re.search(r"cset [^\n]+\n\s+and [^\n]+#(?:0xff|255)", body), body
    binary = tmp_path / "logic"
    binary.write_bytes(link_executable([emit(source, indexed, tmp_path)]))
    binary.chmod(0o755)
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10,
                         env=dict(os.environ, PATH="/nonexistent"))
    assert (run.returncode, run.stdout, run.stderr) == (0, "", "")


@pytest.mark.skipif(platform.system() != "Darwin" or platform.machine() != "arm64",
                    reason="native Darwin arm64 execution")
@pytest.mark.parametrize("literal", (0, 1, 2, 3, -1, -2))
def test_boolean_numeric_masks_execute_with_direct_capture(tmp_path, literal):
    source = ('target triple = "arm64-apple-darwin"\n'
              'define i1 @masked(i1 %value) {\nentry:\n'
              f'  %result = and i1 %value, {literal}\n  ret i1 %result\n}}\n'
              'define i32 @main() {\nentry:\n  %result = call i1 @masked(i1 true)\n'
              f'  %ok = icmp eq i1 %result, {literal & 1}\n'
              '  %status = select i1 %ok, i32 0, i32 83\n  ret i32 %status\n}\n')
    binary = tmp_path / "mask"
    binary.write_bytes(link_executable([direct_capture(source)]))
    binary.chmod(0o755)
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10,
                         env=dict(os.environ, PATH="/nonexistent"))
    assert (run.returncode, run.stdout, run.stderr) == (0, "", "")


@pytest.mark.skipif(platform.system() != "Darwin" or platform.machine() != "arm64",
                    reason="native Darwin arm64 execution")
def test_new_boolean_value_crosses_phi_with_direct_capture(tmp_path):
    source = '''target triple = "arm64-apple-darwin"
define i64 @choose(i1 %condition, i1 %other) {
entry:
  %value = select i1 %condition, i1 true, i1 %other
  br i1 %condition, label %yes, label %no
yes:
  br label %join
no:
  %inverse = xor i1 %value, true
  br label %join
join:
  %result = phi i1 [%value, %yes], [%inverse, %no]
  %wide = zext i1 %result to i64
  ret i64 %wide
}
define i32 @main() {
entry:
  %a = call i64 @choose(i1 false, i1 false)
  %b = call i64 @choose(i1 false, i1 true)
  %c = call i64 @choose(i1 true, i1 false)
  %d = call i64 @choose(i1 true, i1 true)
  %oa = icmp eq i64 %a, 1
  %ob = icmp eq i64 %b, 0
  %oc = icmp eq i64 %c, 1
  %od = icmp eq i64 %d, 1
  %ab = and i1 %oa, %ob
  %cd = and i1 %oc, %od
  %ok = and i1 %ab, %cd
  %status = select i1 %ok, i32 0, i32 84
  ret i32 %status
}
'''
    binary = tmp_path / "phi"
    binary.write_bytes(link_executable([direct_capture(source)]))
    binary.chmod(0o755)
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10,
                         env=dict(os.environ, PATH="/nonexistent"))
    assert (run.returncode, run.stdout, run.stderr) == (0, "", "")

"""Owned x86 CPUID object, checked against the original Go assembly body."""
import os
from pathlib import Path
import subprocess

import pytest

from pcc.frontends.go import DEFAULT_TARGET, function_symbol
from pcc.frontends.go.source_plan import GoSourcePlanError
from scripts.go_std_closure import FIXTURE
from tests.go.reference_build_cache import reference_build_slot
from tests.go.owned_execution import run
from tests.go.test_execution import forbid_external_compiler


SOURCE = FIXTURE / "src/internal/cpu/cpu_x86.go"
ASSEMBLY = SOURCE.with_suffix(".s")


@pytest.mark.parametrize("fault", ("source", "assembly", "target"))
def test_cpuid_owner_rejects_other_sources_or_targets(fault):
    from pcc.frontends.go.native_cpu import emit_locked_cpuid

    source, assembly, target = SOURCE.read_bytes(), ASSEMBLY.read_bytes(), DEFAULT_TARGET
    if fault == "source":
        source += b"\n"
    elif fault == "assembly":
        assembly = assembly.replace(b"CPUID", b"NOP")
    else:
        target = "arm64-apple-darwin"
    with pytest.raises(GoSourcePlanError, match="CPUID"):
        emit_locked_cpuid(source, assembly, target=target)


def test_xgetbv_binding_stays_tied_to_original_guarded_declaration_and_body():
    from pcc.frontends.go.native_cpu import emit_locked_xgetbv, source_xgetbv_binding

    declarations, body = SOURCE.read_bytes(), ASSEMBLY.read_bytes()
    binding = source_xgetbv_binding(declarations, body, target=DEFAULT_TARGET)
    assert binding.parameters == () and binding.results == ("uint32", "uint32")
    assert binding.symbol == function_symbol("xgetbv", "internal/cpu")
    with pytest.raises(GoSourcePlanError, match="XGETBV"):
        emit_locked_xgetbv(declarations.replace(b"if X86.HasOSXSAVE", b"if true"), body, target=DEFAULT_TARGET)


def test_goamd64_level_owner_requires_the_selected_build_level_not_host_features():
    from pcc.frontends.go.native_cpu import emit_locked_goamd64_level

    with pytest.raises(GoSourcePlanError, match="GOAMD64"):
        emit_locked_goamd64_level(SOURCE.read_bytes(), ASSEMBLY.read_bytes(), target=DEFAULT_TARGET, level="v2")


def test_source_instance_accepts_exact_owned_signature_only():
    from pcc.frontends.go import UnsupportedGo
    from pcc.frontends.go.native_cpu import source_cpuid_binding
    from pcc.frontends.go.packages import SourceInstance, analyze_source_instances

    owner = source_cpuid_binding(SOURCE.read_bytes(), ASSEMBLY.read_bytes(), target=DEFAULT_TARGET)
    caller = 'package main;import _ "internal/cpu";func main(){}'
    declaration = "package cpu;func cpuid(eax,ecx uint32)(a,b,c,d uint32)"
    graph = {
        "main": SourceInstance("main", (("main.go", caller),), {"internal/cpu": "internal/cpu"}),
        "internal/cpu": SourceInstance("internal/cpu", (("cpu.go", declaration),), {}, {"cpuid": owner}),
    }
    admitted = analyze_source_instances(graph, main_instance="main", target_triple=DEFAULT_TARGET)
    assert admitted["internal/cpu"].bound_native_functions == {"cpuid": owner}
    assert owner.symbol == function_symbol("cpuid", "internal/cpu") and len(owner.object_sha256) == 64
    graph["internal/cpu"] = SourceInstance(
        "internal/cpu", (("cpu.go", declaration.replace("uint32", "uint64", 1)),), {}, {"cpuid": owner},
    )
    with pytest.raises(UnsupportedGo, match="owned native signature or symbol differs"):
        analyze_source_instances(graph, main_instance="main", target_triple=DEFAULT_TARGET)
    graph["internal/cpu"] = SourceInstance(
        "internal/cpu", (("cpu.go", declaration + ";func xgetbv()(uint32,uint32)"),), {}, {"cpuid": owner},
    )
    with pytest.raises(UnsupportedGo, match="bodyless function requires an owned implementation") as caught:
        analyze_source_instances(graph, main_instance="main", target_triple=DEFAULT_TARGET)
    assert caught.value.code == "go.unsupported.external-function-owner"


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
def test_pcc_call_and_owned_adapter_match_original_go_asm_result_bytes(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.x86_64_asm_driver import assemble_file
    from pcc.frontends.go.native_cpu import emit_locked_cpuid

    native_symbol = function_symbol("cpuid", "internal/cpu")
    result = "{ i32, i32, i32, i32 }"
    ir = f"""target triple = "{DEFAULT_TARGET}"
declare {result} @{native_symbol}(i32, i32)
define {result} @probe(i32 %leaf, i32 %subleaf) {{
entry:
  %words = call {result} @{native_symbol}(i32 %leaf, i32 %subleaf)
  ret {result} %words
}}
"""
    start = """.intel_syntax noprefix
.text
.globl _start
_start:
  sub rsp, 32
  mov rbx, 0x1122334455667788
  xor edi, edi
  xor esi, esi
  call probe
  mov QWORD PTR [rsp], rax
  mov QWORD PTR [rsp + 8], rdx
  mov edi, 7
  mov esi, 1
  call probe
  mov QWORD PTR [rsp + 16], rax
  mov QWORD PTR [rsp + 24], rdx
  mov rax, 0x1122334455667788
  cmp rbx, rax
  jne bad
  mov eax, 1
  mov edi, 1
  mov rsi, rsp
  mov edx, 32
  syscall
  xor edi, edi
  jmp done
bad:
  mov edi, 1
done:
  mov eax, 60
  syscall
"""
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        obj = emit_locked_cpuid(SOURCE.read_bytes(), ASSEMBLY.read_bytes(), target=DEFAULT_TARGET)
        caller = parse_relocatable(emit_owned_object(ir, DEFAULT_TARGET))
        image = link_static_executable([assemble_file(start), caller, obj], entry="_start")
    owned = tmp_path / "owned"
    owned.write_bytes(image)
    owned.chmod(0o700)
    reference = _original_asm_control(tmp_path, """package main
import ("os"; "encoding/binary")
func cpuid(eaxArg, ecxArg uint32) (eax, ebx, ecx, edx uint32)
func main() {
  a,b,c,d:=cpuid(0,0)
  e,f,g,h:=cpuid(7,1)
  var raw [32]byte
  for i, value:=range [8]uint32{a,b,c,d,e,f,g,h} {
    binary.LittleEndian.PutUint32(raw[i*4:], value)
  }
  if n,err:=os.Stdout.Write(raw[:]); n!=len(raw)||err!=nil { panic("write") }
}
""")
    got, want = run(owned), run(reference)
    assert got == want and got.returncode == 0 and len(got.stdout) == 32 and got.stderr == b""


def _original_asm_control(tmp_path, source):
    original = tmp_path / "official"
    original.mkdir()
    (original / ASSEMBLY.name).write_bytes(ASSEMBLY.read_bytes())
    (original / "main.go").write_text(source, encoding="utf-8")
    go = Path(os.environ["PCC_GO_REFERENCE"]).resolve(strict=True)
    reference = original / "reference"
    env = {"PATH": os.defpath, "GOROOT": str(go.parent.parent),
           "GOCACHE": os.environ["PCC_GO_REFERENCE_CACHE"], "GOTOOLCHAIN": "local",
           "GOENV": "off", "GOWORK": "off", "GO111MODULE": "off", "GOPROXY": "off",
           "GOSUMDB": "off", "CGO_ENABLED": "0", "GOOS": "linux", "GOARCH": "amd64",
           "GOAMD64": "v1", "GOMAXPROCS": "2"}
    with reference_build_slot(env['GOCACHE']):
        subprocess.run([str(go), "build", "-p=2", "-o", str(reference), "."],
                       cwd=original, env=env, check=True, capture_output=True, timeout=35)
    return reference


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
def test_xgetbv_native_owner_matches_original_guarded_go_return(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable
    from pcc.backend.x86_64_asm_driver import assemble_file
    from pcc.frontends.go.native_cpu import emit_locked_cpuid, emit_locked_xgetbv

    cpuid, xgetbv = (function_symbol(name, "internal/cpu") for name in ("cpuid", "xgetbv"))
    start = f""".intel_syntax noprefix
.text
.globl _start
_start:
  sub rsp, 16
  mov QWORD PTR [rsp], 0
  mov QWORD PTR [rsp + 8], 0
  mov edi, 1
  xor esi, esi
  call {cpuid}
  mov r9d, 0x08000000
  test edx, r9d
  je output
  call {xgetbv}
  mov QWORD PTR [rsp], rax
  mov QWORD PTR [rsp + 8], 1
output:
  mov eax, 1
  mov edi, 1
  mov rsi, rsp
  mov edx, 16
  syscall
  mov eax, 60
  xor edi, edi
  syscall
"""
    declarations, body = SOURCE.read_bytes(), ASSEMBLY.read_bytes()
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        image = link_static_executable([
            assemble_file(start),
            emit_locked_cpuid(declarations, body, target=DEFAULT_TARGET),
            emit_locked_xgetbv(declarations, body, target=DEFAULT_TARGET),
        ], entry="_start")
    owned = tmp_path / "owned"
    owned.write_bytes(image)
    owned.chmod(0o700)
    reference = _original_asm_control(tmp_path, """package main
import ("os"; "encoding/binary")
func cpuid(eaxArg, ecxArg uint32) (eax, ebx, ecx, edx uint32)
func xgetbv() (eax, edx uint32)
func main() {
  _,_,ecx,_:=cpuid(1,0)
  var raw [16]byte
  if ecx&(1<<27)!=0 {
    a,d:=xgetbv()
    binary.LittleEndian.PutUint32(raw[:], a)
    binary.LittleEndian.PutUint32(raw[4:], d)
    raw[8]=1
  }
  if n,err:=os.Stdout.Write(raw[:]); n!=len(raw)||err!=nil { panic("write") }
}
""")
    got, want = run(owned), run(reference)
    assert got == want and got.returncode == 0 and len(got.stdout) == 16 and got.stderr == b""
    (tmp_path / "executed-xgetbv.txt").write_text(str(got.stdout[8]) + "\n")


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
def test_goamd64_v1_owner_matches_original_four_byte_result(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable
    from pcc.backend.x86_64_asm_driver import assemble_file
    from pcc.frontends.go.native_cpu import emit_locked_goamd64_level

    symbol = function_symbol("getGOAMD64level", "internal/cpu")
    start = f""".intel_syntax noprefix
.text
.globl _start
_start:
  sub rsp, 16
  call {symbol}
  mov DWORD PTR [rsp], eax
  mov eax, 1
  mov edi, 1
  mov rsi, rsp
  mov edx, 4
  syscall
  mov eax, 60
  xor edi, edi
  syscall
"""
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        obj = emit_locked_goamd64_level(SOURCE.read_bytes(), ASSEMBLY.read_bytes(), target=DEFAULT_TARGET, level="v1")
        image = link_static_executable([assemble_file(start), obj], entry="_start")
    owned = tmp_path / "owned"
    owned.write_bytes(image)
    owned.chmod(0o700)
    reference = _original_asm_control(tmp_path, """package main
import ("os"; "encoding/binary")
func getGOAMD64level() int32
func main() {
  var raw [4]byte
  binary.LittleEndian.PutUint32(raw[:],uint32(getGOAMD64level()))
  if n,err:=os.Stdout.Write(raw[:]); n!=len(raw)||err!=nil { panic("write") }
}
""")
    got, want = run(owned), run(reference)
    assert got == want and got.returncode == 0 and got.stdout == b"\x01\x00\x00\x00" and got.stderr == b""

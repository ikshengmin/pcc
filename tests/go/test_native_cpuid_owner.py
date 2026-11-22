"""Owned x86 CPUID object, checked against the original Go assembly body."""
import os
from pathlib import Path
import subprocess

import pytest

from pcc.frontends.go import DEFAULT_TARGET, function_symbol
from pcc.frontends.go.source_plan import GoSourcePlanError
from scripts.go_std_closure import FIXTURE
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
    original = tmp_path / "official"
    original.mkdir()
    (original / ASSEMBLY.name).write_bytes(ASSEMBLY.read_bytes())
    (original / "main.go").write_text("""package main
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
""", encoding="utf-8")
    go = Path(os.environ["PCC_GO_REFERENCE"]).resolve(strict=True)
    reference = original / "reference"
    env = {"PATH": os.defpath, "GOROOT": str(go.parent.parent),
           "GOCACHE": os.environ["PCC_GO_REFERENCE_CACHE"], "GOTOOLCHAIN": "local",
           "GOENV": "off", "GOWORK": "off", "GO111MODULE": "off", "GOPROXY": "off",
           "GOSUMDB": "off", "CGO_ENABLED": "0", "GOOS": "linux", "GOARCH": "amd64",
           "GOAMD64": "v1", "GOMAXPROCS": "2"}
    subprocess.run([str(go), "build", "-p=2", "-o", str(reference), "."],
                   cwd=original, env=env, check=True, capture_output=True, timeout=35)
    got, want = run(owned), run(reference)
    assert got == want and got.returncode == 0 and len(got.stdout) == 32 and got.stderr == b""

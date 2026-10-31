"""Unchanged generated Go table component, never a full math/bits claim."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from pcc.frontends.go import GoDiagnostic, compile_go_packages, function_symbol
from tests.go.owned_execution import Artifact, Execution, compile_module, compile_reference_packages, run, write_artifact
from tests.go.test_execution import forbid_external_compiler

ROOT=Path(__file__).parent
TABLES=ROOT/"upstream/go1.27.3/src/math/bits/bits_tables.go"
TABLE_SHA="c4cb4a05dab6d395d9d51720893a42f0a9d1cdf8a0c2383d87268e49acb73f7f"


def component_inputs():
    assert hashlib.sha256(TABLES.read_bytes()).hexdigest()==TABLE_SHA
    fixtures=ROOT/"fixtures/string_tables"
    return {"main":[("main.go",(fixtures/"main.go").read_text())],
            "pccbitsref":[("bits_tables.go",TABLES.read_bytes().decode()),("reader.go",(fixtures/"reader.go").read_text())]}


def expected_bytes():
    result=bytearray()
    for value in range(256):
        trailing=(value & -value).bit_length()-1 if value else 8
        result.extend((trailing,value.bit_count(),int(f"{value:08b}"[::-1],2),value.bit_length()))
    return bytes(result)


@pytest.mark.integration
def test_complete_original_table_component_has_all_1024_native_bytes(tmp_path,monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable,parse_relocatable
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.x86_64_asm_driver import assemble_file
    inputs=component_inputs()
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        module=compile_go_packages(inputs)
        text=str(module);obj=emit_owned_object(text,"x86_64-unknown-linux-gnu")
        # Observer supplies input bits, saves each uint32 return, writes bytes.
        # It implements no Go table lookup, conversion, comparison or algorithm.
        assembly=""".intel_syntax noprefix
.text
.globl _start
_start:
  sub rsp, 1024
  xor ebx, ebx
  mov r12, rsp
observe_next:
  mov rdi, rbx
  call ENTRY
  mov dword ptr [r12], eax
  add r12, 4
  add ebx, 1
  cmp ebx, 256
  jne observe_next
  mov rax, 1
  mov rdi, 1
  mov rsi, rsp
  mov rdx, 1024
  syscall
  mov rax, 60
  mov rdi, 0
  syscall
""".replace("ENTRY",function_symbol("Result"))
        executable=link_static_executable([assemble_file(assembly),parse_relocatable(obj)],entry="_start")
    artifact=Artifact(text,obj,executable)
    binary=tmp_path/"original-table-component";write_artifact(artifact,binary)
    actual=run(binary);expected=expected_bytes()
    (tmp_path/"raw-string-tables-observation.json").write_text(json.dumps({
        "owner":artifact.owner,"component_not_full_package":True,"original_table_sha256":TABLE_SHA,
        "expected_hex":expected.hex(),"stdout_hex":actual.stdout.hex(),"returncode":actual.returncode,
        "stderr_hex":actual.stderr.hex(),"executable_sha256":hashlib.sha256(executable).hexdigest(),
    },indent=2)+"\n")
    assert actual==Execution(0,expected,b"")
    assert hashlib.sha256(TABLES.read_bytes()).hexdigest()==TABLE_SHA


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
def test_official_go_checks_the_same_complete_table_file_and_authored_component_main(tmp_path,monkeypatch):
    inputs=component_inputs()
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied);artifact=compile_module(compile_go_packages(inputs))
    binary=tmp_path/"table-main";write_artifact(artifact,binary)
    reference=compile_reference_packages(inputs,tmp_path/"reference",compiler=os.environ["PCC_GO_REFERENCE"],cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert run(binary)==run(reference)==Execution(0,b"",b"")
    for package,units in inputs.items():
        for filename,original in units:
            assert (reference.parent/"gopath/src"/package/filename).read_bytes()==original.encode()


def test_complete_table_component_cross_emits_without_calling_a_host_compiler(monkeypatch):
    from pcc.backend.owned_object_emit import emit_owned_object
    forbid_external_compiler(monkeypatch)
    module=compile_go_packages(component_inputs(),target_triple="arm64-apple-darwin")
    assert emit_owned_object(str(module),"arm64-apple-darwin")[:4]==b"\xcf\xfa\xed\xfe"


def test_normal_original_math_bits_file_selection_keeps_error_runtime_dependency():
    directory=TABLES.parent
    filenames=("bits.go","bits_tables.go","bits_errors.go")
    originals=[(name,(directory/name).read_bytes()) for name in filenames]
    assert b"//go:build !compiler_bootstrap" in originals[-1][1]
    inputs={"main":[("main.go",'package main;import "math/bits";func main(){_=bits.LeadingZeros(1)}')],
            "math/bits":[(name,data.decode()) for name,data in originals]}
    with pytest.raises(GoDiagnostic,match="unsupported type 'error'") as failure:
        compile_go_packages(inputs)
    assert failure.value.filename=="bits_errors.go"
    assert all((directory/name).read_bytes()==data for name,data in originals)

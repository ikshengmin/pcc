"""The locked C dependency only. This is not an orjson/Rust or Python-extension build."""
import subprocess

import pytest

from scripts.ci_orjson_c_object import PUBLIC_API,emit_c_object,emit_dependency,require_dependency_object
from scripts.ci_orjson_source_frontier import C_UNIT,DEFINES,ROOT,TARGET,load_inputs
from scripts.ci_source_frontier_support import materialize_snapshot,verify_snapshot

def _sample(machine=None, *, missing=False, noncode=False, noalloc=False, zero=False, local=False, undefined=False):
    from pcc.backend.elf_x86_64 import (EM_X86_64, ElfObject, ElfSection, ElfSymbol, SHF_ALLOC, SHF_EXECINSTR,
                                       SHT_PROGBITS, STB_GLOBAL, STB_LOCAL, STT_FUNC, emit_relocatable)
    sec=ElfSection(".text",SHT_PROGBITS,(0 if noalloc else SHF_ALLOC)|(0 if noncode else SHF_EXECINSTR),1,b"\xc3\xc3")
    syms=[ElfSymbol.null()]
    for i,name in enumerate(PUBLIC_API):
        if missing and i:continue
        syms.append(ElfSymbol(name,0 if undefined else 1,0 if undefined else i,0 if zero or undefined else 1,
                              STB_LOCAL if local else STB_GLOBAL,STT_FUNC))
    return emit_relocatable(ElfObject((sec,),tuple(syms),machine if machine is not None else EM_X86_64))

@pytest.mark.parametrize("kind",["truncated","machine","missing","noncode","noalloc","zero","local","undefined"])
def test_not_just_an_elf_header_or_unresolved_name(kind):
    from pcc.backend.elf_x86_64 import EM_AARCH64
    if kind=="truncated":raw=_sample()[:63]
    elif kind=="machine":raw=_sample(machine=EM_AARCH64)
    else:raw=_sample(**{kind:True})
    with pytest.raises(ValueError):require_dependency_object(raw)
    assert require_dependency_object(_sample())["defined_api_functions"]==list(PUBLIC_API)

def test_external_object_writer_and_other_target_never_run(tmp_path,monkeypatch):
    monkeypatch.setattr(subprocess,"Popen",lambda *a,**k:pytest.fail("an external tool ran"))
    unit=tmp_path/"missing.c";obj=tmp_path/"forbidden.o"
    monkeypatch.setenv("PCC_SELF_OBJ","system-as")
    with pytest.raises(ValueError,match="external C object"):
        emit_c_object(unit,tmp_path,obj,TARGET,DEFINES)
    with pytest.raises(ValueError,match="owned x86_64 Linux"):
        emit_c_object(unit,tmp_path,obj,"aarch64-unknown-linux-gnu",DEFINES)
    assert not obj.exists()

@pytest.mark.integration
@pytest.mark.xdist_group(name="rust-native-reference")
@pytest.mark.pcc_gate(platforms=("linux",),machines=("x86_64","amd64"))
def test_original_dependency_executes_through_its_pinned_c_abi(tmp_path,monkeypatch,pcc_runtime_archive):
    from pcc.backend.elf_x86_64 import link_static_executable,parse_relocatable
    from tests.runtime_fixture_provenance import _verified_test_runtime_archive

    archive,manifest=_verified_test_runtime_archive(pcc_runtime_archive)
    assert all(not item["uses_host_cc"] for item in manifest["members"])
    _,all_files=load_inputs()
    files={key:all_files[key] for key in (C_UNIT,"include/yyjson/yyjson.h")}
    snapshot=tmp_path/"source";materialize_snapshot(snapshot,files)
    with monkeypatch.context() as owned:
        owned.setattr(subprocess,"Popen",lambda *a,**k:pytest.fail("owned compile/link ran an external process"))
        record=emit_dependency(tmp_path,snapshot,files,unit_name=C_UNIT,target=TARGET,defines=DEFINES)
        library=parse_relocatable((tmp_path/record["artifact"]).read_bytes())
        observer_path=ROOT/"tests/c/programs/orjson_yyjson_observer.c"
        observer=parse_relocatable(emit_c_object(observer_path,(snapshot/C_UNIT).parent,tmp_path/"observer.o",TARGET,DEFINES))
        members=[]
        executable=link_static_executable([library,observer],archives=[archive.read_bytes()],
                                          archive_selections=members,compact_archive_relocations=True)
        assert (0,"freestanding_c_linux_start.o") in members
    verify_snapshot(snapshot,files)
    binary=tmp_path/"observer";binary.write_bytes(executable);binary.chmod(0o700)
    run=subprocess.run([str(binary)],env={},capture_output=True,timeout=5)
    assert (run.returncode,run.stdout,run.stderr)==(42,b"",b"")

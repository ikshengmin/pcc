"""Native ownership independently exercised by a PCC-built client; JDK only as an oracle."""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

import pytest

from pcc.frontends.java import compile_source, compile_sources
from pcc.frontends.java.artifact import method_symbol
from tests.java.test_source_managed_reference_calls import ARRAY, DECLARED, NOMINAL, TAG

ROOT=Path(__file__).resolve().parents[2]
TARGET="x86_64-unknown-linux-gnu"

@pytest.mark.integration
@pytest.mark.xdist_group(name="java-managed-reference")
@pytest.mark.pcc_gate(probe=lambda: bool(os.environ.get("PCC_RUNTIME_ARCHIVE")) and
                      (os.environ.get("PCC_CI_REQUIRE_JAVA_MANAGED_INPUT")=="1" or bool(os.environ.get("PCC_JAVA_MANAGED_OBJECT"))) and sys.platform=="linux"
                      and platform.machine()=="x86_64")
def test_managed_reference_native_abi_restores_real_roots(tmp_path,monkeypatch):
    import resource
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    from pcc.tools import runtime_archive_provenance as provenance
    from pcc.frontends.python.owned_runtime_build import runtime_build_config
    from tests.owned_runtime_c_fixture import admitted_runtime
    from tests.java.test_source_frontend import forbid_bytecode_or_external_owner

    assert os.environ.get("PCC_JAVA_MANAGED_OBJECT"), "verified Java GC0 companion object is missing"
    archive=admitted_runtime()
    observer=ROOT/"tests/java/ManagedReferenceObserve.c"
    with monkeypatch.context() as patch:
        forbid_bytecode_or_external_owner(patch)
        programs=[compile_source(ARRAY,filename="References.java"),compile_sources({"RefOps.java":NOMINAL},
            entry_class="observer.RefOps",descriptor=TAG),compile_source(DECLARED,filename="Declared.java")]
        objects=[]
        for i,program in enumerate(programs):
            assert program.managed_runtime
            path=tmp_path/("managed"+str(i)+".o")
            path.write_bytes(program.emit_object())
            objects.append(str(path))
        code=observer.read_text().replace("ARRAY_ENTRY",programs[0].entry_symbol).replace("TOKEN_ENTRY",programs[1].entry_symbol)
        code=code.replace("DECLARED_ENTRY",programs[2].entry_symbol)
        code=code.replace("ARRAY_RELAY",method_symbol("References","relay","(I)[I")).replace("TOKEN_FORWARD",
            method_symbol("observer/RefOps","forward","(Lobserver/Token;)Lobserver/Token;"))
        # Read and verify the optional product input; no provisioning (including
        # no builder invocation inside a validation worker). Copy the verified
        # bytes to the private link input rather than trusting a mutable path.
        selected=Path(os.environ["PCC_JAVA_MANAGED_OBJECT"]).resolve(strict=True)
        assert selected.name=="py_shared_managed.o"
        payload=selected.read_bytes()
        record=json.loads(provenance.receipt_path_for_object(selected).read_text())
        assert provenance._validate_member_record(record,member=selected.name,member_bytes=payload,
            runtime_root=ROOT/"pcc/runtime")==TARGET
        assert record["source"]=="pcc/runtime/py/py_shared_managed.py"
        assert record["codegen_checksum"]==provenance.codegen_checksum()
        assert record["runtime_build_config"]==runtime_build_config()
        assert record["object_emitter"]=="pcc-self-backend-object-writer"
        # Verify the same producer's actual bytes and reject three changed
        # private copies. Never mutate a shared artifact or rebuild on failure.
        from scripts import ci_validation_java_managed as managed
        baseline=managed.object_identity(ROOT,selected.parent)
        assert baseline["sha256"]==record["object_sha256"]
        corrupt=tmp_path/"changed-input";corrupt.mkdir()
        bad_obj=corrupt/selected.name
        bad_receipt=provenance.receipt_path_for_object(bad_obj)
        bad_obj.write_bytes(payload[:-1]+bytes([payload[-1]^1]))
        bad_receipt.write_text(json.dumps(record))
        with pytest.raises(provenance.ProvenanceError,match="does not match its receipt"):
            managed.object_identity(ROOT,corrupt)
        bad_obj.write_bytes(payload)
        for field,value in (("target_triple","aarch64-unknown-linux-gnu"),("codegen_checksum","0"*64)):
            bad_receipt.write_text(json.dumps(dict(record,**{field:value})))
            with pytest.raises(ValueError):
                managed.object_identity(ROOT,corrupt)
        local=tmp_path/selected.name
        local.write_bytes(payload)
        evaluator=CEvaluator(backend="self",target_triple=TARGET)
        units=evaluator.compile_translation_units([TranslationUnit(observer.name,str(observer),code)],
                use_system_cpp=False,use_compile_cache=False,
                include_dirs=[str(ROOT/"pcc/runtime/include"),str(ROOT/"utils/fake_libc_include")])
        exe=tmp_path/"observe"
        evaluator.emit_executable(units,str(exe),link_args=[*objects,str(local),str(archive)])
    def limit():
        resource.setrlimit(resource.RLIMIT_AS,(64*1024*1024,64*1024*1024))
    result=subprocess.run([str(exe)],capture_output=True,timeout=8,preexec_fn=limit,
                          env={**os.environ,"PCC_GC_BACKEND":"0"})
    assert (result.returncode,result.stdout,result.stderr)==(0,b"",b"")

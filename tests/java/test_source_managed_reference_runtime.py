"""Independent development-JDK execution of the unchanged source originals."""
import os
from pathlib import Path

import pytest

from tests.java.test_source_managed_reference_calls import ARRAY, DECLARED, GENERIC, NOMINAL, NONGENERIC, STATIC_QUALIFIED


@pytest.mark.integration
@pytest.mark.xdist_group(name="java-raw-reference")
@pytest.mark.pcc_gate(unavailable=None if os.environ.get("PCC_JAVA_RAW_REFERENCE") else
                      "select an existing development JDK with PCC_JAVA_RAW_REFERENCE")
def test_unchanged_managed_reference_originals_in_selected_jdk(tmp_path):
    from tests.java.test_source_raw_reference import JVM_LIMITS,record_process,tool_identity
    java=Path(os.environ["PCC_JAVA_RAW_REFERENCE"]).resolve(strict=True)
    before=tool_identity(java)
    level=os.environ.get("PCC_JAVA_RAW_REFERENCE_SOURCE","21")
    assert level in {"17","21"}
    originals={"References.java":ARRAY,"RefOps.java":NOMINAL,"Declared.java":DECLARED,"GenericBox.java":GENERIC,
               "A.java":STATIC_QUALIFIED}
    for path,text in originals.items():
        (tmp_path/path).write_bytes(text.encode())
    observers={"ObserveArray.java":"class ObserveArray{public static void main(String[] a){System.out.print(References.run());}}",
               "ObserveDeclared.java":"class ObserveDeclared{public static void main(String[] a){System.out.print(Declared.run());}}",
               "ObserveToken.java":"package observer;class ObserveToken{public static void main(String[] a){System.out.print(RefOps.run(new Token(),new Token()));}}"}
    observers["ObserveStatic.java"]="class ObserveStatic{public static void main(String[] a){System.out.print(A.f(null));}}"
    for path,text in observers.items():
        (tmp_path/path).write_bytes(text.encode())
    command=(str(java),*JVM_LIMITS,"-m","jdk.compiler/com.sun.tools.javac.Main",
             "-proc:none","-encoding","UTF-8","-source",level,"-target",level,"-classpath",str(tmp_path),"-d",str(tmp_path),*(str(tmp_path/n) for n in (*originals,*observers)))
    assert record_process(command,tmp_path,"compile")[0]==0
    for name in ("ObserveArray","observer.ObserveToken","ObserveDeclared","ObserveStatic"):
        result=record_process((str(java),*JVM_LIMITS,"-cp",str(tmp_path),name),tmp_path,name)
        assert result==(0,b"42",b"")
    bad_dir=tmp_path/"invalid"; bad_dir.mkdir()
    invalid=bad_dir/"A.java"
    invalid.write_bytes(NONGENERIC.encode())
    rc,stdout,stderr=record_process((str(java),*JVM_LIMITS,"-m","jdk.compiler/com.sun.tools.javac.Main",
        "-XDrawDiagnostics","-proc:none","-source",level,"-target",level,"-classpath",str(bad_dir),
        "-d",str(bad_dir),str(invalid)),tmp_path,"nongeneric")
    assert rc==1 and stdout==b"" and b"compiler.err.type.doesnt.take.params" in stderr
    assert not (bad_dir/"A.class").exists() and invalid.read_bytes()==NONGENERIC.encode()
    assert tool_identity(java)==before
    assert all((tmp_path/n).read_bytes()==s.encode() for n,s in {**originals,**observers}.items())

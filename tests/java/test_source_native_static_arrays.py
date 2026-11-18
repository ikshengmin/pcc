"""Owned active-use roots on GC0. No JDK table/constant substitutes for Java storage."""
import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, compile_source, compile_sources
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner

# Supplied ordinary classes share a single compilation. Reading only a
# constant field is not an active use. Even the unused declaration must not run
# until one of its own static methods or a nonconstant field is touched.
TABLE = """class Table {
 static final int CONSTANT = 42;
 static final int[] DATA={2,3,5};
 static final int LAST=DATA[DATA.length-1];
 static final int[] ALIAS=DATA;
 static final short ZERO[]=new short[DATA.length];
 static final long WIDENED=DATA[0]+40L;
 static final double FRAC=DATA[0]+0.25;
 static final float HALF=ALIAS[1]+0.5f;
 static int untouched(){return CONSTANT;}
 static int[] get(){return ALIAS;}
 static int sum(){int s=0;for(final int x:DATA){s+=x;}return s;}
 static int force(){return LAST;}
 static int numerics(){return WIDENED==42L&&FRAC==2.25&&HALF==3.5f?42:0;}
}"""
COLD = """class Cold {static final byte[] OTHER={7};static int force(){return OTHER[0];}}"""
INDEX = """class IndexSource { static final int[] SELECT={0,1}; }"""
NAMED = """class FieldNamed { static final int length=42; }"""
ENTRY = """class Entry {
 static int cold(){return Table.CONSTANT + FieldNamed.length - 42;}
 static int use(){return Table.untouched();}
 static int run(){Table.DATA[0]=37;return Table.ALIAS[0]+Table.LAST;}
 static int[] get(){return Table.get();}
 static int last(){return Table.LAST;}
 static int sum(){return Table.sum();}
 static int other(){return Cold.force();}
 static int nums(){return Table.numerics();}
 static int pick(){return Table.DATA[IndexSource.SELECT.length - 1];}
}"""
SOURCE={"Cold.java":COLD, "Entry.java":ENTRY, "Table.java":TABLE, "IndexSource.java":INDEX, "FieldNamed.java":NAMED}


def upstream_declarations_program():
    """A NEW probe class around two byte-exact declarations, never a whole project."""
    from hashlib import sha256
    from scripts.ci_java_primes_project import PRODUCTION, load_inputs
    _, files = load_inputs()  # validates the complete pinned 17-file module fixture
    original = files[next(p for p in PRODUCTION if p.endswith("/SmallPrimes.java"))]
    begin = original.index(b"    static final int[] PRIMES = {")
    end = original.index(b";", original.index(b"    static final int PRIMES_LAST =", begin)) + 1
    statements = original[begin:end]
    assert sha256(statements).hexdigest() == "82a813f7a7d47f883310859fd6c75086b13df5e7eb007d84adc9858c6c43912d"
    return ("class PrimeTableProbe {\n" + statements.decode("utf-8") +
            """
    static int run() {
        int hash = 0;
        for (final int p : PRIMES) { hash = hash * 31 + p; }
        if (hash != 1437380817 || PRIMES.length != 512 || PRIMES_LAST != 3671) return 1;
        PRIMES[PRIMES.length - 1] = 41;
        if (PRIMES_LAST != 3671) return 2;
        return PRIMES[511] + 1;
    }
}""")


def test_native_fields_are_active_use_nonconstant_and_real_object(monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    program=compile_sources(SOURCE,entry_class="Entry")
    assert program.managed_runtime
    module=str(program.module)
    assert "pcc_rt_java_class_enter" in module and "pcc_rt_java_class_publish" in module
    # Only Table, Cold and IndexSource have runtime fields; compile-time constants do not add guards.
    assert module.count("define internal void") == 3
    assert program.emit_object()[:4] == b"\x7fELF"


@pytest.mark.parametrize("body,code", [
    ("static final int[] X={unknown()};static int unknown(){return 2;}", "static-initialization"),
    ("static final int[] X=new int[-1];", "static-initialization"),
    ("static final int[] X=new int[2]{3};", None),
    ("static final int[] X=new short[]{2};", None),
    ("static final int[] X=A.Y;static final int[] Y={2};", "static-initialization"),
    ("static final int[] X=null;", "static-initialization"),
    ("static int[] X={1};", "array-storage"),
    ("static final int[] X={1};static final int K=X[1];", "array-index-exception"),
    ("static final int[] X={1};static final int K=X[X.length];", "array-index-exception"),
    ("static final int[] X={1};static final int K=run();", "static-initialization"),
    ("static final int[] X=Y;static final int[] Y={2};", None),
    ("static final int[] X={1};static final int K=X[0]/X[0];", "arithmetic-exception"),
])
def test_unsupported_class_initialization_never_enters_codegen(body, code, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *_: pytest.fail("unsupported declaration reached IR"))
    with pytest.raises(JavaError) as caught:
        compile_source("class A{"+body+"static int run(){return 42;}}", filename="A.java")
    if code is None:
        assert type(caught.value) is JavaError
    else:
        assert isinstance(caught.value, UnsupportedJava) and caught.value.code == "java.unsupported." + code


def test_local_name_shadowing_an_array_does_not_read_the_class_field():
    with pytest.raises(JavaError) as caught:
        compile_source("class A{static final int[] VALUE={42};static int run(){int VALUE=5;return VALUE.length;}}")
    assert type(caught.value) is JavaError


def test_scalar_constant_does_not_gain_static_managed_storage():
    c=compile_source("class A{static final int X=40+2;static int run(){return X;}}")
    assert not c.managed_runtime and "pcc_rt_java_class_enter" not in str(c.module)

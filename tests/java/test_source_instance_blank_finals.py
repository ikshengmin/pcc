"""JLS 8.3.1.2 and 16.9: blank instance finals, not a managed object ABI."""
from pathlib import Path
import os

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources, compile_source
from tests.java.test_source_array_model import check_originals_reference
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner


def original(members, body="return x;"):
    return "class ArrayExample{" + members + "int run(){" + body + "}}"


PROGRAMS = [
    ("implicit-field", original("final int x;ArrayExample(){x=42;}")),
    ("field-parameter-shadow", original("final int x;ArrayExample(){this(42);}ArrayExample(int x){this.x=x;}")),
    ("multiple-fields", original("final int x,y;ArrayExample(){x=7;y=35;}", "return x+y;")),
    ("grouped-this", original("final int x;ArrayExample(){((this).x)=42;}")),
    ("all-conditional-arms", original("final int x;ArrayExample(){this(false);}ArrayExample(boolean b){if(b)x=7;else x=42;}")),
    ("ordered-initializers", original("final int x,y;{x=7;}int intermediate=x;{y=intermediate+35;}", "return y;")),
    ("forward-initializer-write", original("{x=42;}final int x;")),
    ("initializer-delegation-once", original("final int x;{x=42;}ArrayExample(){this(0);}ArrayExample(int ignored){}")),
    ("explicit-super", original("final int x;{x=42;}ArrayExample(){super();}")),
    ("return-after-assignment", original("final int x;ArrayExample(){this(true);}ArrayExample(boolean b){if(b){x=42;return;}x=7;}")),
    ("exceptional-exit", original("final int x;ArrayExample(){this(false);}ArrayExample(boolean b){if(b)throw null;x=42;}")),
    ("vacuous-return", original("final int x;ArrayExample(){if(false)return;x=42;}")),
    ("while-break", original("final int x;ArrayExample(){while(true){x=42;break;}}")),
    ("do-once", original("final int x;ArrayExample(){do{x=42;}while(false);}")),
    ("label-break", original("final int x;ArrayExample(){outer:{x=42;break outer;}}")),
    ("after-loop-read", original("final int x;int sum;ArrayExample(){x=7;for(int n:new int[]{10,25}){sum+=n;}}",
                                 "return x+sum;")),
    ("vacuous-conditional-read", original("final int x;int y=true?42:this.x;ArrayExample(){x=y;}")),
    ("short-circuit-vacuous-read", original("final int x;ArrayExample(){if(false&&this.x>0){}x=42;}")),
    ("switch-paths", original("final int x;ArrayExample(){this(1);}ArrayExample(int n){switch(n){case 1:x=42;break;default:x=7;}}")),
    ("array-reference", original("final int[] x;ArrayExample(){x=new int[]{7};x[0]=42;}", "return x[0];")),
    ("nominal-reference", original("final ArrayExample x;ArrayExample(){this.x=this;}", "return x==this?42:7;")),
    ("boolean-long-fields", original("final boolean b;final long x;ArrayExample(){b=true;x=42L;}", "return b?(int)x:7;")),
    # Java DA is intraprocedural; an ordinary method called during construction
    # can observe the default value of a final field before its assignment.
    ("method-read-before-write", original("int before;final int x;ArrayExample(){before=peek();x=42;}int peek(){return x;}",
                                          "return before+x;")),
]

VALID_ONLY = [
    ("always-throws", original("final int x;ArrayExample(){throw null;}")),
    ("constant-abrupt", original("final int x;ArrayExample(){if(true)throw null;}")),
    ("delegation-to-throw", original("final int x;ArrayExample(){this(1);}ArrayExample(int n){throw null;}")),
]

DA = "not definitely assigned"
DU = "not definitely unassigned"
FINAL = "cannot assign a final"
INVALID = [
    ("implicit-missing", original("final int x;"), DA),
    ("explicit-missing", original("final int x;ArrayExample(){}"), DA),
    ("one-missing-constructor", original("final int x;ArrayExample(){x=42;}ArrayExample(int n){}"), DA),
    ("missing-arm", original("final int x;ArrayExample(boolean b){if(b)x=42;}"), DA),
    ("missing-return-path", original("final int x;ArrayExample(boolean b){if(b)return;x=42;}"), DA),
    ("before-write-read", original("final int x;int y;ArrayExample(){y=x;x=42;}"), DA),
    ("before-write-this", original("final int x;int y;ArrayExample(){y=(this).x;x=42;}"), DA),
    ("self-assignment", original("final int x;ArrayExample(){x=x;}"), DA),
    ("initializer-read", original("final int x;int y=this.x;ArrayExample(){x=42;}"), DA),
    ("before-array-assignment", original("final int[] x;ArrayExample(){x[0]=42;x=new int[1];}", "return x[0];"), DA),
    ("twice", original("final int x;ArrayExample(){x=7;x=42;}"), DU),
    ("loop-backedge", original("final int x;ArrayExample(boolean b){while(b){x=42;}}"), DU),
    ("continue-backedge", original("final int x;ArrayExample(boolean b){while(b){x=42;continue;}}"), DU),
    ("for-update-backedge", original("final int x;ArrayExample(){for(int n=0;n<1;x=42){n++;}}"), DU),
    ("enhanced-for-backedge", original("final int x;ArrayExample(){for(int n:new int[]{42}){x=n;}}"), DU),
    ("switch-missing-default", original("final int x;ArrayExample(int n){switch(n){case 1:x=42;break;}}"), DA),
    ("short-circuit-live-read", original("final int x;ArrayExample(boolean b){if(b&&this.x>0){}x=42;}"), DA),
    ("false-branch-still-du", original("final int x;ArrayExample(){if(false)x=7;x=42;}"), DU),
    ("twice-initializers", original("final int x;{x=7;}{x=42;}"), DU),
    ("constructor-after-initializer", original("final int x;{x=7;}ArrayExample(){x=42;}"), DU),
    ("delegating-write", original("final int x;ArrayExample(){this(1);x=42;}ArrayExample(int n){x=n;}"), DU),
    ("method-write", original("final int x;ArrayExample(){x=7;}void reset(){x=42;}"), FINAL),
    ("compound-write", original("final int x;ArrayExample(){x=7;x+=35;}"), FINAL),
    ("increment-write", original("final int x;ArrayExample(){x=7;x++;}"), FINAL),
    ("array-rebind", original("final int[] x;ArrayExample(){x=new int[]{42};x=null;}", "return 42;"), DU),
    ("nominal-rebind", original("final ArrayExample x;ArrayExample(){x=this;x=null;}", "return 42;"), DU),
]


@pytest.mark.parametrize("name,text", PROGRAMS + VALID_ONLY, ids=[n for n, _ in PROGRAMS + VALID_ONLY])
def test_blank_instance_final_originals_have_checked_constructors(name, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "lower", lambda *a: pytest.fail("unqualified object reached native lowering"))
    original_inputs = {"ArrayExample.java": text}
    model = analyze_class_sources(original_inputs).models["ArrayExample"]
    assert original_inputs == {"ArrayExample.java": text}
    assert not model.runtime_registered
    assert all(constructor.constructor_target is not None for constructor in model.constructors)
    assert all(slot.field.constant is None for slot in model.fields if "final" in slot.field.modifiers)
    # This semantic change cannot itself register the managed storage ABI.
    with pytest.raises(UnsupportedJava):
        compile_source(text, filename="ArrayExample.java")


@pytest.mark.parametrize("name,text,message", INVALID, ids=[n for n, _, _ in INVALID])
def test_invalid_blank_instance_final_originals_reject_as_java_errors(name, text, message):
    with pytest.raises(JavaError, match=message) as caught:
        analyze_class_sources({"ArrayExample.java": text})
    assert type(caught.value) is JavaError and caught.value.code is None


def test_bound_final_field_identity_is_not_a_local_or_a_folded_value():
    text = dict(PROGRAMS)["field-parameter-shadow"]
    model = analyze_class_sources({"ArrayExample.java": text}).models["ArrayExample"]
    field = model.fields[0].field
    constructors = {item.descriptor: item for item in model.constructors}
    assignment, = constructors["(I)V"].body.children
    assert assignment.kind == "field_assign" and assignment.symbol is field
    assert assignment.children[0].symbol is constructors["(I)V"].locals[0]
    assert constructors["()V"].constructor_target is constructors["(I)V"]
    read = model.source.methods[0].body.children[0].children[0]
    assert read.kind == "instance_field" and read.symbol is field and read.constant is None


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if os.environ.get("PCC_JAVA_RAW_REFERENCE") else
                     "select an existing development JDK with PCC_JAVA_RAW_REFERENCE")
@pytest.mark.xdist_group(name="java-instance-blank-final-reference")
def test_exact_blank_instance_final_originals_against_selected_jdk(tmp_path, request):
    check_originals_reference(tmp_path, request, PROGRAMS, VALID_ONLY, INVALID,
                              invocation="new ArrayExample().run()", source_input=Path(__file__))
    for name, _, _ in INVALID:
        directory = tmp_path / name
        assert (directory / "compiler-status.txt").read_text() == "1"
        assert b"ArrayExample.java:" in (directory / "compiler.stderr.bin").read_bytes()

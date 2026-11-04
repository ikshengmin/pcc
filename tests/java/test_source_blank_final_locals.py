"""Native primitive blank-final locals, with separate DA and DU obligations.

JLS21 4.12.3-4, 14.4, 14.11-14.16, 15.26, and 16.1-16.2:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-4.html#jls-4.12.3
https://docs.oracle.com/javase/specs/jls/se21/html/jls-4.html#jls-4.12.4
https://docs.oracle.com/javase/specs/jls/se21/html/jls-14.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.26
https://docs.oracle.com/javase/specs/jls/se21/html/jls-16.html

These are source-authored CI obligations, not observed execution results.
Complete original units are shared unchanged by native PCC and the selected
development JDK. A later assignment does not make a constant variable. Model
methods, constructors, instance initializers and enhanced-for bodies retain the
explicit blank-final-local boundary until their own DU owner exists. Static
initializers retain their earlier class-initialization boundary.
This corpus neither rewrites nor substitutes for Commons Numbers project inputs.
"""
import hashlib
import json
import os
from pathlib import Path
import re

import pytest

from pcc.frontends.java import (
    JavaError,
    UnsupportedJava,
    analyze_class_sources,
    compile_source,
    compile_sources,
)
from pcc.frontends.java.artifact import method_symbol
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_local_inference import WIDTHS, observer_source
from tests.java.test_source_narrow_primitives import descriptor
from tests.java.test_source_raw_reference import (
    JVM_LIMITS,
    digest,
    record_process,
    tool_identity,
)
from tests.java.test_source_scalars import NATIVE_REASON


def original(body, result="int", parameters="", helpers=""):
    return ("class BlankFinal{" + helpers + "static " + result + " run("
            + parameters + "){" + body + "}}")


def source_owner(text):
    return re.search(r"\bclass\s+(\w+)", text).group(1)


def compile_original(text, lane, arguments, api):
    owner = source_owner(text)
    if api == "compile_source":
        return compile_source(text, filename=owner + ".java", descriptor=descriptor(lane, arguments))
    assert api == "compile_sources"
    return compile_sources({owner + ".java": text}, entry_class=owner, descriptor=descriptor(lane, arguments))


# Each row retains a whole input program, an explicit ABI call, and an exact
# result. Floating-point expected values are unsigned raw IEEE bits.
CASES = [
    ("boolean-local", original("final boolean value;value=true;return value;", "boolean"), "Z", (), 1),
    ("byte-local", original("final byte value;value=-128;return value;", "byte"), "B", (), -128),
    ("short-local", original("final short value;value=-32768;return value;", "short"), "S", (), -32768),
    ("char-local", original("final char value;value=65535;return value;", "char"), "C", (), 65535),
    ("int-local", original("final int value;value=-2147483648;return value;"), "I", (), -2147483648),
    ("long-local", original("final long value;value=0x123456789abcdef0L;return value;", "long"), "J", (), 1311768467463790320),
    ("float-local", original("final float value;value=42.0f;return value;", "float"), "F", (), 0x42280000),
    ("double-local", original("final double value;value=42.0d;return value;", "double"), "D", (), 0x4045000000000000),
    ("float-negative-zero", original("final float value;value=-0.0f;return value;", "float"), "F", (), 0x80000000),
    ("double-negative-zero", original("final double value;value=-0.0d;return value;", "double"), "D", (), 0x8000000000000000),
    ("float-parameter-bits", original("final float value;value=input;return value;", "float", "float input"), "F", (("F", 0x42280000),), 0x42280000),
    ("double-parameter-bits", original("final double value;value=input;return value;", "double", "double input"), "D", (("D", 0x4045000000000000),), 0x4045000000000000),
    ("both-branches-true", original("final int x;if(flag)x=42;else x=7;return x;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("both-branches-false", original("final int x;if(flag)x=42;else x=7;return x;", parameters="boolean flag"), "I", (("Z", 0),), 7),
    ("paired-branches", original("final int a,b;if(flag){a=7;b=35;}else{a=21;b=21;}return a+b;", parameters="boolean flag"), "I", (("Z", 0),), 42),
    ("boolean-branches", original("final boolean same;if(a==b)same=true;else same=false;return same;", "boolean", "int a,int b"), "Z", (("I", 7), ("I", 7)), 1),
    ("sole-normal-branch", original("final int x;if(flag)x=42;else return 7;return x;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("abrupt-assigned-branch", original("final int x;if(flag){x=7;return x;}x=42;return x;", parameters="boolean flag"), "I", (("Z", 0),), 42),
    ("nested-branches", original("final int x;if(a){if(b)x=7;else x=42;}else{x=3;}return x;", parameters="boolean a,boolean b"), "I", (("Z", 1), ("Z", 0)), 42),
    ("conditional-arm-writes-true", original("final int x;int y=flag?(x=21):(x=7);return x+y;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("conditional-arm-writes-false", original("final int x;int y=flag?(x=21):(x=7);return x+y;", parameters="boolean flag"), "I", (("Z", 0),), 14),
    ("conditional-rhs", original("final int x;x=flag?42:7;return x;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("rhs-effects-once", original("int n=0;final int x;x=twice(++n);return n==1?x+40:0;", helpers="static int twice(int n){return n*2;}"), "I", (), 42),
    ("chained-distinct-finals", original("final int a,b;a=b=21;return a+b;"), "I", (), 42),
    ("later-initializer-assigns-earlier", original("final int a,b=(a=21);return a+b;"), "I", (), 42),
    ("mixed-initialized-and-blank", original("final int a=7,b;b=35;return a+b;"), "I", (), 42),
    ("three-declarators", original("final int a,b,c;a=7;b=a*2;c=a+b;return a+b+c;"), "I", (), 42),
    ("grouped-assignment-target", original("final int x;((x))=42;return x;"), "I", (), 42),
    ("call-argument-order", original("final int x;return add(x=21,x);", helpers="static int add(int a,int b){return a+b;}"), "I", (), 42),
    ("arithmetic-sibling-order", original("final int x;return (x=21)+x;"), "I", (), 42),
    ("blank-shadows-constant-field", original("final int VALUE;VALUE=BlankFinal.VALUE;return VALUE;", helpers="static final int VALUE=42;"), "I", (), 42),
    ("short-circuit-and-true", original("final int x;if(flag&&((x=40)>0))return x+2;return 7;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("short-circuit-and-skipped", original("final int x;if(flag&&((x=40)>0))return x+2;return 7;", parameters="boolean flag"), "I", (("Z", 0),), 7),
    ("short-circuit-or-false", original("final int x;if(flag||((x=40)<0))return 7;return x+2;", parameters="boolean flag"), "I", (("Z", 0),), 42),
    ("correlated-or-true-is-du", original("final int x;if(flag||(((x=7)>0)&&false)){x=42;return x;}return 7;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("correlated-or-other-arm", original("final int x;if(flag||(((x=7)>0)&&false)){x=42;return x;}return 7;", parameters="boolean flag"), "I", (("Z", 0),), 7),
    ("correlated-and-false-is-du", original("final int x;if(flag&&(((x=7)>0)||true))return 7;x=42;return x;", parameters="boolean flag"), "I", (("Z", 0),), 42),
    ("correlated-conditional-is-du", original("final int x;if(flag?true:(((x=7)>0)&&false)){x=42;return x;}return 7;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("structural-false-and-abrupt-arm", original("final int x;if(flag&&false){x=7;return x;}x=42;return x;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("structural-false-negation-abrupt-arm", original("final int x;if(!(flag||true)){x=7;return x;}x=42;return x;", parameters="boolean flag"), "I", (("Z", 0),), 42),
    ("structural-false-conditional-abrupt-arm", original("final int x;if(flag?false:false){x=7;return x;}x=42;return x;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("unreachable-if-write-is-vacuous", original("final int x;x=42;if(false)x=7;return x;"), "I", (), 42),
    ("structural-and-write-is-vacuous", original("final int x;x=42;if(flag&&false)x=7;return x;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("structural-negated-write-is-vacuous", original("final int x;x=42;if(!(flag||true))x=7;return x;", parameters="boolean flag"), "I", (("Z", 0),), 42),
    ("structural-conditional-write-is-vacuous", original("final int x;x=42;if(flag?false:false)x=7;return x;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("while-break", original("final int x;while(true){x=42;break;}return x;"), "I", (), 42),
    ("while-backedges-before-single-write", original("final int x;int i=0;while(true){if(++i<3)continue;x=42;break;}return x;"), "I", (), 42),
    ("while-return-or-tail", original("final int x;while(flag){x=7;return x;}x=42;return x;", parameters="boolean flag"), "I", (("Z", 0),), 42),
    ("do-single-iteration", original("final int x;do{x=42;}while(false);return x;"), "I", (), 42),
    ("do-continue-to-false-test", original("final int x;do{x=42;continue;}while(false);return x;"), "I", (), 42),
    ("do-backedges-before-single-write", original("final int x;int i=0;do{if(++i<3)continue;x=42;break;}while(true);return x;"), "I", (), 42),
    ("do-branch-break", original("final int x;do{if(flag){x=7;break;}x=42;}while(false);return x;", parameters="boolean flag"), "I", (("Z", 0),), 42),
    ("do-condition-structural-false", original("final int x;do{}while(((x=42)<0)&&false);return x;"), "I", (), 42),
    ("while-condition-structural-false", original("final int x;while(((x=42)>0)&&false){}return x;"), "I", (), 42),
    ("for-initialize-then-break", original("final int x;for(;;){x=42;break;}return x;"), "I", (), 42),
    ("for-update-before-single-write", original("final int x;for(int i=0;;i++){if(i<3)continue;x=42;break;}return x;"), "I", (), 42),
    ("for-blank-initializer", original("for(final int x;;){x=42;return x;}"), "I", (), 42),
    ("fresh-while-body", original("int i=0,total=0;while(i<3){final int x;x=++i;total+=x;}return total+36;"), "I", (), 42),
    ("fresh-do-body", original("int i=0,total=0;do{final int x;x=++i;total+=x;}while(i<3);return total+36;"), "I", (), 42),
    ("fresh-for-body", original("int total=0;for(int i=0;i<3;i++){final int x;x=i;total+=x;}return total+39;"), "I", (), 42),
    ("fresh-body-continue", original("int total=0;for(int i=0;i<4;i++){final int x;if(i==2)continue;x=i;total+=x;}return total+38;"), "I", (), 42),
    ("fresh-nested-body", original("int total=0;for(int i=0;i<2;i++){for(int j=0;j<3;j++){final int x;x=i+j;total+=x;}}return total+33;"), "I", (), 42),
    ("labeled-break-outer", original("final int x;outer:while(true){while(true){x=42;break outer;}}return x;"), "I", (), 42),
    ("labeled-continue-fresh-outer-local", original("int total=0;outer:for(int i=0;i<3;i++){final int x;for(;;){x=i;total+=x;continue outer;}}return total+39;"), "I", (), 42),
    ("labeled-block-join", original("final int x;done:{if(flag){x=40;break done;}x=7;}return x+2;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("reused-label-identity", original("final int a,b;done:{a=7;break done;}done:{b=35;break done;}return a+b;"), "I", (), 42),
    ("switch-colon-first", original("final int x;switch(n){case 0:x=42;break;case 1:x=7;break;default:x=3;}return x;", parameters="int n"), "I", (("I", 0),), 42),
    ("switch-colon-default", original("final int x;switch(n){case 0:x=42;break;case 1:x=7;break;default:x=3;}return x;", parameters="int n"), "I", (("I", 9),), 3),
    ("switch-arrow", original("final int x;switch(n){case 0->x=42;case 1->x=7;default->x=3;}return x;", parameters="int n"), "I", (("I", 0),), 42),
    ("switch-arrow-blocks", original("final int a,b;switch(n){case 0->{a=7;b=35;}default->{a=21;b=21;}}return a+b;", parameters="int n"), "I", (("I", 0),), 42),
    ("switch-fallthrough-before-write", original("final int x;switch(n){case 0:;case 1:x=42;break;default:x=7;}return x;", parameters="int n"), "I", (("I", 0),), 42),
    ("switch-return-or-tail", original("final int x;switch(n){case 0:x=7;return x;}x=42;return x;", parameters="int n"), "I", (("I", 1),), 42),
    ("switch-break-inside-loop", original("int total=0;for(int i=0;i<3;i++){final int x;switch(i){case 0:x=7;break;case 1:x=14;break;default:x=21;}total+=x;}return total;"), "I", (), 42),
    ("switch-continue-before-write", original("int total=0;for(int i=0;i<3;i++){final int x;switch(i){case 0:continue;default:x=21;}total+=x;}return total;"), "I", (), 42),
    ("fresh-switch-scope-dispatch-bypasses-declarator", original("int total=0;for(int i=0;i<2;i++){switch(i){case 0:final int x;case 1:x=21;total+=x;break;default:break;}}return total;"), "I", (), 42),
    ("fresh-switch-scope-grouped-declarators", original("int total=0;for(int i=0;i<2;i++){switch(i){case 0:final int a,b;case 1:a=7;b=14;total+=a+b;break;default:break;}}return total;"), "I", (), 42),
    ("disjoint-lexical-bindings", original("int total=0;{final int x;x=7;total+=x;}{final int x;x=35;total+=x;}return total;"), "I", (), 42),
    # These exact former gap inputs must not be replaced by nearby rewrites.
    ("original-final-locals-gap", "class FinalLocals{static int run(){final int x;if(true)x=42;else x=7;return x;}}", "I", (), 42),
    ("original-unused-diagnostic-gap", "class Diagnostic{static int run(){final int value;return 42;}}", "I", (), 42),
    ("original-inferred-gap", "class Inferred{static int run(){final int value;value=42;return value;}}", "I", (), 42),
]


DU = "definitely unassigned"
DA = "not definitely assigned"
INVALID = [
    ("second-simple-write", original("final int x;x=7;x=42;return x;"), DU),
    ("not-da-is-not-du", original("final int x;if(flag)x=7;x=42;return x;", parameters="boolean flag"), DU),
    ("already-assigned-in-both-branches", original("final int x;if(flag)x=7;else x=3;x=42;return x;", parameters="boolean flag"), DU),
    ("outer-store-after-rhs-write", original("final int x;x=(x=42);return x;"), DU),
    ("outer-store-after-conditional-rhs-write", original("final int x;x=flag?(x=7):42;return x;", parameters="boolean flag"), DU),
    ("repeated-call-arguments", original("final int x;return add(x=21,x=21);", helpers="static int add(int a,int b){return a+b;}"), DU),
    ("repeated-binary-operands", original("final int x;return (x=21)+(x=21);"), DU),
    ("conditional-test-then-arm-write", original("final int x;return ((x=1)>0)?(x=42):7;"), DU),
    ("earlier-declarator-already-assigned", original("final int a,b=(a=7),c=(a=35);return b+c;"), DU),
    ("short-circuit-and-may-have-written", original("final int x;if(flag&&((x=7)>0))return x;x=42;return x;", parameters="boolean flag"), DU),
    ("short-circuit-or-may-have-written", original("final int x;if(flag||((x=7)>0)){x=42;return x;}return 0;", parameters="boolean flag"), DU),
    ("boolean-assignment-loses-true-correlation", original("final int x;boolean b;if(b=(flag||(((x=7)>0)&&false))){x=42;return x;}return 7;", parameters="boolean flag"), DU),
    ("boolean-assignment-loses-false-correlation", original("final int x;boolean b;if(b=(flag&&(((x=7)>0)||true)))return 7;x=42;return x;", parameters="boolean flag"), DU),
    ("boolean-cast-loses-correlation", original("final int x;if((boolean)(flag||(((x=7)>0)&&false))){x=42;return x;}return 7;", parameters="boolean flag"), DU),
    ("boolean-assignment-loses-vacuous-top", original("final int x;x=42;boolean b;if(b=(flag&&false))x=7;return x;", parameters="boolean flag"), DU),
    ("boolean-cast-loses-vacuous-top", original("final int x;x=42;if((boolean)(flag&&false))x=7;return x;", parameters="boolean flag"), DU),
    ("eager-and-not-short-circuit", original("final int x;if(flag&false)x=7;x=42;return x;", parameters="boolean flag"), DU),
    ("vacuous-if-arm-still-removes-du", original("final int x;if(false)x=7;x=42;return x;"), DU),
    ("vacuous-and-arm-still-removes-du", original("final int x;if(flag&&false)x=7;x=42;return x;", parameters="boolean flag"), DU),
    ("vacuous-negated-arm-still-removes-du", original("final int x;if(!(flag||true))x=7;x=42;return x;", parameters="boolean flag"), DU),
    ("vacuous-conditional-arm-still-removes-du", original("final int x;if(flag?false:false)x=7;x=42;return x;", parameters="boolean flag"), DU),
    ("while-backedge-write", original("final int x;while(flag){x=42;}return 42;", parameters="boolean flag"), DU),
    ("while-continue-backedge", original("final int x;while(flag){x=42;continue;}return 42;", parameters="boolean flag"), DU),
    ("do-backedge-write", original("final int x;do{x=42;}while(flag);return x;", parameters="boolean flag"), DU),
    ("do-condition-assignment-is-not-constant", original("final boolean x;do{}while(x=false);return 42;"), DU),
    ("while-condition-repeats-write", original("final int x;while((x=42)>0){}return 42;"), DU),
    ("for-body-backedge", original("final int x;for(int i=0;i<2;i++){x=42;}return 42;"), DU),
    ("for-one-iteration-is-not-flow-proof", original("final int x;for(int i=0;i<1;i++){x=42;}return 42;"), DU),
    ("for-update-backedge", original("final int x;for(int i=0;i<2;i++,x=i){}return 42;"), DU),
    ("for-continue-reaches-update", original("final int x;for(int i=0;i<2;x=42,i++){continue;}return 42;"), DU),
    ("for-body-then-update-write", original("final int x;for(int i=0;i<2;x=7,i++){x=42;}return 42;"), DU),
    ("disconnected-update-affects-tail", original("final int x;for(;;x=1){break;}x=2;return x;"), DU),
    ("disconnected-update-affects-body", original("final int x;for(;;x=7){x=42;break;}return x;"), DU),
    ("disconnected-do-condition-affects-tail", original("final int x;do{break;}while(((x=1)>0)&&false);x=2;return x;"), DU),
    ("for-blank-header-is-not-fresh-body", original("for(final int x;;){x=42;continue;}"), DU),
    ("inner-break-keeps-outer-backedge", original("final int x;while(flag){while(true){x=42;break;}}return 42;", parameters="boolean flag"), DU),
    ("labeled-continue-is-outer-backedge", original("final int x;outer:while(flag){while(true){x=42;continue outer;}}return 42;", parameters="boolean flag"), DU),
    ("labeled-block-maybe-assigned", original("final int x;done:{if(flag){x=7;break done;}}x=42;return x;", parameters="boolean flag"), DU),
    ("fresh-body-still-cannot-write-twice", original("for(int i=0;i<3;i++){final int x;x=i;x=42;}return 42;"), DU),
    ("switch-colon-fallthrough-double-write", original("final int x;switch(n){case 0:x=7;case 1:x=42;break;default:x=3;}return x;", parameters="int n"), DU),
    ("switch-scope-fallthrough-does-not-reset", original("int total=0;for(int i=0;i<2;i++){switch(i){case 0:final int x;x=7;case 1:x=21;total+=x;break;default:break;}}return total;"), DU),
    ("switch-possible-write-before-tail", original("final int x;switch(n){case 0:x=7;break;default:break;}x=42;return x;", parameters="int n"), DU),
    ("switch-arrow-possible-write-before-tail", original("final int x;switch(n){case 0->x=7;default->{}}x=42;return x;", parameters="int n"), DU),
    ("switch-break-does-not-exit-loop", original("final int x;while(flag){switch(n){case 0:x=42;break;default:break;}}return 42;", parameters="boolean flag,int n"), DU),
    ("read-never-assigned", original("final int x;return x;"), DA),
    ("read-only-one-branch-assigned", original("final int x;if(flag)x=42;return x;", parameters="boolean flag"), DA),
    ("read-short-circuit-might-skip", original("final int x;boolean b=flag&&((x=42)>0);return x;", parameters="boolean flag"), DA),
    ("boolean-assignment-does-not-create-da", original("final int x;boolean b;if(b=(flag&&((x=42)>0)))return x;return 7;", parameters="boolean flag"), DA),
    ("rhs-read-before-first-store", original("final int x;x=x+1;return x;"), DA),
    ("argument-read-precedes-assignment", original("final int x;return add(x,x=42);", helpers="static int add(int a,int b){return a+b;}"), DA),
    ("while-may-not-run", original("final int x;while(flag){x=42;break;}return x;", parameters="boolean flag"), DA),
    ("do-break-can-bypass-assignment", original("final int x;do{if(flag)break;x=42;}while(false);return x;", parameters="boolean flag"), DA),
    ("switch-no-default-not-da", original("final int x;switch(n){case 0:x=42;break;}return x;", parameters="int n"), DA),
    ("switch-dispatch-skips-write", original("final int x;switch(n){case 0:x=42;case 1:return x;default:return 7;}", parameters="int n"), DA),
    ("blank-final-is-not-case-constant", original("final int x;x=42;switch(42){case x:return 42;default:return 0;}"), "constant expression"),
    ("blank-final-is-not-narrowing-constant", original("final int x;x=42;byte b=x;return b;"), "incompatible assignment"),
    ("blank-final-boolean-is-not-constant", original("final boolean never;never=false;int x;if(never)return x;return 42;"), DA),
    ("blank-final-true-is-not-endless-proof", original("final boolean always;always=true;while(always){return 42;}"), "missing return"),
    ("blank-byte-range-checked", original("final byte x;x=128;return x;"), "incompatible assignment"),
    ("blank-boolean-type-checked", original("final boolean x;x=42;return 42;"), "boolean value"),
    ("compound-before-initialization", original("final int x;x+=42;return x;"), "final"),
    ("compound-after-initialization", original("final int x;x=7;x+=35;return x;"), "final"),
    ("prefix-increment", original("final int x;x=41;return ++x;"), "final"),
    ("postfix-increment", original("final int x;x=42;return x++;"), "final"),
    ("prefix-decrement", original("final int x;x=43;return --x;"), "final"),
    ("postfix-decrement", original("final int x;x=42;return x--;"), "final"),
    ("boolean-compound", original("final boolean x;x=true;x^=false;return 42;"), "final"),
    ("initialized-final-still-final", original("final int x=7;x=42;return x;"), "final local"),
    ("initialized-final-dead-write-still-rejected", original("final int x=42;if(false)x=7;return x;"), "final local"),
    ("final-parameter-still-final", original("x=42;return x;", parameters="final int x"), "final parameter"),
    ("final-parameter-increment", original("return ++x;", parameters="final int x"), "final parameter"),
    ("self-initializer-is-not-blank", original("final int x=(x=42);return x;"), "final local"),
    ("duplicate-local-still-rejected", original("final int x;int x=42;return x;"), "already in scope"),
]


PROJECTS = [
    ("cross-unit-narrow-overloads", {
        "Main.java": "class Main{static int run(){final int a,b;a=Other.pick((byte)21);b=Other.pick((char)21);return a+b;}}",
        "Other.java": "class Other{static int pick(byte input){final byte value;value=input;return value;}static int pick(char input){final char value;value=input;return value;}}",
    }, "Main", {("Other", "pick", "(B)I"), ("Other", "pick", "(C)I")}),
    ("cross-package-branch-complete", {
        "Main.java": "package app;import support.Choice;public class Main{public static int run(){final int value;value=Choice.choose(false);return value;}}",
        "Choice.java": "package support;public class Choice{public static int choose(boolean flag){final int a,b;if(flag){a=7;b=35;}else{a=21;b=21;}return a+b;}}",
    }, "app.Main", {("support/Choice", "choose", "(Z)I")}),
    ("single-static-import", {
        "Main.java": "import static support.Values.answer;class Main{static int run(){final int value;value=answer();return value;}}",
        "Values.java": "package support;public class Values{public static int answer(){final int value;do{value=42;}while(false);return value;}}",
    }, "Main", {("support/Values", "answer", "()I")}),
    ("uncalled-valid-method-retained", {
        "Main.java": "class Main{static int run(){final int value;value=42;return value;}}",
        "Other.java": "class Other{static int unused(boolean flag){final int value;if(flag)value=7;else value=42;return value;}}",
    }, "Main", {("Other", "unused", "(Z)I")}),
]

INVALID_PROJECTS = [
    ("uncalled-helper-double-write", {
        "Main.java": "class Main{static int run(){return 42;}}",
        "Other.java": "class Other{static int unused(){final int value;value=7;value=42;return value;}}",
    }, DU),
    ("uncalled-helper-unassigned-read", {
        "Main.java": "class Main{static int run(){return 42;}}",
        "Other.java": "class Other{static int unused(){final int value;return value;}}",
    }, DA),
]


MODEL_BOUNDARIES = [
    ("model-static-method", original("final int x;x=42;return x;"), "java.unsupported.blank-final-local"),
    ("model-instance-method", "class BlankFinal{int run(){final int x;x=42;return x;}}", "java.unsupported.blank-final-local"),
    ("model-constructor", "class BlankFinal{BlankFinal(){final int x;x=42;}}", "java.unsupported.blank-final-local"),
    ("model-instance-initializer", "class BlankFinal{{final int x;x=42;}}", "java.unsupported.blank-final-local"),
    # The initializer itself is the earlier explicit boundary, before its body.
    ("model-static-initializer", "class BlankFinal{static{final int x;x=42;}}", "java.unsupported.static-initialization"),
    ("model-enhanced-for-body", original("for(int item:new int[]{42}){final int x;x=item;}return 42;"), "java.unsupported.blank-final-local"),
    ("model-unused-local", original("final int x;return 42;"), "java.unsupported.blank-final-local"),
    ("model-grouped-declaration", original("final int a=7,b;b=35;return a+b;"), "java.unsupported.blank-final-local"),
    ("model-named-reference", original("final BlankFinal value;value=null;return 42;"), "java.unsupported.blank-final-local"),
    ("model-array-reference", original("final int[] value;value=new int[]{42};return value[0];"), "java.unsupported.blank-final-local"),
]


@pytest.mark.parametrize("name,text,lane,arguments,expected", CASES, ids=[row[0] for row in CASES])
@pytest.mark.parametrize("api", ("compile_source", "compile_sources"))
def test_blank_final_original_has_owned_ir_and_object(name, text, lane, arguments, expected, api, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    compiled = compile_original(text, lane, arguments, api)
    assert compiled.emit_object().startswith(b"\x7fELF")
    assert "bc_" not in str(compiled.module)
    assert "nsw" not in str(compiled.module) and "nuw" not in str(compiled.module)


@pytest.mark.parametrize("name,text,message", INVALID, ids=[row[0] for row in INVALID])
@pytest.mark.parametrize("api", ("compile_source", "compile_sources"))
def test_invalid_blank_final_original_fails_before_ir(name, text, message, api, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *a: pytest.fail("invalid blank final reached IR"))
    with pytest.raises(JavaError, match=message) as caught:
        compile_original(text, "I", (), api)
    assert type(caught.value) is JavaError and caught.value.code is None


@pytest.mark.parametrize("name,text,code", MODEL_BOUNDARIES, ids=[row[0] for row in MODEL_BOUNDARIES])
def test_class_model_blank_final_locals_remain_explicit(name, text, code, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *a: pytest.fail("class-model blank final reached IR"))
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"BlankFinal.java": text})
    assert caught.value.code == code


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def test_du_uses_existing_binding_and_typed_ast_without_retyping(monkeypatch):
    from pcc.frontends.java.source_project import SourceProject
    from pcc.frontends.java.source_sema import SourceAnalyzer
    from pcc.frontends.java.source_syntax import SourceParser
    text = original("int total=0;for(int i=0;i<3;i++){final byte x;x=(byte)i;total+=x;}return total+39;")
    program = SourceParser(text, "BlankFinal.java").parse()
    nodes_before = tuple(walk(program.methods[0].body))
    declared, typed = [], []
    declare = SourceAnalyzer.declare
    type_expression = SourceAnalyzer.type_expression

    def count_declaration(self, token, *args, **kwargs):
        declared.append(token.text)
        return declare(self, token, *args, **kwargs)

    def count_typing(self, node, assigned):
        typed.append(id(node))
        return type_expression(self, node, assigned)

    monkeypatch.setattr(SourceAnalyzer, "declare", count_declaration)
    monkeypatch.setattr(SourceAnalyzer, "type_expression", count_typing)
    SourceProject((program,)).analyze()
    method = program.methods[0]
    assert declared == ["total", "i", "x"]
    assert len(typed) == len(set(typed)), "DU must not retype loop bodies to reach a fixed point"
    assert [id(node) for node in walk(method.body)] == [id(node) for node in nodes_before]
    assert [binding.slot for binding in method.locals] == [0, 1, 2]
    blank = method.locals[2]
    assert blank.name == "x" and blank.dtype == "byte"
    assert blank.is_final and blank.is_blank_final
    accesses = [node for node in walk(method.body) if getattr(node, "symbol", None) is blank]
    assert len(accesses) >= 3
    assert next(node for node in accesses if node.kind == "var").children == []


def test_blank_binding_is_distinct_from_initialized_final_and_parameter():
    from pcc.frontends.java.source_project import SourceProject
    from pcc.frontends.java.source_syntax import SourceParser
    text = original("final int constant=2;final int value;value=input+constant;return value;", parameters="final int input")
    program = SourceParser(text, "BlankFinal.java").parse()
    SourceProject((program,)).analyze()
    parameter, constant, blank = program.methods[0].locals
    assert all(binding.is_final for binding in (parameter, constant, blank))
    assert [binding.is_blank_final for binding in (parameter, constant, blank)] == [False, False, True]
    result = program.methods[0].body.children[-1].children[0]
    assert result.symbol is blank and result.constant is None


def test_rhs_runtime_call_occurs_once_and_blank_reads_are_not_constant_folded(monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    text = original("final int x;x=twice(7);return x+x+x;", helpers="static int twice(int x){return x*2;}")
    ir = str(compile_source(text).module)
    symbol = method_symbol("BlankFinal", "twice", "(I)I")
    calls = [line for line in ir.splitlines() if "call i32" in line and symbol in line]
    assert len(calls) == 1
    assert sum("load i32" in line for line in ir.splitlines()) >= 3


@pytest.mark.parametrize("name,sources,entry,required", PROJECTS, ids=[row[0] for row in PROJECTS])
def test_blank_final_original_project_has_owned_object(name, sources, entry, required, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    before = dict(sources)
    compiled = compile_sources(sources, entry_class=entry)
    assert compiled.emit_object().startswith(b"\x7fELF")
    assert required <= set(compiled.emitted_class_methods)
    assert sources == before


@pytest.mark.parametrize("name,sources,message", INVALID_PROJECTS, ids=[row[0] for row in INVALID_PROJECTS])
def test_uncalled_invalid_project_method_fails_before_ir(name, sources, message, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *a: pytest.fail("invalid project reached IR"))
    with pytest.raises(JavaError, match=message) as caught:
        compile_sources(sources, entry_class="Main")
    assert type(caught.value) is JavaError and caught.value.code is None


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,text,lane,arguments,expected", CASES, ids=[row[0] for row in CASES])
@pytest.mark.parametrize("api", ("compile_source", "compile_sources"))
def test_blank_final_original_executes_exact_native_scalar(name, text, lane, arguments, expected, api, tmp_path, monkeypatch):
    unit = tmp_path / (source_owner(text) + ".java")
    unit.write_bytes(text.encode("utf-8"))
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_original(text, lane, arguments, api)
    assert observe_scalar(compiled, tmp_path, monkeypatch, arguments) == expected
    assert unit.read_bytes() == text.encode("utf-8")


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,sources,entry,required", PROJECTS, ids=[row[0] for row in PROJECTS])
def test_blank_final_original_project_executes_native(name, sources, entry, required, tmp_path, monkeypatch):
    before = dict(sources)
    for filename, text in sources.items():
        (tmp_path / filename).write_bytes(text.encode("utf-8"))
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_sources(sources, entry_class=entry)
    assert required <= set(compiled.emitted_class_methods)
    assert observe_scalar(compiled, tmp_path, monkeypatch) == 42
    assert sources == before
    assert all((tmp_path / filename).read_bytes() == text.encode("utf-8") for filename, text in sources.items())


JDK_REASON = None if os.environ.get("PCC_JAVA_RAW_REFERENCE") else "select an existing JDK with PCC_JAVA_RAW_REFERENCE"


def reference_owner(tmp_path, request):
    java = Path(os.environ["PCC_JAVA_RAW_REFERENCE"]).resolve(strict=True)
    level = os.environ.get("PCC_JAVA_RAW_REFERENCE_SOURCE", "21")
    assert level in {"17", "21"}
    version = re.search(r'^JAVA_VERSION="([^"]+)"', (java.parent.parent / "release").read_text(), re.MULTILINE)
    assert version and int(version.group(1).split(".")[0]) >= int(level)
    root = Path(__file__).parent
    inputs = tuple(root / name for name in (
        "test_source_blank_final_locals.py", "NarrowReference.java", "owned_execution.py",
        "test_source_local_inference.py", "test_source_narrow_primitives.py", "test_source_raw_reference.py"))
    before = {"tools": tool_identity(java), "inputs": {str(path): digest(path) for path in inputs}}
    retained = {}
    owner = {**before, "reference_owner": "explicit existing development-only JDK",
             "version": version.group(1), "source_level": level, "target_level": level,
             "platform_contract": "selected current JDK platform, not --release compatibility",
             "production_dependency": False}
    (tmp_path / "owner-before.json").write_text(json.dumps(owner, indent=2))

    def finish_identity():
        after = {"tools": tool_identity(java), "inputs": {str(path): digest(path) for path in inputs}}
        unchanged = all(digest(path) == expected for path, expected in retained.items())
        (tmp_path / "owner-post.json").write_text(json.dumps({**after,
            "original_observer_hashes": {str(path): checksum for path, checksum in retained.items()},
            "source_inputs_unchanged": unchanged}, indent=2))
        assert before == after and unchanged

    request.addfinalizer(finish_identity)
    assert record_process((str(java), *JVM_LIMITS, "--version"), tmp_path, "version")[0] == 0
    modules = record_process((str(java), *JVM_LIMITS, "--list-modules"), tmp_path, "modules")
    assert modules[0] == 0 and any(line.split(b"@")[0] == b"jdk.compiler" for line in modules[1].splitlines())
    return java, level, retained


def reference_batch(tmp_path, request, *, runtime):
    java, level, retained = reference_owner(tmp_path, request)
    rows = []
    for name, text, *rest in CASES if runtime else INVALID:
        directory = tmp_path / name
        directory.mkdir()
        unit = directory / (source_owner(text) + ".java")
        unit.write_bytes(text.encode("utf-8"))
        retained[unit] = digest(unit)
        if runtime:
            lane, arguments, _ = rest
            observer = directory / "PccNarrowObserve.java"
            observer.write_text(observer_source(source_owner(text), lane, arguments), encoding="utf-8")
            retained[observer] = digest(observer)
        rows.append(name + ("\trun\t" if runtime else "\treject\t") + unit.name)
    manifest = tmp_path / "cases.tsv"
    manifest.write_text("\n".join(rows) + "\n")
    retained[manifest] = digest(manifest)
    driver = tmp_path / "NarrowReference.java"
    driver.write_bytes(Path(__file__).with_name("NarrowReference.java").read_bytes())
    retained[driver] = digest(driver)
    classes = tmp_path / "driver-classes"
    classes.mkdir()
    compiler = record_process((str(java), *JVM_LIMITS, "--module", "jdk.compiler/com.sun.tools.javac.Main",
        "-proc:none", "-encoding", "UTF-8", "-source", level, "-target", level,
        "-classpath", str(classes), "-d", str(classes), str(driver)), tmp_path, "driver-compile", 20)
    assert compiler[0] == 0, compiler
    batch = record_process((str(java), *JVM_LIMITS, "-cp", str(classes), "NarrowReference", str(tmp_path), level),
                           tmp_path, "batch-compile", 30)
    assert batch[0] == 0, batch
    return java


def compare_raw(directory, name, actual, reference, expected, lane, arguments, sources, api):
    native = directory / "native"
    expected_bytes = int(expected).to_bytes(WIDTHS[lane], "little", signed=lane not in {"Z", "C", "F", "D"})
    (directory / "comparison.json").write_text(json.dumps({
        "case": name, "comparison": "raw_process_streams",
        "source_sha256": {filename: hashlib.sha256(text.encode("utf-8")).hexdigest()
                          for filename, text in sources.items()},
        "original_modifiers_or_source_rewritten": False,
        "source_api": api,
        "descriptor": descriptor(lane, arguments), "arguments": arguments,
        "observer_sha256": digest(directory / "PccNarrowObserve.java"),
        "object_sha256": digest(native / "methods.o"), "executable_sha256": digest(native / "observed"),
        "native": {"exit_code": actual[0], "stdout_hex": actual[1].hex(), "stderr_hex": actual[2].hex()},
        "reference": {"exit_code": reference[0], "stdout_hex": reference[1].hex(), "stderr_hex": reference[2].hex()},
    }, indent=2))
    assert actual == reference == (0, expected_bytes, b""), name


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON or JDK_REASON)
@pytest.mark.xdist_group(name="java-raw-reference")
@pytest.mark.parametrize("api", ("compile_source", "compile_sources"))
def test_identical_blank_final_originals_match_jdk_raw_processes(api, tmp_path, monkeypatch, request):
    java = reference_batch(tmp_path, request, runtime=True)
    for name, text, lane, arguments, expected in CASES:
        directory = tmp_path / name
        assert (directory / "compiler-status.txt").read_text() == "0", name
        reference = record_process((str(java), *JVM_LIMITS, "-cp", str(directory / "classes"), "PccNarrowObserve"),
                                   directory, "reference-run")
        native = directory / "native"
        native.mkdir()
        filename = source_owner(text) + ".java"
        with monkeypatch.context() as denied:
            forbid_bytecode_or_external_owner(denied)
            compiled = compile_original(text, lane, arguments, api)
        assert observe_scalar(compiled, native, monkeypatch, arguments) == expected
        actual = record_process((str(native / "observed"),), native, "raw-native")
        compare_raw(directory, name, actual, reference, expected, lane, arguments, {filename: text}, api)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-raw-reference")
def test_identical_invalid_blank_final_originals_are_rejected_by_jdk(tmp_path, request):
    reference_batch(tmp_path, request, runtime=False)
    for name, text, _ in INVALID:
        directory = tmp_path / name
        assert int((directory / "compiler-status.txt").read_text()) != 0, name
        diagnostic = (directory / "compiler.stderr.bin").read_bytes()
        assert re.search(re.escape(source_owner(text).encode()) + rb"\.java:\d+:", diagnostic), (name, diagnostic)


def compile_reference_project(java, level, directory, sources, retained, *, entry=None):
    units = []
    for filename, text in sources.items():
        unit = directory / filename
        unit.write_bytes(text.encode("utf-8"))
        retained[unit] = digest(unit)
        units.append(str(unit))
    if entry is not None:
        observer = directory / "PccNarrowObserve.java"
        observer.write_text(observer_source(entry, "I", ()), encoding="utf-8")
        retained[observer] = digest(observer)
        units.append(str(observer))
    classes = directory / "classes"
    classes.mkdir()
    return record_process((str(java), *JVM_LIMITS, "--module", "jdk.compiler/com.sun.tools.javac.Main",
        "-proc:none", "-encoding", "UTF-8", "-source", level, "-target", level,
        "-classpath", str(classes), "-d", str(classes), *units), directory, "reference-compile", 20)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON or JDK_REASON)
@pytest.mark.xdist_group(name="java-raw-reference")
def test_identical_blank_final_projects_match_jdk_raw_processes(tmp_path, monkeypatch, request):
    java, level, retained = reference_owner(tmp_path, request)
    for name, sources, entry, required in PROJECTS:
        directory = tmp_path / name
        directory.mkdir()
        compiler = compile_reference_project(java, level, directory, sources, retained, entry=entry)
        assert compiler[0] == 0, compiler
        reference = record_process((str(java), *JVM_LIMITS, "-cp", str(directory / "classes"), "PccNarrowObserve"),
                                   directory, "reference-run")
        native = directory / "native"
        native.mkdir()
        with monkeypatch.context() as denied:
            forbid_bytecode_or_external_owner(denied)
            compiled = compile_sources(sources, entry_class=entry)
        assert required <= set(compiled.emitted_class_methods)
        assert observe_scalar(compiled, native, monkeypatch) == 42
        actual = record_process((str(native / "observed"),), native, "raw-native")
        compare_raw(directory, name, actual, reference, 42, "I", (), sources, "compile_sources")


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-raw-reference")
def test_identical_invalid_blank_final_projects_are_rejected_by_jdk(tmp_path, request):
    java, level, retained = reference_owner(tmp_path, request)
    for name, sources, _ in INVALID_PROJECTS:
        directory = tmp_path / name
        directory.mkdir()
        compiler = compile_reference_project(java, level, directory, sources, retained)
        assert compiler[0] != 0, name
        assert re.search(rb"Other\.java:\d+:", compiler[2]), (name, compiler)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-raw-reference")
def test_retained_model_boundary_originals_are_valid_jdk_source(tmp_path, request):
    java, level, retained = reference_owner(tmp_path, request)
    for name, text, _ in MODEL_BOUNDARIES:
        directory = tmp_path / name
        directory.mkdir()
        compiler = compile_reference_project(java, level, directory, {"BlankFinal.java": text}, retained)
        assert compiler[0] == 0, (name, compiler)

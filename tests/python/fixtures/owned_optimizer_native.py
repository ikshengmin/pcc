"""Finite native execution fixture; never a replacement optimizer/provider."""
from pcc.frontends.python.compiled_owned_passes import run_owned_passes
import pcc.frontends.python.compiled_owned_passes as dispatch
from pcc.frontends.python.compiled_default_passes import (
    _has_py_cpy_call, _mem2reg_function, _sroa_function, run_compiled_default_tier,
)
from pcc.ir.optimization.dce import dce_module_text
from pcc.ir.optimization.inline import inline_module

SCALAR = """define i64 @probe(i64 %arg) {
entry:
  %slot = alloca i64, align 8
  store i64 %arg, ptr %slot, align 8
  %value = load i64, ptr %slot, align 8
  ret i64 %value
}
"""
STRUCT = """define i64 @probe(i64 %arg) {
entry:
  %pair = alloca { i64, ptr }, align 8
  %first = getelementptr inbounds { i64, ptr }, ptr %pair, i32 0, i32 0
  store i64 %arg, ptr %first, align 8
  %value = load i64, ptr %first, align 8
  ret i64 %value
}
"""
ADD_ZERO = """define i64 @probe(i64 %arg) {
entry:
  %value = add i64 %arg, 0
  ret i64 %value
}
"""
MULTIPLY = """define i64 @probe(i64 %arg) {
entry:
  %value = mul i64 %arg, 8
  ret i64 %value
}
"""
DEAD = """define i64 @probe(i64 %arg) {
entry:
  %unused = add i64 %arg, 7
  ret i64 %arg
}
"""
CONSTANT_BRANCH = """define i64 @probe() {
entry:
  br i1 true, label %yes, label %no
yes:
  ret i64 11
no:
  ret i64 22
}
"""
DIAMOND = """define i64 @probe(i1 %condition) {
entry:
  br i1 %condition, label %done, label %no
no:
  br label %done
done:
  %value = phi i64 [ 11, %entry ], [ 22, %no ]
  ret i64 %value
}
"""
INTERNAL = """define internal i64 @leaf(i64 %arg) {
entry:
  %value = add i64 %arg, 1
  ret i64 %value
}
define i64 @probe(i64 %arg) {
entry:
  %value = call i64 @leaf(i64 %arg)
  ret i64 %value
}
"""
CALL = """declare ptr @py_cpy_import(ptr)
define ptr @probe() {
entry:
  %value = call ptr @py_cpy_import(ptr null)
  ret ptr %value
}
"""
events: list[str] = []


def replacement(text: str) -> tuple[str, bool]:
    events.append(text)
    return text + "; replacement executed\n", True


def main():
    out = run_owned_passes(SCALAR, ["mem2reg"], False)
    assert "alloca" not in out and "load i64" not in out and "ret i64 %arg" in out
    print("mem2reg")
    out = run_owned_passes(STRUCT, ["sroa"], False)
    assert "alloca {" not in out and "getelementptr" not in out
    print("sroa")
    out = run_owned_passes(ADD_ZERO, ["instsimplify"], False)
    assert "add i64" not in out and "ret i64 %arg" in out
    print("instsimplify")
    out = run_owned_passes(CONSTANT_BRANCH, ["simplifycfg"], False)
    assert "br i1 true" not in out and "ret i64 22" not in out
    print("simplifycfg")
    out = run_owned_passes(DIAMOND, ["fold-two-entry-phi"], False)
    assert "select i1" in out and "phi i64" not in out
    print("fold-two-entry-phi")
    out = run_owned_passes(MULTIPLY, ["instcombine"], False)
    assert "mul i64" not in out and "shl i64 %arg, 3" in out
    print("instcombine")
    out = run_owned_passes(DEAD, ["dce"], False)
    assert "%unused" not in out and "ret i64 %arg" in out
    print("dce")
    out = run_owned_passes(INTERNAL, ["inline"], False)
    assert "call i64 @leaf" not in out
    external = INTERNAL.replace("define internal", "define")
    out = run_owned_passes(external, ["inline-defined"], False)
    assert "call i64 @leaf" not in out and "define i64 @leaf" in out
    print("inline-default-and-defined")

    out, changed = dce_module_text(DEAD)
    explicit, explicit_changed = dce_module_text(DEAD, None)
    assert changed and explicit_changed and out == explicit and "%unused" not in out
    ordinary, changed = inline_module(INTERNAL)
    explicit, explicit_changed = inline_module(INTERNAL, require_alwaysinline=False, include_definitions=False)
    assert changed and explicit_changed and ordinary == explicit
    refused, refused_changed = inline_module(INTERNAL, require_alwaysinline=True)
    assert not refused_changed and "call i64 @leaf" in refused
    refused, refused_changed = inline_module(external)
    assert not refused_changed and "call i64 @leaf" in refused
    accepted, accepted_changed = inline_module(external, include_definitions=True)
    assert accepted_changed and "call i64 @leaf" not in accepted
    print("literal-defaults-and-bool-keywords")

    out = "".join(_mem2reg_function(SCALAR.splitlines(keepends=True)))
    assert "alloca" not in out and "ret i64 %arg" in out
    out = "".join(_sroa_function(STRUCT.splitlines(keepends=True)))
    assert "alloca {" not in out and "getelementptr" not in out
    out = run_compiled_default_tier(SCALAR, ["mem2reg", "sroa"], strict_no_libpython=False)
    assert "alloca" not in out and "ret i64 %arg" in out
    out = run_owned_passes(SCALAR, ["mem2reg", "sroa"], False)
    assert "alloca" not in out and "ret i64 %arg" in out
    assert _has_py_cpy_call(CALL)
    assert not _has_py_cpy_call("declare ptr @py_cpy_import(ptr)\n")
    assert run_owned_passes(CALL, ["mem2reg"], True) == CALL
    assert run_compiled_default_tier(CALL, ["mem2reg", "sroa"], strict_no_libpython=True) == CALL
    print("scalar-helpers-and-strict-bool")

    functions = [inline_module]
    caught = False
    try:
        functions[0](INTERNAL, True)
    except TypeError:
        caught = True
    assert caught
    tiers = [run_compiled_default_tier]
    caught = False
    try:
        tiers[0](SCALAR, ["mem2reg"])
    except TypeError:
        caught = True
    assert caught
    print("native-keyword-arity-errors")

    original = dispatch.inline_module
    dispatch.inline_module = replacement
    out = run_owned_passes(INTERNAL, ["inline"], False)
    assert out == INTERNAL + "; replacement executed\n"
    assert events == [INTERNAL]
    dispatch.inline_module = original
    out = run_owned_passes(INTERNAL, ["inline"], False)
    assert "call i64 @leaf" not in out and events == [INTERNAL]
    print("live-rebinding-and-one-replacement-call")
    print("owned-optimizer-native-ok")


main()

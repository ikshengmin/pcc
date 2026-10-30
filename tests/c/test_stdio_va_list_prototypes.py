"""Owned stdio declarations must preserve each target's real va_list type.

These are host-driven preprocessing/codegen/verifier checks, not native
execution evidence for any of the four targets.
"""

import builtins
from pathlib import Path
import subprocess

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend.self_backend_parse import (
    parse_self_backend_module,
    split_top_level,
)
from pcc.backend.self_backend_verify import verify_parsed_module
from pcc.frontends.c.codegen.c_codegen import CCodeGenerator, postprocess_ir_text
from pcc.frontends.c.parse import make_c_parser
from pcc.frontends.c.preprocessor import preprocess
from pcc.ir.compat import ir_c as ir


ROOT = Path(__file__).resolve().parents[2]
TARGETS = (
    ("aarch64-unknown-linux-gnu", "aggregate"),
    ("x86_64-unknown-linux-gnu", "array"),
    ("arm64-apple-darwin", "pointer"),
    ("x86_64-pc-windows-msvc", "pointer"),
)
FUNCTIONS = ("vprintf", "vfprintf", "vsprintf", "vsnprintf")
WRAPPERS = """
int call_vprintf(const char *format, va_list ap) {
    return vprintf(format, ap);
}
int call_vfprintf(FILE *stream, const char *format, va_list ap) {
    return vfprintf(stream, format, ap);
}
int call_vsprintf(char *buffer, const char *format, va_list ap) {
    return vsprintf(buffer, format, ap);
}
int call_vsnprintf(char *buffer, size_t size, const char *format, va_list ap) {
    return vsnprintf(buffer, size, format, ap);
}
"""


@pytest.fixture
def owned_only(monkeypatch):
    original_import = builtins.__import__

    def checked_import(name, *args, **kwargs):
        if name == "llvmlite" or name.startswith("llvmlite."):
            raise AssertionError("owned stdio control imported " + name)
        return original_import(name, *args, **kwargs)

    def forbidden_process(*args, **kwargs):
        raise AssertionError("owned stdio control attempted an external process")

    monkeypatch.setattr(builtins, "__import__", checked_import)
    monkeypatch.setattr(subprocess, "Popen", forbidden_process)


@pytest.mark.parametrize("target,shape", TARGETS)
@pytest.mark.parametrize("includes", (
    "#include <stdio.h>\n",
    "#include <stdarg.h>\n#include <stdio.h>\n#include <stdio.h>\n",
), ids=("stdio-only", "stdarg-first-repeated-stdio"))
def test_stdio_va_list_prototypes_preserve_target_type(target, shape, includes, owned_only):
    source = preprocess(
        includes + WRAPPERS,
        include_dirs=[str(ROOT / "utils/fake_libc_include")],
        target_triple=target,
    )
    codegen = CCodeGenerator(translation_unit_name="stdio_va_list.c")
    codegen.set_target_text(target, "")
    codegen.generate_code(make_c_parser().parse(source))
    va_list_type = codegen._get_ir_type("va_list")
    expected = va_list_type
    if shape == "aggregate":
        assert isinstance(va_list_type, ir.IdentifiedStructType)
        members = va_list_type.elements
        assert len(members) == 5
        assert all(isinstance(member, ir.PointerType) for member in members[:3])
        assert all(isinstance(member, ir.IntType) and member.width == 32
                   for member in members[3:])
    elif shape == "array":
        assert isinstance(va_list_type, ir.ArrayType)
        assert va_list_type.count == 1
        assert isinstance(va_list_type.element, ir.IdentifiedStructType)
        expected = va_list_type.element.as_pointer()
    else:
        assert isinstance(va_list_type, ir.PointerType)
        assert isinstance(va_list_type.pointee, ir.IntType)
        assert va_list_type.pointee.width == 8

    text = postprocess_ir_text(str(codegen.module))
    for name in FUNCTIONS:
        # Missing header prototypes would silently choose the old void* libc
        # fallback. Require the declarations emitted from the actual header.
        assert name not in codegen._declared_libc
        declaration = codegen.module.globals[name]
        wrapper = codegen.module.globals["call_" + name]
        declared = declaration.function_type.args[-1]
        assert not declaration.function_type.var_arg
        assert str(declared) == str(expected)
        assert str(wrapper.function_type.args[-1]) == str(expected)
        if shape == "aggregate":
            # A same-layout invented named struct is not the typedef's type.
            assert declared is va_list_type
            assert wrapper.function_type.args[-1] is va_list_type
        elif shape == "array":
            assert declared.pointee is va_list_type.element
        else:
            assert isinstance(declared, ir.PointerType)
            assert declared.pointee is va_list_type.pointee
            assert wrapper.function_type.args[-1].pointee is va_list_type.pointee
        calls = [line for line in text.splitlines()
                 if "call " in line and "@" + name + "(" in line]
        assert len(calls) == 1, calls
        arguments = calls[0].split("@" + name + "(", 1)[1].rsplit(")", 1)[0]
        assert split_top_level(arguments)[-1].startswith(str(expected) + " ")

    parsed = parse_self_backend_module(text)
    verify_parsed_module(parsed)
    if shape == "aggregate":
        function = next(func for func in parsed.functions if func.name == "call_vsnprintf")
        assert function.args[-1].type.is_struct
        assert function.args[-1].type.slot_size == 32


def test_verifier_still_rejects_aggregate_relabelled_as_va_list_pointer():
    text = """
target triple = "aarch64-unknown-linux-gnu"
%va = type { void*, void*, void*, i32, i32 }
declare i32 @vsnprintf(i8*, i64, i8*, void*)
define i32 @legacy(i8* %buffer, i64 %size, i8* %format, %va %ap) {
entry:
  %result = call i32 (i8*, i64, i8*, void*) @vsnprintf(i8* %buffer, i64 %size, i8* %format, void* %ap)
  ret i32 %result
}
"""
    with pytest.raises(BackendUnavailable, match=r"\[operand-type\].*expects void\*.*got %va"):
        verify_parsed_module(parse_self_backend_module(text))

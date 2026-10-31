"""The runtime's implicit C pointer ABI is not an explicit raw Python view."""

from pathlib import Path
import re

import pytest

from pcc.frontends.python.owned_runtime_build import _compile_runtime_module
from pcc.frontends.python.pipeline import compile_python
from tests.owned_ir_validation import verify_ir_text


ROOT = Path(__file__).resolve().parents[2]
TARGET = "x86_64-unknown-linux-gnu"


def _assert_no_implicit_pointer_management(body):
    # Mixed int/bool expressions can retain their own boxed scalar result.
    # That does not authorize retaining the raw sentinel/value parameter (or
    # an alias), creating a root for it, or calling other managed GC helpers.
    scalar_values = set(re.findall(
        r"^\s*(%[\w.$]+) = call ptr \(i32\) @py_bool_from_bit\(i32 [^\n)]+\)$",
        body, re.M,
    ))
    scalar_values.update(value for value, integer in re.findall(
        r"^\s*(%[\w.$]+) = inttoptr i64 (-?\d+) to ptr$", body, re.M,
    ) if int(integer) & 1)
    for helper, arguments in re.findall(r"\bcall\b[^\n]*@(pcc_gc_\w+)\(([^)]*)\)", body):
        if helper == "pcc_gc_safepoint":
            continue
        assert helper == "pcc_gc_retain", (helper, arguments)
        assert arguments.startswith("ptr ") and arguments[4:] in scalar_values, arguments


def test_capi_exception_runtime_compiles_through_owned_builder(tmp_path):
    output = tmp_path / "py_capi_exc_runtime.ll"
    _compile_runtime_module(
        "py_capi_exc_runtime",
        str(ROOT / "pcc/runtime/py/py_capi_exc_runtime.py"),
        str(output),
        TARGET,
    )
    text = output.read_text()
    verify_ir_text(text)
    for symbol, parameters in (
        ("pcc_capi_exception_class", r"ptr %type"),
        ("PyErr_SetString", r"ptr %type, ptr %message"),
        ("PyErr_SetNone", r"ptr %type"),
        ("PyErr_SetObject", r"ptr %type, ptr %value"),
    ):
        assert re.search(r"^define[^\n]*@" + symbol + r"\(" + parameters + r"\)", text, re.M)
    for helper in ("_capi_is_unicode_sentinel", "_capi_is_unicode_encode_type",
                   "_capi_set_unicode_encode_value"):
        match = re.search(r"^define[^\n]*@[^\n]*" + helper + r"\(ptr %\w+\)[^\n{]*\{\n(.*?)^}", text, re.M | re.S)
        assert match, helper
        _assert_no_implicit_pointer_management(match.group(1))
    assert re.search(r"\bcall\b[^\n]*@py_unicode_encode_error_normalize\(ptr", text)


@pytest.mark.parametrize("managed", [
    "%r = call ptr (ptr) @pcc_gc_retain(ptr %type)",
    "%alias = bitcast ptr %type to ptr\n%r = call ptr (ptr) @pcc_gc_retain(ptr %alias)",
    "call void (ptr, ptr) @pcc_gc_store_root(ptr %slot, ptr %value)",
    "%raw = inttoptr i64 8 to ptr\n%r = call ptr (ptr) @pcc_gc_retain(ptr %raw)",
])
def test_raw_helper_management_check_rejects_sentinel_roots_and_retains(managed):
    scalar = ("%zero = inttoptr i64 1 to ptr\n"
              "%kept = call ptr (ptr) @pcc_gc_retain(ptr %zero)\n"
              "%boolean = call ptr (i32) @py_bool_from_bit(i32 1)\n"
              "%kept_bool = call ptr (ptr) @pcc_gc_retain(ptr %boolean)\n")
    _assert_no_implicit_pointer_management(scalar)
    with pytest.raises(AssertionError):
        _assert_no_implicit_pointer_management(scalar + managed)


@pytest.mark.parametrize("runtime_port", [False, True], ids=["application", "runtime-port"])
def test_unproven_pointer_argument_reports_source_location(tmp_path, runtime_port):
    source = tmp_path / "unproven_pointer.py"
    directive = "__pcc_runtime_port__ = True" if runtime_port else "# Application module"
    source.write_text(
        "from pcc.extern import c_abi_typed_export, c_ptr\n"
        f"{directive}\n"
        "def consume(value: c_ptr) -> None:\n"
        "    pass\n"
        "@c_abi_typed_export('unproven_pointer_entry', 'void', ('ptr',))\n"
        "def reject(value) -> None:\n"
        "    consume(value)\n"
    )
    with pytest.raises(NotImplementedError, match="raw pointer cannot cross") as error:
        compile_python(str(source), str(tmp_path / "unproven.ll"),
                       emit_llvm_only=True, python_library=True,
                       libpython_mode="off", backend="self", target_triple=TARGET)
    assert str(source) + ":7:" in str(error.value)

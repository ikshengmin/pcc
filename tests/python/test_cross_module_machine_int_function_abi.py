"""Explicit free-function machine lanes remain exact across module boundaries."""
from __future__ import annotations

import re
import subprocess
import sys

import pytest

from tests.ir_function_body import function_body

OWNER = '''from pcc import i64, u64

def touch(value: i64) -> None:
    print(value)

def signed(value: i64) -> i64:
    return value

def unsigned(value: u64) -> u64:
    return value

def ordinary(value: int) -> int:
    return value
'''

USER = '''from pcc import i64, u64
from owner import touch, signed, unsigned, ordinary

def use_touch(value: i64) -> None:
    touch(value)

def use_signed(value: i64) -> i64:
    return signed(value)

def use_unsigned(value: u64) -> u64:
    return unsigned(value)

def use_ordinary(value: int) -> int:
    return ordinary(value)

use_touch(37)
assert use_signed(-9223372036854775808) == -9223372036854775808
assert use_signed(9223372036854775807) == 9223372036854775807
assert use_unsigned(0) == 0
assert use_unsigned(9223372036854775808) == 9223372036854775808
assert use_unsigned(18446744073709551615) == 18446744073709551615
assert use_ordinary(42) == 42
print("RAW_FUNCTION_ABI_OK")
'''

EXPECTED = b"37\nRAW_FUNCTION_ABI_OK\n"


def _write(tmp_path):
    owner, user = tmp_path / "owner.py", tmp_path / "user.py"
    owner.write_text(OWNER, encoding="utf-8")
    user.write_text(USER, encoding="utf-8")
    return user, owner


def test_raw_free_function_source_descriptors_are_exact_in_both_wire_forms(tmp_path):
    from pcc.frontends.python.pipeline_context import build_closed_world_context
    from pcc.frontends.python.pipeline_closed_world import _closed_world_module_dependencies
    from pcc.frontends.python.pipeline_exports import _write_native_exports_wire, _read_native_exports_wire_for_module
    from pcc.frontends.python.type_infer import build_unique_external_class_preload_index

    user, owner = _write(tmp_path)
    paths, names = [str(user), str(owner)], ["user", "owner"]
    parsed, exports, derived = build_closed_world_context(paths, names)
    raw_signed = ("raw_int", "pcc.i64", 64, True)
    raw_unsigned = ("raw_int", "pcc.u64", 64, False)
    ordinary = ("int", 64, True)

    def check(table):
        for name, param, result in (
            ("touch", raw_signed, ("none",)),
            ("signed", raw_signed, raw_signed),
            ("unsigned", raw_unsigned, raw_unsigned),
            ("ordinary", ordinary, ordinary),
        ):
            row = table["owner"][name]
            assert row["param_types"][0] == row["call_sig"][0]["annotation"] == param
            assert row["return_ty"] == result
        assert table["owner"]["ordinary"]["box_int_abi"] is True

    check(exports)
    for indexed in (False, True):
        path = tmp_path / ("indexed.wire" if indexed else "legacy.wire")
        kwargs = {}
        if indexed:
            kwargs = {
                "module_dependencies": dict(_closed_world_module_dependencies(parsed, names, paths, names)),
                "unique_class_preload_index": build_unique_external_class_preload_index(exports),
            }
        _write_native_exports_wire(path, exports, derived, **kwargs)
        selected, _, _, is_indexed = _read_native_exports_wire_for_module(path, "user")
        assert is_indexed is indexed
        check(selected)


def test_raw_free_function_provider_consumer_abi_matches(tmp_path):
    from pcc.frontends.python.pipeline import compile_python_multi

    user, owner = _write(tmp_path)
    out = tmp_path / "combined.ll"
    compile_python_multi([str(user), str(owner)], str(out), emit_llvm_only=True,
                         entry_module="user", module_names=["user", "owner"])
    text = out.read_text(encoding="utf-8")
    for name, result, param in (("touch", "void", "i64"), ("signed", "i64", "i64"),
                                ("unsigned", "i64", "i64"), ("ordinary", "ptr", "ptr")):
        body = function_body(text, "user_user_use_" + name)
        assert re.search(r"call " + result + r" \(" + param + r"\) @user_owner_" + name + r"\(", body)
        assert re.search(r"define external " + result + " @user_owner_" + name + r"\(" + param + r" %value\)", text)


@pytest.mark.integration
def test_raw_free_function_native_signed_unsigned_boundaries(tmp_path, threaded_pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python_multi

    user, owner = _write(tmp_path)
    reference = subprocess.run([sys.executable, str(user)], capture_output=True, timeout=10)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, EXPECTED, b"")
    native = tmp_path / "native"
    compile_python_multi([str(user), str(owner)], str(native), entry_module="user",
                         module_names=["user", "owner"], backend="self", ir_scaffold_mode="on",
                         runtime_archive=str(threaded_pcc_runtime_archive))
    actual = subprocess.run([str(native)], capture_output=True, timeout=10)
    assert (actual.returncode, actual.stdout, actual.stderr) == (0, EXPECTED, b"")


@pytest.mark.integration
def test_raw_free_function_native_void_isolated(tmp_path, threaded_pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python_multi

    owner = tmp_path / "owner.py"
    owner.write_text("from pcc import i64\ndef touch(value: i64) -> None:\n    print(value)\n", encoding="utf-8")
    user = tmp_path / "user.py"
    user.write_text("from pcc import i64\nfrom owner import touch\n"
                    "def run(value: i64) -> None:\n    touch(value)\nrun(37)\n", encoding="utf-8")
    reference = subprocess.run([sys.executable, str(user)], capture_output=True, timeout=10)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, b"37\n", b"")
    native = tmp_path / "native"
    compile_python_multi([str(user), str(owner)], str(native), entry_module="user",
                         module_names=["user", "owner"], backend="self", ir_scaffold_mode="on",
                         runtime_archive=str(threaded_pcc_runtime_archive))
    actual = subprocess.run([str(native)], capture_output=True, timeout=10)
    assert (actual.returncode, actual.stdout, actual.stderr) == (0, b"37\n", b"")

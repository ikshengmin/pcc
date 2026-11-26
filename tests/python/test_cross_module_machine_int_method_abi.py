"""Explicit raw method lanes retain their source names across module exports."""
from __future__ import annotations

import re
import subprocess
import sys

import pytest

from tests.ir_function_body import function_body

OWNER = '''from pcc import i64, u64

class Parent:
    def touch(self, value: i64) -> None:
        print(value)

    def signed(self, value: i64) -> i64:
        return value

    def unsigned(self, value: u64) -> u64:
        return value

    def ordinary(self, value: int) -> int:
        return value
'''

USER = '''from pcc import i64, u64
from owner import Parent

def touch(parent: Parent, value: i64) -> None:
    parent.touch(value)

def signed(parent: Parent, value: i64) -> i64:
    return parent.signed(value)

def unsigned(parent: Parent, value: u64) -> u64:
    return parent.unsigned(value)

def ordinary(parent: Parent, value: int) -> int:
    return parent.ordinary(value)

# The consumer itself is the entry: no unrelated imported free-function ABI
# can hide or duplicate the cross-module method boundary being tested.
parent = Parent()
touch(parent, 37)
assert signed(parent, -9223372036854775808) == -9223372036854775808
assert signed(parent, 9223372036854775807) == 9223372036854775807
assert unsigned(parent, 0) == 0
assert unsigned(parent, 9223372036854775808) == 9223372036854775808
assert unsigned(parent, 18446744073709551615) == 18446744073709551615
assert ordinary(parent, 42) == 42
print("RAW_METHOD_ABI_OK")
'''

EXPECTED = b"37\nRAW_METHOD_ABI_OK\n"


def _write(tmp_path):
    owner = tmp_path / "owner.py"
    user = tmp_path / "user.py"
    owner.write_text(OWNER, encoding="utf-8")
    user.write_text(USER, encoding="utf-8")
    return user, owner


def test_raw_method_source_type_descriptors_are_exact_in_both_wire_forms(tmp_path):
    from pcc.frontends.python.export_meta import decode_type
    from pcc.frontends.python.pipeline_context import build_closed_world_context
    from pcc.frontends.python.pipeline_closed_world import _closed_world_module_dependencies
    from pcc.frontends.python.pipeline_exports import (
        _write_native_exports_wire, _read_native_exports_wire_for_module,
    )
    from pcc.frontends.python.type_infer import build_unique_external_class_preload_index

    user, owner = _write(tmp_path)
    paths, names = [str(user), str(owner)], ["user", "owner"]
    parsed, exports, derived = build_closed_world_context(paths, names)
    raw_signed = ("raw_int", "pcc.i64", 64, True)
    raw_unsigned = ("raw_int", "pcc.u64", 64, False)
    ordinary = ("int", 64, True)

    def check(table):
        methods = {row["name"]: row for row in table["owner"]["Parent"]["methods"]}
        for method_name, param, result in (
            ("touch", raw_signed, ("none",)),
            ("signed", raw_signed, raw_signed),
            ("unsigned", raw_unsigned, raw_unsigned),
            ("ordinary", ordinary, ordinary),
        ):
            row = methods[method_name]
            assert row["param_types"][1] == row["call_sig"][1]["annotation"] == param
            assert row["return_ty"] == result
        assert methods["touch"]["void_managed_pointer_abi"] is False

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
    for desc, name, signed in ((raw_signed, "pcc.i64", True),
                               (raw_unsigned, "pcc.u64", False),
                               (ordinary, "int", True)):
        ty = decode_type(desc)
        assert (ty.name, ty.width, ty.signed) == (name, 64, signed)


@pytest.mark.parametrize("bad", (
    ("raw_int",),
    ("raw_int", "int", 64, True),
    ("raw_int", "pcc.i64", 64, False),
    ("raw_int", "pcc.u64", 64, True),
    ("raw_int", "pcc.i64", 32, True),
    ("raw_int", "pcc.u64", 64, 0),
    ("raw_int", "pcc.i64", 64, True, "extra"),
))
def test_malformed_raw_machine_descriptor_does_not_become_python_int(bad):
    from pcc.frontends.python.export_meta import decode_type

    # Warm both valid descriptors first: integer0/1 must not alias the
    # Boolean signedness field through the generic decode cache.
    decode_type(("raw_int", "pcc.i64", 64, True))
    decode_type(("raw_int", "pcc.u64", 64, False))
    with pytest.raises(TypeError, match="invalid raw integer export descriptor"):
        decode_type(bad)


def test_explicit_raw_method_source_signatures_match_consumer_abi(tmp_path):
    from pcc.frontends.python.pipeline import compile_python_multi

    user, owner = _write(tmp_path)
    out = tmp_path / "combined.ll"
    compile_python_multi([str(user), str(owner)], str(out), emit_llvm_only=True,
                         entry_module="user", module_names=["user", "owner"])
    text = out.read_text(encoding="utf-8")
    touch = function_body(text, "user_user_touch")
    assert re.search(r"call void \(ptr, i64\) @user_owner_Parent_touch\(", touch)
    assert "live_void" not in touch
    assert re.search(r"define external void @user_owner_Parent_touch\(ptr %self, i64 %value\)", text)
    for name in ("signed", "unsigned"):
        body = function_body(text, "user_user_" + name)
        assert re.search(r"call i64 \(ptr, i64\) @user_owner_Parent_" + name + r"\(", body)
        assert re.search(r"define external i64 @user_owner_Parent_" + name + r"\(ptr %self, i64 %value\)", text)
    ordinary = function_body(text, "user_user_ordinary")
    assert "@py_obj_call" in ordinary and "@user_owner_Parent_ordinary(" not in ordinary


@pytest.mark.integration
def test_raw_machine_method_native_signed_unsigned_boundaries(tmp_path, threaded_pcc_runtime_archive):
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
def test_raw_machine_void_method_native_isolated(tmp_path, threaded_pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python_multi

    owner = tmp_path / "owner.py"
    owner.write_text("from pcc import i64\nclass Parent:\n"
                     "    def touch(self, value: i64) -> None:\n        print(value)\n",
                     encoding="utf-8")
    user = tmp_path / "user.py"
    user.write_text("from pcc import i64\nfrom owner import Parent\n"
                    "def run(parent: Parent, value: i64) -> None:\n    parent.touch(value)\n"
                    "run(Parent(), 37)\n",
                    encoding="utf-8")
    reference = subprocess.run([sys.executable, str(user)], capture_output=True, timeout=10)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, b"37\n", b"")
    native = tmp_path / "native"
    compile_python_multi([str(user), str(owner)], str(native), entry_module="user",
                         module_names=["user", "owner"], backend="self", ir_scaffold_mode="on",
                         runtime_archive=str(threaded_pcc_runtime_archive))
    actual = subprocess.run([str(native)], capture_output=True, timeout=10)
    assert (actual.returncode, actual.stdout, actual.stderr) == (0, b"37\n", b"")

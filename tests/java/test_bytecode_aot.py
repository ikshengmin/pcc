"""Real .class -> PCC IR -> self object -> owned ELF -> native execution.

Fixtures are original literal bytecode, not evidence of javac/source support.
The optional JVM differential gate is explicitly a development-only oracle.
"""
import os
import platform
import shutil
import struct
import subprocess
import sys

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, compile_class, parse_class
from pcc.frontends.java.bytecode import verify_method
from tests.java.class_builder import ClassBuilder, u2


_NATIVE = None if sys.platform.startswith("linux") and platform.machine() == "x86_64" else "native Java slice execution requires x86_64 Linux"


def one(code, **kwargs):
    return ClassBuilder().method("run", code, **kwargs).build()


def verify(data):
    cls = parse_class(data)
    return verify_method(cls, cls.methods[0])


def test_public_ir_and_owned_elf_object(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("external tool/JVM subprocess attempted")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    compilation = compile_class(one(b"\x10\x2a\xac"))
    assert compilation.admitted_methods == (("run", "()I"),)
    assert "ret i32" in str(compilation.module)
    assert compilation.emit_object()[:4] == b"\x7fELF"


@pytest.mark.parametrize("code,error", [
    (b"\x60\xac", "underflow"),
    (b"\x1a\xac", "uninitialized"),
    (b"\x03", "falls off"),
    (b"\xa7\x00\x01", "instruction boundary"),
    (b"\xa7\xff\xff", "instruction boundary"),
    (b"\x10", "truncated"),
    (b"\x03\xb1", "void return"),
    (b"\x84\x00\x01\x03\xac", "uninitialized"),
    # Conditional predecessors disagree on operand-stack height at offset 8.
    (b"\x03\x99\x00\x07\x04\xa7\x00\x03\x03\xac", "incompatible operand stacks"),
    # One path does not initialize local 0 before the merge/load at offset 9.
    (b"\x03\x99\x00\x08\x04\x3b\xa7\x00\x03\x1a\xac", "uninitialized"),
])
def test_malformed_code_rejected(code, error):
    with pytest.raises(JavaError, match=error):
        compile_class(one(code))


def test_local_and_stack_bounds():
    with pytest.raises(JavaError, match="local index"):
        compile_class(one(b"\x15\x08\xac"))
    with pytest.raises(JavaError, match="max_stack"):
        compile_class(one(b"\x03\xac", stack=0))
    with pytest.raises(JavaError, match="parameter count"):
        compile_class(one(b"\x1a\xac", descriptor="(I)I", locals_=0), descriptor="(I)I")


@pytest.mark.parametrize("opcode,feature", [(0xC2, "monitorenter"), (0xC3, "monitorexit"),
                                          (0xBA, "invokedynamic"), (0xBB, "heap"),
                                          (0x6C, "ArithmeticException"), (0x70, "ArithmeticException"),
                                          (0xBF, "unwind"), (0xB6, "invokevirtual")])
def test_unsupported_is_not_a_pass_or_fallback(opcode, feature):
    with pytest.raises(UnsupportedJava, match=feature):
        compile_class(one(bytes([opcode])))


def test_unreachable_unsupported_opcode_still_rejected_in_selected_method():
    with pytest.raises(UnsupportedJava, match="monitorenter"):
        compile_class(one(b"\x03\xac\xc2"))


def test_nonselected_constructor_is_not_claimed_compiled():
    builder = ClassBuilder().method("run", b"\x03\xac")
    builder.method("<init>", b"\xb1", "()V", flags=1)
    compilation = compile_class(builder.build())
    assert compilation.admitted_methods == (("run", "()I"),)


@pytest.mark.parametrize("flags", [0x0001, 0x0029])
def test_instance_and_synchronized_methods_rejected(flags):
    with pytest.raises(UnsupportedJava):
        compile_class(one(b"\x03\xac", flags=flags))


def test_class_initialization_is_not_silently_omitted():
    builder = ClassBuilder().method("run", b"\x03\xac").method("<clinit>", b"\xb1", "()V")
    with pytest.raises(UnsupportedJava, match="initialization"):
        compile_class(builder.build())


def test_external_class_call_rejected():
    builder = ClassBuilder()
    ref = builder.method_ref("value", owner="Other")
    builder.method("run", b"\xb8" + u2(ref) + b"\xac")
    with pytest.raises(UnsupportedJava, match="closed-world class dependency"):
        compile_class(builder.build())


def test_float_descriptor_and_ldc_tag_rejected():
    with pytest.raises(UnsupportedJava, match="descriptors"):
        compile_class(one(b"\xb1", descriptor="(F)V"), descriptor="(F)V")
    builder = ClassBuilder()
    index = builder.utf8("not an Integer")
    builder.method("run", b"\x12" + bytes([index]) + b"\xac")
    with pytest.raises(UnsupportedJava, match="CONSTANT_Integer"):
        compile_class(builder.build())


def test_exception_handler_rejected_before_emission():
    with pytest.raises(UnsupportedJava, match="exception handlers"):
        compile_class(one(b"\x03\xac", handlers=((0, 1, 0, 0),)))


def test_stackmap_frames_are_checked_not_ignored():
    # ifeq byte 1 branches to byte 6, where one int is on the stack.
    code = b"\x04\x03\x99\x00\x04\x00\xac"
    builder = ClassBuilder(major=52).method("run", code, nested=(("StackMapTable", b"\x00\x01\x46\x01"),))
    assert compile_class(builder.build()).admitted_methods
    wrong = ClassBuilder(major=52).method("run", code, nested=(("StackMapTable", b"\x00\x01\x06"),))
    with pytest.raises(JavaError, match="incompatible StackMapTable"):
        compile_class(wrong.build())
    missing = ClassBuilder(major=52).method("run", code)
    with pytest.raises(JavaError, match="missing StackMapTable"):
        compile_class(missing.build())


def test_stackmap_top_does_not_fabricate_initialized_local():
    # Store local 0; full frame deliberately forgets it before an iload.
    frame = b"\x00\x01\xff\x00\x05\x00\x01\x00\x00\x00"
    builder = ClassBuilder(major=52).method("run", b"\x03\x3b\xa7\x00\x03\x1a\xac", nested=(("StackMapTable", frame),))
    with pytest.raises(JavaError, match="uninitialized"):
        compile_class(builder.build())


def loop_class():
    # sum(i for i in range(10)); 32-bit locals and a real back edge.
    return one(bytes.fromhex("03 3b 03 3c 1b 10 0a a2 00 0d 1a 1b 60 3b 84 01 01 a7 ff f3 1a ac"))


def recursive_class():
    builder = ClassBuilder()
    ref = builder.method_ref("factorial", "(I)I")
    builder.method("run", b"\x10\x05\xb8" + u2(ref) + b"\xac")
    builder.method("factorial", bytes.fromhex("1a 04 a3 00 05 04 ac 1a 1a 04 64 b8") + u2(ref) + bytes.fromhex("68 ac"), "(I)I", locals_=1)
    return builder.build()


def switch_class(lookup):
    builder = ClassBuilder()
    if lookup:
        # switch at byte 1, targets at bytes 28,30,32.
        code = b"\x05\xab\0\0" + struct.pack(">iiiii", 31, 2, 1, 27, 2) + struct.pack(">i", 29)
    else:
        code = b"\x05\xaa\0\0" + struct.pack(">iiiii", 27, 1, 2, 23, 25)
    code += b"\x04\xac\x10\x2a\xac\x03\xac" if lookup else b"\x04\xac\x10\x2a\xac\x03\xac"
    # Fix default: second arm is bipush (3 bytes), not iconst (2 bytes).
    code = bytearray(code)
    code[4:8] = struct.pack(">i", (len(code) - 2) - 1)
    builder.method("run", bytes(code))
    return builder.build()


@pytest.mark.pcc_gate(unavailable=_NATIVE)
@pytest.mark.parametrize("data,expected", [(one(b"\x10\x2a\xac"), 42), (loop_class(), 45),
                                          (recursive_class(), 120), (switch_class(False), 42), (switch_class(True), 42)])
def test_native_control_flow_and_static_calls(tmp_path, data, expected):
    compilation = compile_class(data)
    output = compilation.write_executable(tmp_path / "program")
    assert output.read_bytes()[:4] == b"\x7fELF"
    result = subprocess.run([str(output)], timeout=5, capture_output=True)
    assert result.returncode == expected, result.stderr


def checked_arithmetic(left, right, opcode, expected):
    builder = ClassBuilder()
    code = b"\x13" + u2(builder.integer(left))
    if right is not None:
        code += b"\x13" + u2(builder.integer(right))
    code += bytes([opcode]) + b"\x13" + u2(builder.integer(expected))
    code += b"\xa0\x00\x05\x03\xac\x04\xac"
    builder.method("run", code)
    return builder.build()


@pytest.mark.pcc_gate(unavailable=_NATIVE)
@pytest.mark.parametrize("left,right,opcode,expected", [
    (2147483647, 1, 0x60, -2147483648), (-2147483648, 1, 0x64, 2147483647),
    (65536, 65536, 0x68, 0), (-2147483648, None, 0x74, -2147483648),
    (1, 33, 0x78, 2), (-8, 34, 0x7A, -2), (-1, 32, 0x7C, -1), (-1, -1, 0x7C, 1),
    (0x12345678, 0x0F0F0F0F, 0x7E, 0x02040608), (0x1234, 0x4321, 0x80, 0x5335),
    (0x1234, 0x4321, 0x82, 0x5115), (255, None, 0x91, -1),
    (-1, None, 0x92, 65535), (65535, None, 0x93, -1),
])
def test_native_exact_int_semantics(tmp_path, left, right, opcode, expected):
    # Bytecode compares all 32 bits before returning an exit-code assertion.
    output = compile_class(checked_arithmetic(left, right, opcode, expected)).write_executable(tmp_path / "arithmetic")
    assert subprocess.run([str(output)], timeout=5).returncode == 0


@pytest.mark.pcc_gate(unavailable=_NATIVE)
def test_wide_locals_and_iinc_native(tmp_path):
    code = bytes.fromhex("04 c4 36 01 00 c4 84 01 00 00 29 c4 15 01 00 ac")
    output = compile_class(one(code, locals_=257)).write_executable(tmp_path / "wide")
    assert subprocess.run([str(output)], timeout=5).returncode == 42


@pytest.mark.pcc_gate(
    env="PCC_JAVA_REFERENCE",
    unavailable=_NATIVE or (None if shutil.which("java") else "Java development oracle executable unavailable"),
)
def test_dev_only_jvm_differential(tmp_path):
    # The same independently authored class is run by Java only as a reference.
    builder = ClassBuilder("Reference")
    run_ref = builder.method_ref("run")
    exit_ref = builder.method_ref("exit", "(I)V", owner="java/lang/System")
    builder.method("run", parse_class(loop_class()).methods[0].code.bytecode)
    builder.method("main", b"\xb8" + u2(run_ref) + b"\xb8" + u2(exit_ref) + b"\xb1", "([Ljava/lang/String;)V")
    data = builder.build()
    (tmp_path / "Reference.class").write_bytes(data)
    reference = subprocess.run([shutil.which("java"), "-Xverify:all", "-cp", str(tmp_path), "Reference"], timeout=15, capture_output=True)
    assert reference.returncode == 45, reference.stderr
    output = compile_class(data).write_executable(tmp_path / "owned")
    assert subprocess.run([str(output)], timeout=5).returncode == reference.returncode


@pytest.mark.pcc_gate(unavailable=_NATIVE)
@pytest.mark.parametrize("left,right,opcode,expected", [
    (2147483647, 1, 0x60, -2147483648), (-2147483648, 1, 0x64, 2147483647),
    (65536, 65536, 0x68, 0), (-2147483648, None, 0x74, -2147483648),
    (1, 33, 0x78, 2), (-8, 34, 0x7A, -2), (-1, 32, 0x7C, -1), (-1, -1, 0x7C, 1),
    (0x12345678, 0x0F0F0F0F, 0x7E, 0x02040608), (0x1234, 0x4321, 0x80, 0x5335),
    (0x1234, 0x4321, 0x82, 0x5115), (255, None, 0x91, -1),
    (-1, None, 0x92, 65535), (65535, None, 0x93, -1),
])
def test_full_i32_native_observer(tmp_path, monkeypatch, left, right, opcode, expected):
    from tests.java.owned_execution import observe_i32

    builder = ClassBuilder()
    code = b"\x13" + u2(builder.integer(left))
    if right is not None:
        code += b"\x13" + u2(builder.integer(right))
    code += bytes([opcode, 0xAC])
    data = builder.method("run", code).build()
    (tmp_path / "input.class").write_bytes(data)
    assert observe_i32(compile_class(data), tmp_path, monkeypatch) == expected


def test_modern_return_followed_by_unframed_dead_code_rejected():
    # Go peer-review literal regression; previously the last ireturn was skipped.
    data = ClassBuilder(major=51).method("run", b"\x03\xac\xac").build()
    with pytest.raises(JavaError, match="frame after unconditional"):
        compile_class(data)


def test_declared_disconnected_block_is_typechecked():
    # Frame at byte 2 has an empty stack, so its dead ireturn must underflow.
    data = ClassBuilder(major=51).method("run", b"\x03\xac\xac",
        nested=(("StackMapTable", b"\x00\x01\x02"),)).build()
    with pytest.raises(JavaError, match="underflow"):
        compile_class(data)


def test_valid_declared_disconnected_block_is_preserved():
    data = ClassBuilder(major=51).method("run", b"\x03\xac\x04\xac",
        nested=(("StackMapTable", b"\x00\x01\x02"),)).build()
    checked = verify(data)
    assert set(checked.frames) == {0, 1, 2, 3}
    assert "bc_2:" in str(compile_class(data).module)


@pytest.mark.parametrize("flags", [0x000B, 0x000D, 0x000E, 0x000F])
def test_method_visibility_flags_are_mutually_exclusive(flags):
    with pytest.raises(JavaError, match="mutually exclusive"):
        compile_class(one(b"\x03\xac", flags=flags))

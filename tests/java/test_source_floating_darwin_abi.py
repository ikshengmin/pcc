"""Strict external-caller regression for Darwin's compact float stack slots.

Apple ARM64 calling convention: stack arguments occupy their natural size,
unlike AAPCS64's minimum eight-byte slots. Float arguments 9/10/11/12 therefore
occupy SP+0/+4/+8/+12 at the call, after v0-v7 have been allocated:
https://developer.apple.com/documentation/xcode/writing-arm64-code-for-apple-platforms
Java primitive parameter/result identity is specified by JLS21 4.2.3 and 15.12:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-4.html#jls-4.2.3
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.12

This is NOT PCC on both sides of the ABI. A real clang compiles the C caller
once, separately from both providers. The identical caller object is linked
against (1) PCC's unchanged Java original and (2) a clang C identity-provider
control. The C provider is an ABI control, not a Java semantic reference.

Known source boundary at introduction: self_backend_aarch64_darwin_abi uses
eight-byte scalar stack slots, so the Java callee reads argument 11 in place
of argument 10. This test must FAIL on that implementation; no xfail, expected
wrong value, PCC-generated caller or backend workaround masks the mismatch.
Cross-object emission alone does not qualify Darwin ABI interoperability.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys

import pytest

from pcc.frontends.java import compile_source
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_raw_reference import digest, record_process


TARGET = "arm64-apple-darwin"
DESCRIPTOR = "(" + "F" * 12 + ")F"
JAVA_SOURCE = """public final class FloatStack {
    public static float run(float a, float b, float c, float d,
                            float e, float f, float g, float h,
                            float i, float j, float k, float l) {
        return j;
    }
}
"""

# JAVA_ENTRY is a compile-time symbol-binding option, not source rewriting.
# Volatile inputs plus separate non-LTO translation units force a real call.
# The nonzero 11th argument occupies the exact slot incorrectly read by PCC;
# a ten-argument-only test would instead observe indeterminate stack padding.
C_CALLER = """#include <float.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#if !defined(__APPLE__) || !defined(__aarch64__)
#error This caller requires the actual Apple ARM64 ABI
#endif
_Static_assert(sizeof(float) == 4 && FLT_RADIX == 2 && FLT_MANT_DIG == 24 && FLT_MAX_EXP == 128,
               "observer requires binary32 float");
_Static_assert(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__,
               "observer emits little-endian raw float bytes");
extern float JAVA_ENTRY(float, float, float, float, float, float,
                        float, float, float, float, float, float);
static volatile float inputs[12] = {
    0.25f, 1.25f, 2.25f, 3.25f, 4.25f, 5.25f, 6.25f, 7.25f,
    13.0f, 42.0f, -91.0f, 17.0f
};
int main(void) {
    float result = JAVA_ENTRY(inputs[0], inputs[1], inputs[2], inputs[3],
                              inputs[4], inputs[5], inputs[6], inputs[7],
                              inputs[8], inputs[9], inputs[10], inputs[11]);
    unsigned char raw[sizeof(result)];
    memcpy(raw, &result, sizeof(raw));
    if (fwrite(raw, 1, sizeof(raw), stdout) != sizeof(raw)) return 111;
    return fflush(stdout) == 0 ? 0 : 112;
}
"""

C_REFERENCE_PROVIDER = """float JAVA_ENTRY(float a, float b, float c, float d,
                 float e, float f, float g, float h,
                 float i, float j, float k, float l) {
    return j;
}
"""

EXPECTED_BYTES = bytes.fromhex("00002842")  # binary32 42.0, not an exit-code truncation
CLANG = os.environ.get("PCC_JAVA_DARWIN_CLANG") or shutil.which("clang")
NATIVE_REASON = ("requires actual Darwin arm64 execution, not a cross object or Rosetta" if
                 sys.platform != "darwin" or platform.machine().lower() != "arm64" else
                 None if CLANG else "requires clang; select it with PCC_JAVA_DARWIN_CLANG")


def compile_provider(monkeypatch):
    with monkeypatch.context() as owned:
        forbid_bytecode_or_external_owner(owned)
        compilation = compile_source(JAVA_SOURCE, filename="FloatStack.java",
                                     descriptor=DESCRIPTOR, target=TARGET)
        data = compilation.emit_object()
    assert data[:4] == b"\xcf\xfa\xed\xfe"  # Mach-O 64-bit little endian
    assert compilation.entry == ("run", DESCRIPTOR)
    assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", compilation.entry_symbol)
    return compilation, data


def test_float_stack_original_has_owned_darwin_cross_object_only(monkeypatch):
    before = hashlib.sha256(JAVA_SOURCE.encode("utf-8")).hexdigest()
    compilation, data = compile_provider(monkeypatch)
    assert "ret float" in str(compilation.module) and len(data) > 32
    assert hashlib.sha256(JAVA_SOURCE.encode("utf-8")).hexdigest() == before
    # This test intentionally makes no call, execution or external-ABI claim.


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
def test_real_clang_caller_observes_tenth_java_float_on_darwin_arm64(tmp_path, monkeypatch, request):
    # The gate is not a permission to access a user's Mac. Only an explicitly
    # selected actual-target CI runner executes this ordinary native test.
    assert sys.platform == "darwin" and platform.machine().lower() == "arm64"
    clang = Path(CLANG).resolve(strict=True)
    root = Path(__file__).resolve().parents[2]
    closure = [Path(__file__), root / "tests/java/test_source_frontend.py",
               root / "tests/java/test_source_raw_reference.py",
               root / "pcc/frontends/java/source_syntax.py", root / "pcc/frontends/java/source_sema.py",
               root / "pcc/frontends/java/source_lowering.py", root / "pcc/frontends/java/source_types.py",
               root / "pcc/frontends/java/floating_semantics.py",
               root / "pcc/backend/self_backend_aarch64_darwin_abi.py"]
    before = {"clang": {"path": str(clang), "sha256": digest(clang)},
              "source_inputs": {path.relative_to(root).as_posix(): digest(path) for path in closure}}
    retained = {}

    def retain(path):
        retained[path] = digest(path)

    def finish_identity():
        after = {"clang": {"path": str(clang), "sha256": digest(clang)},
                 "source_inputs": {path.relative_to(root).as_posix(): digest(path) for path in closure}}
        unchanged = all(digest(path) == expected for path, expected in retained.items())
        (tmp_path / "owner-post.json").write_text(json.dumps({**after, "unchanged": unchanged,
            "artifact_sha256": {path.name: value for path, value in retained.items()}}, indent=2))
        assert before == after and unchanged

    request.addfinalizer(finish_identity)
    (tmp_path / "owner-before.json").write_text(json.dumps({**before, "target": TARGET,
        "java_owner": "host-pcc0/java-source/pcc-self-owned-Mach-O",
        "caller_and_link_owner": "external-clang-development-ABI-reference",
        "native_compiler_qualified": False, "pcc_owns_caller": False}, indent=2))
    version = record_process((str(clang), "--version"), tmp_path, "clang-version")
    assert version[0] == 0 and b"clang" in version[1].lower(), version

    java_path, caller_path, control_path = (tmp_path / name for name in
                                          ("FloatStack.java", "caller.c", "reference-provider.c"))
    for path, text in ((java_path, JAVA_SOURCE), (caller_path, C_CALLER), (control_path, C_REFERENCE_PROVIDER)):
        path.write_bytes(text.encode("utf-8"))
        retain(path)
    compilation, provider_bytes = compile_provider(monkeypatch)
    (tmp_path / "provider.ll").write_text(str(compilation.module), encoding="utf-8")
    retain(tmp_path / "provider.ll")
    java_object = tmp_path / "java-provider.o"
    java_object.write_bytes(provider_bytes)
    retain(java_object)

    options = ("-arch", "arm64", "-std=c11", "-O0", "-fno-lto", "-fno-fast-math",
               "-DJAVA_ENTRY=" + compilation.entry_symbol)
    caller_object, control_object = tmp_path / "caller.o", tmp_path / "reference-provider.o"
    for name, source, output in (("caller-compile", caller_path, caller_object),
                                 ("control-compile", control_path, control_object)):
        result = record_process((str(clang), *options, "-c", str(source), "-o", str(output)), tmp_path, name, 20)
        assert result[0] == 0, result
        retain(output)
    # Compile the caller exactly once. Both links consume that same frozen
    # external object; neither PCC nor the Java provider supplies an ABI shim.
    executables = {}
    for name, provider in (("native", java_object), ("reference", control_object)):
        executable = tmp_path / name
        linked = record_process((str(clang), "-arch", "arm64", "-fno-lto", str(caller_object),
                                 str(provider), "-o", str(executable)), tmp_path, name + "-link", 20)
        assert linked[0] == 0, linked
        retain(executable)
        executables[name] = executable
    reference = record_process((str(executables["reference"]),), tmp_path, "reference-run", 5)
    actual = record_process((str(executables["native"]),), tmp_path, "native-run", 5)
    (tmp_path / "comparison.json").write_text(json.dumps({
        "comparison": "identical-external-caller-raw-process-streams", "target": TARGET,
        "java_original_sha256": digest(java_path), "caller_object_sha256": digest(caller_object),
        "native_provider_sha256": digest(java_object), "control_provider_sha256": digest(control_object),
        "argument_count": 12, "selected_argument": 10,
        "apple_stack_offsets_at_call": {"argument9": 0, "argument10": 4, "argument11": 8, "argument12": 12},
        "expected_stdout_hex": EXPECTED_BYTES.hex(),
        "reference": {"exit_code": reference[0], "stdout_hex": reference[1].hex(), "stderr_hex": reference[2].hex()},
        "native": {"exit_code": actual[0], "stdout_hex": actual[1].hex(), "stderr_hex": actual[2].hex()},
        "boundary_under_test": "Apple four-byte float slots versus the inherited eight-byte PCC scalar layout",
    }, indent=2))
    assert reference == (0, EXPECTED_BYTES, b""), "independent clang ABI control failed"
    assert actual == reference, "PCC Java callee disagrees with the real external Apple ARM64 caller"

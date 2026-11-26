from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from pathlib import Path

import pytest

from pcc.frontends.python import pipeline
from pcc.frontends.python.codegen.unsafe_lowering import UnsafeIntrinsicMixin

REPO_ROOT = Path(__file__).absolute().parents[2]
ERRNO_SOURCE = REPO_ROOT / "pcc" / "runtime" / "py" / "freestanding_errno.py"


def _emit_ir(tmp_path: Path, target: str | None = None) -> str:
    output = tmp_path / "freestanding_errno.ll"
    pipeline.compile_python(
        str(ERRNO_SOURCE),
        str(output),
        emit_llvm_only=True,
        python_library=True,
        target_triple=target,
    )
    return output.read_text(encoding="utf-8")


def test_linux_errno_ir_has_pcc_tls_and_no_libc_or_loader_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        UnsafeIntrinsicMixin, "_target_sys_platform_text", lambda self: "linux"
    )
    monkeypatch.setattr(
        UnsafeIntrinsicMixin, "_target_machine_text", lambda self: "x86_64"
    )
    ir_text = _emit_ir(tmp_path)
    assert "thread_local global i32 0" in ir_text
    for forbidden in (
        "@__error",
        "@__errno_location",
        "@strerror",
        "@strerror_r",
        "@dlopen",
        "@dlsym",
        "@dlclose",
    ):
        assert forbidden not in ir_text


def _build_errno_object(tmp_path: Path, emitter: str) -> Path:
    ir_text = _emit_ir(tmp_path)
    source = tmp_path / "freestanding_errno.ll"
    output = tmp_path / f"freestanding_errno_{emitter}.o"
    if emitter == "self":
        from pcc.backend.owned_object_emit import emit_owned_object
        from pcc.frontends.python.pipeline_targets import host_target_triple

        # This is the actual owned object, not host-assembled self text.
        output.write_bytes(emit_owned_object(ir_text, host_target_triple()))
        return output
    result = subprocess.run(
        ["clang", "-c", str(source), "-o", str(output)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return output


@pytest.mark.parametrize("emitter", ["llvm", "self"])
def test_errno_message_and_native_tls_are_thread_isolated(
    tmp_path: Path, emitter: str
) -> None:
    supported = (sys.platform == "darwin" and platform.machine() == "arm64") or (
        sys.platform.startswith("linux") and platform.machine() == "x86_64"
    )
    assert supported

    obj = _build_errno_object(tmp_path, emitter)
    undefined = subprocess.run(
        ["nm", "-u", str(obj)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert undefined.returncode == 0, undefined.stdout + undefined.stderr
    undefined_names = {
        line.split()[-1].lstrip("_")
        for line in undefined.stdout.splitlines()
        if line.strip()
    }
    if sys.platform == "darwin":
        # Both real Mach-O objects contain the fallback native TLS
        # descriptor; its initializer names the libSystem TLV bootstrap.
        # Self emits the access sequence itself, not a loader-free descriptor.
        expected = {"error", "dlclose", "dlopen", "dlsym", "tlv_bootstrap"}
        assert undefined_names == expected
    else:
        assert undefined_names == set()

    harness = tmp_path / "errno_harness.c"
    executable = tmp_path / f"errno_harness_{emitter}"
    harness.write_text(
        "#include <errno.h>\n"
        "#include <pthread.h>\n"
        "#include <stdint.h>\n"
        "#include <stdio.h>\n"
        "#include <string.h>\n"
        "int *pcc_errno_location(void);\n"
        "int pcc_errno_get(void);\n"
        "void pcc_errno_set(int);\n"
        "int pcc_errno_message_into(int, char *, uint64_t);\n"
        "typedef struct { int code; int seen; int host_seen; uintptr_t location; char message[128]; } Task;\n"
        "static pthread_mutex_t gate_lock = PTHREAD_MUTEX_INITIALIZER;\n"
        "static pthread_cond_t gate_cond = PTHREAD_COND_INITIALIZER;\n"
        "static int gate_arrived = 0;\n"
        "static void gate_wait(void) {\n"
        "  pthread_mutex_lock(&gate_lock);\n"
        "  gate_arrived++;\n"
        "  if (gate_arrived == 2) pthread_cond_broadcast(&gate_cond);\n"
        "  while (gate_arrived < 2) pthread_cond_wait(&gate_cond, &gate_lock);\n"
        "  pthread_mutex_unlock(&gate_lock);\n"
        "}\n"
        "static void *worker(void *raw) {\n"
        "  Task *task = (Task *)raw;\n"
        "#if defined(__linux__)\n"
        "  errno = task->code == ENOENT ? EACCES : ENOENT;\n"
        "#endif\n"
        "  pcc_errno_set(task->code);\n"
        "  gate_wait();\n"
        "  task->seen = pcc_errno_get();\n"
        "  task->host_seen = errno;\n"
        "  task->location = (uintptr_t)pcc_errno_location();\n"
        "  pcc_errno_message_into(task->seen, task->message, sizeof(task->message));\n"
        "  return NULL;\n"
        "}\n"
        "int main(void) {\n"
        "  char message[128];\n"
        "#if defined(__linux__)\n"
        "  /* Linux owns independent native TLS, not libc's errno location. */\n"
        "  if (pcc_errno_location() == &errno) return 10;\n"
        "  pcc_errno_set(EACCES); errno = ENOENT;\n"
        "  if (pcc_errno_get() != EACCES || errno != ENOENT) return 26;\n"
        "  pcc_errno_set(ENOENT); errno = EACCES;\n"
        "  if (pcc_errno_get() != ENOENT || errno != EACCES) return 27;\n"
        "#else\n"
        "  /* Darwin deliberately aliases the named host errno ABI. */\n"
        "  if (pcc_errno_location() != &errno) return 10;\n"
        "  errno = ENOENT;\n"
        "  if (pcc_errno_get() != ENOENT) return 10;\n"
        "#endif\n"
        "  if (pcc_errno_message_into(ENOENT, message, sizeof(message)) != 0) return 11;\n"
        "  if (strcmp(message, strerror(ENOENT)) != 0) return 12;\n"
        "  pcc_errno_set(EACCES);\n"
        "  if (pcc_errno_get() != EACCES) return 13;\n"
        "  if (pcc_errno_message_into(EACCES, message, sizeof(message)) != 0) return 14;\n"
        "  if (strcmp(message, strerror(EACCES)) != 0) return 15;\n"
        "  if (pcc_errno_message_into(9999, message, sizeof(message)) != 0) return 16;\n"
        '  if (strstr(message, "9999") == NULL) return 17;\n'
        "#if defined(__linux__) && defined(__GLIBC__)\n"
        "  for (int code = 0; code <= 133; code++) {\n"
        "    if (pcc_errno_message_into(code, message, sizeof(message)) != 0) return 24;\n"
        "    if (strcmp(message, strerror(code)) != 0) return 25;\n"
        "  }\n"
        "#endif\n"
        "  uintptr_t main_location = (uintptr_t)pcc_errno_location();\n"
        "  Task left = { ENOENT, 0, 0, 0, {0} };\n"
        "  Task right = { EACCES, 0, 0, 0, {0} };\n"
        "  pthread_t a, b;\n"
        "  if (pthread_create(&a, NULL, worker, &left) != 0) return 19;\n"
        "  if (pthread_create(&b, NULL, worker, &right) != 0) return 20;\n"
        "  pthread_join(a, NULL); pthread_join(b, NULL);\n"
        "  if (left.seen != left.code || right.seen != right.code) return 21;\n"
        "  if (left.location == right.location || left.location == main_location || right.location == main_location) return 28;\n"
        "#if defined(__linux__)\n"
        "  if (left.host_seen != EACCES || right.host_seen != ENOENT || pcc_errno_get() != EACCES) return 29;\n"
        "#else\n"
        "  if (left.host_seen != left.code || right.host_seen != right.code) return 29;\n"
        "#endif\n"
        "  if (strcmp(left.message, strerror(left.code)) != 0) return 22;\n"
        "  if (strcmp(right.message, strerror(right.code)) != 0) return 23;\n"
        "  return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    build = subprocess.run(
        ["cc" if emitter == "self" else "clang", str(harness), str(obj), "-pthread", "-o", str(executable)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    run = subprocess.run(
        [str(executable)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert (
        run.returncode == 0
    ), f"exit={run.returncode} stdout={run.stdout!r} stderr={run.stderr!r}"



def test_errno_owned_linux_object_has_no_undefined_raw_closure(tmp_path):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.elf_x86_64 import parse_relocatable, STB_GLOBAL, STB_WEAK

    target = "x86_64-unknown-linux-gnu"
    ir = _emit_ir(tmp_path, target)
    image = emit_owned_object(ir, target)
    symbols = [symbol for symbol in parse_relocatable(image).symbols
               if symbol.binding in (STB_GLOBAL, STB_WEAK)]
    assert {symbol.name for symbol in symbols if symbol.section_index == 0} == set()
    assert {"pcc_errno_location", "pcc_errno_get", "pcc_errno_set", "pcc_errno_message_into"} <= {
        symbol.name for symbol in symbols if symbol.section_index != 0
    }


def test_errno_darwin_retains_named_libsystem_callback_abi(tmp_path):
    from pcc.backend.owned_object_emit import emit_owned_object
    from tests.owned_ir_validation import verify_ir_text

    target = "arm64-apple-darwin"
    ir = _emit_ir(tmp_path, target)
    verify_ir_text(ir)
    assert "@__error" in ir and "@dlopen" in ir and "@dlsym" in ir
    body = re.search(r"^define[^\n]*@pcc_errno_message_into\([^\n]*\{\n(.*?)^}", ir, re.M | re.S)
    assert body is not None
    assert re.search(r"call i32 \(i32, ptr, i64\) %[^\n]+\(i32 [^,]+, ptr [^,]+, i64 [^)]+\)", body.group(1)), body.group(1)
    assert "@pcc_null_callee_trap" not in body.group(1)
    from pcc.backend.macho_spec import parse_object, N_EXT, N_TYPE, N_UNDF

    image = emit_owned_object(ir, target)
    assert image[:4] == b"\xcf\xfa\xed\xfe"
    obj = parse_object(image)
    symbols = obj.symbols()
    assert {row["name"] for row in symbols if row["n_type"] & N_EXT
            and row["n_type"] & N_TYPE == N_UNDF} == {
        "___error", "__tlv_bootstrap", "_dlclose", "_dlopen", "_dlsym",
    }
    # The bootstrap must be the actual TLV descriptor relocation, not an
    # arbitrary undefined-symbol relaxation.
    references = [(section["sectname_str"], relocation)
                  for section in obj.sections() for relocation in obj.relocations(section)
                  if relocation["r_extern"] and symbols[relocation["r_symbolnum"]]["name"] == "__tlv_bootstrap"]
    assert len(references) == 1 and references[0][0] == "__thread_vars"
    assert references[0][1]["r_address"] == 0 and references[0][1]["r_length"] == 3


def _guarded_callback_ir(tmp_path, target, argument):
    source = tmp_path / "guarded_callback.py"
    output = tmp_path / "guarded_callback.ll"
    source.write_text('''from pcc import i64
from pcc.extern import c_abi_typed_export, c_ptr
from pcc.unsafe import call_i32_i32_ptr_i64, null
__pcc_freestanding__ = True
@c_abi_typed_export("guarded_callback", "i32", ("ptr", "i32", "ptr", "i64"))
def guarded_callback(fn: c_ptr, value: i64, output: c_ptr, capacity: i64) -> i64:
    return call_i32_i32_ptr_i64(''' + argument + ")\n")
    pipeline.compile_python(str(source), str(output), emit_llvm_only=True,
                            python_library=True, target_triple=target)
    return output.read_text()


@pytest.mark.parametrize("target, matched", [
    ("x86_64-unknown-linux-gnu", False), ("arm64-apple-darwin", True),
])
def test_raw_callback_optional_target_is_a_literal_compile_time_guard(tmp_path, target, matched):
    from pcc.backend.owned_object_emit import emit_owned_object
    from tests.owned_ir_validation import verify_ir_text

    ir = _guarded_callback_ir(tmp_path, target, 'fn, value, output, capacity, "darwin"')
    verify_ir_text(ir)
    body = re.search(r"^define[^\n]*@guarded_callback\([^\n]*\{\n(.*?)^}", ir, re.M | re.S)
    assert body is not None
    if matched:
        assert re.search(r"call i32 \(i32, ptr, i64\) %[^\n]+\(i32 [^,]+, ptr [^,]+, i64 [^)]+\)", body.group(1))
        assert emit_owned_object(ir, target)[:4] == b"\xcf\xfa\xed\xfe"
    else:
        assert "call " not in body.group(1)
        returned = re.search(r"(%[^ ]+) = trunc i64 -1 to i32\n  ret i32 \1", body.group(1))
        assert returned or "ret i32 -1" in body.group(1)
        from pcc.backend.elf_x86_64 import parse_relocatable, STB_GLOBAL, STB_WEAK
        obj = emit_owned_object(ir, target)
        assert not any(symbol.section_index == 0 and symbol.binding in (STB_GLOBAL, STB_WEAK)
                       for symbol in parse_relocatable(obj).symbols)


@pytest.mark.parametrize("argument", [
    'fn, value, output, capacity, "unsupported"',
    'fn, value, output, capacity, value',
    'fn, value, output, capacity, ""',
    'fn, value, output, capacity, "darwin", "linux"',
    'fn, value, output, capacity, target_platform="darwin"',
])
def test_raw_callback_rejects_invalid_target_literal(tmp_path, argument):
    with pytest.raises(NotImplementedError, match="target platform must be a supported string literal|and one optional target-platform literal"):
        _guarded_callback_ir(tmp_path, "x86_64-unknown-linux-gnu", argument)


def test_unguarded_null_callback_still_binds_the_real_trap(tmp_path):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.elf_x86_64 import parse_relocatable, STB_GLOBAL, STB_WEAK

    target = "x86_64-unknown-linux-gnu"
    ir = _guarded_callback_ir(tmp_path, target, "null(), value, output, capacity")
    obj = emit_owned_object(ir, target)
    assert {symbol.name for symbol in parse_relocatable(obj).symbols
            if symbol.section_index == 0 and symbol.binding in (STB_GLOBAL, STB_WEAK)} == {"pcc_null_callee_trap"}



def test_guarded_and_unguarded_callback_native_abi(tmp_path):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline_targets import host_target_triple

    host = sys.platform
    assert host in ("linux", "darwin")
    other = "darwin" if host == "linux" else "linux"
    target = host_target_triple()
    source = tmp_path / "callback_native.py"
    ir_path = tmp_path / "callback_native.ll"
    declarations = []
    for name, suffix in (("same", ', "' + host + '"'), ("other", ', "' + other + '"'), ("raw", "")):
        declarations.append(
            '@c_abi_typed_export("callback_' + name + '", "i32", ("ptr", "i32", "ptr", "i64"))\n'
            'def callback_' + name + '(fn: c_ptr, value: i64, output: c_ptr, capacity: i64) -> i64:\n'
            '    return call_i32_i32_ptr_i64(fn, value, output, capacity' + suffix + ')\n'
        )
    source.write_text(
        'from pcc import i64\nfrom pcc.extern import c_abi_typed_export, c_ptr\n'
        'from pcc.unsafe import call_i32_i32_ptr_i64\n__pcc_freestanding__ = True\n'
        + ''.join(declarations)
    )
    pipeline.compile_python(str(source), str(ir_path), emit_llvm_only=True,
                            python_library=True, target_triple=target)
    obj = tmp_path / "callback_native.o"
    obj.write_bytes(emit_owned_object(ir_path.read_text(), target))
    harness = tmp_path / "callback_native.c"
    harness.write_text(
        '#include <stdint.h>\n'
        'typedef int32_t (*Callback)(int32_t, void *, uint64_t);\n'
        'int32_t callback_same(Callback, int32_t, void *, uint64_t);\n'
        'int32_t callback_other(Callback, int32_t, void *, uint64_t);\n'
        'int32_t callback_raw(Callback, int32_t, void *, uint64_t);\n'
        'static int calls;\n'
        'static int32_t probe(int32_t value, void *output, uint64_t capacity) {\n'
        '  if (value != -123 || capacity != UINT64_C(4294967297)) return 1;\n'
        '  ++calls; *(int32_t *)output = value; return -37;\n'
        '}\n'
        'int main(void) {\n'
        '  int32_t value = 99;\n'
        '  if (callback_other((Callback)0, -123, &value, UINT64_C(4294967297)) != -1 || calls != 0 || value != 99) return 1;\n'
        '  if (callback_same(probe, -123, &value, UINT64_C(4294967297)) != -37 || calls != 1 || value != -123) return 2;\n'
        '  value = 99;\n'
        '  if (callback_raw(probe, -123, &value, UINT64_C(4294967297)) != -37 || calls != 2 || value != -123) return 3;\n'
        '  return 0;\n'
        '}\n'
    )
    executable = tmp_path / "callback_native"
    linked = subprocess.run(["cc", str(harness), str(obj), "-o", str(executable)],
                            capture_output=True, text=True, timeout=30)
    assert linked.returncode == 0, linked.stdout + linked.stderr
    ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=15)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")

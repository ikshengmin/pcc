"""Host checks for the constructor audit used on actual native linker inputs."""
import re
from pathlib import Path

import pytest

from tests.python.constructor_native_support import audit_caller_ir
from tests.python.test_callable_construction_codegen import SOURCES


PERSISTENT_STATIC = '''def outer():
    yield 0
    def inner(value=7):
        return value
    yield inner
'''


def _audit(text, persistent=False):
    return audit_caller_ir(text, Path("actual-host-emitted.ll"), persistent)


@pytest.fixture(scope="module")
def emitted_ir(tmp_path_factory):
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple

    cache = {}

    def emit(source):
        if source not in cache:
            directory = tmp_path_factory.mktemp("constructor-audit-ir")
            path = directory / "constructor_audit.py"
            output = directory / "constructor_audit.ll"
            path.write_text(source, encoding="utf-8")
            # The explicit self emit-only route selects the same owned
            # default pass pipeline as the native observer's compile. It
            # stops before runtime selection, object emission and linking.
            compile_python(str(path), str(output), emit_llvm_only=True,
                           backend="self", libpython_mode="off", ir_scaffold_mode="on",
                           target_triple=host_target_triple())
            cache[source] = output.read_text(encoding="utf-8")
        return cache[source]

    return emit


@pytest.mark.parametrize("source, helper, persistent", [
    (SOURCES[0], "py_func_new_static_def", False),
    (SOURCES[1], "py_func_new_signature_slots", False),
    (SOURCES[2], "py_func_new_signature_slots", True),
    (SOURCES[3], "py_func_new_signature_slots", False),
    (PERSISTENT_STATIC, "py_func_new_static_def", True),
])
def test_native_constructor_audit_accepts_actual_constructor_routes(source, helper, persistent, emitted_ir):
    rows = _audit(emitted_ir(source), persistent)
    selected = [row for row in rows if row["constructor_helper"] == helper
                and (not persistent or row["persistent"])]
    assert selected
    for row in selected:
        assert row["no_callback_or_branch_before_status_check"]
        if helper == "py_func_new_static_def":
            assert row["static_contract"]["descriptor_words"] > 0
            assert row["static_contract"]["success_handoff_paths"] > 0
            assert row["metadata_initialization"] == "runtime_static_constructor"
            assert row["inputs_retired_before_metadata"] is None
        else:
            assert row["inputs_retired_before_metadata"] is True
            assert row["metadata_initialization"] == "caller_slot_helper"


@pytest.fixture(scope="module")
def static_ir(emitted_ir):
    return emitted_ir(SOURCES[0])


def _static_site(text, persistent=False):
    for function in re.finditer(r"(?ms)^define[^\n]*\{\n.*?^}", text):
        if persistent and "__gen_resume" not in function.group().splitlines()[0]:
            continue
        call = re.search(r"(?m)^\s*(%\S+) = call i64[^\n]*@py_func_new_static_def\(([^\n]+)\)\n",
                         function.group())
        if call is not None:
            start, end = (function.start() + call.start(), function.start() + call.end())
            output = re.search(r", ptr (%[^ ,]+)$", call.group(2)).group(1)
            return start, end, call.group(1), output, function
    raise AssertionError("actual static constructor site missing")


@pytest.mark.parametrize("mutation", [
    "wrong-status", "callback-before-status", "unregistered-output",
    "mutable-descriptor", "cleared-output-success", "wrong-take-owner",
    "safepoint-after-frame-retirement", "wrong-frame-protocol", "missing-success-leave",
])
def test_native_static_constructor_audit_rejects_corruption(static_ir, mutation):
    text = static_ir
    assert _audit(text), "negative control needs a passing actual constructor"
    start, end, status, output, function = _static_site(text)
    if mutation == "wrong-status":
        old = "icmp slt i64 " + status
        assert old in text[end:]
        text = text[:end] + text[end:].replace(old, "icmp sgt i64 " + status, 1)
    elif mutation == "callback-before-status":
        callback = "%constructor.audit.unexpected.callback"
        assert callback not in text
        text = text[:end] + "  " + callback + " = call i64 () @py_err_occurred()\n" + text[end:]
    elif mutation == "unregistered-output":
        call = text[start:end].replace("ptr " + output + ")", "ptr %unregistered.constructor.out)")
        text = text[:start] + call + text[end:]
        entry = text.index("entry:\n", function.start()) + len("entry:\n")
        text = text[:entry] + ("  %unregistered.constructor.out = alloca ptr\n"
                              "  store ptr null, ptr %unregistered.constructor.out\n") + text[entry:]
    elif mutation == "mutable-descriptor":
        descriptor = re.search(r"ptr (%static\.agg\.func\.sig\.[^ ,]+)", text[start:end]).group(1)
        definition = re.search(r"(?m)^\s*" + re.escape(descriptor)
                               + r" = [^\n]*ptr (@[^ ,]+),", text)
        assert definition is not None
        text, count = re.subn(r"(?m)^(" + re.escape(definition.group(1))
                              + r" = [^\n]*?)\bconstant\b", r"\1global", text, count=1)
        assert count == 1
    elif mutation == "wrong-take-owner":
        text, count = re.subn(r"(@pcc_gc_take_pinned_slot\(ptr )" + re.escape(output) + r"(?=,)",
                              r"\1null", text, count=1)
        assert count == 1
    elif mutation == "cleared-output-success":
        branch = re.search(r"br i1 %[^,\s]+, label %[^,\s]+, label (%[^\s]+)", text[end:])
        assert branch is not None
        ready = re.search(r"(?m)^" + re.escape(branch.group(1).lstrip("%")) + r":\n", text)
        assert ready is not None
        insertion = ready.end()
        line = "  call void (ptr, ptr) @pcc_gc_store_root(ptr " + output + ", ptr null)\n"
        text = text[:insertion] + line + text[insertion:]
    else:
        # Anchor the real final handoff, not the earlier error-cleanup leave.
        # Mutate its protocol, balance, or the no-parking interval between
        # successful retirement and the exact owning take.
        take = re.search(r"(?m)^\s*%\S+ = call ptr[^\n]*@pcc_gc_take_pinned_slot\(ptr "
                         + re.escape(output) + r",[^\n]*\)\n", text[end:])
        assert take is not None
        take_start = end + take.start()
        block = list(re.finditer(r"(?m)^[-a-zA-Z0-9_.$]+:\n", text[:take_start]))[-1]
        leave = re.search(r"(?m)^\s*call void[^\n]*@pcc_gc_frame_leave_lifo(?:_mapped)?\(ptr "
                          + re.escape(output) + r"\)\n", text[block.end():take_start])
        assert leave is not None and "@pcc_gc_safepoint(" in text
        leave_start, leave_end = block.end() + leave.start(), block.end() + leave.end()
        if mutation == "wrong-frame-protocol":
            text = (text[:leave_start] + text[leave_start:leave_end].replace(
                "@pcc_gc_frame_leave_lifo", "@pcc_gc_frame_leave", 1) + text[leave_end:])
        elif mutation == "missing-success-leave":
            text = text[:leave_start] + text[leave_end:]
        else:
            text = text[:leave_end] + "  call void () @pcc_gc_safepoint()\n" + text[leave_end:]
    if mutation == "callback-before-status":
        with pytest.raises(RuntimeError, match="Callback or failure edge before ownership/status check"):
            _audit(text)
    elif mutation == "safepoint-after-frame-retirement":
        with pytest.raises(RuntimeError, match="Static output frame retired before its final take"):
            _audit(text)
    elif mutation == "wrong-frame-protocol":
        with pytest.raises(RuntimeError, match="Static output frame retirement uses the wrong protocol"):
            _audit(text)
    elif mutation == "missing-success-leave":
        with pytest.raises(RuntimeError, match="Static output (lifetime disagrees at CFG join|frame remains active at return)"):
            _audit(text)
    else:
        with pytest.raises((AssertionError, RuntimeError)):
            _audit(text)


@pytest.mark.parametrize("replacement, diagnostic", (
    ("", "owned flag"),
    ("  store i1 0, ptr {flag}\n", "first constructor successor"),
))
def test_native_static_constructor_audit_rejects_missing_persistent_flag(replacement, diagnostic, emitted_ir):
    text = emitted_ir(PERSISTENT_STATIC)
    assert any(row["persistent"] for row in _audit(text, True))
    _, end, _, _, _ = _static_site(text, True)
    flag = re.match(r"\s*store i1 1, ptr (%[^,\s]+)\n", text[end:])
    assert flag is not None
    text = text[:end] + replacement.format(flag=flag.group(1)) + text[end + flag.end():]
    with pytest.raises(RuntimeError, match=diagnostic):
        _audit(text, True)


@pytest.mark.parametrize("mutation", ("missing-input-retirement", "wrong-metadata-output"))
def test_native_dynamic_constructor_audit_keeps_original_contracts(mutation, emitted_ir):
    text = emitted_ir(SOURCES[3])
    assert any(row["constructor_helper"] == "py_func_new_signature_slots" for row in _audit(text))
    call = re.search(r"call i64[^\n]*@py_func_new_signature_slots\(ptr (%[^ ,]+), ptr (%[^ ,]+),[^\n]*\)\n", text)
    assert call is not None
    tail = text[call.end():]
    metadata = re.search(r"call i64[^\n]*@py_func_init_metadata_slots\(ptr (%[^ ,]+),", tail)
    assert metadata is not None
    if mutation == "missing-input-retirement":
        prefix, count = re.subn(r"(?m)^\s*call void[^\n]*@pcc_gc_store_root\(ptr "
                               + re.escape(call.group(1)) + r", ptr null\)\n", "", tail[:metadata.start()])
        assert count > 0
        text = text[:call.end()] + prefix + tail[metadata.start():]
    else:
        offset = call.end() + metadata.start(1)
        text = text[:offset] + call.group(1) + text[offset + len(metadata.group(1)):]
    with pytest.raises(RuntimeError):
        _audit(text)


def test_native_constructor_audit_does_not_count_declarations_or_static_as_persistent(static_ir):
    target = re.search(r'(?m)^target triple = "[^"]+"', static_ir).group()
    assert _audit(target + "\ndeclare i64 @py_func_new_static_def(ptr, ptr, ptr, ptr, ptr, ptr, i64, ptr, ptr)\n") == []
    with pytest.raises(RuntimeError, match="persistent constructor output"):
        _audit(static_ir, True)

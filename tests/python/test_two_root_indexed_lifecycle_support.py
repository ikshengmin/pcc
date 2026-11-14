"""Typed ownership evidence keeps the textual witness's negative controls.

The positive fixtures use the real direct/no-text builder and codec, including
instruction-position error edges and shared traceback landing payloads. They
never manufacture LLVM text from the indexed representation.
"""

from dataclasses import replace
import hashlib

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend.self_backend_indexed_codec import encode_indexed_module_file
from pcc.backend.self_backend_ir import (
    PARSED_INSTRUCTION_KIND_CALL as CALL,
    PARSED_INSTRUCTION_KIND_ICMP as ICMP,
)
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.ir import ir
from tests.python.two_root_indexed_lifecycle_support import (
    _Graph,
    _assert_probe,
    assert_indexed_compiler_two_root_error_edge,
)


_REJECTED = (AssertionError, BackendUnavailable, ValueError)
_HELPER = "py_cleanup_two_roots_preserving_exception"


def _module(monkeypatch, *, inline=True, mapped=False, landing=False,
            landing_before=False, preliminary=False, spare_payload=False, fault=None):
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_FUSE_USES", "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_VALIDATE", "0")
    monkeypatch.setenv("PCC_TEXT_INDEXED_KERNEL_EMIT", "0")
    monkeypatch.setenv("PCC_DIRECT_INLINE_ERROR_EDGE_CAPTURE", "1" if inline else "0")
    module = ir.Module("two-root-indexed-witness")
    module.triple = "x86_64-unknown-linux-gnu"
    i32, i64 = ir.IntType(32), ir.IntType(64)
    ptr, void = ir.IntType(8).as_pointer(), ir.VoidType()
    null = ir.Constant(ptr, None)
    suffix = "_local_mapped" if mapped else "_local"

    def declaration(name, result, args):
        return ir.Function(module, ir.FunctionType(result, args), name=name)

    enter = declaration("pcc_gc_frame_enter_lifo" + suffix, void, [ptr, ptr])
    leave = declaration("pcc_gc_frame_leave_lifo" + suffix, void, [ptr])
    wrong_leave = declaration("pcc_gc_frame_leave_lifo", void, [ptr])
    helper = declaration(_HELPER, void, [ptr, ptr])
    fail = declaration("witness_fail", i64, [])
    error = declaration("py_err_occurred", i64, [])
    mapping = ir.GlobalVariable(module, i32, name="root.map")
    mapping.linkage = "internal"
    mapping.global_constant = fault != "mutable_map"
    mapping.initializer = ir.Constant(i32, 2 if fault == "map_count" else 1)
    function = declaration("witness_probe", i64, [])
    entry = function.append_basic_block("entry")
    cleanup = function.append_basic_block("cleanup")
    success = function.append_basic_block("success")
    done = function.append_basic_block("done")
    bridge = function.append_basic_block("bridge") if fault in ("diversion", "loop") else None
    landing_block = function.append_basic_block("landing") if landing else None
    builder = ir.IRBuilder(entry)
    first = builder.alloca(ptr, name="x.args.operand.0")
    second = builder.alloca(ptr, name="x.args.item.operand.1")
    builder.store(null, first)
    builder.store(null, second)
    payload_slot = builder.alloca(i32, name="payload.slot") if landing else None
    if spare_payload:
        spare = builder.alloca(i32, name="spare.payload")
        builder.store(ir.Constant(i32, 0), spare)
    map_ptr = builder.bitcast(mapping, ptr, name="map.ptr")
    roots = [builder.bitcast(first, ptr, name="first.ptr"),
             builder.bitcast(second, ptr, name="second.ptr")]
    builder.call(enter, [map_ptr, roots[0]])
    builder.call(enter, [map_ptr, roots[1]])
    if fault == "duplicate_enter":
        builder.call(enter, [map_ptr, roots[0]])
    if fault == "edge_on_fail":
        before_fail = builder.icmp_signed("!=", ir.Constant(i64, 1), ir.Constant(i64, 0),
                                         name="before.fail.condition")
        assert ir.IRBuilder_try_inline_error_edge(builder, before_fail, success, 41, 0)
    result = builder.call(fail, [], name="result")
    if fault == "duplicate_fail":
        builder.call(fail, [], name="second.result")
    if preliminary:
        preliminary_cmp = builder.icmp_signed("<", result, ir.Constant(i64, 0), name="preliminary")
        early = function.append_basic_block("preliminary.error")
        if inline:
            assert ir.IRBuilder_try_inline_error_edge(builder, preliminary_cmp, early, 41, 0)
        else:
            continuation = function.append_basic_block("preliminary.continue")
            builder.cbranch(preliminary_cmp, early, continuation)
            builder.position_at_end(continuation)
        saved = builder.block
        builder.position_at_end(early)
        builder.call(leave, [roots[1]])
        builder.call(leave, [roots[0]])
        builder.branch(done)
        builder.position_at_end(saved)
    flag = builder.call(error, [], name="error.flag")
    condition = builder.icmp_signed("!=", flag, ir.Constant(i64, 0), name="error.condition")
    target = landing_block if landing_before else (bridge if bridge is not None else cleanup)
    if inline:
        if landing:
            assert ir.IRBuilder_declare_inline_error_landing(builder, landing_block, payload_slot)
        assert ir.IRBuilder_try_inline_error_edge(
            builder, condition, target, 42, 0, 0 if landing_before else -1,
        )
        builder.add(result, ir.Constant(i64, 0), name="after.error.condition")
        builder.branch(success)
    else:
        builder.cbranch(condition, target, success)
    builder.position_at_end(success)
    builder.call(leave, [roots[1]])
    builder.call(leave, [roots[0]])
    builder.branch(done)
    builder.position_at_end(cleanup)
    cleanup_roots = roots if fault != "aliased_roots" else [roots[0], roots[0]]
    if fault == "helper_after_leave":
        builder.call(leave, [roots[1]])
        builder.call(leave, [roots[0]])
    builder.call(helper, cleanup_roots)
    if fault == "duplicate_helper":
        builder.call(helper, cleanup_roots)
    if fault in ("tls_bypass", "store_bypass"):
        name = "py_tls_exc_swap_slot" if fault == "tls_bypass" else "pcc_gc_store_root"
        arguments = [roots[0]] if fault == "tls_bypass" else [roots[0], null]
        bypass = declaration(name, void, [ptr] * len(arguments))
        builder.call(bypass, arguments)
    if fault != "helper_after_leave":
        order = roots if fault == "forward_leaves" else roots[::-1]
        for root in order:
            builder.call(wrong_leave if fault == "leave_abi" else leave, [root])
    if landing and not landing_before:
        builder.store(ir.Constant(i32, 1 if fault == "stored_payload" else 0), payload_slot)
        builder.branch(landing_block)
    else:
        builder.branch(done)
    if bridge is not None:
        builder.position_at_end(bridge)
        if fault == "loop":
            builder.branch(bridge)
        else:
            builder.cbranch(condition, cleanup, success)
    if landing:
        lines_type = ir.ArrayType(i32, 2)
        lines = ir.GlobalVariable(module, lines_type, name="traceback.lines")
        lines.linkage = "internal"
        lines.global_constant = True
        lines.initializer = ir.Constant(lines_type, [ir.Constant(i32, 42), ir.Constant(i32, 99)])
        sources_type = ir.ArrayType(ptr, 2)
        sources = ir.GlobalVariable(module, sources_type, name="traceback.sources")
        sources.linkage = "internal"
        sources.global_constant = True
        source_values = []
        for name, data in (("first", b"x\0"), ("second", b"y\0")):
            source_type = ir.ArrayType(ir.IntType(8), len(data))
            source = ir.GlobalVariable(module, source_type, name="traceback.source." + name)
            source.linkage = "internal"
            source.global_constant = True
            source.initializer = ir.Constant(source_type, bytearray(data))
            source_values.append(ir.Constant(ptr, source))
        sources.initializer = ir.Constant(sources_type, source_values)
        append = declaration("py_exc_append_frame_indexed", void, [ptr] * 5 + [i32])
        builder.position_at_end(landing_block)
        index = builder.load(payload_slot, name="payload.value")
        lines_ptr = builder.bitcast(lines, ptr, name="lines.ptr")
        sources_ptr = builder.bitcast(sources, ptr, name="sources.ptr")
        builder.call(append, [null, null, null, lines_ptr, sources_ptr, index])
        builder.branch(cleanup if landing_before else done)
    builder.position_at_end(done)
    builder.ret(ir.Constant(i64, 0))
    return module.direct_indexed_module()


def _raw(tmp_path, module, name="witness"):
    path = tmp_path / (name + ".pidx")
    encode_indexed_module_file(str(path), module)
    return path.read_bytes()


def _rows(kernel, kind):
    for block in range(len(kernel.block_names)):
        for position in range(kernel.instruction_count(block)):
            if kernel.instruction_kind_id(block, position) == kind:
                yield block, position, kernel.instruction_record_id(block, position)


def _condition_record(kernel):
    for block, position, record in _rows(kernel, ICMP):
        value = kernel.defined_value_id(block, position)
        if kernel.value_name(value) == "error.condition":
            return record
    raise AssertionError("fixture has no pending-error condition")


@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("mapped", [False, True])
@pytest.mark.parametrize("preliminary", [False, True])
def test_real_typed_roots_and_exact_pending_route(tmp_path, monkeypatch, inline, mapped, preliminary):
    module = _module(monkeypatch, inline=inline, mapped=mapped, preliminary=preliminary)
    raw = _raw(tmp_path, module)
    before = hashlib.sha256(raw).hexdigest()
    assert_indexed_compiler_two_root_error_edge([raw])
    # A second proof must not see stack-preparation mutations from the first.
    assert_indexed_compiler_two_root_error_edge([raw])
    assert hashlib.sha256(raw).hexdigest() == before
    assert _raw(tmp_path, module, "live-after-proof") == raw


@pytest.mark.parametrize("landing_before", [False, True])
def test_shared_landing_uses_exact_payload_and_continuation(tmp_path, monkeypatch, landing_before):
    module = _module(monkeypatch, landing=True, landing_before=landing_before)
    assert_indexed_compiler_two_root_error_edge([_raw(tmp_path, module)])


@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("fault", [
    "aliased_roots", "duplicate_enter", "map_count", "mutable_map",
    "forward_leaves", "helper_after_leave", "leave_abi", "duplicate_fail",
    "duplicate_helper", "tls_bypass", "store_bypass", "diversion", "loop",
])
def test_typed_lifecycle_rejects_corrupted_protocol(tmp_path, monkeypatch, inline, fault):
    module = _module(monkeypatch, inline=inline, fault=fault)
    # Exercise the witness predicate itself before the independent full-module
    # verifier/stackmap checks can reject a protocol fault for another reason.
    with pytest.raises(AssertionError):
        _assert_probe(_Graph(module, module.functions[0]))
    with pytest.raises(_REJECTED):
        assert_indexed_compiler_two_root_error_edge([_raw(tmp_path, module)])


@pytest.mark.parametrize("fault", [
    "polarity", "wrong_query", "wrong_trigger", "wrong_target", "wrong_inline_payload",
    "wrong_fail", "root_name", "enter_abi", "mixed_enter_abi", "wrong_alloca",
])
def test_scalar_arena_mutations_fail_closed(tmp_path, monkeypatch, fault):
    module = _module(monkeypatch)
    function = next(item for item in module.functions if item.name == "witness_probe")
    kernel = get_indexed_function_kernel(function)
    if fault == "polarity":
        record = _condition_record(kernel)
        kernel.call_texts.append("eq")
        kernel.instruction_record_scalars.set_unchecked(record * 4, len(kernel.call_texts) - 1)
    elif fault == "wrong_query":
        record = _condition_record(kernel)
        kernel.instruction_record_scalars.set_unchecked(record * 4 + 2, kernel.value_id("result"))
    elif fault == "wrong_trigger":
        # This later same-block instruction still dominates the edge. Full
        # module verification alone cannot reject the delayed error trigger.
        kernel.error_edge_scalars.set_unchecked(1, kernel.inline_error_edge_trigger(0) + 1)
    elif fault == "wrong_target":
        kernel.error_edge_scalars.set_unchecked(3, kernel.block_id("success"))
    elif fault == "wrong_inline_payload":
        kernel.error_edge_scalars.set_unchecked(6, 0)
    elif fault == "wrong_fail":
        for _block, _position, call_id in _rows(kernel, CALL):
            header = kernel.call_header(call_id)
            if kernel.call_texts[header.second] == "witness_fail":
                kernel.call_texts.append("unrelated_fail")
                kernel.call_scalars.set_unchecked(call_id * 8 + 1, len(kernel.call_texts) - 1)
                break
    elif fault == "root_name":
        value = kernel.value_id("x.args.operand.0")
        kernel.value_names[value] = "wrong.root"
    elif fault in ("enter_abi", "mixed_enter_abi"):
        for _block, _position, call_id in _rows(kernel, CALL):
            header = kernel.call_header(call_id)
            if kernel.call_texts[header.second] == "pcc_gc_frame_enter_lifo_local":
                kernel.call_texts.append("pcc_gc_frame_enter_lifo" if fault == "enter_abi"
                                         else "pcc_gc_frame_enter_lifo_local_mapped")
                kernel.call_scalars.set_unchecked(call_id * 8 + 1, len(kernel.call_texts) - 1)
                if fault == "mixed_enter_abi":
                    break
    else:
        value = kernel.value_id("x.args.operand.0")
        from pcc.backend.self_backend_ir import TypeDesc
        kernel.value_scalars.set_unchecked(value * 8 + 4, kernel.intern_type(TypeDesc("int", 32)))
    if fault in ("polarity", "wrong_query", "wrong_trigger"):
        from pcc.backend.self_backend_verify import verify_parsed_module
        # These are structurally valid typed inputs. The lifecycle predicate,
        # not a malformed codec/SSA record, must reject their wrong condition.
        verify_parsed_module(module)
    with pytest.raises(AssertionError):
        _assert_probe(_Graph(module, function))
    with pytest.raises(_REJECTED):
        assert_indexed_compiler_two_root_error_edge([_raw(tmp_path, module)])


@pytest.mark.parametrize("fault", ["payload", "source_line", "slot", "stored_payload"])
def test_shared_landing_rejects_wrong_site_payload(tmp_path, monkeypatch, fault):
    module = _module(monkeypatch, landing=True, landing_before=fault != "stored_payload",
                     fault=fault)
    kernel = get_indexed_function_kernel(module.functions[0])
    if fault == "payload":
        kernel.error_edge_scalars.set_unchecked(6, 1)
    elif fault == "source_line":
        kernel.error_edge_scalars.set_unchecked(4, 43)
    elif fault == "slot":
        kernel.error_landing_scalars.set_unchecked(1, kernel.value_id("x.args.operand.0"))
    with pytest.raises(AssertionError):
        _assert_probe(_Graph(module, module.functions[0]))
    with pytest.raises(_REJECTED):
        assert_indexed_compiler_two_root_error_edge([_raw(tmp_path, module)])


def test_duplicate_probe_modules_are_ambiguous(tmp_path, monkeypatch):
    raw = _raw(tmp_path, _module(monkeypatch))
    with pytest.raises(AssertionError, match="exactly one corresponding probe"):
        assert_indexed_compiler_two_root_error_edge([raw, raw])


def test_every_module_is_verified_not_only_selected_probe(tmp_path, monkeypatch):
    raw = _raw(tmp_path, _module(monkeypatch))
    other = _module(monkeypatch)
    function = other.functions[0]
    function.name = "unrelated"
    kernel = get_indexed_function_kernel(function)
    kernel.error_edge_scalars.set_unchecked(3, 100000)
    with pytest.raises(_REJECTED):
        assert_indexed_compiler_two_root_error_edge([raw, _raw(tmp_path, other, "unrelated")])


@pytest.mark.parametrize("raw_modules,reason", [
    ([b"x" * (2 * 1024 * 1024 + 1)], "indexed module size"),
    ([b"x" * (1024 * 1024 + 1)] * 2, "indexed module aggregate size"),
    ([b"x"] * 32, "indexed module inventory"),
])
def test_indexed_decoder_bound_precedes_materialization(monkeypatch, raw_modules, reason):
    from pcc.backend import self_backend_indexed_codec as codec

    def forbidden_decode(*args, **kwargs):
        raise AssertionError("decoder was called")

    monkeypatch.setattr(codec, "decode_indexed_module_file", forbidden_decode)
    with pytest.raises(AssertionError, match=reason):
        assert_indexed_compiler_two_root_error_edge(raw_modules)


def test_real_frontend_inline_witness_is_not_a_text_projection(tmp_path, monkeypatch):
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.pipeline_context import build_closed_world_context
    from pcc.frontends.python.pipeline_frontend_worker_execution import _concrete_direct_indexed_target
    from pcc.frontends.python.type_infer import infer_module
    from tests.python.two_root_lifecycle_support import TRACING_TWO_ROOT_PROGRAM

    for name in ("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "PCC_DIRECT_INDEXED_KERNEL_EMIT",
                 "PCC_DIRECT_INLINE_ERROR_EDGE_CAPTURE"):
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_VALIDATE", "0")
    monkeypatch.setenv("PCC_TEXT_INDEXED_KERNEL_EMIT", "0")
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "witness.py"
    source.write_text(TRACING_TWO_ROOT_PROGRAM, encoding="utf-8")
    modules, exports, derived = build_closed_world_context([str(source)], ["witness"])
    typed = infer_module(modules[-1], external_exports=exports, derived_class_map=derived)
    codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")
    codegen._strict_no_libpython = True
    codegen.module._direct_indexed_retain_text = False
    assert codegen._generate_initial_module() == ""
    module = _concrete_direct_indexed_target(codegen._direct_indexed_module,
                                            "x86_64-unknown-linux-gnu")
    assert sum(len(get_indexed_function_kernel(function).error_edge_scalars)
               for function in module.functions) > 0
    raw = _raw(tmp_path, module)
    assert_indexed_compiler_two_root_error_edge([raw])


_ALIAS_SOURCE = '''target triple = "x86_64-unknown-linux-gnu"
@map = internal constant i32 1
define i64 @witness_probe() {
entry:
  %x.args.operand.0 = alloca ptr
  %x.args.item.operand.1 = alloca ptr
  store ptr null, ptr %x.args.operand.0
  store ptr null, ptr %x.args.item.operand.1
  %first.alias = bitcast ptr %x.args.operand.0 to ptr
  %second.alias = bitcast ptr %x.args.item.operand.1 to ptr
  call void (ptr, ptr) @pcc_gc_frame_enter_lifo_local(ptr @map, ptr %first.alias)
  call void (ptr, ptr) @pcc_gc_frame_enter_lifo_local(ptr @map, ptr %second.alias)
  %result = call i64 () @witness_fail()
  %flag = call i64 () @py_err_occurred()
  %condition = icmp ne i64 %flag, 0
  br i1 %condition, label %cleanup, label %success
cleanup:
  call void (ptr, ptr) @py_cleanup_two_roots_preserving_exception(ptr %first.alias, ptr %second.alias)
  call void (ptr) @pcc_gc_frame_leave_lifo_local(ptr %second.alias)
  call void (ptr) @pcc_gc_frame_leave_lifo_local(ptr %first.alias)
  ret i64 0
success:
  call void (ptr) @pcc_gc_frame_leave_lifo_local(ptr %second.alias)
  call void (ptr) @pcc_gc_frame_leave_lifo_local(ptr %first.alias)
  ret i64 0
}
'''


@pytest.mark.parametrize("fault", [None, "cycle", "integer_roundtrip", "wrong_root"])
def test_typed_alias_resolution_is_only_pointer_bitcasts(tmp_path, fault):
    from pcc.backend.self_backend_parse import parse_self_backend_module

    # This is original explicit IR input, not a diagnostic rendering of a
    # direct module. Current builders elide ptr-to-ptr identity casts, while
    # older indexed receipts and external explicit inputs can contain them.
    source = _ALIAS_SOURCE
    if fault == "cycle":
        source = source.replace("bitcast ptr %x.args.operand.0 to ptr", "bitcast ptr %second.alias to ptr")
        source = source.replace("bitcast ptr %x.args.item.operand.1 to ptr", "bitcast ptr %first.alias to ptr")
    elif fault == "integer_roundtrip":
        source = source.replace("%first.alias = bitcast ptr %x.args.operand.0 to ptr",
                                "%integer = ptrtoint ptr %x.args.operand.0 to i64\n"
                                "  %first.alias = inttoptr i64 %integer to ptr")
    elif fault == "wrong_root":
        source = source.replace("%first.alias = bitcast ptr %x.args.operand.0 to ptr",
                                "%unrelated = alloca ptr\n"
                                "  store ptr null, ptr %unrelated\n"
                                "  %first.alias = bitcast ptr %unrelated to ptr")
    module = parse_self_backend_module(source)
    for function in module.functions:
        get_indexed_function_kernel(function)
    if fault is None:
        _assert_probe(_Graph(module, module.functions[0]))
        assert_indexed_compiler_two_root_error_edge([_raw(tmp_path, module)])
    else:
        with pytest.raises(AssertionError):
            _assert_probe(_Graph(module, module.functions[0]))
        with pytest.raises(_REJECTED):
            assert_indexed_compiler_two_root_error_edge([_raw(tmp_path, module)])


@pytest.mark.parametrize("slot_name", ["x.args.operand.0", "spare.payload"])
def test_cleanup_cannot_double_as_inline_landing(tmp_path, monkeypatch, slot_name):
    from pcc.backend.self_backend_verify import verify_parsed_module

    module = _module(monkeypatch, spare_payload=True)
    function = module.functions[0]
    kernel = get_indexed_function_kernel(function)
    kernel.error_landing_scalars.append2(kernel.block_id("cleanup"), kernel.value_id(slot_name))
    kernel.error_edge_scalars.set_unchecked(6, 0)
    # Both a pointer root overwrite and an otherwise valid spare i32 landing
    # pass ordinary edge/SSA verification. Neither has a landing consumer at
    # cleanup entry, and neither may bypass the lifecycle proof's landing check.
    verify_parsed_module(module)
    with pytest.raises(AssertionError, match="cleanup site cannot be a shared landing"):
        _assert_probe(_Graph(module, function))
    with pytest.raises(_REJECTED):
        assert_indexed_compiler_two_root_error_edge([_raw(tmp_path, module)])


def test_inline_edge_at_fail_call_cannot_skip_pending_check(tmp_path, monkeypatch):
    from pcc.backend.self_backend_verify import verify_parsed_module

    module = _module(monkeypatch, fault="edge_on_fail")
    function = module.functions[0]
    kernel = get_indexed_function_kernel(function)
    fails = [(block, position) for block, position, call_id in _rows(kernel, CALL)
             if kernel.call_texts[kernel.call_header(call_id).second] == "witness_fail"]
    assert len(fails) == 1
    block, position = fails[0]
    span = kernel.inline_error_edge_span(block)
    assert span.second == 2
    edge = span.first
    assert kernel.inline_error_edge_target(edge) == kernel.block_id("success")
    kernel.error_edge_scalars.set_unchecked(edge * 8 + 1, position)
    # Its true i1 condition dominates the call; both successors retire roots.
    # The input remains structurally valid, but the actual emitter takes the
    # success edge immediately after fail instead of running py_err_occurred.
    verify_parsed_module(module)
    with pytest.raises(AssertionError, match="inline edge at fail call"):
        _assert_probe(_Graph(module, function))
    with pytest.raises(_REJECTED):
        assert_indexed_compiler_two_root_error_edge([_raw(tmp_path, module)])


@pytest.mark.parametrize("fault,reason", [
    ("truncated", "source cardinality"),
    ("wrong_entry_type", "not a typed pointer"),
    ("missing_global", "source global"),
    ("wrong_global_type", "source global type"),
    ("truncated_global_bytes", "source global bytes"),
])
def test_shared_landing_source_initializer_is_complete(tmp_path, monkeypatch, fault, reason):
    from pcc.backend.self_backend_verify import verify_parsed_module

    module = _module(monkeypatch, landing=True, landing_before=True)
    changed = []
    for global_ in module.globals_:
        if global_.name == "traceback.sources":
            replacements = {
                "truncated": "[ptr null]",
                "wrong_entry_type": "[i32 0, ptr null]",
                "missing_global": "[ptr @missing.source, ptr null]",
                "wrong_global_type": "[ptr @root.map, ptr null]",
            }
            if fault in replacements:
                global_ = replace(global_, initializer=replacements[fault])
        elif global_.name == "traceback.source.first" and fault == "truncated_global_bytes":
            global_ = replace(global_, initializer='c"\\00"')
        changed.append(global_)
    module = replace(module, globals_=tuple(changed))
    # The declared array types remain valid. Full module verification does not
    # certify these initializer entries, so directly exercise the added proof.
    verify_parsed_module(module)
    with pytest.raises(AssertionError, match=reason):
        _assert_probe(_Graph(module, module.functions[0]))
    with pytest.raises(_REJECTED):
        assert_indexed_compiler_two_root_error_edge([_raw(tmp_path, module)])

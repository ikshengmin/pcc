"""The two GC entries have different link boundaries and real call records.

Host compiler only: lower ordinary Python and the unchanged runtime source,
then inspect symbolic call facts. No runtime, object, image, or stack walk.
A direct call and a safepoint are not a caller-root completeness proof.
"""

from pathlib import Path

from pcc.backend.precise_stackmap import SAFEPOINT_CALL
from pcc.backend.self_backend_call_flags import CALL_FLAG_INDIRECT, CALL_FLAG_VARARG
from pcc.backend.self_backend_ir import PARSED_INSTRUCTION_KIND_CALL
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.backend.self_backend_precise_stackmaps import build_function_stack_map_plan
from pcc.backend.self_backend_prepare import prepare_parsed_function
from pcc.backend.self_backend_stackprep import assign_stack_slots
from pcc.backend.self_backend_x86_64_linux import _aggregate_returned_indirect
from pcc.backend.target_objects import emit_indexed_assembly
from pcc.driver.paths import module_name_from_src
from pcc.driver.project import TranslationUnit
from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
from pcc.frontends.python.codegen.module_name_lowering import module_symbol_suffix
from pcc.frontends.python.pipeline import compile_python


TARGET = "x86_64-unknown-linux-gnu"
PY_OBJ = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_obj.py"
EXPLICIT = "pcc_gc_collect"
AUTOMATIC = "_gc_tracing_automatic_collect"


def _compile_source(path, output):
    compile_python(str(path), str(output), target_triple=TARGET, backend="self",
                   emit_llvm_only=True, python_library=True, ir_scaffold_mode="on")
    return parse_self_backend_module(output.read_text())


def _entry_calls(module, caller, target):
    """Read the same packed call plane and plan used by the x86 emitter."""
    fn = next(f for f in module.functions if f.name == caller)
    prepare_parsed_function(fn)
    assign_stack_slots(fn, aggregate_returned_indirect=_aggregate_returned_indirect)
    kernel = get_indexed_function_kernel(fn)
    plan = build_function_stack_map_plan(fn, list(module.globals_), target="x86_64-linux")
    by_site = {(b, index): label for b, index, label in plan.instruction_suffix_labels}
    by_label = {record.label: record for record in plan.records}
    calls = []
    for block_id, block_name in enumerate(kernel.block_names):
        for i in range(kernel.block_fact(block_id).second):
            if kernel.instruction_kind_id(block_id, i) != PARSED_INSTRUCTION_KIND_CALL:
                continue
            site = kernel.instruction_call_id(block_id, i)
            header = kernel.call_header(site)
            if kernel.call_texts[header.second] != target:
                continue
            assert not header.third & (CALL_FLAG_INDIRECT | CALL_FLAG_VARARG)
            data = kernel.instruction_data(block_id, i)
            result_type = data[1].describe()
            parameters = tuple(t.describe() for t, _ in data[4])
            record = by_label[by_site[block_name, i]]
            assert record.kind == SAFEPOINT_CALL
            calls.append((result_type, parameters, record.label))
    return calls


def test_python_gc_collect_is_a_direct_call_to_an_external_declaration(tmp_path):
    source = tmp_path / "client.py"
    source.write_text("import gc\ndef collect_now():\n    return gc.collect()\n")
    client = _compile_source(source, tmp_path / "client.ll")
    assert EXPLICIT not in {f.name for f in client.functions}
    calls = _entry_calls(client, "user_client_collect_now", EXPLICIT)
    assert [(rt, args) for rt, args, _ in calls] == [("i64", ("i32",))]


def test_runtime_auto_calls_target_the_defined_module_symbol_not_the_python_name(tmp_path):
    module = _compile_source(PY_OBJ, tmp_path / "runtime.ll")
    defined = {f.name for f in module.functions}
    assert EXPLICIT in defined
    # Take the real runtime source path and its compiler namespace, not a
    # suffix match against arbitrary definitions or an assumed private label.
    prefix = "user_" + module_symbol_suffix(module_name_from_src(str(PY_OBJ))) + "_"
    auto = prefix + AUTOMATIC
    assert auto in defined and AUTOMATIC not in defined
    by_caller = {}
    for fn in module.functions:
        if auto in get_indexed_function_kernel(fn).call_texts:
            calls = _entry_calls(module, fn.name, auto)
            if calls:
                by_caller[fn.name] = calls
    # Deliberate inventory of these direct edges in this module/target, not
    # a global count: new adapters/callers must be reviewed and added. Two
    # real emitted adapters are absent as direct calls in the Python source.
    assert {name: len(calls) for name, calls in by_caller.items()} == {
        prefix + "_gc_maybe_auto_collect": 1,
        prefix + "_gc4_maybe_auto_step": 3,
        auto + "_native_adapter": 1,
        auto + "_native_adapter_vector": 1,
    }
    actual = [call for calls in by_caller.values() for call in calls]
    declaration = next(f for f in module.functions if f.name == auto)
    signature = (declaration.ret_type.describe(),
                 tuple(arg.type.describe() for arg in declaration.args))
    assert signature == ("i64", ("i64",))
    assert all((rt, args) == signature for rt, args, _ in actual)
    assert len({label for _, _, label in actual}) == len(actual)


def test_public_c_caller_can_need_a_pointer_across_a_rootless_call(tmp_path, monkeypatch):
    # The argument remains live because the function must return it. It is
    # not an argument of collect and not a compiler-managed frame slot.
    source = """extern long long pcc_gc_collect(int);
void *keep_across_collect(void *value) {
    (void)pcc_gc_collect(0);
    return value;
}
"""
    path = tmp_path / "caller.c"
    path.write_text(source)
    evaluator = CEvaluator(backend="self", target_triple=TARGET)
    (unit,) = evaluator.compile_translation_units(
        [TranslationUnit(str(path), str(path), source)],
        use_system_cpp=False, use_compile_cache=False,
    )
    parsed = parse_self_backend_module(unit[1])
    fn = next(f for f in parsed.functions if f.name == "keep_across_collect")
    assert fn.ret_type.is_ptr and len(fn.args) == 1 and fn.args[0].type.is_ptr
    (block,) = get_indexed_function_kernel(fn).materialize_legacy_blocks(fn)
    ins = block.instructions
    (at,) = (i for i, inst in enumerate(ins) if inst.kind == "call" and inst.data[2] == EXPLICIT)
    assert block.terminator.kind == "ret"
    returned = block.terminator.data[1]
    if returned != fn.args[0].name:  # Unpromoted form reloads the caller's cell.
        (home,) = (item.data[3] for item in ins[at + 1:]
                   if item.kind == "load" and item.data[0] == returned)
        writes = [(i, item.data[1]) for i, item in enumerate(ins)
                  if item.kind == "store" and item.data[3] == home]
        assert len(writes) == 1 and writes[0][0] < at and writes[0][1] == fn.args[0].name
    # Request the existing compile-time plans, not a published runtime table.
    # The ordinary emitter still has an exact return label even for a call
    # whose empty *managed* record may subsequently be omitted by serialization.
    monkeypatch.setenv("PCC_PRECISE_STACKMAP_TABLES", "1")
    plans = []
    lines = emit_indexed_assembly(parsed, stack_map_plans_out=plans).splitlines()
    plan = next(p for p in plans if p.function_name == fn.name)
    (call_site,) = (i for i, line in enumerate(lines)
                    if line.strip() == "call " + EXPLICIT)
    (record,) = (r for r in plan.records if r.label + ":" == lines[call_site + 1])
    assert record.kind == SAFEPOINT_CALL and not record.locations

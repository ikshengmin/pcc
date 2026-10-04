"""Static ownership regressions for native virtual-thread lowering.

Source contracts and parsed emitted ownership checks cover native lowering.
The dynamic callback test follows actual slot identities, suspension-frame
cells and cleanup edges; native controls use the same lifetime scenario.
"""

import textwrap
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
LOWERING = REPO / "pcc" / "frontends" / "python" / "codegen" / "native_virtual_thread.py"


def _source() -> str:
    return LOWERING.read_text(encoding="utf-8")


def _between(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    return source[start_index:source.index(end, start_index)]


def test_worker_completion_drops_the_independent_owned_result() -> None:
    resume = _between(
        _source(),
        "    def _emit_virtual_thread_resume_function(",
        "    def _emit_virtual_thread_spawn(",
    )

    complete = resume.index('self.runtime["py_virtual_thread_complete"]')
    release = resume.index("self._gc_release(result_obj)", complete)
    returned = resume.index("self.builder.ret(rc)", release)
    assert "if ret_ty is not None and not isinstance(ret_ty, NoneType):" in resume
    assert complete < release < returned


def test_spawn_failure_releases_vthread_before_allocating_owned_error() -> None:
    source = _source()
    ordinary = _between(
        source,
        "    def _emit_virtual_thread_spawn(",
        "    def _emit_virtual_thread_generator_spawn(",
    )
    generator = _between(
        source,
        "    def _emit_virtual_thread_generator_spawn(",
        "    def _emit_native_virtual_thread_value_call(",
    )

    for failure_path in (ordinary, generator):
        release_thread = failure_path.index("self._gc_release(vt)")
        allocate_error = failure_path.index('self.runtime["py_exc_new"]', release_thread)
        raise_error = failure_path.index(
            'self.runtime["py_raise"]', allocate_error
        )
        release_error = failure_path.index("self._gc_release(exc)", raise_error)
        assert release_thread < allocate_error < raise_error < release_error


def test_dynamic_vthread_arguments_release_through_owned_expression_policy() -> None:
    source = _source()
    boundaries = (
        ("cancel", "run", "py_virtual_thread_cancel"),
        ("result", "exception", "py_virtual_thread_result"),
        ("exception", "outcome", "py_virtual_thread_exception"),
        ("outcome", "state", "py_virtual_thread_outcome"),
        ("state", "sleep", "py_virtual_thread_state"),
    )

    for operation, next_operation, runtime_name in boundaries:
        branch = _between(
            source,
            f'        if kind == "pcc.virtual_thread.{operation}":',
            f'        if kind == "pcc.virtual_thread.{next_operation}":',
        )
        emitted = branch.index("target_obj = self._emit_as_object(args[0])")
        called = branch.index(f'self.runtime["{runtime_name}"]', emitted)
        released = branch.index(
            "self._release_virtual_thread_argument(target_obj, args[0])", called
        )
        assert emitted < called < released


def test_multioperand_vthread_calls_trace_reload_and_lifo_release_early_objects() -> None:
    source = _source()
    branches = (
        ("send", "recv", "py_virtual_thread_channel_send_begin"),
        ("recv", "close_sender", "py_virtual_thread_channel_recv_begin"),
        ("select2", "join", "py_virtual_thread_channel_select2_begin"),
        ("join", "cancel", "py_virtual_thread_join"),
        ("sleep", "block_on_fd", "py_virtual_thread_sleep"),
        ("block_on_fd", "block_current_on_fd", "py_virtual_thread_block_on_fd"),
    )

    for operation, next_operation, runtime_name in branches:
        branch = _between(
            source,
            f'        if kind == "pcc.virtual_thread.{operation}":',
            f'        if kind == "pcc.virtual_thread.{next_operation}":',
        )
        rooted = branch.index("self._enter_virtual_thread_operand_root(")
        later_operand = branch.index(
            "self._emit_expr_with_cpy_operand_cleanup(", rooted
        )
        runtime_call = branch.index(f'self.runtime["{runtime_name}"]')
        reloaded = branch.index(
            "self._load_virtual_thread_operand_root(", later_operand
        )
        released = branch.index(
            "self._release_rooted_pcc_lifetimes(", runtime_call
        )
        assert "rooted_pcc_lifetimes=" in branch
        assert rooted < later_operand < runtime_call < reloaded < released


def test_current_wait_and_sleep_current_root_current_across_scalar_lowering() -> None:
    source = _source()
    current_wait = _between(
        source,
        "    def _emit_virtual_thread_current_fd_wait(",
        "    def _emit_virtual_thread_resume_function(",
    )
    sleep_current = _between(
        source,
        '        if kind == "pcc.virtual_thread.sleep_current":',
        '        if kind == "pcc.virtual_thread.result":',
    )

    for branch, runtime_name in (
        (current_wait, "py_virtual_thread_block_on_fd"),
        (sleep_current, "py_virtual_thread_sleep"),
    ):
        root = branch.index("self._enter_virtual_thread_operand_root(")
        scalar = branch.index("as_i64=True", root)
        reload = branch.index(
            "self._load_virtual_thread_operand_root(", scalar
        )
        runtime_call = branch.index(f'self.runtime["{runtime_name}"]')
        release = branch.index(
            "self._release_rooted_pcc_lifetimes(", runtime_call
        )
        assert "rooted_pcc_lifetimes=" in branch
        assert root < scalar < runtime_call < reload < release


def _callback_lifetime_program(failing_argument=False):
    # Reuse the executed may-park/finalizer scenario; make the callable itself
    # temporary so a caller-owned parameter cannot hide its missing root.
    from tests.python.test_may_park_operand_publication import _MAY_PARK_NATIVE_PROGRAM

    program = _MAY_PARK_NATIVE_PROGRAM.replace(
        "class Combine:\n", "class Combine:\n    def __del__(self):\n        events.append('callback-drop')\n",
    ).replace(
        "def handler(callback):\n    return callback(Token(), leaf())",
        "def handler():\n    return vt.call(Combine(), Token(), leaf())",
    ).replace("vt.spawn(handler, Combine())", "vt.spawn(handler)")
    program = program.replace(
        "    vt.yield_now()\n    return [2]",
        "    vt.yield_now()\n    gc.collect()\n    assert events == []\n    return [2]",
    ).replace(
        "    def __call__(self, token, value):\n",
        "    def __call__(self, token, value):\n        assert events == []\n",
    )
    if failing_argument:
        program = program.replace("    return [2]", "    raise ValueError('argument failed')")
        program = program.replace(
            "def handler():\n    return vt.call(Combine(), Token(), leaf())",
            "def handler():\n    try:\n        return vt.call(Combine(), Token(), leaf())\n"
            "    except ValueError:\n        return 17",
        )
    return program


def _assert_callback_slot_contract(text):
    from pcc.backend.self_backend_kernel import get_indexed_function_kernel
    from pcc.backend.self_backend_parse import parse_self_backend_module
    from pcc.backend.self_backend_verify import verify_parsed_module

    module = parse_self_backend_module(text)
    verify_parsed_module(module)
    function = next(fn for fn in module.functions
                    if fn.name == "user_slot_operand_handler__gen_resume")
    blocks = get_indexed_function_kernel(function).materialize_legacy_blocks(function)
    by_name = {block.name: block for block in blocks}
    rows = [(block, index, instruction) for block in blocks
            for index, instruction in enumerate(block.instructions)]
    aliases = {ins.data[1]: ins.data[3] for _, _, ins in rows
               if ins.kind == "cast" and ins.data[0] == "bitcast"}
    loads = {ins.data[0]: ins.data[3] for _, _, ins in rows if ins.kind == "load"}

    def slot(value):
        seen = set()
        while value in aliases:
            assert value not in seen
            seen.add(value)
            value = aliases[value]
        return value

    def called(ins, name):
        return ins.kind == "call" and ins.data[2] == name

    def args(ins):
        return [value for _, value in ins.data[4]]

    def clears(instructions):
        return [slot(args(ins)[0]) for ins in instructions
                if called(ins, "pcc_gc_store_root") and args(ins)[1] == "null"]

    def straight_path(start, *, success):
        # Follow the emitted status/error branches, never textual block order.
        seen, instructions = set(), []
        block = start
        while block.name not in seen:
            seen.add(block.name)
            instructions.extend(block.instructions)
            term = block.terminator
            if term.kind == "br":
                block = by_name[term.data[0]]
            elif term.kind == "br_cond":
                block = by_name[term.data[2 if success else 1]]
            else:
                break
        return instructions

    invocations = [(block, index, ins) for block, index, ins in rows
                   if called(ins, "py_obj_call_deferred")]
    assert len(invocations) == 1
    block, index, invoke = invocations[0]
    callable_slot, args_slot = [slot(loads[value]) for value in args(invoke)[:2]]
    published = block.instructions[index + 1]
    assert published.kind == "store" and published.data[1] == invoke.data[0]
    result_slot = slot(published.data[3])
    roots = (callable_slot, args_slot, result_slot)
    assert len(set(roots)) == 3
    for root in roots:
        assert any(ins.kind == "alloca" and ins.data[0] == root for _, _, ins in rows)
        assert any(ins.kind == "store" and ins.data[1] == "null"
                   and slot(ins.data[3]) == root for ins in by_name["entry"].instructions)
        assert any(called(ins, "pcc_gc_frame_enter") and slot(args(ins)[1]) == root
                   for _, _, ins in rows)
        assert not any(called(ins, "pcc_gc_frame_enter_lifo") and slot(args(ins)[1]) == root
                       for _, _, ins in rows)

    # The list holding the first argument and the callable must both be saved
    # at the argument's suspension, then restored from the same heap cells
    # before dispatch. Stack-only roots or mismatched frame indices fail here.
    tuples = [ins for _, _, ins in rows if called(ins, "py_tuple_from_list")]
    args_tuple = next(ins for ins in tuples if any(
        following.kind == "store" and following.data[1] == ins.data[0]
        and slot(following.data[3]) == args_slot for _, _, following in rows))
    list_slot = slot(loads[args(args_tuple)[0]])
    dispatch = by_name["gen.dispatch"]
    restore_calls = {ins.data[0]: args(ins)[1] for ins in dispatch.instructions
                     if called(ins, "py_gen_frame_get")}
    restored = {slot(ins.data[3]): restore_calls[ins.data[1]]
                for ins in dispatch.instructions
                if ins.kind == "store" and ins.data[1] in restore_calls}
    saved_groups = []
    for candidate in blocks:
        saves = {slot(loads[args(ins)[2]]): args(ins)[1]
                 for ins in candidate.instructions
                 if called(ins, "py_gen_frame_set") and args(ins)[2] in loads}
        if callable_slot in saves and list_slot in saves and result_slot not in saves:
            saved_groups.append(saves)
    assert saved_groups, "no callable/list owner save across argument suspension"
    for root in (callable_slot, args_slot, list_slot, result_slot):
        assert root in restored
    assert any(all(saved[root] == restored[root] for root in (callable_slot, list_slot))
               for saved in saved_groups)
    assert dispatch.terminator.kind == "switch"

    # Independent counted leases protect both raw ABI operands. The exact
    # result is published above before any release/error check can safepoint.
    success = straight_path(block, success=True)
    releases = [(slot(args(ins)[0]), args(ins)[1]) for ins in success
                if called(ins, "pcc_gc_foreign_lease_release")
                and slot(args(ins)[0]) in (callable_slot, args_slot)]
    assert [root for root, token in releases[:2]] == [args_slot, callable_slot]
    for root, token in releases[:2]:
        acquired = [ins for _, _, ins in rows
                    if called(ins, "pcc_gc_foreign_lease_acquire")
                    and ins.data[0] == token]
        assert len(acquired) == 1 and slot(args(acquired[0])[0]) == root
    moves = [ins for ins in success if called(ins, "pcc_gc_root_move")
             and slot(args(ins)[1]) == result_slot]
    assert len(moves) == 1
    moved = moves[0]
    null_checks = [ins for ins in success if ins.kind == "icmp" and ins.data[0] == "eq"
                   and ins.data[4] == "null" and ins.data[3] in loads
                   and slot(loads[ins.data[3]]) == result_slot]
    assert len(null_checks) == 1
    statuses = [ins for ins in success if ins.kind == "select"
                and ins.data[2] == null_checks[0].data[1]
                and ins.data[3:] == ("-1", "0")]
    assert len(statuses) == 1 and success.index(statuses[0]) < success.index(moved)
    for checked in (statuses[0].data[0], moved.data[0]):
        checks = [(owner, ins) for owner, _, ins in rows if ins.kind == "icmp"
                  and ins.data[0] == "slt" and ins.data[3:] == (checked, "0")]
        assert len(checks) == 1
        owner, check = checks[0]
        assert owner.terminator.kind == "br_cond" and owner.terminator.data[0] == check.data[1]
        error_path = straight_path(by_name[owner.terminator.data[1]], success=False)
        assert clears(error_path)[:3] == list(reversed(roots))
    child_slot = slot(args(moved)[0])
    assert child_slot not in roots
    after_move = success[success.index(moved) + 1:]
    assert clears(after_move)[:3] == list(reversed(roots))
    tag = next(ins for ins in after_move if called(ins, "py_obj_type_tag"))
    reload = next(ins for ins in after_move if called(ins, "pcc_gc_load_ptr")
                  and ins.data[0] == args(tag)[0])
    assert slot(args(reload)[1]) == child_slot
    assert after_move.index(reload) < after_move.index(tag)

    # Result-publication/lease failures unwind every owner in reverse order,
    # while the saved exception brackets disposal. Argument errors must unwind
    # the partial tuple/list before the earlier temporary callable as well.
    cleanup = straight_path(by_name[block.terminator.data[1]], success=False)
    assert clears(cleanup)[:3] == list(reversed(roots))
    swaps = [i for i, ins in enumerate(cleanup) if called(ins, "py_tls_exc_swap_slot")]
    drops = [i for i, ins in enumerate(cleanup) if called(ins, "pcc_gc_store_root")
             and slot(args(ins)[0]) in roots]
    assert len(swaps) >= 2 and swaps[0] < min(drops) < max(drops) < swaps[1]
    partial = [candidate for candidate in blocks if clears(candidate.instructions) == [list_slot, args_slot]]
    assert partial and all(callable_slot in clears(straight_path(candidate, success=False))
                           for candidate in partial)


def test_vthread_callback_roots_callable_during_dynamic_argument_build(tmp_path) -> None:
    from tests.python.test_slot_call_operand_roots import _emit

    for failing_argument in (False, True):
        text = _emit(_callback_lifetime_program(failing_argument))
        (tmp_path / ("callback_error.ll" if failing_argument else "callback_success.ll")).write_text(text)
        _assert_callback_slot_contract(text)


@pytest.mark.integration
@pytest.mark.parametrize("failing_argument", [False, True], ids=["success", "argument-error"])
def test_vthread_temporary_callback_lifetime_native(
    tmp_path, pcc_runtime_archive, failing_argument,
):
    import ast
    import os
    import subprocess

    from pcc.diagnostics.gc_log import parse_log_lines
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "callback_lifetime.py"
    executable = tmp_path / "callback_lifetime"
    source.write_text(_callback_lifetime_program(failing_argument), encoding="utf-8")
    compile_python(
        str(source), str(executable), backend="self", libpython_mode="off",
        ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive),
    )
    for backend in range(5):
        log = tmp_path / ("gc" + str(backend) + ".jsonl")
        ran = subprocess.run(
            [str(executable)], capture_output=True, text=True, timeout=20,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend),
                     PCC_LOG="gc", PCC_LOG_FORMAT="json", PCC_LOG_FILE=str(log)),
        )
        assert ran.returncode == 0, (backend, ran.stdout, ran.stderr)
        output = ran.stdout.splitlines()
        assert len(output) == 3 and output[:2] == [str(backend), "17" if failing_argument else "42"]
        assert sorted(ast.literal_eval(output[2])) == ["callback-drop", "drop"]
        assert ran.stderr == ""
        assert log.is_file()
        observed = {event.fields["value1"] for event in parse_log_lines(log.read_text().splitlines())
                    if event.fields.get("category") == "gc"
                    and event.event in ("collect_start", "collect_stop", "collect_end")}
        assert observed == {backend}


def test_vthread_callback_args_tuple_is_traced_across_each_later_operand() -> None:
    source = _source()
    builder = _between(
        source,
        "    def _emit_virtual_thread_rooted_args_tuple(",
        "    def _emit_virtual_thread_current_fd_wait(",
    )
    allocation = builder.index('self.runtime[runtime_new]')
    root = builder.index("self._enter_virtual_thread_operand_root(", allocation)
    operand = builder.index("self._emit_expr_with_cpy_operand_cleanup(", root)
    reload = builder.index(
        "self._load_virtual_thread_operand_root(", operand
    )
    store = builder.index('self.runtime["py_tuple_set_item"]', reload)
    checked = builder.index(
        "self._emit_virtual_thread_container_call_check(", store
    )
    assert allocation < root < operand < reload < store < checked
    assert "rooted_pcc_lifetimes=(container_root,)" in builder
    assert '"vthread.call.args.value"' in builder
    assert "rooted_value = self._load_virtual_thread_operand_root(value_root)" in (
        builder
    )
    assert "(container_root, value_root)" in builder
    assert "self._release_rooted_pcc_lifetimes((value_root,))" in builder
    assert 'self.runtime["pcc_gc_pin"]' not in builder
    assert 'operation = "py_list_extend" if is_splat else "py_list_append"' in (
        builder
    )
    assert 'self.runtime["py_tuple_from_list"]' in builder
    assert "(container_root, tuple_root)" in builder


def test_vthread_args_container_errors_unwind_before_callable_root() -> None:
    source = _source()
    check = _between(
        source,
        "    def _emit_virtual_thread_container_call_check(",
        "    def _emit_virtual_thread_rooted_args_tuple(",
    )
    wrapper = _between(
        source,
        "    def _emit_virtual_thread_dynamic_args_with_roots(",
        "    def _emit_virtual_thread_container_call_check(",
    )

    assert "rooted_pcc_lifetimes=container_roots" not in check
    assert "container_roots," in check
    assert "pinned_values" not in check
    assert "self._try_err_block = cleanup" in check
    assert "roots," in wrapper
    assert "self._try_err_block = pcc_cleanup" in wrapper


def test_rooted_operand_cleanup_reloads_and_unwinds_both_error_kinds() -> None:
    cleanup_source = (
        REPO / "pcc" / "frontends" / "python" / "codegen" / "cpy_call_lowering.py"
    ).read_text(encoding="utf-8")
    release = _between(
        cleanup_source,
        "    def _release_rooted_pcc_lifetimes(",
        "    def _emit_expr_with_cpy_operand_cleanup(",
    )
    evaluate = _between(
        cleanup_source,
        "    def _emit_expr_with_cpy_operand_cleanup(",
        "    def _release_cpy_callable_if_owned(",
    )

    assert "for root_slot, release_owned in reversed(roots):" in release
    loaded = release.index('self.runtime["pcc_gc_load_ptr"]')
    left = release.index("self._leave_container_temp_root(root_slot)", loaded)
    balanced = release.index("self._gc_release(rooted_value, known_object=True)", left)
    assert loaded < left < balanced
    # Separate pcc and CPython unwind blocks each receive the same live roots.
    assert evaluate.count("rooted_pcc_lifetimes,") >= 2
    assert "self._try_err_block = pcc_cleanup" in evaluate
    assert "self._cpy_operand_cleanup_block = cpy_cleanup" in evaluate


def test_vthread_root_helpers_are_in_the_pcc1_host_method_closure() -> None:
    host_contract = (
        REPO / "pcc" / "frontends" / "python" / "codegen" / "host_contract.py"
    ).read_text(encoding="utf-8")
    static_methods = (
        REPO
        / "pcc" / "frontends" / "python"
        / "codegen"
        / "_l1_codegen_static_methods.py"
    ).read_text(encoding="utf-8")
    helpers = (
        "_emit_virtual_thread_container_call_check",
        "_emit_virtual_thread_dynamic_args_with_roots",
        "_emit_virtual_thread_rooted_args_tuple",
        "_enter_virtual_thread_operand_root",
        "_load_virtual_thread_operand_root",
        "_release_rooted_pcc_lifetimes",
    )
    for helper in helpers:
        assert f'"{helper}"' in host_contract
        # The generated table stores compact ``_append_method(out, '<name>',
        # ...)`` entries; the tuple-of-dicts ``'name': ...`` schema is built
        # eagerly at import, not written to the file.
        assert f"_append_method(out, '{helper}'," in static_methods


def test_dynamic_vthread_result_producers_override_ambiguous_any_ownership() -> None:
    helper = _between(
        _source(),
        "    def _release_virtual_thread_argument(",
        "    def _emit_virtual_thread_current_fd_wait(",
    )
    for producer in ("spawn", "call", "join", "current", "result", "exception"):
        assert f'"pcc.virtual_thread.{producer}"' in helper
    assert "self._gc_release(obj)" in helper
    assert "self._gc_release_if_owned(obj, source_expr)" in helper


def test_vthread_runtime_error_helper_releases_local_exception_owner() -> None:
    helper = _between(
        _source(),
        "    def _emit_virtual_thread_rc_check(",
        "    def _emit_virtual_thread_current_fd_wait(",
    )
    raised = helper.index('self.runtime["py_raise"]')
    released = helper.index("self._gc_release(exc)", raised)
    assert raised < released


def test_vthread_owned_result_classifier_is_exact_and_raw_scaffold_equal() -> None:
    ownership = (
        REPO / "pcc" / "frontends" / "python" / "codegen" / "ownership_lowering.py"
    ).read_text(encoding="utf-8")
    object_classifier = _between(
        ownership,
        "    def _expr_returns_owned_object(",
        "    def _return_type_is_owned_object(",
    )
    raw_classifier = _between(
        ownership,
        "    def _raw_scaffold_object_rhs_is_owned(",
        "    def _valueclass_payload_expr_fields_are_owned(",
    )
    owned = ("spawn", "call", "join", "current", "result", "exception")
    scalar_or_none = ("cancel", "outcome", "state", "sleep", "block_on_fd")

    for classifier in (object_classifier, raw_classifier):
        assert "self._native_builtin_value_kind_for_expr(expr.func)" in classifier
        for operation in owned:
            assert f'"pcc.virtual_thread.{operation}"' in classifier
        for operation in scalar_or_none:
            assert f'"pcc.virtual_thread.{operation}"' not in classifier

    # C-ABI-exporting runtime modules normally opt out of automatic ownership,
    # but exact native vthread producers remain owned there as well.
    assert raw_classifier.index("native_call =") < raw_classifier.index(
        "if self._module_has_c_abi_export:"
    )


def _compile_dynamic_call_owned_ir(tmp_path, import_source: str, call_name: str) -> str:
    from pcc.frontends.python.pipeline import compile_python

    source_path = tmp_path / (call_name.replace(".", "_") + "_owned.py")
    output_path = source_path.with_suffix(".ll")
    source_path.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import c_int64, extern
            {import_source}

            monotonic_us = extern("pcc_runtime_monotonic_us", (), c_int64)

            def callback(value: int) -> str:
                return str(value)

            def dispatch(callback, value: int):
                response = {call_name}(callback, value)
                response = {call_name}(callback, value + 1)
                return response
            """
        ).lstrip(),
        encoding="utf-8",
    )
    compile_python(
        str(source_path),
        str(output_path),
        emit_llvm_only=True,
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    return output_path.read_text(encoding="utf-8")


def test_vthread_call_module_alias_result_gets_owned_local_management(tmp_path) -> None:
    ir_text = _compile_dynamic_call_owned_ir(
        tmp_path,
        "import pcc.virtual_thread as vt",
        "vt.call",
    )
    assert "response.owned.resolve" in ir_text


def test_vthread_call_import_from_alias_result_gets_owned_local_management(
    tmp_path,
) -> None:
    ir_text = _compile_dynamic_call_owned_ir(
        tmp_path,
        "from pcc.virtual_thread import call as invoke",
        "invoke",
    )
    assert "response.owned.resolve" in ir_text

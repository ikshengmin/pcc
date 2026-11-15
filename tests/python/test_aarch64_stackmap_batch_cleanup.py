"""Unpublished batch ownership, not RSS, Stage2, or speedup acceptance.

compiler-contract.md requires exception and ownership preservation. These host
checks parse/prepare real IR and retain actual completed packed plans. Injection
at the builder seam happens BEFORE the failing builder allocates anything.
Partial builder/constructor allocation is deliberately outside this contract.
Line-event MemoryError injection models append/tuple acquisition boundaries; it
is not an allocator failure or native OOM test. The frozen oracle intentionally
retains unpublished plans so the negative control can detect the original gap.
"""

from __future__ import annotations

import ast
from contextlib import contextmanager
import hashlib
import inspect
import sys
import textwrap

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend import self_backend_aarch64_darwin as emitter
from pcc.backend import self_backend_precise_stackmaps as maps
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.backend.self_backend_value_arena import CompilerIntArena
from tests.python.test_precise_stackmap_abi import _stale_managed_ssa_ir


_ARENA_FIELDS = (
    "scalars", "spans", "location_scalars", "location_group_spans",
    "reload_scalars", "entry_routes", "suffix_routes", "terminator_routes",
    "entry_route_spans", "suffix_route_spans", "terminator_route_spans",
)
# Static case inventory; test collection is not needed to enumerate the scope.
_FAILURE_CASES = ("builder", "callback", "iteration", "tuple", "append", "reset")
_PUBLIC_ROUTES = (
    ("aarch64-darwin", False),
    ("x86_64-linux", False),
    ("x86_64-linux", True),
)
_CLOSE_PACKED = maps.PackedPlannedSafepoints.close
_LEGACY_BATCH_SHA256 = "5eedf06d21edc4f33939c3e9352fbe4503f26031999921350836d78d39bb7fbc"
_LEGACY_BATCH_SOURCE = '''def build_stack_map_plans(
    functions: list[ParsedFunction],
    globals_: list[GlobalDef],
    *,
    target: str,
    function_symbol=None,
    _share_globals_index: bool = False,
) -> tuple[FunctionStackMapPlan, ...]:
    # Only the emitter's initial AArch64 batch opts in: its symbol callback is
    # pure and globals do not change inside that batch. Public callers retain
    # per-function indexing, including mutations made by function_symbol.
    if _share_globals_index and target == "aarch64-darwin":
        globals_index: list[dict[str, GlobalDef]] = []
        plans: list[FunctionStackMapPlan] = []
        try:
            for func in functions:
                identity_name = (
                    function_symbol(func.name)
                    if function_symbol is not None
                    else func.name
                )
                plans.append(_build_function_stack_map_plan_native(
                    func, globals_, identity_name, globals_index,
                ))
            return tuple(plans)
        finally:
            # Do not retain the name table across emission, timing callbacks,
            # or tail-call replanning. Clear entries even on a failed plan.
            if globals_index:
                globals_index[0].clear()
            globals_index.clear()
    return tuple(
        build_function_stack_map_plan(
            func,
            globals_,
            target=target,
            identity_name=(
                function_symbol(func.name)
                if function_symbol is not None
                else func.name
            ),
        )
        for func in functions
    )
'''


class _PrimaryFault(RuntimeError):
    pass


class _StopFault(BaseException):
    pass


class _FinallyFault(RuntimeError):
    pass


class _CleanupFault(BaseException):
    pass


def _legacy_batch():
    assert hashlib.sha256(_LEGACY_BATCH_SOURCE.encode()).hexdigest() == _LEGACY_BATCH_SHA256
    namespace = dict(vars(maps))
    exec(compile(_LEGACY_BATCH_SOURCE, "<frozen-before-batch>", "exec"), namespace)
    return namespace["build_stack_map_plans"]


def _prepare(target="aarch64-darwin"):
    triple = "x86_64-unknown-linux-gnu" if target == "x86_64-linux" else "arm64-apple-darwin23.6.0"
    source = _stale_managed_ssa_ir(triple) + "\n@batch_unused = internal constant i32 2\n"
    return emitter.prepare_parsed_module_for_target(
        parse_self_backend_module(source),
        aggregate_returned_indirect=emitter._aggregate_returned_indirect,
        aggregate_returned_indirect_indexed=emitter._aggregate_returned_indirect_indexed,
        materialize_legacy_slots=target == "x86_64-linux",
    )


def _arenas(plan):
    packed = plan.packed_records
    assert packed is not None
    arenas = tuple(getattr(packed, field) for field in _ARENA_FIELDS)
    assert all(isinstance(arena, CompilerIntArena) for arena in arenas)
    return arenas


def _plan_value(plan):
    # Materializing diagnostics here is a test observer, not production work.
    return (plan.function_name, plan.function_id, plan.frame_size, plan.end_label,
            plan.target, plan.diagnostic_records(), plan.block_entry_labels,
            plan.instruction_suffix_labels, plan.terminator_prefix_labels)


class _FailingIteration:
    def __init__(self, functions, count, fault):
        self.functions = functions
        self.count = count
        self.fault = fault

    def __iter__(self):
        for index, function in enumerate(self.functions):
            if index == self.count:
                raise self.fault
            yield function
        raise AssertionError("iteration fault fixture needs a trailing function")


def _injection_line(function, boundary):
    """Discover the actual function's line, never rely on a frozen line number."""
    lines, start = inspect.getsourcelines(function)
    tree = ast.parse(textwrap.dedent("".join(lines)))
    matches = []
    for node in ast.walk(tree.body[0]):
        if (boundary == "tuple" and isinstance(node, ast.Return)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "tuple"
                and len(node.value.args) == 1
                and isinstance(node.value.args[0], ast.Name)
                and node.value.args[0].id == "plans"):
            matches.append(node.lineno)
        if (boundary == "append" and isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and isinstance(node.value.func.value, ast.Name)
                and node.value.func.value.id == "plans"
                and node.value.func.attr == "append"):
            assert isinstance(node.value.args[0], ast.Name)
            assert node.value.args[0].id == "pending_plan"
            matches.append(node.lineno)
        if (boundary == "reset" and isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant) and node.value.value is None
                and any(isinstance(target, ast.Name) and target.id == "pending_plan"
                        for target in node.targets)):
            matches.append(node.lineno)
    assert len(matches) == 1, (boundary, matches)
    return start + matches[0] - 1


@contextmanager
def _line_fault(function, boundary, fault, completed, observed):
    if boundary not in ("tuple", "append", "reset"):
        yield
        return
    lineno = _injection_line(function, boundary)
    previous = sys.gettrace()

    def trace(frame, event, arg):
        if (frame.f_code is function.__code__ and event == "line"
                and frame.f_lineno == lineno and not observed):
            plans = frame.f_locals["plans"]
            expected = completed - 1 if boundary == "append" else completed
            if len(plans) == expected:
                pending = frame.f_locals.get("pending_plan")
                if boundary in ("append", "reset"):
                    assert pending is not None
                if boundary == "reset":
                    assert pending is plans[-1]  # Must not close this alias twice.
                observed.append((boundary, len(plans), pending))
                raise fault
        return trace

    sys.settrace(trace)
    try:
        yield
    finally:
        sys.settrace(previous)


def _run(monkeypatch, *, case="success", legacy=False, count=1,
         fault_type=_PrimaryFault, close_failures=(), finally_failure=False,
         target="aarch64-darwin", share=True, implicit_default=False,
         mutate_callback=False, duplicate_kind="repeated", use_callback=True):
    prepared = _prepare(target)
    original_functions = tuple(prepared.functions)
    kernels = tuple(get_indexed_function_kernel(func) for func in original_functions)
    kernel_names = [(kernel.block_names, tuple(kernel.block_names)) for kernel in kernels]
    globals_alias = prepared.globals_
    globals_before = tuple(globals_alias)
    functions = [original_functions[0]] * (count + 1)
    if duplicate_kind == "names":
        # Separate parsed/prepared functions with equal names, not cloned plans.
        second = _prepare(target)
        other = second.functions[0]
        assert other is not functions[0] and other.name == functions[0].name
        functions[1:] = [other] * count
        kernels += tuple(get_indexed_function_kernel(func) for func in second.functions)
        kernel_names.extend((kernel.block_names, tuple(kernel.block_names)) for kernel in kernels[len(original_functions):])
    if case == "empty":
        functions = []
    elif case in ("success", "tuple", "append", "reset"):
        functions = functions[:count]
    function_alias = functions
    function_values = tuple(functions)
    primary = fault_type("batch primary: " + case)
    final_fault = _FinallyFault("globals finally takes precedence")
    plans, arenas, before_values, callbacks, calls, events = [], [], [], [], [], []
    index_aliases, table_aliases, trace_hits, cleanup_errors = [], [], [], []
    global_reads = []
    baseline = maps._build_function_stack_map_plan_native
    public = maps.build_function_stack_map_plan
    result = None
    caught = None

    class FailingTable(dict):
        def clear(self):
            events.append(("globals-clear-failure",))
            raise final_fault

    def observe_builder(func, globals_, identity_name, index=None):
        calls.append((func, identity_name, index is not None))
        global_reads.append(tuple(global_.name for global_ in globals_))
        if case == "builder" and len(plans) == count:
            raise primary
        plan = baseline(func, globals_, identity_name, index)
        plans.append(plan)
        arenas.append(_arenas(plan))
        before_values.append(_plan_value(plan))
        assert plan.packed_records.block_names is get_indexed_function_kernel(func).block_names
        if index is not None:
            if not index_aliases:
                index_aliases.append(index)
                if finally_failure:
                    index[0] = FailingTable(index[0])
                table_aliases.append(index[0])
            assert index is index_aliases[0]
        return plan

    def observe_public(func, globals_, **kwargs):
        if target == "aarch64-darwin":
            # The actual public builder dispatches to observe_builder below.
            return public(func, globals_, **kwargs)
        calls.append((func, kwargs["identity_name"], False))
        global_reads.append(tuple(global_.name for global_ in globals_))
        if case == "builder" and len(plans) == count:
            raise primary
        plan = public(func, globals_, **kwargs)
        plans.append(plan)
        before_values.append(_plan_value(plan))
        if plan.packed_records is not None:
            arenas.append(_arenas(plan))
        return plan

    def symbol(name):
        callbacks.append(name)
        if case == "callback" and len(callbacks) == count + 1:
            raise primary
        if mutate_callback:
            # Public path must still see this mutation before each build.
            globals_alias.reverse()
        return "_batch_" + name

    def close(packed):
        index = next(i for i, plan in enumerate(plans) if plan.packed_records is packed)
        events.append(("plan-close", index))
        if share and target == "aarch64-darwin":
            if not finally_failure:
                assert index_aliases[0] == [] and table_aliases[0] == {}
            else:
                assert events[0] == ("globals-clear-failure",)
        if index in close_failures:
            error = _CleanupFault("plan cleanup " + str(index))
            cleanup_errors.append(error)
            raise error
        _CLOSE_PACKED(packed)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(maps, "_build_function_stack_map_plan_native", observe_builder)
            patch.setattr(maps, "build_function_stack_map_plan", observe_public)
            patch.setattr(maps.PackedPlannedSafepoints, "close", close)
            function = _legacy_batch() if legacy else maps.build_stack_map_plans
            iterable = (_FailingIteration(functions, count, primary)
                        if case == "iteration" else functions)
            kwargs = {"target": target, "function_symbol": symbol if use_callback else None}
            if not implicit_default:
                kwargs["_share_globals_index"] = share
            try:
                with _line_fault(function, case, primary, count, trace_hits):
                    result = function(iterable, globals_alias, **kwargs)
            except BaseException as error:
                caught = error
        if case in ("append", "reset", "tuple"):
            assert len(trace_hits) == 1, "line-event simulation must actually fire"
        assert function_alias == list(function_values)
        assert prepared.globals_ is globals_alias
        assert sorted(map(id, globals_alias)) == sorted(map(id, globals_before))
        assert tuple(prepared.functions) == original_functions
        for kernel, (names, values) in zip(kernels, kernel_names):
            assert kernel.block_names is names and tuple(names) == values
            assert not kernel.instruction_metadata._closed
        if index_aliases and not finally_failure:
            assert index_aliases[0] == [] and table_aliases[0] == {}
        if result is not None:
            assert len(result) == len(plans)
            assert all(result[index] is plans[index] for index in range(len(plans)))
        closed = tuple(tuple(arena._closed for arena in group) for group in arenas)
        return {
            "result": None if result is None else tuple(_plan_value(plan) for plan in result),
            "returned": result, "plans": tuple(plans), "before": tuple(before_values),
            "closed": closed, "events": tuple(events), "callbacks": tuple(callbacks),
            "calls": tuple((func.name, identity, shared) for func, identity, shared in calls),
            "error": caught, "primary": primary, "finally": final_fault,
            "cleanup_errors": tuple(cleanup_errors), "trace_hits": tuple(trace_hits),
            "global_reads": tuple(global_reads),
        }
    finally:
        # Restore instrumentation first, then release only known actual owners.
        # This also makes the old leaking oracle and cleanup-error cases safe.
        for plan in plans:
            if plan.packed_records is not None:
                _CLOSE_PACKED(plan.packed_records)
        for kernel in kernels:
            kernel.close_native_tables()


@pytest.mark.parametrize("case", _FAILURE_CASES)
@pytest.mark.parametrize("fault_type", (_PrimaryFault, _StopFault, MemoryError))
def test_unpublished_completed_plans_close_on_each_failure(monkeypatch, case, fault_type):
    observed = _run(monkeypatch, case=case, count=2, fault_type=fault_type)
    assert observed["error"] is observed["primary"]
    assert len(observed["plans"]) == 2
    assert observed["closed"] == ((True,) * 11,) * 2
    # Pending acquisition closes before already-appended plans; a reset fault
    # refers to an appended plan and must not cause a duplicate close.
    expected = (("plan-close", 1), ("plan-close", 0)) if case == "append" else (
        ("plan-close", 0), ("plan-close", 1),
    )
    assert observed["events"] == expected


@pytest.mark.parametrize("case", ("builder", "callback", "iteration"))
def test_frozen_negative_control_detects_the_original_unpublished_plans(monkeypatch, case):
    old = _run(monkeypatch, case=case, legacy=True, count=2)
    new = _run(monkeypatch, case=case, count=2)
    assert old["error"] is old["primary"] and new["error"] is new["primary"]
    assert old["closed"] == ((False,) * 11,) * 2
    assert new["closed"] == ((True,) * 11,) * 2
    assert old["before"] == new["before"]
    assert old["callbacks"] == new["callbacks"]
    assert old["calls"] == new["calls"]


@pytest.mark.parametrize("failures", ((0,), (1,), (0, 1), (0, 1, 2)))
@pytest.mark.parametrize("case", ("builder", "append", "reset"))
def test_each_completed_owner_is_attempted_even_when_cleanup_raises(monkeypatch, failures, case):
    result = _run(monkeypatch, case=case, count=3, close_failures=failures)
    assert result["error"] is result["primary"]
    assert len(result["cleanup_errors"]) == len(failures)
    assert sorted(event[1] for event in result["events"]) == [0, 1, 2]
    assert result["closed"] == tuple((index not in failures,) * 11 for index in range(3))


@pytest.mark.parametrize("case", ("success", "builder", "callback", "iteration"))
@pytest.mark.parametrize("legacy", (False, True))
def test_globals_finally_keeps_existing_precedence(monkeypatch, case, legacy):
    result = _run(monkeypatch, case=case, count=2, legacy=legacy, finally_failure=True)
    assert result["error"] is result["finally"]
    assert result["events"][0] == ("globals-clear-failure",)
    assert result["closed"] == (((False if legacy else True),) * 11,) * 2
    if case != "success":
        assert result["error"].__context__ is result["primary"]


@pytest.mark.parametrize("case", ("empty", "success"))
@pytest.mark.parametrize("duplicate_kind", ("repeated", "names"))
def test_success_returns_open_plans_and_preserves_block_name_aliases(monkeypatch, case, duplicate_kind):
    old = _run(monkeypatch, case=case, count=3, legacy=True, duplicate_kind=duplicate_kind)
    new = _run(monkeypatch, case=case, count=3, duplicate_kind=duplicate_kind)
    assert old["error"] is new["error"] is None
    assert old["result"] == new["result"]
    assert old["before"] == new["before"]
    assert old["callbacks"] == new["callbacks"]
    assert old["closed"] == new["closed"] == (((False,) * 11,) * (0 if case == "empty" else 3))
    assert old["events"] == new["events"] == ()


@pytest.mark.parametrize("target,share", _PUBLIC_ROUTES)
@pytest.mark.parametrize("case", ("success", "builder", "callback", "iteration"))
def test_default_and_other_target_paths_preserve_public_failure_states(monkeypatch, target, share, case):
    kwargs = dict(case=case, count=2, target=target, share=share,
                  implicit_default=not share, mutate_callback=True)
    old = _run(monkeypatch, legacy=True, **kwargs)
    new = _run(monkeypatch, **kwargs)
    assert old["result"] == new["result"]
    assert old["before"] == new["before"]
    assert old["callbacks"] == new["callbacks"]
    assert old["calls"] == new["calls"]
    assert old["global_reads"] == new["global_reads"]
    assert len(set(new["global_reads"])) == 2
    assert old["closed"] == new["closed"]
    assert not any(any(group) for group in new["closed"])
    assert old["events"] == new["events"] == ()
    if case == "success":
        assert old["error"] is new["error"] is None
    else:
        assert old["error"] is old["primary"] and new["error"] is new["primary"]


def test_frozen_oracle_hash_and_unchanged_public_branch():
    assert hashlib.sha256(_LEGACY_BATCH_SOURCE.encode()).hexdigest() == _LEGACY_BATCH_SHA256
    old = ast.parse(_LEGACY_BATCH_SOURCE).body[0]
    new = ast.parse(inspect.getsource(maps.build_stack_map_plans)).body[0]
    assert ast.dump(old.args) == ast.dump(new.args)
    assert ast.dump(old.body[-1]) == ast.dump(new.body[-1])
    assert ast.dump(old.body[0].test) == ast.dump(new.body[0].test)


@pytest.mark.parametrize("legacy", (False, True))
def test_actual_ambiguous_provenance_failure_after_a_completed_plan(monkeypatch, legacy):
    source = _stale_managed_ssa_ir("arm64-apple-darwin23.6.0", ambiguous=True)
    source = source.replace("define void @refresh", """
define i64 @first(i64 %value) {
entry:
  %result = add i64 %value, 1
  ret i64 %result
}

define void @refresh""", 1)
    prepared = emitter.prepare_parsed_module_for_target(
        parse_self_backend_module(source),
        aggregate_returned_indirect=emitter._aggregate_returned_indirect,
        aggregate_returned_indirect_indexed=emitter._aggregate_returned_indirect_indexed,
        materialize_legacy_slots=False,
    )
    builder = maps._build_function_stack_map_plan_native
    initialize = CompilerIntArena.__init__
    completed, constructed, caught = [], [], []
    kernels = [get_indexed_function_kernel(func) for func in prepared.functions]
    names = [(kernel.block_names, tuple(kernel.block_names)) for kernel in kernels]

    def initialize_and_retain(arena, *args, **kwargs):
        initialize(arena, *args, **kwargs)
        constructed.append(arena)

    def observe_completed(*args, **kwargs):
        try:
            plan = builder(*args, **kwargs)
        except BackendUnavailable as error:
            caught.append(error)
            raise
        completed.append(plan)
        return plan

    try:
        with monkeypatch.context() as patch:
            patch.setattr(CompilerIntArena, "__init__", initialize_and_retain)
            patch.setattr(maps, "_build_function_stack_map_plan_native", observe_completed)
            function = _legacy_batch() if legacy else maps.build_stack_map_plans
            with pytest.raises(BackendUnavailable, match="ambiguous root provenance") as error:
                function(prepared.functions, prepared.globals_, target="aarch64-darwin",
                         _share_globals_index=True)
        assert len(completed) == len(caught) == 1
        assert error.value is caught[0]
        assert completed[0].function_name == "first"
        assert all(arena._closed is (not legacy) for arena in _arenas(completed[0]))
        for kernel, (alias, value) in zip(kernels, names):
            assert kernel.block_names is alias and tuple(alias) == value
            assert not kernel.instruction_metadata._closed
    finally:
        # The failing lower builder's PARTIAL allocations are not a batch claim.
        # Retain actual constructed objects solely to recover this test's owned
        # storage after observation. Never recover/free a remembered raw pointer.
        for arena in constructed:
            arena.close()
        for kernel in kernels:
            kernel.close_native_tables()


@pytest.mark.parametrize("case", ("empty", "success", "builder"))
def test_absent_symbol_callback_preserves_names_and_open_success(monkeypatch, case):
    old = _run(monkeypatch, case=case, count=2, legacy=True, use_callback=False)
    new = _run(monkeypatch, case=case, count=2, use_callback=False)
    assert old["callbacks"] == new["callbacks"] == ()
    assert old["before"] == new["before"]
    assert old["result"] == new["result"]
    assert all(name == identity for name, identity, _ in new["calls"])
    if case == "builder":
        assert old["error"] is old["primary"] and new["error"] is new["primary"]
        assert old["closed"] == ((False,) * 11,) * 2
        assert new["closed"] == ((True,) * 11,) * 2
    else:
        assert old["error"] is new["error"] is None
        assert old["closed"] == new["closed"]
        assert not any(any(group) for group in new["closed"])


@pytest.mark.parametrize("failure", ("callback", "iteration"))
def test_same_prepared_inputs_retry_after_private_batch_failure(monkeypatch, failure):
    prepared = _prepare()
    functions = [prepared.functions[0]] * 3
    function_alias = tuple(functions)
    globals_alias = prepared.globals_
    globals_values = tuple(globals_alias)
    kernels = [get_indexed_function_kernel(func) for func in prepared.functions]
    names = [(kernel.block_names, tuple(kernel.block_names)) for kernel in kernels]
    retained = []
    builder = maps._build_function_stack_map_plan_native
    primary = _PrimaryFault("same prepared input retry")
    callback_count = 0

    def observe_builder(*args, **kwargs):
        plan = builder(*args, **kwargs)
        retained.append(plan)
        return plan

    def identity(name):
        return "_retry_" + name

    def fail_symbol(name):
        nonlocal callback_count
        callback_count += 1
        if callback_count == 2:
            raise primary
        return identity(name)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(maps, "_build_function_stack_map_plan_native", observe_builder)
            # The frozen successful oracle uses THESE exact prepared inputs.
            # Keep its caller-owned plans open across the failed private batch.
            expected_plans = _legacy_batch()(
                functions, globals_alias, target="aarch64-darwin",
                function_symbol=identity, _share_globals_index=True,
            )
            expected = tuple(_plan_value(plan) for plan in expected_plans)
            start = len(retained)
            inputs = (_FailingIteration(functions, 1, primary)
                      if failure == "iteration" else functions)
            with pytest.raises(_PrimaryFault) as error:
                maps.build_stack_map_plans(
                    inputs, globals_alias, target="aarch64-darwin",
                    function_symbol=fail_symbol if failure == "callback" else identity,
                    _share_globals_index=True,
                )
            assert error.value is primary
            failed = retained[start:]
            assert len(failed) == 1
            assert all(arena._closed for arena in _arenas(failed[0]))
            assert all(not arena._closed for plan in expected_plans for arena in _arenas(plan))
            for kernel, (alias, values) in zip(kernels, names):
                assert kernel.block_names is alias and tuple(alias) == values
                assert not kernel.instruction_metadata._closed
            assert tuple(functions) == function_alias
            assert prepared.globals_ is globals_alias and tuple(globals_alias) == globals_values
            # No parse, prepare, or kernel reconstruction between failure/retry.
            retry = maps.build_stack_map_plans(
                functions, globals_alias, target="aarch64-darwin",
                function_symbol=identity, _share_globals_index=True,
            )
            assert tuple(_plan_value(plan) for plan in retry) == expected
            assert tuple(_plan_value(plan) for plan in expected_plans) == expected
            assert all(not arena._closed for plan in retry for arena in _arenas(plan))
            assert len(retry) == 3 and len({id(plan) for plan in retry}) == 3
    finally:
        for plan in retained:
            _CLOSE_PACKED(plan.packed_records)
        for kernel in kernels:
            kernel.close_native_tables()


@pytest.mark.parametrize("case", ("builder", "callback", "iteration", "tuple"))
def test_failure_before_any_owner_returns_preserves_the_primary(monkeypatch, case):
    result = _run(monkeypatch, case=case, count=0)
    assert result["error"] is result["primary"]
    assert result["plans"] == result["closed"] == result["events"] == ()
    assert result["returned"] is None

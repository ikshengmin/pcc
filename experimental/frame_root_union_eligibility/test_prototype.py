"""Finite research-only eligibility and original-output differential tests."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from pcc.backend import self_backend_precise_stackmaps as maps
from pcc.backend import x86_64_frame_registers as frame
from pcc.backend.self_backend_prepare import prepare_module_for_target
from pcc.backend.self_backend_ir import SlotInfo, TypeDesc


from experimental.frame_root_union_eligibility import prototype
from experimental.frame_root_union_eligibility.held_v1 import reference_slot_offsets as _reference


def _eligible(plan):
    # Functional coverage of both branches, not a proposed production policy.
    return prototype.slot_offsets(plan, minimum_saved_items=0, minimum_saved_items_per_record=0)


def _record(index, locations=(), reloads=()):
    return maps.PlannedSafepoint(index + 1, ".Lroot_" + str(index), 1,
                                 locations, reloads=reloads)


def _plan(records):
    return maps.FunctionStackMapPlan("probe", 1, 1024, ".Lend", records,
                                     (), (), (), "x86_64-linux")


def _items(function, plan):
    return list(function(plan).items())


@pytest.mark.parametrize("shape", ["empty", "single", "reused", "nonconsecutive",
                                   "equal_distinct", "reloads"])
def test_ordinary_groups_preserve_values_and_insertion_order(shape):
    first = (maps.PlannedRootLocation(-8, True), maps.PlannedRootLocation(-24, False))
    second = (maps.PlannedRootLocation(-16, False),)
    if shape == "empty":
        records = ()
    elif shape == "single":
        records = (_record(0, first),)
    elif shape == "reused":
        records = tuple(_record(index, first) for index in range(32))
    elif shape == "nonconsecutive":
        records = (_record(0, first), _record(1, second), _record(2, first))
    elif shape == "equal_distinct":
        equal = tuple(maps.PlannedRootLocation(root.offset, root.owned) for root in first)
        assert equal == first and equal is not first
        records = (_record(0, first), _record(1, equal), _record(2, second))
    else:
        reload = (maps.PlannedManagedReload(-32, -48),)
        records = (_record(0, first, reload), _record(1, second), _record(2, first, reload))
    plan = _plan(records)
    assert _items(_eligible, plan) == _items(_reference, plan)


@pytest.mark.parametrize("identity_mode", ["collision", "moving"])
def test_identity_changes_or_collisions_only_miss(monkeypatch, identity_mode):
    groups = [(maps.PlannedRootLocation(-8 * (index + 1), True),) for index in range(4)]
    plan = _plan(tuple(_record(index, groups[index % 4]) for index in range(24)))
    expected = _items(_reference, plan)
    calls = []

    def changing_identity(value):
        calls.append(value)  # Retain the actual tuples, as a moving root would.
        return 1 if identity_mode == "collision" else len(calls)

    monkeypatch.setattr(prototype, "id", changing_identity, raising=False)
    assert _items(_eligible, plan) == expected
    assert len(calls) == len(plan.records)


def _opaque_case(kind):
    events = []
    roots = (maps.PlannedRootLocation(-8, True),)
    first = _record(0, roots)
    last = _record(2, roots)

    class Location:
        @property
        def offset(self):
            events.append("location")
            return -16

    class Reload:
        @property
        def source_offset(self):
            events.append("reload")
            object.__setattr__(roots[0], "offset", -24)
            return -32

    class Offset(int):
        def __neg__(self):
            events.append("negative")
            object.__setattr__(roots[0], "offset", -40)
            return 48

    class Record:
        @property
        def locations(self):
            events.append("record.locations")
            object.__setattr__(roots[0], "offset", -56)
            return roots

        @property
        def reloads(self):
            events.append("record.reloads")
            return ()

    def values(value, label):
        events.append(label + ".begin")
        yield value
        events.append(label + ".end")

    middle = _record(1, roots)
    if kind == "record":
        middle = Record()
    elif kind == "locations":
        middle = replace(middle, locations=values(Location(), "locations"))
    elif kind == "locations_list":
        middle = replace(middle, locations=list(roots))
    elif kind == "reloads":
        middle = replace(middle, reloads=values(Reload(), "reloads"))
    elif kind == "location":
        middle = replace(middle, locations=(Location(),))
    elif kind == "reload":
        middle = replace(middle, reloads=(Reload(),))
    elif kind == "offset":
        middle = replace(middle, locations=(maps.PlannedRootLocation(Offset(-16), True),))
    elif kind == "error":
        class Broken:
            @property
            def offset(self):
                events.append("error")
                raise RuntimeError("original location failure")
        middle = replace(middle, locations=(Broken(),))
    records = (first, _record(1, roots), middle, last)
    if kind == "records":
        def stream():
            for record in records:
                events.append("record.next")
                yield record
        plan = _plan(stream())
    elif kind == "records_list":
        plan = _plan(list(records))
    else:
        plan = _plan(records)
    return plan, events


@pytest.mark.parametrize("kind", ["record", "locations", "locations_list", "reloads",
                                  "location", "reload", "offset", "records",
                                  "records_list", "error"])
def test_opaque_callbacks_mutations_and_errors_keep_original_order(kind):
    def observe(function):
        plan, events = _opaque_case(kind)
        try:
            result = ("return", _items(function, plan))
        except Exception as error:
            result = ("error", type(error), error.args)
        return result, events
    assert observe(_eligible) == observe(_reference)


def _ordinary_ir(root_count, call_count):
    calls = "\n".join("  call void @opaque_call(ptr %obj)" for _ in range(call_count))
    return f'''target triple = "x86_64-unknown-linux-gnu"
@frame_map = internal constant i32 {root_count}
declare void @pcc_gc_frame_enter(ptr, ptr)
declare void @pcc_gc_frame_leave(ptr)
declare void @opaque_call(ptr)
define void @probe(ptr %obj) {{
entry:
  %roots = alloca [{root_count} x ptr], align 8
  call void @pcc_gc_frame_enter(ptr @frame_map, ptr %roots)
{calls}
  call void @pcc_gc_frame_leave(ptr %roots)
  ret void
}}
'''


def _actual_plan(root_count, call_count):
    prepared = prepare_module_for_target(_ordinary_ir(root_count, call_count),
                                         aggregate_returned_indirect=lambda _type: False)
    plan = maps.build_stack_map_plans(prepared.functions, prepared.globals_,
                                      target="x86_64-linux")[0]
    return prepared, plan


def _group_counts(plan):
    # Outside every timer. Keep references and compare identity, not bare IDs.
    groups = []
    total = 0
    for record in plan.records:
        total += len(record.locations)
        if not any(group is record.locations for group in groups):
            groups.append(record.locations)
    return {"records": len(plan.records), "unique_location_tuples": len(groups),
            "location_items": total, "unique_location_items": sum(map(len, groups))}


@pytest.mark.parametrize("root_count,call_count", [(1, 1), (8, 16), (64, 128)])
def test_actual_planner_shared_groups_match_original(root_count, call_count, record_property):
    prepared, plan = _actual_plan(root_count, call_count)
    try:
        counts = _group_counts(plan)
        for key, value in counts.items():
            record_property(key, value)
        record_property("input_scope", "bounded ordinary IR fixture; not captured Stage1 plan")
        assert counts["location_items"] == root_count * call_count
        assert counts["unique_location_items"] == root_count
        assert _items(_eligible, plan) == _items(_reference, plan)
    finally:
        for function in prepared.functions:
            function.indexed_kernel.close_native_tables()


@pytest.mark.parametrize("windows", [False, True])
def test_whole_frame_pass_matches_original_root_union(monkeypatch, windows):
    roots = (maps.PlannedRootLocation(-8, True),)
    reloads = (maps.PlannedManagedReload(-16, -24),)
    plan = _plan(tuple(_record(index, roots, reloads) for index in range(16)))
    ty = TypeDesc("ptr", pointee=TypeDesc("void"))
    function = SimpleNamespace(value_slots={"%a": SlotInfo(8, ty), "%b": SlotInfo(16, ty),
                                            "%c": SlotInfo(24, ty)},
                               is_vararg=False, platform_frame_extra=144 if windows else 48,
                               max_call_stack=0)
    # Complete frame anchors ensure this is not merely an early-return test.
    if windows:
        prologue = ["f:", "  push rbp", "  mov rbp, rsp", "  sub rsp, 176",
                    "  mov QWORD PTR [rsp + 32], rbx", "  mov QWORD PTR [rsp + 40], rsi",
                    "  mov QWORD PTR [rsp + 48], rdi", "  .seh_endprologue"]
        epilogue = ["  mov rdi, QWORD PTR [rsp + 48]", "  mov rsi, QWORD PTR [rsp + 40]",
                    "  mov rbx, QWORD PTR [rsp + 32]", "  add rsp, 176", "  pop rbp", "  ret"]
    else:
        prologue = ["f:", "  push rbp", "  mov rbp, rsp", "  sub rsp, 80",
                    "  mov QWORD PTR [rsp + 0], rbx"]
        epilogue = ["  mov rbx, QWORD PTR [rsp + 0]", "  add rsp, 80", "  pop rbp", "  ret"]
    lines = prologue + ["  mov QWORD PTR [rbp - 8], rax", "  mov QWORD PTR [rbp - 16], rax",
             "  mov QWORD PTR [rbp - 24], rax", "  call g",
             "  mov r10, QWORD PTR [rbp - 8]", "  mov r11, QWORD PTR [rbp - 16]",
             "  mov rax, QWORD PTR [rbp - 24]"] + epilogue
    monkeypatch.setattr(frame, "_stack_map_slot_offsets", _eligible)
    actual = frame.keep_frame_slots_in_registers(lines, function, plan, windows=windows)
    monkeypatch.setattr(frame, "_stack_map_slot_offsets", _reference)
    expected = frame.keep_frame_slots_in_registers(lines, function, plan, windows=windows)
    assert actual == expected
    assert actual != lines
    assert "  mov r10, QWORD PTR [rbp - 8]" in actual
    assert "  mov r11, QWORD PTR [rbp - 16]" in actual


@pytest.mark.parametrize("shape", ["rootless", "shared", "unique", "mixed"])
def test_preflight_counts_only_metadata(shape):
    roots = tuple(maps.PlannedRootLocation(-8 * (index + 1), True) for index in range(4))
    groups = [roots] * 8
    if shape == "rootless":
        groups = [()] * 8
    elif shape == "unique":
        groups = [tuple(list(roots)) for _ in range(8)]
    elif shape == "mixed":
        groups = [tuple(list(roots)) if index % 2 else roots for index in range(8)]
    result = prototype.scan_location_sharing(tuple(_record(index, group) for index, group in enumerate(groups)))
    assert result.records == 8
    assert result.location_items == (0 if shape == "rootless" else 32)
    expected = {"rootless": (0, 0), "shared": (4, 1), "unique": (32, 8), "mixed": (20, 5)}[shape]
    assert (result.unique_location_items, result.unique_nonempty_groups) == expected
    assert sum(result.first_use) == expected[1]


@pytest.mark.parametrize("items,per_record,admitted", [(1, 0, True), (13, 0, False),
                                                     (0, 3, True), (0, 4, False)])
def test_policy_boundary_selects_validation_only_when_requested(monkeypatch, items, per_record, admitted):
    roots = tuple(maps.PlannedRootLocation(-8 * (index + 1), True) for index in range(4))
    plan = _plan(tuple(_record(index, roots) for index in range(4)))
    calls = []
    implementation = prototype._validated_union

    def observe(records, first_use):
        calls.append(True)
        return implementation(records, first_use)

    monkeypatch.setattr(prototype, "_validated_union", observe)
    actual = prototype.slot_offsets(plan, minimum_saved_items=items, minimum_saved_items_per_record=per_record)
    assert list(actual.items()) == _items(_reference, plan)
    assert bool(calls) is admitted


def test_preflight_does_not_invoke_opaque_root_or_reload_fields():
    class Opaque:
        def __getattribute__(self, name):
            raise AssertionError("preflight invoked opaque field " + name)

    roots = (Opaque(),)
    records = (_record(0, roots, Opaque()), _record(1, roots, Opaque()))
    sharing = prototype.scan_location_sharing(records)
    assert sharing.location_items == 2 and sharing.unique_location_items == 1


@pytest.mark.parametrize("packed", [False, True])
def test_plan_getters_are_read_once(packed):
    def observe(function):
        events = []

        class Scalars:
            def __len__(self):
                events.append("scalars.len")
                return 0

        class Plan:
            @property
            def packed_records(self):
                events.append("plan.packed")
                return SimpleNamespace(location_scalars=Scalars(), reload_scalars=Scalars()) if packed else None

            @property
            def records(self):
                events.append("plan.records")
                return (_record(0), _record(1))

        return _items(function, Plan()), events

    assert observe(_eligible) == observe(_reference)


@pytest.mark.parametrize("items,per_record", [(-1, 0), (0, -1), (True, 0), (0, 1.0)])
def test_policy_requires_explicit_nonnegative_ints(items, per_record):
    with pytest.raises(ValueError, match="nonnegative ordinary ints"):
        prototype.slot_offsets(None, minimum_saved_items=items, minimum_saved_items_per_record=per_record)

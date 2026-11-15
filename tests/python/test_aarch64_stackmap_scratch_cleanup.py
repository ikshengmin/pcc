"""Exception ownership for the actual target-final AArch64 stack-map writer.

The contract is compiler-contract.md's exception/ownership preservation, not
an RSS or Stage2 performance claim. Host fault injection uses real packed
plans and real CompilerIntArena list storage. The packed-branch host control
does not claim native allocation, native unwinding or bootstrap acceptance.
The exact pre-change writer below is a frozen differential oracle, including
its intentional failure-path leaks, not the implementation under test.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend import precise_stackmap as wire
from pcc.backend import self_backend_precise_stackmaps as maps
from pcc.backend.self_backend_value_arena import CompilerIntArena


_ARENA_FIELDS = (
    "scalars", "spans", "location_scalars", "location_group_spans",
    "reload_scalars", "entry_routes", "suffix_routes", "terminator_routes",
    "entry_route_spans", "suffix_route_spans", "terminator_route_spans",
)
_SIDE_FIELDS = (
    "labels", "exceptional_blocks", "location_group_keys", "block_names",
)
_CLOSE_ARENA = CompilerIntArena.close
_CLOSE_PACKED = maps.PackedPlannedSafepoints.close
_NATIVE_PROPERTY = CompilerIntArena.uses_native_storage
_PACK_RECORDS = maps._pack_stack_map_record_arena
_LEGACY_SOURCE_SHA256 = "d604ca0807f731f4a98b375a2ed061b2e8147252c2dfcf81c557fec3dc49004d"


class _PrimaryFault(RuntimeError):
    pass


class _CleanupFault(RuntimeError):
    pass


class _StopFault(BaseException):
    pass


def _symbol(name):
    return "_" + name


def _block(function, name):
    return "L_" + function + "_" + name


def _fixture():
    plans, offsets = [], {}
    for name, function_id, start in (("alpha", 10, 0), ("beta", 20, 64)):
        # Different objects with equal contents exercise global interning.
        roots = (maps.PlannedRootLocation(-16, True),
                 maps.PlannedRootLocation(-24, False))
        records = maps.PackedPlannedSafepoints(block_names=["entry", "rescue"])
        base = function_id * 10
        records.append(base + 3, _block(name, "call"), wire.SAFEPOINT_CALL,
                       roots, 0, "", 0, (maps.PlannedManagedReload(-16, -32),))
        records.append(base + 1, _block(name, "entry"), wire.SAFEPOINT_ENTRY,
                       roots, 0, "", 0, ())
        records.append(base + 2, _block(name, "loop"), wire.SAFEPOINT_LOOP,
                       (), 0, "", 0, ())
        records.append(base + 4, _block(name, "exception"), wire.SAFEPOINT_EXCEPTION,
                       roots, wire.RECORD_HAS_EXCEPTION_EDGE, "rescue", 0, ())
        records.append(base + 5, _block(name, "continuation"), wire.SAFEPOINT_CONTINUATION,
                       roots, wire.RECORD_SUSPENDED, "", 17, ())
        records.add_entry_route(0, 1)
        records.add_suffix_route(0, 0, 0)
        records.add_terminator_route(0, 3, False)
        records.finish_build()
        plans.append(maps.FunctionStackMapPlan(
            name, function_id, 64, _block(name, "end"), (), (), (), (),
            "aarch64-darwin", records,
        ))
        offsets[_symbol(name)] = start
        for label, pc in (("entry", 0), ("loop", 8), ("call", 12),
                          ("exception", 16), ("continuation", 20),
                          ("rescue", 24), ("end", 32)):
            offsets[_block(name, label)] = start + pc
    return tuple(reversed(plans)), offsets


def _snapshot(plans):
    """Observe caller storage without materializing diagnostic record objects."""
    states = []
    for plan in plans:
        packed = plan.packed_records
        if packed is None:
            states.append((plan.function_name, None))
            continue
        states.append((
            plan.function_name,
            tuple((field, getattr(packed, field)._closed,
                   len(getattr(packed, field)),
                   tuple(getattr(packed, field)._values)) for field in _ARENA_FIELDS),
            tuple((field, tuple(getattr(packed, field))) for field in _SIDE_FIELDS),
            dict(packed.location_identity_index),
            dict(packed.location_content_index),
            packed.diagnostic_projections,
        ))
    return tuple(states)


def _identities(plans):
    return tuple(
        (id(plan), id(plan.packed_records),
         tuple(id(getattr(plan.packed_records, field)) for field in
               _ARENA_FIELDS + _SIDE_FIELDS))
        for plan in plans if plan.packed_records is not None
    )


def _configure(plans, offsets, scenario):
    """Corrupt the real finite fixture at the boundary being tested."""
    beta, alpha = plans
    packed = alpha.packed_records
    if scenario == "empty":
        return (), offsets
    if scenario == "missing-start":
        offsets.pop(_symbol("alpha"))
    elif scenario == "missing-end":
        offsets.pop(alpha.end_label)
    elif scenario == "inverted-range":
        offsets[alpha.end_label] = offsets[_symbol("alpha")]
    elif scenario == "missing-safepoint":
        offsets.pop(_block("alpha", "call"))
    elif scenario == "missing-exception":
        offsets.pop(_block("alpha", "rescue"))
    elif scenario == "second-plan-label":
        offsets.pop(_block("beta", "call"))
    elif scenario == "duplicate-function":
        beta = replace(beta, function_id=alpha.function_id)
    elif scenario == "duplicate-id":
        packed.scalars.set_unchecked(0, packed.scalar(1).first)
    elif scenario == "zero-id":
        packed.scalars.set_unchecked(0, 0)
    elif scenario == "duplicate-pc":
        offsets[_block("alpha", "call")] = offsets[_block("alpha", "loop")]
    elif scenario == "outside-pc":
        offsets[_block("alpha", "call")] = offsets[alpha.end_label]
    elif scenario == "negative-frame":
        alpha = replace(alpha, frame_size=-16)
    elif scenario == "unaligned-frame":
        alpha = replace(alpha, frame_size=24)
    elif scenario == "missing-packed":
        alpha = replace(alpha, packed_records=None)
    elif scenario == "exception-flag":
        packed.scalars.set_unchecked(3 * 4 + 2, 0)
    elif scenario == "continuation-id":
        packed.scalars.set_unchecked(4 * 4 + 3, 0)
    elif scenario == "suspended-flag":
        packed.scalars.set_unchecked(4 * 4 + 2, 0)
    elif scenario == "positive-location":
        packed.location_scalars.set_unchecked(0, 8)
    elif scenario == "outside-location":
        packed.location_scalars.set_unchecked(0, -72)
    elif scenario == "unaligned-location":
        packed.location_scalars.set_unchecked(0, -17)
    elif scenario == "lazy-key-then-fail":
        packed.location_group_keys.clear()
        # Entry materializes the lazy key; a later record fails validation.
        packed.scalars.set_unchecked(0, 0)
    elif scenario == "no-records":
        # Keep a genuinely packed, empty plan, not a fake records interface.
        alpha = replace(alpha, packed_records=maps.PackedPlannedSafepoints())
        return (alpha,), offsets
    return (beta, alpha), offsets


class _TrackScratch:
    def __init__(self, patch, plans, *, packed_path, primary,
                 constructor_failure=None, method_failure=None,
                 close_failures=(), cleanup_type=_CleanupFault):
        self.arenas = []
        self.roles = {}
        self.events = []
        self.constructor_attempts = 0
        self.method_calls = 0
        self.pack_calls = 0
        self.packed_path = packed_path
        self.close_failures = set(close_failures)
        self.cleanup_errors = {}
        self.primary = primary
        self.plans = {id(plan.packed_records): plan.function_name for plan in plans}

        def allocate(*args, **kwargs):
            ordinal = self.constructor_attempts
            self.constructor_attempts += 1
            if ordinal == constructor_failure:
                raise primary
            arena = CompilerIntArena(*args, **kwargs)
            role = ("global_locations" if ordinal == 0 else
                    "packed_record_scalars" if ordinal == 1 else
                    "final_records:" + str(ordinal - 2))
            self.arenas.append(arena)
            self.roles[id(arena)] = role
            return arena

        def close(arena):
            role = self.roles.get(id(arena))
            if role is not None:
                self.events.append(("close", role, arena._closed))
                if role in self.close_failures:
                    error = self.cleanup_errors.setdefault(role, cleanup_type(role))
                    raise error
            _CLOSE_ARENA(arena)

        def close_packed(packed):
            name = self.plans.get(id(packed))
            self.events.append(("caller-close", name))
            _CLOSE_PACKED(packed)

        def native_storage(arena):
            return (self.packed_path and self.roles.get(id(arena)) == "packed_record_scalars"
                    or _NATIVE_PROPERTY.fget(arena))

        def pack(arena):
            self.pack_calls += 1
            # Select the writer's packed-record branch, then use the actual
            # packer's host oracle on real scalar storage. No raw-pointer claim.
            previous = self.packed_path
            self.packed_path = False
            try:
                return _PACK_RECORDS(arena)
            finally:
                self.packed_path = previous

        patch.setattr(maps, "CompilerIntArena", allocate)
        patch.setattr(CompilerIntArena, "close", close)
        patch.setattr(CompilerIntArena, "uses_native_storage", property(native_storage))
        patch.setattr(maps.PackedPlannedSafepoints, "close", close_packed)
        patch.setattr(maps, "_pack_stack_map_record_arena", pack)
        if method_failure is not None:
            wanted_role, method_name, wanted_call = method_failure
            original = getattr(CompilerIntArena, method_name)

            def operation(arena, *args, **kwargs):
                if self.roles.get(id(arena)) == wanted_role:
                    self.method_calls += 1
                    if self.method_calls == wanted_call:
                        raise primary
                return original(arena, *args, **kwargs)

            patch.setattr(CompilerIntArena, method_name, operation)

    def states(self):
        return tuple((self.roles[id(arena)], arena._closed, len(arena))
                     for arena in self.arenas)


class _FailingCodec:
    def __init__(self, codec, error):
        self.size = codec.size
        self.error = error

    def pack(self, *args):
        raise self.error


def _run(monkeypatch, *, legacy=False, scenario="ok", packed_path=False,
         primary=None, stage=None, constructor_failure=None,
         method_failure=None, close_failures=(), cleanup_type=_CleanupFault):
    primary = _PrimaryFault("injected primary") if primary is None else primary
    allocated_plans, offsets = _fixture()
    plans, offsets = _configure(allocated_plans, offsets, scenario)
    before, identities = _snapshot(plans), _identities(plans)
    track = None
    try:
        with monkeypatch.context() as patch:
            track = _TrackScratch(
                patch, plans, packed_path=packed_path, primary=primary,
                constructor_failure=constructor_failure, method_failure=method_failure,
                close_failures=close_failures, cleanup_type=cleanup_type,
            )

            def fail(*args, **kwargs):
                raise primary

            symbol, block = _symbol, _block
            if scenario == "invalid-symbol":
                symbol = lambda name: "bad\0symbol"
            if stage == "function-symbol":
                symbol = fail
            elif stage == "block-label":
                block = fail
            elif stage in ("_sort_final_stack_map_records", "_packed_record_is_rootless",
                           "_validate_planned_location_offset", "_pack_stack_map_record_arena",
                           "Section", "Relocation"):
                patch.setattr(maps, stage, fail)
            elif stage in ("_STACK_MAP_FUNCTION_CODEC", "_STACK_MAP_RECORD_CODEC",
                           "_STACK_MAP_HEADER_CODEC", "_STACK_MAP_LOCATION_CODEC"):
                patch.setattr(maps, stage, _FailingCodec(getattr(maps, stage), primary))
            elif stage in ("caller-close-first", "caller-close-second"):
                original_close = maps.PackedPlannedSafepoints.close
                target = "alpha" if stage == "caller-close-first" else "beta"

                def fail_caller(packed):
                    if track.plans[id(packed)] == target:
                        track.events.append(("caller-close", target))
                        raise primary
                    original_close(packed)

                patch.setattr(maps.PackedPlannedSafepoints, "close", fail_caller)
            elif stage == "caller-close-partial":
                original_close = CompilerIntArena.close
                beta_spans = next(plan.packed_records.spans for plan in plans
                                  if plan.function_name == "beta")

                def fail_caller_arena(arena):
                    if arena is beta_spans:
                        raise primary
                    original_close(arena)

                patch.setattr(CompilerIntArena, "close", fail_caller_arena)

            writer = maps.build_aarch64_stack_map_section
            if legacy:
                namespace = dict(vars(maps))
                exec(_LEGACY_WRITER_SOURCE, namespace)
                writer = namespace["build_aarch64_stack_map_section"]
            result, error = None, None
            try:
                result = writer([], plans, function_symbol=symbol, block_label=block,
                                target_offsets=offsets)
            except BaseException as caught:
                error = caught
            return {
                "result": result, "error": error, "before": before,
                "after": _snapshot(plans), "same-identities": identities == _identities(plans),
                "events": tuple(track.events), "arenas": track.states(),
                "constructors": track.constructor_attempts, "pack-calls": track.pack_calls,
                "cleanup-errors": dict(track.cleanup_errors),
            }
    finally:
        # The legacy control deliberately leaks on failure. Test-owned cleanup
        # happens only after observations and outside every injected wrapper.
        if track is not None:
            for arena in track.arenas:
                _CLOSE_ARENA(arena)
        for plan in allocated_plans + plans:
            if plan.packed_records is not None:
                _CLOSE_PACKED(plan.packed_records)


def _assert_owned_releases(outcome, *, failed=()):
    attempts = [event[1] for event in outcome["events"] if event[0] == "close"]
    roles = [role for role, _closed, _length in outcome["arenas"]]
    assert sorted(attempts) == sorted(roles), "each acquired scratch owner gets one close attempt"
    assert all(event[2] is False for event in outcome["events"] if event[0] == "close")
    for role, closed, length in outcome["arenas"]:
        assert closed is (role not in failed)
        if closed:
            assert length == 0


def _assert_legacy_failure_state(actual, expected):
    assert actual["result"] is expected["result"] is None
    assert type(actual["error"]) is type(expected["error"])
    assert str(actual["error"]) == str(expected["error"])
    assert actual["after"] == expected["after"]
    assert actual["same-identities"] and expected["same-identities"]
    assert [event for event in actual["events"] if event[0] == "caller-close"] == [
        event for event in expected["events"] if event[0] == "caller-close"
    ]


@pytest.mark.parametrize("packed_path", (False, True), ids=("direct-host", "packed-branch-host"))
@pytest.mark.parametrize("scenario", ("ok", "empty", "no-records"))
def test_success_matches_exact_legacy_bytes_relocations_and_close_order(monkeypatch, packed_path, scenario):
    expected = _run(monkeypatch, legacy=True, packed_path=packed_path, scenario=scenario)
    actual = _run(monkeypatch, packed_path=packed_path, scenario=scenario)
    assert actual["error"] is expected["error"] is None
    assert actual["result"] == expected["result"]
    assert actual["events"] == expected["events"]
    assert actual["after"] == expected["after"]
    assert actual["same-identities"]
    _assert_owned_releases(actual)
    if scenario == "ok":
        assert actual["events"] == (
            ("close", "final_records:0", False), ("close", "final_records:1", False),
            ("close", "global_locations", False), ("close", "packed_record_scalars", False),
            ("caller-close", "alpha"), ("caller-close", "beta"),
        )
        assert actual["pack-calls"] == (2 if packed_path else 0)
        decoded = wire.decode_stack_map(actual["result"].data, expected_arch=wire.ARCH_AARCH64)
        assert len(decoded.functions) == 2
        assert all(len(function.records) == 4 for function in decoded.functions)
        assert all(record.locations for function in decoded.functions for record in function.records)


_VALIDATION_CASES = (
    "missing-start", "missing-end", "inverted-range", "missing-safepoint",
    "missing-exception", "second-plan-label", "duplicate-function", "duplicate-id",
    "zero-id", "duplicate-pc", "outside-pc", "negative-frame", "unaligned-frame",
    "missing-packed", "invalid-symbol", "exception-flag", "continuation-id",
    "suspended-flag", "positive-location", "outside-location", "unaligned-location",
    "lazy-key-then-fail",
)


@pytest.mark.parametrize("packed_path", (False, True), ids=("direct-host", "packed-branch-host"))
@pytest.mark.parametrize("scenario", _VALIDATION_CASES)
def test_real_validation_failure_releases_only_private_scratch(monkeypatch, packed_path, scenario):
    expected = _run(monkeypatch, legacy=True, packed_path=packed_path, scenario=scenario)
    actual = _run(monkeypatch, packed_path=packed_path, scenario=scenario)
    assert isinstance(actual["error"], BackendUnavailable)
    _assert_legacy_failure_state(actual, expected)
    _assert_owned_releases(actual)
    assert not any(event[0] == "caller-close" for event in actual["events"])
    if scenario != "lazy-key-then-fail":
        assert actual["before"] == actual["after"]
    else:
        assert actual["before"] != actual["after"], "the oracle must exercise lazy key mutation"


@pytest.mark.parametrize("constructor_failure", (0, 1, 2, 3))
def test_constructor_failure_cleans_previously_acquired_owners(monkeypatch, constructor_failure):
    primary = MemoryError("constructor allocation failed")
    kwargs = dict(constructor_failure=constructor_failure, primary=primary)
    expected = _run(monkeypatch, legacy=True, **kwargs)
    actual = _run(monkeypatch, **kwargs)
    _assert_legacy_failure_state(actual, expected)
    assert actual["error"] is primary
    assert actual["constructors"] == constructor_failure + 1
    assert len(actual["arenas"]) == constructor_failure
    _assert_owned_releases(actual)
    assert actual["before"] == actual["after"]


@pytest.mark.parametrize("method_failure,packed_path", (
    (("final_records:0", "append4", 2), False),
    (("final_records:1", "append4", 2), False),
    (("global_locations", "append2", 2), False),
    (("packed_record_scalars", "append4", 2), True),
    (("packed_record_scalars", "append2", 1), True),
    (("packed_record_scalars", "clear", 1), True),
))
def test_loop_and_packed_record_failure_preserves_primary(monkeypatch, method_failure, packed_path):
    primary = _PrimaryFault("loop failed")
    kwargs = dict(method_failure=method_failure, packed_path=packed_path, primary=primary)
    expected = _run(monkeypatch, legacy=True, **kwargs)
    actual = _run(monkeypatch, **kwargs)
    _assert_legacy_failure_state(actual, expected)
    assert actual["error"] is primary
    _assert_owned_releases(actual)
    assert actual["before"] == actual["after"]


@pytest.mark.parametrize("stage,packed_path", (
    ("function-symbol", False), ("block-label", False),
    ("_sort_final_stack_map_records", False), ("_packed_record_is_rootless", False),
    ("_validate_planned_location_offset", False), ("Relocation", False),
    ("_STACK_MAP_FUNCTION_CODEC", False), ("_STACK_MAP_RECORD_CODEC", False),
    ("_STACK_MAP_HEADER_CODEC", False), ("_STACK_MAP_LOCATION_CODEC", False),
    ("_pack_stack_map_record_arena", True), ("Section", False),
    ("caller-close-first", False), ("caller-close-second", False),
    ("caller-close-partial", False),
))
def test_callback_pack_and_publication_failure_match_legacy_caller_state(monkeypatch, stage, packed_path):
    primary = _PrimaryFault(stage)
    kwargs = dict(stage=stage, packed_path=packed_path, primary=primary)
    expected = _run(monkeypatch, legacy=True, **kwargs)
    actual = _run(monkeypatch, **kwargs)
    _assert_legacy_failure_state(actual, expected)
    assert actual["error"] is primary
    _assert_owned_releases(actual)
    if stage not in ("Section", "caller-close-first", "caller-close-second", "caller-close-partial"):
        assert actual["before"] == actual["after"]
    if stage == "Section":
        # The existing successful caller-close sites precede construction.
        # An exceptional Section must not reopen or re-close caller plans.
        assert actual["events"] == expected["events"]


@pytest.mark.parametrize("role", ("final_records:0", "final_records:1",
                                   "global_locations", "packed_record_scalars"))
@pytest.mark.parametrize("cleanup_type", (_CleanupFault, _StopFault))
def test_normal_close_failure_is_primary_and_not_retried(monkeypatch, role, cleanup_type):
    kwargs = dict(close_failures=(role,), cleanup_type=cleanup_type)
    expected = _run(monkeypatch, legacy=True, **kwargs)
    actual = _run(monkeypatch, **kwargs)
    _assert_legacy_failure_state(actual, expected)
    assert actual["error"] is actual["cleanup-errors"][role]
    _assert_owned_releases(actual, failed=(role,))
    assert actual["before"] == actual["after"]
    assert not any(event[0] == "caller-close" for event in actual["events"])


@pytest.mark.parametrize("primary_type", (_PrimaryFault, _StopFault, KeyboardInterrupt, SystemExit))
@pytest.mark.parametrize("cleanup_type", (_CleanupFault, _StopFault))
@pytest.mark.parametrize("failed", (
    ("final_records:0",), ("global_locations",), ("packed_record_scalars",),
    ("final_records:0", "global_locations", "packed_record_scalars"),
))
def test_cleanup_errors_preserve_first_exception_and_attempt_other_releases(
    monkeypatch, primary_type, cleanup_type, failed,
):
    primary = primary_type("first failure")
    roles = ("final_records:0", "global_locations", "packed_record_scalars")
    actual = _run(
        monkeypatch, stage="_sort_final_stack_map_records", primary=primary,
        close_failures=failed, cleanup_type=cleanup_type,
    )
    assert actual["error"] is primary
    assert actual["events"] == tuple(("close", role, False) for role in roles)
    assert set(actual["cleanup-errors"]) == set(failed)
    _assert_owned_releases(actual, failed=failed)
    assert actual["before"] == actual["after"]


def test_completed_plan_scratch_is_not_reclosed_during_later_failure(monkeypatch):
    actual = _run(monkeypatch, scenario="second-plan-label")
    assert isinstance(actual["error"], BackendUnavailable)
    assert actual["events"] == (
        ("close", "final_records:0", False), ("close", "final_records:1", False),
        ("close", "global_locations", False), ("close", "packed_record_scalars", False),
    )
    _assert_owned_releases(actual)
    assert actual["before"] == actual["after"]


def test_frozen_oracle_is_exact_prechange_function_text():
    assert hashlib.sha256(_LEGACY_WRITER_SOURCE.encode()).hexdigest() == _LEGACY_SOURCE_SHA256


# Exact function text from ikshengmin/pcc commit
# 7fc7c694d77652de66e0c9a5aaa70eb920b27916, source blob
# bf1f7255b8e53147996104ea00e7c55a080968c9, lines 6508-6773.
# Keep this frozen: changing production must never silently update the oracle.
_LEGACY_WRITER_SOURCE = r'''def build_aarch64_stack_map_section(
    lines: list[str],
    plans: tuple[FunctionStackMapPlan, ...],
    *,
    function_symbol,
    block_label,
    target_offsets: dict[str, int] | None = None,
) -> Section:
    """Build the final Mach-O stack-map section without text projection.

    The public ABI codecs in ``precise_stackmap`` remain the byte-layout
    authority.  Packed plans stay in compiler-native scalar arenas; the only
    per-record object produced here is its final immutable byte chunk.
    """

    offsets = (
        _aarch64_text_label_offsets(lines)
        if target_offsets is None else target_offsets
    )
    ordered_plans = sorted(plans, key=lambda item: item.function_id)
    chunks: list[bytes] = [b""]
    relocations: list[Relocation] = []
    global_locations = CompilerIntArena()
    global_location_content_index: dict[str, int] = {}
    payload_size = _STACK_MAP_HEADER_CODEC.size
    previous_function_id = -1
    seen_safepoints: set[int] = set()
    packed_record_scalars = CompilerIntArena()
    packed_record_path = packed_record_scalars.uses_native_storage

    for plan in ordered_plans:
        if plan.function_id <= previous_function_id:
            raise BackendUnavailable(
                "stack-map functions need unique ordered stable ids"
            )
        previous_function_id = plan.function_id
        symbol = function_symbol(plan.function_name)
        if not symbol or "\0" in symbol:
            raise BackendUnavailable("stack-map function symbol is invalid")
        start = offsets.get(symbol)
        end = offsets.get(plan.end_label)
        if start is None or end is None or end <= start:
            raise BackendUnavailable(
                f"target-final stack-map range missing for {plan.function_name!r}"
            )
        code_size = end - start
        if plan.frame_size < 0 or plan.frame_size % 16:
            raise BackendUnavailable(
                f"stack-map frame size is invalid for {plan.function_name!r}"
            )
        maybe_packed_records = plan.packed_records
        if maybe_packed_records is None:
            raise BackendUnavailable(
                "AArch64 stack-map plan is missing packed records"
            )
        packed_records: PackedPlannedSafepoints = maybe_packed_records
        final_records = CompilerIntArena(max(1, len(packed_records) * 4))
        record_index = 0
        while record_index < len(packed_records):
            scalar: CompilerInt4 = packed_records.scalar(record_index)
            label = packed_records.label(record_index)
            pc = offsets.get(label)
            if pc is None:
                raise BackendUnavailable(
                    f"target-final safepoint label missing: {label!r}"
                )
            exceptional_offset = NO_OFFSET
            exceptional_block = packed_records.exceptional_block(record_index)
            if exceptional_block:
                exceptional_pc = offsets.get(
                    block_label(plan.function_name, exceptional_block)
                )
                if exceptional_pc is None:
                    raise BackendUnavailable(
                        "target-final exception successor label missing for "
                        f"{plan.function_name!r}/{exceptional_block!r}"
                    )
                exceptional_offset = exceptional_pc - start
            final_records.append4(
                pc - start,
                scalar.first,
                record_index,
                exceptional_offset,
            )
            record_index += 1
        _sort_final_stack_map_records(final_records)

        relocations.append(Relocation(
            payload_size + 8,
            symbol,
            _macho_spec.ARM64_RELOC_UNSIGNED,
            pcrel=False,
            length=3,
        ))
        # Every record is validated below; rootless ones are not written.
        kept_records = 0
        kept_scan = 0
        while kept_scan < len(final_records) // 4:
            if not _packed_record_is_rootless(
                packed_records, final_records.get_unchecked(kept_scan * 4 + 2)
            ):
                kept_records += 1
            kept_scan += 1
        function_chunk = _STACK_MAP_FUNCTION_CODEC.pack(
            plan.function_id,
            0,
            code_size,
            plan.frame_size,
            kept_records,
            0,
        )
        chunks.append(function_chunk)
        payload_size += len(function_chunk)

        local_location_indices: dict[int, int] = {}
        previous_pc = -1
        final_record_index = 0
        while final_record_index < len(final_records) // 4:
            final_offset = final_record_index * 4
            instruction_offset = final_records.get_unchecked(final_offset)
            record_id = final_records.get_unchecked(final_offset + 1)
            record_index = final_records.get_unchecked(final_offset + 2)
            exceptional_offset = final_records.get_unchecked(final_offset + 3)
            scalar: CompilerInt4 = packed_records.scalar(record_index)
            span: CompilerInt4 = packed_records.span(record_index)
            record_kind = scalar.second
            record_flags = scalar.third
            continuation_id = scalar.fourth
            if record_id <= 0 or record_id in seen_safepoints:
                raise BackendUnavailable(
                    "stack-map safepoint id is invalid or duplicate"
                )
            seen_safepoints.add(record_id)
            if not previous_pc < instruction_offset < code_size:
                raise BackendUnavailable(
                    "stack-map safepoints are not ordered inside the function"
                )
            previous_pc = instruction_offset
            has_exception = exceptional_offset != NO_OFFSET
            if has_exception != bool(record_flags & RECORD_HAS_EXCEPTION_EDGE):
                raise BackendUnavailable(
                    "stack-map exception-edge flag and offset disagree"
                )
            is_continuation = record_kind == SAFEPOINT_CONTINUATION
            if is_continuation != bool(continuation_id):
                raise BackendUnavailable(
                    "stack-map continuation needs one non-zero continuation id"
                )
            if bool(record_flags & RECORD_SUSPENDED) != is_continuation:
                raise BackendUnavailable(
                    "stack-map suspended flag is reserved for continuations"
                )
            location_group_id = span.first
            location_group: CompilerInt2 = packed_records.location_group(
                record_index
            )
            if location_group_id in local_location_indices:
                location_index = local_location_indices[location_group_id]
            else:
                location_offset = 0
                while location_offset < location_group.second:
                    location: CompilerInt2 = packed_records.location_scalar(
                        location_group.first + location_offset
                    )
                    _validate_planned_location_offset(
                        location.first,
                        frame_size=plan.frame_size,
                    )
                    location_offset += 1
                content_key = packed_records.location_group_key(
                    location_group_id
                )
                if content_key in global_location_content_index:
                    location_index = global_location_content_index[content_key]
                else:
                    location_index = len(global_locations) // 2
                    global_location_content_index[content_key] = location_index
                    location_offset = 0
                    while location_offset < location_group.second:
                        global_location: CompilerInt2 = (
                            packed_records.location_scalar(
                                location_group.first + location_offset
                            )
                        )
                        global_locations.append2(
                            global_location.first,
                            global_location.second,
                        )
                        location_offset += 1
                local_location_indices[location_group_id] = location_index
            if not _packed_record_is_rootless(packed_records, record_index):
                if packed_record_path:
                    packed_record_scalars.append4(
                        record_id,
                        instruction_offset,
                        exceptional_offset,
                        continuation_id,
                    )
                    packed_record_scalars.append4(
                        location_group.second,
                        0,
                        record_kind,
                        record_flags,
                    )
                    packed_record_scalars.append2(0, location_index)
                else:
                    chunks.append(_STACK_MAP_RECORD_CODEC.pack(
                        record_id,
                        instruction_offset,
                        exceptional_offset,
                        continuation_id,
                        location_group.second,
                        0,
                        record_kind,
                        record_flags,
                        0,
                        location_index,
                    ))
                payload_size += _STACK_MAP_RECORD_CODEC.size
            final_record_index += 1
        if packed_record_path and len(packed_record_scalars):
            chunks.append(_pack_stack_map_record_arena(packed_record_scalars))
            packed_record_scalars.clear()
        final_records.close()

    location_count = len(global_locations) // 2
    chunks[0] = _STACK_MAP_HEADER_CODEC.pack(
        MAGIC,
        VERSION,
        ARCH_AARCH64,
        POINTER_SIZE,
        len(ordered_plans),
        location_count,
        0,
    )
    location_index = 0
    while location_index < location_count:
        location: CompilerInt2 = global_locations.get2_unchecked(location_index)
        flags = LOCATION_MANAGED | (
            LOCATION_OWNED if location.second != 0 else 0
        )
        chunks.append(_STACK_MAP_LOCATION_CODEC.pack(
            LOCATION_STACK_INDIRECT,
            flags,
            POINTER_SIZE,
            29,
            NO_BASE,
            location.first,
            POINTER_SIZE,
        ))
        location_index += 1
    global_locations.close()
    packed_record_scalars.close()
    for plan in ordered_plans:
        maybe_packed_records = plan.packed_records
        assert maybe_packed_records is not None
        packed_records: PackedPlannedSafepoints = maybe_packed_records
        packed_records.close()
    return Section(
        sectname="__pcc_stackmaps",
        segname="__DATA",
        data=b"".join(chunks),
        align_log2=3,
        flags=PCC_STACKMAP_SECTION_FLAGS,
        relocations=tuple(relocations),
    )
'''

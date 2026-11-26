"""Private direct-host protocol facts preserve the complete x86 stack-map plan."""
import pytest

from pcc.backend import BackendUnavailable
from pcc.backend import self_backend_precise_stackmaps as maps
from pcc.backend import self_backend_prepare as preparation
from pcc.backend import self_backend_x86_64_linux as emitter
from pcc.backend.self_backend_indexed_codec import encode_indexed_module_file, decode_indexed_module_file
from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.backend.target_objects import emit_x86_64_streamed_module
from tests.python.test_stackmap_global_index import source_text


@pytest.fixture
def direct_sidecar(tmp_path):
    path = tmp_path / "protocol.pidx"
    source = source_text(shape="derived", triple="x86_64-linux-gnu", functions=3)
    encode_indexed_module_file(str(path), parse_self_backend_module(source))
    return path


def _prepared(path):
    module = decode_indexed_module_file(str(path))
    return preparation.prepare_parsed_module_for_target(
        module, aggregate_returned_indirect=emitter._aggregate_returned_indirect,
        materialize_legacy_slots=False,
    )


def test_direct_protocol_facts_reuse_reachable_group_and_keep_active_errors(direct_sidecar):
    prepared = _prepared(direct_sidecar)
    func = prepared.functions[0]
    kernel = maps.get_indexed_function_kernel(func)
    assert not func.blocks
    facts = maps._x86_protocol_facts(func, kernel, prepared.globals_)
    assert facts == {}
    globals_by_name = {g.name: g for g in prepared.globals_}
    aliases = maps._pointer_aliases(func)
    expected = maps._block_entry_states(func, globals_by_name, aliases)
    actual = maps._block_entry_states(func, globals_by_name, aliases, facts)
    assert actual == expected
    assert facts
    assert maps._registered_stack_root_offsets(func, globals_by_name, aliases, facts) == maps._registered_stack_root_offsets(func, globals_by_name, aliases)

    enters = [(call_id, fact[1]) for call_id, fact in facts.items()
              if fact[0] and fact[1] is not None and fact[1].locations]
    leaves = [(call_id, fact[2]) for call_id, fact in facts.items()
              if not fact[0] and fact[2]]
    assert enters and leaves
    call_id, group = enters[0]
    active = {}
    assert maps._apply_frame_protocol_indexed(func, globals_by_name, aliases, active, kernel, call_id, facts)
    assert active[group.key] is group
    with pytest.raises(BackendUnavailable, match="registered twice"):
        maps._apply_frame_protocol_indexed(func, globals_by_name, aliases, active, kernel, call_id, facts)
    leave_id, _key = leaves[0]
    with pytest.raises(BackendUnavailable, match="leaves without an active enter"):
        maps._apply_frame_protocol_indexed(func, globals_by_name, aliases, {}, kernel, leave_id, facts)


def test_direct_protocol_fact_cache_exact_streamed_object(direct_sidecar, monkeypatch):
    enabled = emit_x86_64_streamed_module(decode_indexed_module_file(str(direct_sidecar))).encode_object(None)
    with monkeypatch.context() as patch:
        patch.setattr(maps, "_x86_protocol_facts", lambda *_args: None)
        disabled = emit_x86_64_streamed_module(decode_indexed_module_file(str(direct_sidecar))).encode_object(None)
    assert enabled == disabled


def test_protocol_cache_preserves_public_nonexact_helper_routes(direct_sidecar, monkeypatch):
    prepared = _prepared(direct_sidecar)
    func = prepared.functions[0]
    kernel = maps.get_indexed_function_kernel(func)
    original = maps._frame_protocol_enter_group
    monkeypatch.setattr(maps, "_frame_protocol_enter_group", lambda *args: original(*args))
    assert maps._x86_protocol_facts(func, kernel, prepared.globals_) is None



def test_missing_enter_fact_retains_original_registered_validation(direct_sidecar, monkeypatch):
    prepared = _prepared(direct_sidecar)
    func = prepared.functions[0]
    kernel = maps.get_indexed_function_kernel(func)
    globals_by_name = {g.name: g for g in prepared.globals_}
    aliases = maps._pointer_aliases(func)
    facts = maps._x86_protocol_facts(func, kernel, prepared.globals_)
    maps._block_entry_states(func, globals_by_name, aliases, facts)
    expected = maps._registered_stack_root_offsets(func, globals_by_name, aliases)
    call_id = next(call_id for call_id, fact in facts.items()
                   if fact[0] and fact[1] is not None and fact[1].locations)
    del facts[call_id]
    original = maps._root_group
    observed = []

    def checked(*args):
        observed.append(args[-1])
        return original(*args)

    monkeypatch.setattr(maps, "_root_group", checked)
    # The missing fact (which could also be an unvisited CFG call) must
    # traverse the canonical frame-map / alloca validator.
    assert maps._registered_stack_root_offsets(func, globals_by_name, aliases, facts) == expected
    assert observed


def test_protocol_cache_rejects_nonexact_global_initializer(direct_sidecar):
    from dataclasses import replace

    class Nonexact(str):
        pass

    prepared = _prepared(direct_sidecar)
    func = prepared.functions[0]
    kernel = maps.get_indexed_function_kernel(func)
    globals_ = list(prepared.globals_)
    assert maps._x86_protocol_facts(func, kernel, globals_) == {}
    globals_[0] = replace(globals_[0], initializer=Nonexact(globals_[0].initializer))
    assert maps._x86_protocol_facts(func, kernel, globals_) is None


def test_protocol_fact_owner_clears_on_saved_planner_exception(direct_sidecar, monkeypatch):
    original = maps._x86_protocol_facts
    holders = []

    def capture(*args):
        facts = original(*args)
        if facts is not None:
            holders.append(facts)
        return facts

    def fail(*args, **kwargs):
        assert holders and holders[-1]
        raise BackendUnavailable("injected reload failure")

    monkeypatch.setattr(maps, "_x86_protocol_facts", capture)
    monkeypatch.setattr(maps, "_planned_managed_reloads", fail)
    with pytest.raises(BackendUnavailable, match="injected reload failure") as saved:
        emit_x86_64_streamed_module(decode_indexed_module_file(str(direct_sidecar)))
    assert saved.value.__traceback__ is not None
    assert holders and all(not facts for facts in holders)

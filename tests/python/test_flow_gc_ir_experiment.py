"""Real source -> owned PCC IR; no runtime archive or native execution."""

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/research"))
try:
    import flow_gc_ir_experiment as experiment
finally:
    sys.path.pop(0)

pytestmark = pytest.mark.xdist_group(name="flow_gc_real_ir_experiment")


@pytest.fixture(scope="module")
def emitted():
    source = experiment.source_program(3)
    shared, shared_snapshots, _ = experiment.emit(source)
    inline, inline_snapshots, _ = experiment.emit(source, inline=True)
    shared_signature, shared_counts, _ = experiment.validate(shared, shared_snapshots)
    inline_signature, inline_counts, _ = experiment.validate(inline, inline_snapshots, inline=True)
    return (shared, shared_snapshots, shared_signature, shared_counts,
            inline, inline_snapshots, inline_signature, inline_counts)


def test_actual_source_has_nonempty_suffixes_and_both_return_kinds(emitted):
    shared, snapshots, signature, counts, *_ = emitted
    assert "ret.shared.unpin." in shared and counts["suffix_blocks"] == 6
    assert {item.rooted for item in snapshots} == {False, True}
    assert len(signature) == 2 and counts["return_paths"] == 16
    expected = ((), (0,), (0,), (1, 0), (1, 0), (2, 1, 0), (2, 1, 0), (2, 1, 0))
    for name, paths in signature.items():
        assert paths[0][1] == ()
        assert tuple(sequence for _, sequence in paths) == expected
        assert all(rooted == (name == "user_flow_experiment_probe") for rooted, _ in paths)


def test_actual_shared_and_existing_inline_production_paths_agree(emitted):
    _, _, shared_signature, _, _, _, inline_signature, _ = emitted
    assert shared_signature == inline_signature


def test_timed_unobserved_emission_is_exactly_the_validated_real_ir(emitted):
    source = experiment.source_program(3)
    for inline, expected in ((False, emitted[0]), (True, emitted[4])):
        text, observations, _ = experiment.emit(source, inline=inline, observe=False)
        assert not observations and text == expected


def test_static_savings_do_not_claim_executed_path_savings(emitted):
    _, _, _, shared, _, _, _, inline = emitted
    assert shared["cleanup_path_unpins"] == inline["cleanup_path_unpins"] == 30
    assert inline["static_calls"] - shared["static_calls"] == 48
    assert shared["blocks"] - inline["blocks"] == 6


@pytest.mark.parametrize("kind", experiment.NEGATIVE_CASES)
def test_damaged_real_ir_passes_existing_structure_but_fails_suffix_contract(emitted, kind):
    text, snapshots, *_ = emitted
    damaged = experiment.mutate(text, snapshots, kind)
    module, functions = experiment.parsed_functions(damaged)
    for blocks in functions.values():
        experiment.RootSlotContract(blocks, module.globals_).assert_frame_exits()
    with pytest.raises(AssertionError):
        experiment.check_contract(module, functions, snapshots)


def test_missing_observed_emission_cannot_pass_vacuously(emitted):
    module, functions = experiment.parsed_functions(emitted[0])
    with pytest.raises(AssertionError, match="observed production return paths"):
        experiment.check_contract(module, functions, ())

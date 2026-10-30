from dataclasses import replace
from pathlib import Path
import os
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.research.confined_jdk.closures import ActionKeyScope, Counts, count_baseline
from scripts.research.confined_jdk.run import load_dag


@pytest.fixture
def dag():
    return load_dag(Path(os.environ.get("PCC_RESEARCH_BASELINE_SOURCE", str(ROOT))))


def make_state(dag, edges):
    return dag.GraphState.create(
        compiler_digest="1" * 64, runtime_abi_digest="2" * 64,
        options_digest="3" * 64, target="host-test-only",
        modules=[dag.ModuleState.create(name, "4" * 64, children,
                    dag.PublicSummary.create(exports=[name]))
                 for name, children in edges.items()])


@pytest.mark.parametrize("eager", [False, True])
@pytest.mark.parametrize("edges", [
    {"a": (), "b": ()},
    {"a": ("b",), "b": ("a",)},
    {"a": ("b", "c"), "b": ("d",), "c": ("d",), "d": ()},
    {"a": ("a",)},
])
def test_keys_equal_including_cycles_diamonds_and_self_edges(dag, edges, eager):
    state = make_state(dag, edges)
    scope = ActionKeyScope(dag, state, eager=eager)
    for module in state.modules:
        assert scope.facts(module.name) == dag._dependency_summary_closure(state.module_map(), module)
        for stage in dag.ACTION_STAGES:
            assert scope.key(module.name, stage) == dag.action_key(state, module.name, stage)


def test_new_graph_scope_preserves_all_invalidation_inputs(dag):
    state = make_state(dag, {"a": ("b",), "b": ()})
    original = ActionKeyScope(dag, state).key("a", "module-ir")
    b = state.modules[1]
    variants = [
        replace(state, compiler_digest="5" * 64),
        replace(state, runtime_abi_digest="5" * 64),
        replace(state, options_digest="5" * 64),
        replace(state, target="different"),
        replace(state, modules=(replace(state.modules[0], source_digest="5" * 64), b)),
        replace(state, modules=(state.modules[0], replace(b, summary=dag.PublicSummary.create(exports=["changed"])))),
        replace(state, modules=(replace(state.modules[0], dependencies=()), b)),
    ]
    for changed in variants:
        key = ActionKeyScope(dag, changed).key("a", "module-ir")
        assert key == dag.action_key(changed, "a", "module-ir")
        assert key != original
    assert ActionKeyScope(dag, state).key("a", "module-ir") == original


def test_actual_baseline_counts_and_scoped_reuse(dag):
    state = make_state(dag, {"a": ("b",), "b": ()})
    before, after = Counts(), Counts()
    with count_baseline(dag, before):
        baseline = [dag.action_key(state, "a", stage) for stage in dag.ACTION_STAGES]
    scope = ActionKeyScope(dag, state, after)
    assert [scope.key("a", stage) for stage in dag.ACTION_STAGES] == baseline
    assert before.module_maps == before.closure_walks == before.dependency_visits == 5
    assert before.summary_digests == 10
    assert after.module_maps == after.closure_walks == after.dependency_visits == 1
    assert after.summary_digests == 2


def test_errors_are_not_memoized_as_valid_keys(dag):
    state = make_state(dag, {"a": ()})
    scope = ActionKeyScope(dag, state)
    with pytest.raises(KeyError):
        scope.key("absent", "module-ir")
    with pytest.raises(ValueError):
        scope.key("a", "absent")


def test_lazy_single_leaf_does_not_hash_unqueried_modules(dag):
    state = make_state(dag, {"a": (), "b": (), "c": ()})
    counts = Counts()
    scope = ActionKeyScope(dag, state, counts, eager=False)
    assert scope.key("a", "module-ir") == dag.action_key(state, "a", "module-ir")
    assert counts.summary_digests == 1
    assert set(scope.digests) == {"a"}


def test_five_stage_keys_are_actual_global_rebuild_plan_keys(dag):
    state = make_state(dag, {"a": ("b",), "b": ()})
    previous = replace(state, options_digest="a" * 64)
    plan = dag.plan_actions(previous, state)
    assert plan.full_rebuild and plan.reason == "global-identity-changed"
    scope = ActionKeyScope(dag, state, eager=False)
    assert [scope.key(action.module, action.stage) for action in plan.actions] == [action.key for action in plan.actions]

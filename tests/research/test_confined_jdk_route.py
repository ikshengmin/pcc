from pathlib import Path
import os
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.research.confined_jdk.route_probe import displayed_plan_actions, probe_route


@pytest.fixture
def source():
    return Path(os.environ.get("PCC_RESEARCH_BASELINE_SOURCE", str(ROOT)))


@pytest.mark.parametrize("direct", ["1", "true", "yes", "on", " TRUE "])
@pytest.mark.parametrize("with_timing", [False, True])
def test_direct_dispatch_never_plans_or_loads_action_cache(source, direct, with_timing):
    result = probe_route(source, direct, with_timing=with_timing)
    assert result["cache_plan_is_none"]
    assert set(result["cache_callback_calls"].values()) == {0}
    assert result["displayed_plan_actions_when_no_plan"] == 2420


def test_text_route_is_a_positive_control_for_callback_observation(source):
    result = probe_route(source, "0")
    assert not result["cache_plan_is_none"]
    assert result["cache_callback_calls"]["plan_cache"] == 1
    assert result["cache_callback_calls"]["publish_cache"] == 1
    assert result["cache_callback_calls"]["release_cache"] == 1


def test_reported_action_count_is_not_executed_key_count(source):
    assert displayed_plan_actions(source, 0) == 0
    assert displayed_plan_actions(source, 21) == 105
    assert displayed_plan_actions(source, 484) == 2420

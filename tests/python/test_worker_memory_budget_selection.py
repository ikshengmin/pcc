"""Observed automatic caps preserve explicit and external guard contracts."""
import importlib.util
from pathlib import Path

import pytest

from scripts import bootstrap
from scripts import run_pcc_stage1_build as stage1
from scripts import run_process_tree_sample as guard


GIB = 1024**3


def darwin_observation(**changes):
    observation = {
        "platform": "darwin", "reclaimable_bytes": 306 * GIB // 10,
        "disk_free_bytes": 100 * GIB, "swap_total_bytes": 8 * GIB,
        "swap_used_bytes": 6 * GIB, "swap_free_bytes": 2 * GIB,
    }
    observation.update(changes)
    return observation


def test_pressured_darwin_auto_cap_is_admitted_by_unchanged_policy():
    observation = darwin_observation()
    selected = guard.select_tree_memory_budget(0, observation=observation)
    assert selected["max_tree_rss_bytes"] == observation["reclaimable_bytes"] // 2 - 8 * GIB
    assert selected["selection_kind"] == "automatic"
    assert selected["resource_preflight"]["reserve_bytes"] == 8 * GIB
    assert selected["resource_preflight"]["swap_pressure_waived_by_reclaimable"]


@pytest.mark.parametrize("cap", [8 * GIB, 16 * GIB])
def test_explicit_darwin_cap_is_refused_without_reselection(cap):
    with pytest.raises(guard.ProcessTreeSampleError, match="swap is already pressured"):
        guard.select_tree_memory_budget(cap, observation=darwin_observation())


def test_darwin_auto_also_obeys_disk_reserve():
    selected = guard.select_tree_memory_budget(0, observation=darwin_observation(
        reclaimable_bytes=80 * GIB, disk_free_bytes=10 * GIB,
    ))
    assert selected["max_tree_rss_bytes"] == 2 * GIB


def test_darwin_auto_refuses_when_reserve_cannot_fit():
    with pytest.raises(guard.ProcessTreeSampleError, match="positive automatic"):
        guard.select_tree_memory_budget(0, observation=darwin_observation(disk_free_bytes=8 * GIB))


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_other_platforms_use_available_ram_without_darwin_disk_swap_policy(platform):
    selected = guard.select_tree_memory_budget(0, observation={
        "platform": platform, "available_bytes": 6 * GIB,
        "cgroup_limits_verified": True,
    })
    assert selected["max_tree_rss_bytes"] == 3 * GIB
    assert selected["resource_preflight"] is None


def test_external_guard_adoption_is_exact_and_conflicts_fail_before_work():
    selected = guard.select_tree_memory_budget(0, external_budget=6 * GIB)
    assert selected["max_tree_rss_bytes"] == 6 * GIB
    assert selected["selection_kind"] == "external_guard"
    assert guard.select_tree_memory_budget(6 * GIB, external_budget=6 * GIB)["max_tree_rss_bytes"] == 6 * GIB
    for cap in (4 * GIB, 8 * GIB):
        with pytest.raises(guard.ProcessTreeSampleError, match="restart under a matching guard"):
            guard.select_tree_memory_budget(cap, external_budget=6 * GIB)


def make_linux_proc(tmp_path, *, version=2, child_limit="max"):
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    mount = tmp_path / "cgroup"
    child = mount / "worker"
    child.mkdir(parents=True)
    (proc / "meminfo").write_text("MemTotal: 268435456 kB\nMemAvailable: 104857600 kB\n")
    if version == 2:
        (proc / "self/cgroup").write_text("0::/worker\n")
        (proc / "self/mountinfo").write_text(f"20 1 0:2 / {mount} rw - cgroup2 cgroup rw\n")
        (mount / "memory.max").write_text(str(8 * GIB))
        (mount / "memory.current").write_text(str(6 * GIB))
        (child / "memory.max").write_text(str(child_limit))
        (child / "memory.current").write_text(str(GIB))
    else:
        (proc / "self/cgroup").write_text("5:memory:/worker\n")
        (proc / "self/mountinfo").write_text(f"20 1 0:2 / {mount} rw - cgroup cgroup rw,memory\n")
        (mount / "memory.limit_in_bytes").write_text(str(8 * GIB))
        (mount / "memory.usage_in_bytes").write_text(str(6 * GIB))
        (child / "memory.limit_in_bytes").write_text(str(16 * GIB))
        (child / "memory.usage_in_bytes").write_text(str(GIB))
    return proc


@pytest.mark.parametrize("version", [1, 2])
def test_linux_auto_accounts_for_constrained_ancestor_not_machine_ram(tmp_path, version):
    observation = guard._linux_memory_observation(make_linux_proc(tmp_path, version=version))
    assert observation["mem_available_bytes"] == 100 * GIB
    assert observation["available_bytes"] == 2 * GIB
    assert observation["cgroup_limits_verified"]
    assert guard.select_tree_memory_budget(0, observation=observation)["max_tree_rss_bytes"] == GIB


def test_linux_unavailable_cgroup_mount_does_not_mean_unlimited(tmp_path):
    proc = make_linux_proc(tmp_path)
    (proc / "self/mountinfo").write_text("")
    observation = guard._linux_memory_observation(proc)
    assert not observation["cgroup_limits_verified"]
    with pytest.raises(guard.ProcessTreeSampleError, match="cgroup memory limits"):
        guard.select_tree_memory_budget(0, observation=observation)


def test_stage1_and_bootstrap_default_share_darwin_admission(monkeypatch):
    monkeypatch.delenv("PCC_WORKER_TREE_BUDGET_BYTES", raising=False)
    monkeypatch.setattr(guard, "_host_memory_observation", darwin_observation)
    expected = darwin_observation()["reclaimable_bytes"] // 2 - 8 * GIB
    assert stage1._host_memory_budget_bytes(0) == expected
    options = bootstrap.Options({})
    bootstrap._resolve_tree_memory_budget(options)
    assert options.max_tree_rss_bytes == expected
    assert options.memory_budget_selection["selection_kind"] == "automatic"


def test_stage2_default_is_distinct_from_explicit_eight_gib(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    spec = importlib.util.spec_from_file_location("stage2_memory_selection", root / "scripts/run_pcc_stage2_from_receipt.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    monkeypatch.delenv("PCC_WORKER_TREE_BUDGET_BYTES", raising=False)
    monkeypatch.setattr(tool.memory_guard, "_host_memory_observation", darwin_observation)
    args = tool._parser().parse_args(["--stage1-dir", "a", "--output-dir", "b"])
    assert args.max_tree_rss_bytes == 0
    selected = tool._select_stage2_memory_budget(args)
    assert selected["selection_kind"] == "automatic"
    assert selected["max_tree_rss_bytes"] == darwin_observation()["reclaimable_bytes"] // 2 - 8 * GIB


def test_external_bootstrap_requires_a_known_cap():
    options = bootstrap.Options({"PCC_BOOTSTRAP_EXTERNAL_MEMORY_GUARD": "1"})
    with pytest.raises(bootstrap.BootstrapError, match="explicit or inherited"):
        bootstrap._resolve_tree_memory_budget(options)

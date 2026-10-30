"""Finite ownership/budget contracts, never execution of a native bootstrap."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import subprocess

import pytest

from scripts import ci_bootstrap_partition as partition
from scripts import ci_pytest_markers as gate


def _owner():
    return {
        "partition": "bootstrap", "marker": "integration", "status": "PASS",
        "source": {"commit": "a" * 40, "source_sha256": "b" * 64, "clean_commit": True},
        "producer": {"run_id": 123, "run_attempt": 2, "sha": "a" * 40},
        "platform": "macos-arm64", "manifest_sha256": "c" * 64,
        "timeout_s": 1800, "guard_elapsed_s": 3.25,
    }


def _budget(row, guard=None):
    identity = _owner()
    if guard is None:
        guard = {"status": "COMPLETE", "timeout_s": 1800, "elapsed_s": 3.0, "returncode": 0}
    return partition.remaining_seconds(row, guard=guard, **{key: identity[key] for key in
        ("source", "producer", "platform", "manifest_sha256")})


@pytest.mark.parametrize("status", ["PASS", "FAIL"])
def test_owner_failure_does_not_reset_or_hide_consumed_budget(status):
    row = _owner()
    row["status"] = status
    assert _budget(row) == 1796
    # Positive finite tampering must not reset the budget either. Bind the
    # wrapper's charged interval to the independent guard's own elapsed time.
    undercount = dict(row, guard_elapsed_s=2.0)
    with pytest.raises(ValueError, match="undercounts"):
        _budget(undercount)
    guard = {"status": "COMPLETE", "timeout_s": 1800, "elapsed_s": 3.0, "returncode": 0}
    for changed in ({"timeout_s": 1799}, {"status": "RUNNING"}, {"elapsed_s": None}):
        with pytest.raises(ValueError):
            _budget(row, dict(guard, **changed))


@pytest.mark.parametrize("field", ["source", "producer", "platform", "manifest_sha256", "partition", "marker", "timeout_s", "status"])
def test_owner_identity_or_budget_tamper_fails_closed(field):
    row = _owner()
    row[field] = "tampered"
    with pytest.raises(ValueError):
        _budget(row)


@pytest.mark.parametrize("elapsed", [-1, float("nan"), float("inf"), True, "2", None])
def test_invalid_owner_elapsed_cannot_grant_a_remainder(elapsed):
    row = _owner()
    row["guard_elapsed_s"] = elapsed
    with pytest.raises(ValueError, match="elapsed"):
        _budget(row)


@pytest.mark.parametrize("elapsed", [1799.1, 1800, 1900])
def test_exhausted_budget_is_explicit_failure_not_fresh_timeout(elapsed):
    row = _owner()
    row["guard_elapsed_s"] = elapsed
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        _budget(row)


@pytest.mark.parametrize("suffix", ["", "@bootstrap_group"])
def test_disjoint_partition_retains_exact_original_inventory(suffix):
    owned = [node + suffix for node in partition.BOOTSTRAP_NODES]
    original = owned + ["tests/test_other.py::test_integration@other"]
    rest, selected, moved = partition.partition_inventory(original, original, "remainder")
    assert rest == selected == original[-1:] and moved == owned
    partition.verify_union({"partition": "bootstrap", "selected": owned},
                           {"partition": "remainder", "selected": selected,
                            "transferred": moved, "original_expected": original})


@pytest.mark.parametrize("bad", ["missing", "duplicate", "extra-owner"])
def test_original_five_owner_inventory_is_exact(bad):
    nodes = list(partition.BOOTSTRAP_NODES)
    if bad == "missing":
        nodes.pop()
    elif bad == "duplicate":
        nodes.append(nodes[0])
    else:
        nodes.append("tests/test_other.py::test_other")
    with pytest.raises(ValueError):
        partition.partition_inventory(nodes, nodes, "bootstrap")


@pytest.mark.parametrize("bad", ["overlap", "missing", "unknown", "transfer"])
def test_union_rejects_gap_duplicate_unknown_or_wrong_transfer(bad):
    nodes = list(partition.BOOTSTRAP_NODES)
    owner = {"partition": "bootstrap", "selected": nodes}
    remainder = {"partition": "remainder", "selected": ["other"],
                 "transferred": nodes, "original_expected": nodes + ["other"]}
    if bad == "overlap": remainder["selected"].append(nodes[0])
    if bad == "missing": remainder["selected"] = []
    if bad == "unknown": remainder["selected"].append("unknown")
    if bad == "transfer": remainder["transferred"] = nodes[:-1]
    with pytest.raises(ValueError):
        partition.verify_union(owner, remainder)


@pytest.mark.parametrize("platform", ["macos-arm64", "linux-aarch64", "linux-x86_64"])
def test_owner_and_remainder_preserve_original_watchdog_caps(tmp_path, platform):
    owner = gate.watchdog_command(tmp_path, "integration", platform, partition="bootstrap")
    remainder = gate.watchdog_command(tmp_path, "integration", platform, partition="remainder", timeout=123)
    assert owner[owner.index("--timeout") + 1] == "1800"
    assert remainder[remainder.index("--timeout") + 1] == "123"
    assert owner[owner.index("--") + 1:] == gate.marker_command(tmp_path, "integration", "bootstrap")
    assert owner[-5:] == list(partition.BOOTSTRAP_NODES)
    assert remainder[-1] == "tests"
    for cmd in (owner, remainder):
        assert "-n" not in cmd and "--collect-only" not in cmd and "--no-performance-lock" in cmd
        if platform == "macos-arm64":
            assert cmd[cmd.index("--auto-tree-rss-ceiling-bytes") + 1] == "4294967296"
            assert cmd[cmd.index("--min-tree-rss-bytes") + 1] == "2147483648"
            assert cmd[cmd.index("--darwin-preflight-reserve-bytes") + 1] == "536870912"
        else:
            assert cmd[cmd.index("--max-tree-rss-bytes") + 1] == "17179869184"


def test_actual_six_worker_partition_runs_five_bodies_once_then_remainder(tmp_path):
    """Actual harmless pytest bodies, not importing or running the GC fixtures."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts="-n 6 --dist=loadgroup"\n'
        'markers=["integration: finite fixture"]\n')
    for i, node in enumerate(partition.BOOTSTRAP_NODES):
        filename, name = node.split("::")
        path = corpus / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('from pathlib import Path\nimport pytest\npytestmark=pytest.mark.integration\n'
                        + 'def ' + name + '():\n    with Path("owner-' + str(i) + '").open("x") as stream: stream.write("body")\n')
    (corpus / "tests/test_other.py").write_text(
        'from pathlib import Path\nimport pytest\n@pytest.mark.integration\n'
        'def test_other(): Path("remainder-ran").write_text("actual body")\n')
    inventories = []
    for scope in ("bootstrap", "remainder"):
        out = tmp_path / scope
        out.mkdir()
        env = dict(os.environ, PYTHONPATH=str(gate.ROOT), PCC_MARKER_REPORT_DIR=str(out), PCC_MARKER_PARTITION=scope)
        env.pop("PYTEST_ADDOPTS", None)
        result = subprocess.run(gate.marker_command(out, "integration", scope), cwd=corpus,
                                env=env, capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "created: 6/6 workers" in result.stdout and "LoadGroupScheduling" in result.stdout
        assert gate.summarize(out, "integration", scope)["status"] == "PASS"
        inventories.append(json.loads((out / "inventory-gw0.json").read_text()))
    partition.verify_union(*inventories)
    assert len(list(corpus.glob("owner-*"))) == 5
    assert (corpus / "remainder-ran").read_text() == "actual body"


def test_workflow_keeps_local_owner_exclusive_and_old_postcheck_prerequisites():
    workflow = (gate.ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    nonmac, following = workflow.split("\n  other-platform-native-wheel:", 1)[1].split("\n  pcc1-package-parity:", 1)
    mac = following.split("\n  pytest-markers:", 1)[0]
    for block in (nonmac, mac):
        assert "\n    needs:" not in block
        assert block.count("id: bootstrap_owner") == 1
        assert block.index("id: stage1_export") < block.index("id: bootstrap_owner") < block.index("id: native_prior")
        assert "continue-on-error" not in block
        prior = block.split("      - name: Verify the new pcc1 on native prior worker shapes", 1)[1].split("\n      - ", 1)[0]
        assert "!cancelled()" in prior and "steps.stage1_bundle.outcome == 'success'" in prior
        assert "bootstrap_owner" not in prior
        expression = re.search(r"if: \$\{\{ (.+) \}\}", prior).group(1)
        prerequisites = re.findall(r"steps\.([a-z0-9_]+)\.outcome", expression)
        def eligible(failed):
            rendered = re.sub(r"steps\.([a-z0-9_]+)\.outcome",
                              lambda match: repr("failure" if match.group(1) == failed else "success"),
                              expression)
            rendered = rendered.replace("!cancelled()", "True").replace("&&", " and ").replace("||", " or ")
            # Only the source-owned boolean expression above, no external code.
            return eval(rendered, {"__builtins__": {}}, {})
        assert eligible("bootstrap_owner") and eligible("bootstrap_owner_upload")
        assert all(not eligible(name) for name in prerequisites)
        wheel = block.split("      - name: " + ("Build, install, and execute the native wheel" if block == mac else "Hand the Stage1 pcc1 to the wheel build"), 1)[1].split("\n      - ", 1)[0]
        assert "steps.native_prior.outcome == 'success'" in wheel
        assert "steps.bound_find.outcome == 'success'" in wheel
        assert "steps.qualification_evidence.outcome == 'success'" in wheel
    assert "!inputs.full_chain && inputs.pytest_selection != 'not integration'" in nonmac
    assert "(inputs.full_chain || inputs.pytest_selection != 'not integration')" in mac
    assert "--partition remainder --owner-report build/pytest-bootstrap-owner" in workflow
    assert "cancel-in-progress: false" in workflow and "queue: max" in workflow
    assert "timeout-minutes: 360" in nonmac and "timeout-minutes: 200" in mac


@pytest.fixture
def in_place_product(tmp_path, monkeypatch):
    # Reuse the existing tiny exported-byte fixture. It forbids subprocesses;
    # neither this test nor the fixture compiler is native execution evidence.
    from tests import test_ci_stage1_artifact_export as fixture
    from scripts import ci_stage1_bundle as bundle
    fixture.lease_env.__wrapped__(monkeypatch)
    admitted = fixture.admitted.__wrapped__(tmp_path, monkeypatch)
    manifest = fixture._export(admitted)
    return admitted, manifest, tmp_path / fixture.exporter.OUTPUT_DIRECTORY, bundle


def test_in_place_admission_preserves_original_bytes_and_never_extracts(in_place_product, monkeypatch):
    admitted, manifest, directory, bundle = in_place_product
    root = admitted[0]
    before = {name: ((root / name).read_bytes(), (root / name).stat().st_mode)
              for name in manifest["files"]}
    def forbidden(*args, **kwargs):
        raise AssertionError("in-place admission must not extract/build")
    monkeypatch.setattr(bundle.tarfile, "open", forbidden)
    assert bundle.admit_in_place(root, directory, manifest["source"]["commit"],
                                 manifest["platform"], manifest["producer"]) == manifest
    assert all(((root / name).read_bytes(), (root / name).stat().st_mode) == original
               for name, original in before.items())


@pytest.mark.parametrize("tamper", ["compiler", "runtime", "source", "run", "attempt", "platform", "path", "inventory", "stage", "header"])
def test_in_place_admission_rejects_tampered_producer_inputs(in_place_product, tamper):
    admitted, original, directory, bundle = in_place_product
    root = admitted[0]
    manifest = deepcopy(original)
    if tamper in ("compiler", "runtime"):
        (root / manifest["paths"][tamper]).write_bytes(b"changed")
    elif tamper == "source": manifest["source"]["commit"] = "d" * 40
    elif tamper == "run": manifest["producer"]["run_id"] += 1
    elif tamper == "attempt": manifest["producer"]["run_attempt"] += 1
    elif tamper == "platform": manifest["platform"] = "macos-arm64"
    elif tamper == "path": manifest["files"][manifest["paths"]["compiler"]]["source_path"] = "/different"
    elif tamper == "inventory": manifest["files"].pop(manifest["paths"]["runtime"])
    elif tamper == "stage":
        stage = root / manifest["paths"]["stage_receipt"]
        row = json.loads(stage.read_text()); row["status"] = "FAIL"; stage.write_text(json.dumps(row))
    else:
        # Keep size but break real ELF header, even if a receipt hash is edited.
        compiler = root / manifest["paths"]["compiler"]
        data = bytearray(compiler.read_bytes()); data[0] = 0; compiler.write_bytes(data)
        manifest["files"][manifest["paths"]["compiler"]]["sha256"] = bundle.digest(compiler)
        stage_path = root / manifest["paths"]["stage_receipt"]
        stage = json.loads(stage_path.read_text()); stage["compiler"]["sha256"] = bundle.digest(compiler)
        stage_path.write_text(json.dumps(stage))
        shared_path = root / manifest["paths"]["shared_receipt"]
        shared = json.loads(shared_path.read_text()); shared["stages"][0]["sha256"] = bundle.digest(compiler)
        shared_path.write_text(json.dumps(shared))
        for path in (stage_path, shared_path):
            row = manifest["files"][path.relative_to(root).as_posix()]
            row.update(size=path.stat().st_size, sha256=bundle.digest(path))
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises((ValueError, KeyError)):
        bundle.admit_in_place(root, directory, original["source"]["commit"],
                              original["platform"], original["producer"])


def test_large_remainder_filter_uses_one_set_and_keeps_exact_item_order(tmp_path, monkeypatch):
    import ast
    import inspect
    from types import SimpleNamespace
    source = ast.parse(inspect.getsource(gate.pytest_configure))
    assignments = [node for node in ast.walk(source) if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "selected_set" for target in node.targets)]
    assert len(assignments) == 1
    assert ast.dump(assignments[0].value, include_attributes=False) == ast.dump(ast.parse("set(selected)", mode="eval").body, include_attributes=False)
    tests = [node for node in ast.walk(source) if isinstance(node, ast.Compare)
             and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
             and isinstance(node.left, ast.Attribute) and node.left.attr == "nodeid"]
    assert len(tests) == 2 and all(isinstance(node.comparators[0], ast.Name)
                                  and node.comparators[0].id == "selected_set" for node in tests)
    registered, deselected, saved = [], [], []
    config = SimpleNamespace(workerinput={"workerid": "gw0"},
                             getoption=lambda name: "integration",
                             pluginmanager=SimpleNamespace(register=lambda plugin, name: registered.append(plugin)),
                             hook=SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items)))
    monkeypatch.setenv("PCC_MARKER_REPORT_DIR", str(tmp_path))
    monkeypatch.setenv("PCC_MARKER_PARTITION", "remainder")
    monkeypatch.setattr(gate, "save", lambda path, value: saved.append(value))
    class Item:
        def __init__(self, nodeid): self.nodeid = nodeid
        def get_closest_marker(self, marker): return object() if marker == "integration" else None
    owned = [Item(node) for node in partition.BOOTSTRAP_NODES]
    remaining = [Item(f"tests/test_large.py::test_{i}") for i in range(10000)]
    items = remaining[:4321] + owned + remaining[4321:]
    gate.pytest_configure(config)
    hook = registered[0].pytest_collection_modifyitems(items)
    next(hook)
    with pytest.raises(StopIteration): next(hook)
    assert items == remaining and deselected == owned
    assert saved[0]["selected"] == [item.nodeid for item in remaining]
    assert saved[0]["transferred"] == list(partition.BOOTSTRAP_NODES)

"""Read-only collection witness and exact planned-lane selector for unified CI.

Never admits an unmet pcc_gate or supplies a Rust certification token. Original
repository gates run first; their removed nodes remain explicit UNRUN records.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def serial(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (tuple, list)):
        return [serial(item) for item in value]
    if isinstance(value, dict):
        return {str(key): serial(item) for key, item in value.items()}
    return {"type": type(value).__name__}


def classify(path, function, integration, registry):
    entry = registry.get(path, {}).get(function)
    if entry and not (isinstance(entry, dict) and entry.get("conditional")):
        return "reference"
    # Conditional Rust certification stays with the actual source test. Moving
    # it to an optional oracle lane would remove a production prerequisite.
    return "integration" if integration else "ordinary"


def pytest_configure(config):
    directory = os.environ.get("PCC_VALIDATION_INVENTORY")
    if not directory:
        raise pytest.UsageError("explicit inventory directory required")
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    registry = json.loads(Path(os.environ["PCC_VALIDATION_REFERENCES"]).read_text())["functions"]
    selection_file = os.environ.get("PCC_VALIDATION_SELECTION")
    allowed = set(json.loads(Path(selection_file).read_text())) if selection_file else None
    collecting = config.getoption("collectonly")
    local_schedule = {"workers": config.getoption("numprocesses"), "dist": config.getoption("dist")}
    if hasattr(config, "workerinput"):
        schedule = config.workerinput.get("pcc_validation_schedule")
    else:
        schedule = local_schedule
    if not collecting and schedule != {"workers": 6, "dist": "loadgroup"}:
        raise pytest.UsageError("execution requires original six controller workers and loadgroup")
    if config.getoption("markexpr") or config.getoption("keyword"):
        raise pytest.UsageError("lane partition owns the complete unfiltered marker union")

    class Inventory:
        @pytest.hookimpl(optionalhook=True)
        def pytest_configure_node(self, node):
            # xdist workers deliberately run local -n0/dist=no. The scheduling
            # owner is the controller; transmit its checked policy explicitly.
            node.workerinput["pcc_validation_schedule"] = schedule

        @pytest.hookimpl(wrapper=True, tryfirst=True)
        def pytest_collection_modifyitems(self, items):
            before = []
            for item in items:
                path = item.path.relative_to(config.rootpath).as_posix()
                function = getattr(item, "originalname", None) or item.name
                entry = registry.get(path, {}).get(function)
                before.append((item, {
                    "canonical_nodeid": item.nodeid, "file": path, "function": function,
                    "lane": classify(path, function, bool(item.get_closest_marker("integration")), registry),
                    "reference_activity": entry or "reference-classification-unknown; retained in original marker lane",
                    "groups": sorted(str(mark.kwargs.get("name", mark.args[0] if mark.args else ""))
                                     for mark in item.iter_markers("xdist_group")),
                    "gates": [serial(mark.kwargs) for mark in item.iter_markers("pcc_gate")],
                }))
            result = yield
            selected_ids = {id(item) for item in items}
            rows = []
            for item, row in before:
                row["nodeid"] = item.nodeid  # final xdist @group spelling
                row["selected_by_repository"] = id(item) in selected_ids
                rows.append(row)
            if allowed is not None:
                selected = {row["canonical_nodeid"] for row in rows if row["selected_by_repository"]}
                missing = allowed - selected
                if missing:
                    raise pytest.UsageError("planned nodes disappeared or prerequisites changed: " + repr(sorted(missing)))
                moved = [item for item, row in before if id(item) in selected_ids and row["canonical_nodeid"] not in allowed]
                config.hook.pytest_deselected(items=moved)
                retained = {id(item) for item, row in before if row["canonical_nodeid"] in allowed}
                items[:] = [item for item in items if id(item) in retained]
            worker = getattr(config, "workerinput", {}).get("workerid", "controller")
            value = {"rows": rows, "selected": [item.nodeid for item in items],
                     "collect_only": collecting, **schedule,
                     "local_schedule": local_schedule, "marker": config.getoption("markexpr")}
            (out / (worker + ".json")).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
            return result

    config.pluginmanager.register(Inventory(), "pcc-unified-validation-inventory")

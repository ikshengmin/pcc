"""Execute the real action-cache dispatch without compiling or spawning workers.

This is a branch proof, not a Stage1 replay or a full-graph timing benchmark.
It also evaluates the real counter expression: the displayed action-plan count
can be a fallback estimate when no action plan (and no action keys) exists.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
from types import SimpleNamespace
import zipfile


OWNER = "pcc/frontends/python/pipeline_frontend_parallel.py"


def source_function(source_root, name, environment):
    path = source_root / OWNER
    tree = ast.parse(path.read_text())
    matches = [node for node in tree.body
               if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(matches) != 1:
        raise ValueError("source function not uniquely found: " + name)
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        matches[0],
    ], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), environment)
    return environment[name]


def displayed_plan_actions(source_root, module_count):
    tree = ast.parse((source_root / OWNER).read_text())
    matches = [node for node in ast.walk(tree)
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == "profile_counter" and len(node.args) == 3
               and isinstance(node.args[1], ast.Constant)
               and node.args[1].value == "multi_frontend_action_plan_actions"
               and isinstance(node.args[2], ast.IfExp)]
    if len(matches) != 1:
        raise ValueError("counter fallback expression changed")
    expression = ast.Expression(matches[0].args[2])
    return eval(compile(expression, str(source_root / OWNER), "eval"),
                {"action_plan": None, "src_paths": [None] * module_count})


def probe_route(source_root, direct_value="1", module_count=484, with_timing=False):
    calls = {}
    received = []
    sentinel = object()
    plan = {"action_root": "unopened-test-only-path"}

    def callback(name, result=None):
        def invoke(*args, **kwargs):
            calls[name] = calls.get(name, 0) + 1
            return result
        return invoke

    def compile_uncached(*args, **kwargs):
        # Deliberately stop at the compile boundary. No frontend, object,
        # linker, runtime, subprocess, or native diagnostics are executed.
        received.append(kwargs)
        return sentinel

    environment = {"os": SimpleNamespace(environ={"PCC_DIRECT_INDEXED_KERNEL_EMIT": direct_value})}
    compile_parallel = source_function(source_root, "compile_parallel", environment)
    result = compile_parallel(
        ["input.py"] * module_count, ["input"] * module_count,
        jobs=6, entry_module="input", sibling_inits=[], libpython_mode="off",
        ir_scaffold_mode="on", verbose=False,
        coordinator_timing=(object() if with_timing else None),
        can_spawn_worker=callback("can_spawn_worker", True),
        worker_command_prefix=callback("worker_command_prefix", ["unexecuted-python"]),
        compile_uncached=compile_uncached, plan_cache=callback("plan_cache", plan),
        load_cache=callback("load_cache"), acquire_cache=callback("acquire_cache", True),
        wait_cache=callback("wait_cache"), publish_cache=callback("publish_cache", True),
        release_cache=callback("release_cache"), host_python_command=callback("host_python"),
        source_root=callback("source_root"), profile_begin=callback("profile_begin", 0),
        profile_end=callback("profile_end"), profile_counter=callback("profile_counter"),
    )
    assert result is sentinel and len(received) == 1
    cache_plan_is_none = received[0].get("action_cache_plan") is None
    # This actual early-return function must not touch the DAG/cache owner.
    class ForbiddenDag:
        def __getattr__(self, name):
            raise AssertionError("unexpected action-DAG access: " + name)
    noop = source_function(source_root, "_load_noop_action_result",
                           {"_module_action_dag": ForbiddenDag()})
    assert noop(None, ["input.py"] * module_count, ["input"] * module_count) is None
    return {
        "claim": "actual dispatch function with inert compile boundary; not a compile replay",
        "direct_value": direct_value,
        "modules": module_count,
        "cache_plan_is_none": cache_plan_is_none,
        "cache_callback_calls": {name: calls.get(name, 0) for name in
                                 ("plan_cache", "load_cache", "acquire_cache", "wait_cache",
                                  "publish_cache", "release_cache")},
        "displayed_plan_actions_when_no_plan": displayed_plan_actions(source_root, module_count),
    }


def existing_linux_artifact(path):
    if "linux" not in path.name or path.suffix != ".zip":
        raise ValueError("only an explicitly named existing Linux artifact is admitted")
    with zipfile.ZipFile(path) as archive:
        def read(name):
            info = archive.getinfo(name)
            if info.file_size > 16 * 1024 * 1024:
                raise ValueError("artifact member exceeds bounded reader")
            return archive.read(name)
        stage = json.loads(read("worker-prior-gate/stage1.json"))
        receipt = json.loads(read("platform-qualification/shared/receipt.json"))
        log = read("platform-qualification/shared/stage1.log").decode("utf-8")
    captures = set(re.findall(r"pcc direct indexed capture module=(\S+)", log))
    emits = set(re.findall(r"pcc direct indexed emit module=(\S+)", log))
    completed = stage["metrics"]["completed_modules"]
    return {
        "commit": stage["source"]["commit"], "status": stage["status"],
        "completed_modules": completed,
        "direct_capture_modules": len(captures), "direct_emit_modules": len(emits),
        "all_completed_modules_observed_direct": captures == emits and len(emits) == completed,
        "backend": receipt["configuration"]["backend"],
        "python_ir_passes": receipt["configuration"]["python_ir_passes"],
        "compile_wall_seconds": stage["metrics"]["compile_wall_s"],
        "cpu_seconds": None,
        "cpu_note": "artifact worker_seconds are wall-duration area, not measured CPU",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--linux-artifact", type=Path, action="append", default=[])
    args = parser.parse_args()
    result = {"direct": probe_route(args.source_root),
              "text_route_positive_control": probe_route(args.source_root, "0"),
              "existing_artifacts": [existing_linux_artifact(path) for path in args.linux_artifact]}
    print(json.dumps(result, indent=2))

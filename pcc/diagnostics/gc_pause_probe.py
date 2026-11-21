"""Prepare a separate tracing-GC runtime build with exclusive phase counters.

python -m pcc.diagnostics.gc_pause_probe CHECKOUT NEW_DIRECTORY

The input checkout, its ordinary compiler/runtime and caches are not modified.
Build the new archive using the copy's pcc package and point a *diagnostic*
application at it. Read pcc_exp_pause_value(0..31) and telemetry 119--121
together after workers stop. See docs/developer-reference.md.
"""
from __future__ import annotations

import argparse
import ast
import shutil
from pathlib import Path

from pcc.diagnostics._gc_pause_kernel import RUNTIME
from pcc.diagnostics.gc_pause_report import PHASES

# Instruments coarse *function scopes*, never per-object mutator operations.
# Any time not claimed by a nested scope remains in its enclosing phase.
SCOPES = {
    "py_obj": {"_gc_tracing_collect_step": "mark_other"},
    "freestanding_gc_incremental_concurrent_scheduler": {
        "pcc_gc_tracing_step_cycle": "mark_other"},
    "freestanding_gc_common_mark_cycle": {
        "pcc_gc_seed_roots": "registered_roots",
        "pcc_gc_drain_all_gray_unlocked": "gray_drain"},
    "freestanding_gc_object_root_seeding": {
        "pcc_gc_prepare_object_list_mark": "prepare_heap_for_mark",
        "pcc_gc_gray_current_roots": "registered_roots",
        "pcc_gc_gray_registered_roots": "registered_roots"},
    "freestanding_gc_conservative_roots": {
        "_capture_thread_words": "capture_sort_root_words",
        "_pin_snapshot_owners": "pin_owner_lookup"},
    "freestanding_gc_public_collection": {"pcc_gc_collect_tracing": "sweep_other"},
    "freestanding_gc_tracing_sweep_collector": {
        "pcc_gc_concurrent_sweep_finish_stopped": "retired_sweep_drain",
        "pcc_gc_tracing_recheck_reachability_after_finalizers": "candidate_recheck",
        "_sweep_weak_callbacks": "weak_targets",
        "_sweep_finalizers": "select_and_call_finalizers",
        "_sweep_clear_and_reclaim": "clear_reclaim"},
}


def _call(node, name):
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name


def _one(matches, description):
    matches = list(matches)
    if len(matches) != 1:
        raise ValueError(f"cannot instrument {description}: expected one anchor, got {len(matches)}")
    return matches[0]


def _nodes(code):
    return ast.parse(code).body


def _tree(node):
    return ast.dump(node, include_attributes=False)


def _statement(node):
    return _tree(_nodes(node)[0])


class _Scope(ast.NodeTransformer):
    """Store the return value before restoring the phase, even on early exits."""
    def __init__(self, returns):
        self.returns = returns

    def visit_Return(self, node):
        out = []
        if node.value is not None:
            out.append(ast.AnnAssign(
                target=ast.Name(id="exp_pause_return_value", ctx=ast.Store()),
                annotation=self.returns, value=node.value, simple=1))
        out += _nodes("if exp_previous_state >= 0:\n    pcc_exp_pause_switch(exp_previous_state)")
        out.append(ast.Return(ast.Name(id="exp_pause_return_value", ctx=ast.Load())
                              if node.value is not None else None))
        return out


def _scope(fun, phase, word):
    for item in ast.walk(fun):
        if ((isinstance(item, (ast.FunctionDef, ast.Lambda)) and item is not fun)
                or isinstance(item, (ast.Yield, ast.YieldFrom, ast.Await, ast.Raise))):
            raise ValueError(f"unsupported control flow in phase scope: {fun.name}")
    if fun.returns is None:
        raise ValueError(f"missing return type in phase scope: {fun.name}")
    _Scope(fun.returns).visit(fun)
    enter = _nodes(f"exp_previous_state: {word} = pcc_exp_pause_switch({PHASES.index(phase)})")
    fun.body[0:0] = enter
    if ast.unparse(fun.returns) == "None" and not isinstance(fun.body[-1], ast.Return):
        fun.body += _nodes("if exp_previous_state >= 0:\n    pcc_exp_pause_switch(exp_previous_state)")


class _Replace(ast.NodeTransformer):
    def __init__(self, find, replacement):
        self.find = _statement(find)
        self.replacement = replacement
        self.count = 0

    def generic_visit(self, node):
        if isinstance(node, ast.stmt) and _tree(node) == self.find:
            self.count += 1
            return _nodes(self.replacement)
        return super().generic_visit(node)


def _replace(fun, original, replacement):
    replace = _Replace(original, replacement)
    replace.visit(fun)
    if replace.count != 1:
        raise ValueError(f"cannot identify collection window in {fun.name}: {original}")


def _phase_window(fun):
    start = "collection_stw_start: int = pcc_runtime_monotonic_us()"
    _replace(fun, start, start + "\nexp_phase_context = stack_alloc(168)\n"
             "pcc_exp_pause_begin(exp_phase_context, collection_stw_start)")
    _replace(fun, "pcc_gc_record_collection_stw_pause(collection_stw_start, pcc_runtime_monotonic_us())",
             "exp_end_us: int = pcc_runtime_monotonic_us()\n"
             "pcc_exp_pause_finish(exp_phase_context, collection_stw_start, exp_end_us)\n"
             "pcc_gc_record_collection_stw_pause(collection_stw_start, exp_end_us)")


def instrument_module(module: str, source: str) -> str:
    if "pcc_exp_pause_" in source:
        raise ValueError(f"{module} is already instrumented")
    tree = ast.parse(source)
    scopes = SCOPES[module]
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    patched = dict.fromkeys(scopes)
    if module == "py_obj":
        patched.update(dict.fromkeys(("_gc_tracing_automatic_collect", "pcc_gc_collect")))
    if module == "freestanding_gc_tracing_sweep_collector":
        patched.update(dict.fromkeys(("_user_code_window_open", "_user_code_window_close")))
    for name in patched:
        if name not in functions:
            raise ValueError(f"missing phase function {module}.{name}")
    lines = source.splitlines(keepends=True)
    # Only targeted function regions are regenerated; existing decorators remain.
    for name in sorted(patched, key=lambda n: functions[n].lineno, reverse=True):
        fun = functions[name]
        start, end = fun.lineno - 1, fun.end_lineno
        fun.decorator_list = []
        if name in scopes:
            _scope(fun, scopes[name], "int" if module == "py_obj" else "i64")
        elif name == "_user_code_window_open":
            release = "store_i64(window, 0, pcc_thread_world_release_held())"
            _replace(fun, release, release + "\nif load_i64(window, 0) > 0:\n    pcc_exp_pause_window(1)")
        elif name == "_user_code_window_close":
            reacquire = "pcc_thread_world_reacquire_held(load_i64(window, 0))"
            _replace(fun, reacquire, reacquire + "\nif load_i64(window, 0) > 0:\n    pcc_exp_pause_window(0)")
        else:
            _phase_window(fun)
        lines[start:end] = [ast.unparse(ast.fix_missing_locations(fun)) + "\n"]
    if module == "py_obj":
        imported = _one((n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "pcc.unsafe"),
                        "native intrinsics")
        lines[imported.lineno-1:imported.lineno-1] = [
            "from pcc.unsafe import define_thread_local_ptr_null, define_global_i64_array\n"]
        lines.append(RUNTIME + "\n")
    else:
        first = next((n for n in tree.body if isinstance(n, ast.Assign) and _call(n.value, "extern")), None)
        if first is None:
            raise ValueError(f"missing extern declarations in {module}")
        externs = ['pcc_exp_pause_switch = extern("pcc_exp_pause_switch", (c_int64,), c_int64)\n']
        if module == "freestanding_gc_tracing_sweep_collector":
            externs.append('pcc_exp_pause_window = extern("pcc_exp_pause_window", (c_int64,), c_void)\n')
        lines[first.lineno-1:first.lineno-1] = externs
    result = "".join(lines)
    ast.parse(result)
    return result


def _add_abi(source: str) -> str:
    body = ast.parse(source).body
    insertions = []
    for name in ("FREESTANDING_GC_PTR_GLOBALS", "FREESTANDING_GC_THREAD_LOCAL_GLOBALS"):
        node = _one((n for n in body if isinstance(n, ast.AnnAssign)
                     and isinstance(n.target, ast.Name) and n.target.id == name), name)
        baggage = node.value
        if not _call(baggage, "frozenset") or not isinstance(baggage.args[0], ast.Set):
            raise ValueError(f"unknown TLS registration schema for {name}")
        insertions.append((baggage.args[0].elts[0].lineno - 1, '        "pcc_exp_pause_owner",\n'))
    # Use a short existing chunk, never exceed the runtime ABI's 50-entry limit.
    chunks = [n for n in body if isinstance(n, ast.FunctionDef)
              and n.name.startswith("_cross_object_signatures_part_")]
    for chunk in reversed(chunks):
        expr = chunk.body[-1]
        if isinstance(expr, ast.Return) and isinstance(expr.value, ast.Dict) and 0 < len(expr.value.keys) <= 48:
            insertions.append((expr.value.keys[0].lineno - 1,
                               '        "pcc_exp_pause_switch": (("c_int64",), "c_int64"),\n'
                               '        "pcc_exp_pause_window": (("c_int64",), "c_void"),\n'))
            break
    else:
        raise ValueError("no available diagnostic runtime ABI chunk")
    lines = source.splitlines(keepends=True)
    for index, text in sorted(insertions, reverse=True):
        lines.insert(index, text)
    result = "".join(lines)
    ast.parse(result)
    return result


def render_changes(root: Path) -> dict[Path, str]:
    changed = {}
    for module in SCOPES:
        path = Path("pcc/runtime/py") / (module + ".py")
        changed[path] = instrument_module(module, (root / path).read_text())
    abi = Path("pcc/frontends/python/codegen/runtime_abi.py")
    changed[abi] = _add_abi((root / abi).read_text())
    return changed


def make_overlay(root: Path, output: Path) -> None:
    root, output = root.resolve(), output.resolve()
    if output == root or root in output.parents:
        raise ValueError("use a new output directory outside the source checkout")
    if output.exists():
        raise FileExistsError(output)
    changed = render_changes(root)
    output.mkdir()
    # The owned archive validates a complete compiler identity, which also
    # covers scripts/bootstrap.py and the owned Mach-O linker script.
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "build_owned", "build_py",
                                   ".pcc-runtime-build.lock", ".pytest_cache")
    for directory in ("pcc", "scripts"):
        shutil.copytree(root / directory, output / directory, ignore=ignore)
    for path, code in changed.items():
        (output / path).write_text(code)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("new_directory", type=Path)
    args = parser.parse_args()
    try:
        make_overlay(args.checkout, args.new_directory)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"cannot prepare GC phase diagnostic: {exc}\n")


if __name__ == "__main__":
    main()

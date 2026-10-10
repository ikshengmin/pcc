"""Test-only old-scan control; all real liveness and allocation still execute."""

import ast
import inspect

from pcc.backend import self_backend_aarch64_darwin_regalloc as regalloc
from pcc.backend.self_backend_kernel import get_indexed_function_kernel


MODES = ("default", "function", "local")


def select_mode(monkeypatch, mode, *, call_results="1"):
    assert mode in MODES
    if mode == "default":
        monkeypatch.delenv("PCC_SELF_CALLEE_SAVED_REGISTERS", raising=False)
    else:
        monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "0")
    monkeypatch.setenv("PCC_SELF_FUNCTION_LIVE_INTERVALS", "1" if mode == "function" else "0")
    monkeypatch.setenv("PCC_SELF_CALL_RESULT_REGISTERS", call_results)


def restore_redundant_scans(tree):
    """Undo only the two scan guards, leaving the allocator's other code intact.

    The safety else-body is the original early-break scan. The call-position
    body is the original full scan. This reference consumes those scans rather
    than stubbing _function_level_facts, safety, liveness, or allocation.
    Fail if the two expected seams move; never silently compare a function to
    itself. The independent instruction_count assignment remains just before
    the safety scan; the preimage placed it after that scan's initialization.
    """
    class Restore(ast.NodeTransformer):
        safety = 0
        calls = 0

        def visit_If(self, node):
            if (ast.unparse(node.test) == "function_level"
                    and len(node.body) == 1
                    and ast.unparse(node.body[0]) == "block_is_safe = block_safe[block_id]"):
                assert node.orelse
                self.safety += 1
                return node.orelse
            if (ast.unparse(node.test) == "not function_level"
                    and any(isinstance(child, ast.Call)
                            and ast.unparse(child.func) == "call_positions.append"
                            for statement in node.body for child in ast.walk(statement))):
                assert not node.orelse
                self.calls += 1
                return node.body
            return self.generic_visit(node)

    restore = Restore()
    tree = restore.visit(tree)
    assert (restore.safety, restore.calls) == (1, 1)
    return ast.fix_missing_locations(tree)


def old_scan_allocator():
    tree = restore_redundant_scans(ast.parse(inspect.getsource(
        regalloc.allocate_aarch64_block_registers
    )))
    namespace = dict(vars(regalloc))
    exec(compile(tree, "<aarch64-original-metadata-scans>", "exec"), namespace)
    return namespace["allocate_aarch64_block_registers"]


def allocation_state(func):
    kernel = get_indexed_function_kernel(func)
    return {
        "value_names": tuple(kernel.value_names),
        "value_scalars": tuple(kernel.value_scalars.diagnostic_values()),
        "slot_scalars": tuple(kernel.slot_scalars.diagnostic_values()),
        "frame_size": func.frame_size,
        "callee_saved": tuple(func.aarch64_callee_saved),
        "fused_branch_values": dict(func.aarch64_fused_branch_values),
        "frameless": func.aarch64_frameless,
        "madd_fusions": tuple(func.aarch64_madd_fusions),
        "reload_offsets": tuple(func.aarch64_reload_slot_offsets),
    }


def removed_metadata_reads(func):
    """Exact old safety prefix plus old safe-block call scan visits."""
    if func.is_vararg:
        return 0
    kernel = get_indexed_function_kernel(func)
    count = 0
    for block_id in range(len(kernel.block_names)):
        block = kernel.block_fact(block_id)
        for index in range(block.second):
            kind = kernel.instruction_metadata_by_id(block.first + index).first
            count += 1
            if (not 0 <= kind < len(regalloc.PARSED_INSTRUCTION_KINDS)
                    or kind in regalloc._POOL_REJECTED_INSTRUCTION_KIND_IDS):
                break
        else:
            count += block.second
    return count

"""Compiled frames read one published word to decide frame registration.

The AArch64 Darwin short path of every pcc_gc_frame_* call and the runtime's
frame entries decided "register nothing" from three words: the configuration
is parsed, backend 0 is selected and its frame roots are off. The runtime
publishes that conjunction as pcc_gc_frame_registry_inactive wherever one of
the three words changes, so a frame call loads one word instead of three.

Mapped frames on collectors 1-4 asked pcc_gc_stackmap_roots_mode on every
enter and leave. Its answer becomes final once a valid table is published:
pcc_gc_stackmap_walk_state caches it as 1, the stack walk roots mapped
frames on every collector. No setting turns the walk off
(PCC_GC_STACKMAP_ROOTS accepts only "report"); a published table that fails
validation aborts.
"""

import os
import subprocess
from types import SimpleNamespace

import pytest

from pcc.frontends.python.pipeline import compile_python


TARGET = "arm64-apple-darwin"
FRAME_CALLS = (
    "pcc_gc_frame_enter", "pcc_gc_frame_enter_lifo",
    "pcc_gc_frame_enter_mapped", "pcc_gc_frame_enter_lifo_mapped",
    "pcc_gc_frame_leave", "pcc_gc_frame_leave_lifo",
    "pcc_gc_frame_leave_mapped", "pcc_gc_frame_leave_lifo_mapped",
)
COMPONENT_FLAGS = (
    "pcc_gc_config_initialized", "pcc_gc_backend_selected",
    "pcc_gc_backend0_frame_roots_enabled",
)


def _marked_symbols():
    from pcc.backend.self_backend_aarch64_gc_calls import GC_ABI_MARKER
    from pcc.backend.self_backend_module_symbols import PreparedModuleSymbols

    marker = frozenset({GC_ABI_MARKER})
    return PreparedModuleSymbols("module_", marker, marker, frozenset(), TARGET)


def test_frame_short_path_loads_one_published_flag():
    from pcc.backend.self_backend_aarch64_gc_calls import gc_short_call_lines
    from pcc.backend.self_backend_kernel import TYPE_KIND_PTR, TYPE_KIND_VOID

    symbols = _marked_symbols()
    for callee in FRAME_CALLS:
        count = 1 if "frame_leave" in callee else 2
        lines = gc_short_call_lines(SimpleNamespace(name="probe"), callee, 0,
                                    TYPE_KIND_VOID, (TYPE_KIND_PTR,) * count, symbols)
        text = "\n".join(lines)
        assert "pcc_gc_frame_registry_inactive" in text, callee
        assert "pcc_thread_stop_requested" in text, callee
        for flag in COMPONENT_FLAGS:
            assert flag not in text, (callee, flag)
        # stop poll (address, ldr, cbnz), the published word (address, ldr,
        # cbz) and the branch over the cold call.
        fast = lines[:lines.index(next(line for line in lines if line.endswith(".slow:")))]
        assert len(fast) == 9, (callee, fast)
    barrier = "\n".join(gc_short_call_lines(
        SimpleNamespace(name="probe"), "pcc_gc_note_write_barrier", 0,
        TYPE_KIND_VOID, (TYPE_KIND_PTR, TYPE_KIND_PTR), symbols))
    assert "pcc_gc_frame_registry_inactive" not in barrier
    assert "pcc_gc_config_initialized" in barrier
    assert "pcc_gc_backend_selected" in barrier


_PROGRAM = '''import gc
from pcc.unsafe import global_addr, load_i32


class Node:
    def __init__(self, value, next_node):
        self.value = value
        self.next_node = next_node


def build(n):
    head = None
    i = 0
    while i < n:
        head = Node(i, head)
        i += 1
    return head


def total(node):
    s = 0
    while node is not None:
        s += node.value
        node = node.next_node
    return s


def keep(n):
    local = build(n)
    ring = [local]
    ring.append(ring)
    gc.collect()
    return total(local)


def main():
    results = []
    round_index = 0
    while round_index < 20:
        head = build(200)
        results.append(keep(100 + round_index))
        gc.collect()
        results.append(total(head))
        round_index += 1
    print(sum(results), len(results))
    initialized = load_i32(global_addr("pcc_gc_config_initialized"), 0)
    backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    roots = load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0)
    inactive = load_i32(global_addr("pcc_gc_frame_registry_inactive"), 0)
    walk_state = load_i32(global_addr("pcc_gc_stackmap_walk_state"), 0)
    print(initialized, backend, roots, inactive, walk_state)


main()
'''

# keep(100 + k) sums 0..99+k; total(head) sums 0..199, twenty rounds each.
_TOTAL = sum((99 + k) * (100 + k) // 2 for k in range(20)) + 20 * 19900


@pytest.fixture(scope="module")
def frame_flag_program(tmp_path_factory):
    work = tmp_path_factory.mktemp("frame_registry_flags")
    source = work / "frame_registry_flags.py"
    source.write_text(_PROGRAM, encoding="utf-8")
    executable = work / "frame_registry_flags.out"
    compile_python(str(source), str(executable), backend="self",
                   ir_scaffold_mode="on")
    return executable


def _run(executable, backend, **extra):
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=120,
        env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend), **extra),
    )
    assert (run.returncode, run.stderr) == (0, "")
    lines = run.stdout.splitlines()
    assert lines[0] == f"{_TOTAL} 40"
    return [int(word) for word in lines[1].split()]


@pytest.mark.parametrize("backend", range(5), ids=lambda backend: f"gc{backend}")
def test_published_frame_flags_match_their_components(frame_flag_program, backend):
    initialized, selected, roots, inactive, walk_state = _run(frame_flag_program, backend)
    assert (initialized, selected) == (1, backend)
    assert inactive == int(initialized == 1 and selected == 0 and roots == 0)
    if backend == 0:
        assert (roots, inactive) == (0, 1)
    else:
        # Every mapped frame of this run asked, and the answer is final:
        # every collector walks the stack instead of registering them.
        assert (inactive, walk_state) == (0, 1)


@pytest.mark.parametrize("backend", range(1, 5), ids=lambda backend: f"gc{backend}")
def test_the_stack_walk_cannot_be_turned_off(frame_flag_program, backend):
    # "0" once made mapped frames register; the walk is now the only root
    # source of a published table's frames, so the value is ignored.
    flags = _run(frame_flag_program, backend, PCC_GC_STACKMAP_ROOTS="0")
    assert flags == [1, backend, 0, 0, 1]

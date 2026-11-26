"""Compiled frames read one published word to decide frame registration.

The AArch64 Darwin short path of every pcc_gc_frame_* call and the runtime's
frame entries decided "register nothing" from three words: the configuration
is parsed, backend 0 is selected and its frame roots are off. The runtime
publishes that conjunction as pcc_gc_frame_registry_inactive wherever one of
the three words changes, so a frame call loads one word instead of three.

A valid published table makes the stack walk the root source for its
mapped frames. The sole collector's inactive short path can leave the mode
query unused; ELF/PE main does not publish a linked table at all. Explicit
mode admission on a published table caches walk state 1; no-table admission
returns 0 without disabling the policy. No setting turns a valid published
walk off (PCC_GC_STACKMAP_ROOTS accepts only "report"); a corrupt published
table aborts.
"""

import os
import subprocess
from types import SimpleNamespace

import pytest

from pcc.backend.precise_stackmap import (
    ARCH_AARCH64, ARCH_X86_64, FunctionStackMap, PreciseStackMap,
    PRECISE_STACKMAP_TABLES_ENV, encode_stack_map, function_address_offsets,
    function_id, precise_stackmap_target_publishes_tables,
)
from pcc.frontends.python.pipeline import compile_python
from pcc.frontends.python.pipeline_targets import host_target_triple


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
from pcc.unsafe import global_addr, global_load_ptr, load_i32, load_i64, ptr_is_null
from pcc.extern import extern, c_int64
stackmap_mode = extern("pcc_gc_stackmap_roots_mode", (), c_int64)


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
    walk_before = load_i32(global_addr("pcc_gc_stackmap_walk_state"), 0)
    published = 1 - ptr_is_null(global_load_ptr("pcc_gc_precise_stackmap_table"))
    mode = stackmap_mode()
    walk_after = load_i32(global_addr("pcc_gc_stackmap_walk_state"), 0)
    setting = load_i32(global_addr("pcc_gc_stackmap_roots_setting"), 0)
    index_count = load_i64(global_addr("pcc_gc_stackmap_index_count"), 0)
    print(initialized, backend, roots, inactive, walk_before, published, mode, walk_after, setting, index_count)


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
    # Test the documented target publication policy, not the ambient
    # experimental object-table setting or host-cc linker override.
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv(PRECISE_STACKMAP_TABLES_ENV, raising=False)
        patch.setenv("PCC_SELF_LINK", "pcc")
        compile_python(str(source), str(executable), backend="self",
                       ir_scaffold_mode="on")
    return executable


def _run(executable, backend, **extra):
    env = dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend))
    env.pop("PCC_GC_STACKMAP_ROOTS", None)
    env.update(extra)
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=120, env=env,
    )
    assert (run.returncode, run.stderr) == (0, "")
    lines = run.stdout.splitlines()
    assert len(lines) == 2 and lines[0] == f"{_TOTAL} 40"
    return [int(word) for word in lines[1].split()]


@pytest.mark.parametrize("backend", (0,), ids=lambda backend: f"gc{backend}")
def test_published_frame_flags_match_their_components(frame_flag_program, backend):
    flags = _run(frame_flag_program, backend)
    initialized, selected, roots, inactive = flags[:4]
    assert (initialized, selected) == (1, backend)
    assert inactive == int(initialized == 1 and selected == 0 and roots == 0)
    if backend == 0:
        assert (roots, inactive) == (0, 1)
    _assert_application_stackmap_policy(flags)


def _assert_application_stackmap_policy(flags):
    assert len(flags) == 10
    before, published, mode, after, setting, count = flags[4:]
    triple = host_target_triple()
    expected_publication = "apple" in triple or "darwin" in triple
    assert precise_stackmap_target_publishes_tables(triple) is expected_publication
    assert published == int(expected_publication), "actual main publication must match target policy"
    assert setting == 1, "the requested 0 must never disable the policy"
    if expected_publication:
        # The inactive short path may not have queried the mode yet. The
        # explicit query must admit the actual linked table and cache 1.
        assert before in (0, 1)
        assert (mode, after) == (1, 1) and count > 0
    else:
        # ELF/PE can emit object tables experimentally, but their executable
        # link does not publish one. No table is not a disabled valid walk.
        assert (before, mode, after, count) == (0, 0, 0, 0)


def test_the_stack_walk_cannot_be_turned_off(frame_flag_program):
    default = _run(frame_flag_program, 0)
    requested_off = _run(frame_flag_program, 0, PCC_GC_STACKMAP_ROOTS="0")
    assert default == requested_off
    assert default[:4] == [1, 0, 0, 1]
    _assert_application_stackmap_policy(default)


def _mode_control_source():
    triple = host_target_triple()
    arch = ARCH_AARCH64 if triple.startswith(("arm64", "aarch64")) else ARCH_X86_64
    # An actual backend-encoded, address-resolved one-function table. This
    # control only validates mode/index admission, not stack traversal;
    # the ordinary app above must still report its true link publication.
    payload = encode_stack_map(PreciseStackMap(arch, (
        FunctionStackMap(function_id("mode_probe"), 1, 1, 0, ()),
    )), final_image=True)
    offsets = function_address_offsets(payload)
    assert len(offsets) == 1
    data = ", ".join(str(byte) for byte in payload)
    return rf'''#include <stdint.h>
#include <stdio.h>
#include <string.h>
extern void pcc_gc_register_precise_stackmap_table(void *);
extern int64_t pcc_gc_stackmap_roots_mode(void);
extern int32_t pcc_gc_stackmap_walk_state;
extern int32_t pcc_gc_stackmap_roots_setting;
extern int64_t pcc_gc_stackmap_index_count;
static unsigned char table[] = {{{data}}};
static int mode_probe(void) {{ return 42; }}
int main(int argc, char **argv) {{
    if (pcc_gc_stackmap_roots_mode() != 0 ||
        pcc_gc_stackmap_walk_state != 0 ||
        pcc_gc_stackmap_index_count != 0) return 1;
    uint64_t address = (uint64_t)(uintptr_t)&mode_probe;
    memcpy(table + {offsets[0]}, &address, 8);
    if (argc > 1 && argv[1][0] == 'b') table[0] = 0;
    pcc_gc_register_precise_stackmap_table(table);
    int64_t mode = pcc_gc_stackmap_roots_mode();
    if (mode != pcc_gc_stackmap_roots_setting ||
        pcc_gc_stackmap_walk_state != 1 || pcc_gc_stackmap_index_count != 1)
        return 2;
    printf("mode=%lld state=%d index=%lld\n",
           (long long)mode, pcc_gc_stackmap_walk_state,
           (long long)pcc_gc_stackmap_index_count);
    return 0;
}}
'''


def test_published_table_mode_is_admitted_even_when_requested_off(tmp_path, pcc_runtime_archive):
    from tests.owned_c_execution import compile_owned_c_with_runtime

    source = tmp_path / "stackmap_mode.c"
    source.write_text(_mode_control_source())
    executable = tmp_path / "stackmap_mode"
    compile_owned_c_with_runtime(source, executable, runtime_archive=pcc_runtime_archive)
    env = dict(os.environ, PATH="/nonexistent")
    env.pop("PCC_GC_STACKMAP_ROOTS", None)
    for setting, mode in ((None, 1), ("0", 1), ("report", 2)):
        configured = dict(env)
        if setting is not None:
            configured["PCC_GC_STACKMAP_ROOTS"] = setting
        run = subprocess.run([str(executable)], capture_output=True, text=True,
                             timeout=15, env=configured)
        assert (run.returncode, run.stderr) == (0, "")
        assert run.stdout == f"mode={mode} state=1 index=1\n"
    bad = subprocess.run([str(executable), "bad"], capture_output=True, text=True,
                         timeout=15, env=dict(env, PCC_GC_STACKMAP_ROOTS="0"))
    assert bad.returncode != 0 and bad.stdout == ""
    assert "pcc runtime: the published stack-map table is invalid" in bad.stderr

"""Actual GC3 source over finite memory, not native stack-capture evidence.

Budgeted movement consults current stopped words, including interior payload
addresses, without walking or rewriting unrelated ObjectNodes. Full marking
and forwarding retirement retain their materialized pin contract.
"""
from contextlib import contextmanager

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.test_gc3_norc_collection_entry import PipelineGC3


@contextmanager
def snapshot(m):
    context = m.allocate(32)
    m.ns['pcc_gc_conservative_step_pin_begin'](context)
    try:
        yield context
    finally:
        m.ns['pcc_gc_conservative_step_pin_end'](context)


@pytest.mark.parametrize('offset', [0, 1, 16, 23])
def test_current_object_interior_word_prevents_copy_without_materialized_pin(offset):
    m = PipelineGC3()
    obj, node, _ = m.value()
    m.words = [obj + offset]
    with snapshot(m):
        assert m.ns['pcc_gc_generational_object_is_address_pinned'](obj) == 1
        assert m.ns['pcc_gc_generational_oldify_copy'](obj) == 0
        assert m.load(node, 56) == 0
    assert m.load('pcc_gc_backend3_step_pin_snapshot', 0) == 0


def test_new_stop_discards_previous_words_and_stale_materialized_pin():
    m = PipelineGC3()
    obj, node, _ = m.value()
    m.words = [obj + 16]
    m.ns['pcc_gc_pin_conservative_thread_roots']()
    assert m.load(node, 56) & 1
    m.words = []
    with snapshot(m):
        assert m.ns['pcc_gc_generational_object_is_address_pinned'](obj) == 0
        target = m.ns['pcc_gc_generational_oldify_copy'](obj)
        assert target != 0 and target != obj
        assert m.load(target, 12) & 256
    # The global query is unchanged outside this moving-step scope.
    assert m.ns['pcc_gc_object_is_address_pinned'](obj) == 1


@pytest.mark.parametrize('kind', ['header', 'lease', 'static'])
def test_nonconservative_address_pins_survive_empty_snapshot(kind):
    m = PipelineGC3()
    obj, node, _ = m.value()
    if kind == 'header':
        m.store(obj, 12, m.load(obj, 12) | abi.PY_FLAG_GC_PINNED)
    elif kind == 'lease':
        m.store(node, 80, 1)
    else:
        m.ns['pcc_gc_static_literal_contains'] = lambda value: int(value == obj)
    with snapshot(m):
        assert m.ns['pcc_gc_generational_object_is_address_pinned'](obj) == 1
        assert m.ns['pcc_gc_generational_oldify_copy'](obj) == 0


def test_payload_interior_word_pins_only_its_owner():
    m = PipelineGC3()
    obj, node, _ = m.value()
    other, _, _ = m.value()
    owner = m.allocate(72)
    span = m.allocate(48)
    payload = m.allocate(32)
    m.store(node, 48, owner)
    m.store(owner, 64, span)
    m.store(span, 8, payload)
    m.store(span, 16, 32)
    m.words = [payload + 7]
    with snapshot(m):
        assert m.ns['pcc_gc_generational_object_is_address_pinned'](obj) == 1
        assert m.ns['pcc_gc_generational_object_is_address_pinned'](other) == 0
    m.words = [payload + 32]
    with snapshot(m):
        assert m.ns['pcc_gc_generational_object_is_address_pinned'](obj) == 0


def test_nested_snapshot_refreshes_words_and_restores_outer_ownership():
    m = PipelineGC3()
    first, _, _ = m.value()
    second, _, _ = m.value()
    m.words = [first]
    with snapshot(m) as outer:
        outer_words = m.load(outer, 0)
        m.words = [first, second]
        with snapshot(m) as inner:
            inner_words = m.load(inner, 0)
            assert inner_words != outer_words
            assert m.ns['pcc_gc_generational_object_is_address_pinned'](second) == 1
        assert m.load('pcc_gc_backend3_step_pin_snapshot', 0) == outer
        assert inner_words in m.freed and outer_words not in m.freed
        assert m.ns['pcc_gc_generational_object_is_address_pinned'](first) == 1
        # A newly captured inner root must not disappear when restoring the
        # outer scope. It conservatively disables further outer movement.
        assert m.ns['pcc_gc_generational_object_is_address_pinned'](second) == 1
    assert outer_words in m.freed


def test_nested_full_scan_invalidates_older_step_cut():
    m = PipelineGC3()
    obj, node, _ = m.value()
    m.words = []
    with snapshot(m):
        m.words = [obj + 9]
        m.ns['pcc_gc_pin_conservative_thread_roots']()
        assert m.load(node, 56) & 1
        assert m.ns['pcc_gc_generational_oldify_copy'](obj) == 0


def test_public_forwarding_young_target_is_not_copied_away_from_raw_source():
    m = PipelineGC3()
    source, _, _ = m.value()
    target, _, _ = m.value()
    with m.locked():
        assert m.ns['pcc_gc_install_forwarding_unlocked'](source, target) == 0
    assert m.load(target, 12) & 128
    m.words = [source + 9]
    with snapshot(m):
        assert m.ns['pcc_gc_generational_object_is_address_pinned'](target) == 1
        assert m.ns['pcc_gc_generational_oldify_copy'](target) == 0


def _linux_x86_64():
    import platform
    return platform.system() == 'Linux' and platform.machine().lower() in ('x86_64', 'amd64')


@pytest.mark.pcc_gate(probe=_linux_x86_64)
def test_owned_native_snapshot_kernel_with_finite_stopped_words(tmp_path, monkeypatch):
    """Complete changed kernel, owned C driver/emitter/linker; no host cc.

    The driver supplies explicit finite memory and stopped words. This is not
    OS stack capture, threaded full-runtime or five-collector qualification.
    """
    import subprocess
    from pathlib import Path
    from unittest.mock import patch

    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.x86_64_asm_driver import assemble_file
    from pcc.frontends.python.pipeline import compile_python
    from tests.python.test_owned_external_tls import _c_ir

    if not _linux_x86_64():
        pytest.fail('this native probe requires Linux x86-64')
    root = Path(__file__).resolve().parents[2]
    kernel = root / 'pcc/runtime/py/freestanding_gc_conservative_roots.py'
    driver = Path(__file__).parent / 'fixtures/gc3_step_pin_snapshot.c'
    target = 'x86_64-unknown-linux-gnu'
    monkeypatch.setenv('PCC_IMPLICIT_THREAD_SAFEPOINTS', '0')
    ir = tmp_path / 'snapshot.ll'
    with patch.object(subprocess, 'Popen', side_effect=AssertionError('external build process')):
        compile_python(str(kernel), str(ir), emit_llvm_only=True,
                       python_library=True, backend='self', target_triple=target)
        objects = [parse_relocatable(emit_owned_object(ir.read_text(), target)),
                   parse_relocatable(emit_owned_object(_c_ir(driver.read_text(), target), target))]
        # Compile every changed strict caller/definition as a complete owner,
        # including its finite cross-object ABI checks. Only the kernel and
        # explicit finite driver are linked/executed by this component test.
        for name in ('freestanding_gc_state', 'freestanding_gc_forwarding_identity',
                     'freestanding_gc_generational_oldification',
                     'freestanding_gc_generational_scheduler'):
            owner_ir = tmp_path / (name + '.ll')
            compile_python(str(kernel.parent / (name + '.py')), str(owner_ir),
                           emit_llvm_only=True, python_library=True,
                           backend='self', target_triple=target)
            assert emit_owned_object(owner_ir.read_text(), target)
        startup = ('.intel_syntax noprefix\n.text\n.globl _start\n_start:\n'
                   ' call snapshot_probe\n mov edi, eax\n mov eax, 60\n syscall\n')
        image = link_static_executable([*objects, assemble_file(startup)])
    executable = tmp_path / 'snapshot'
    executable.write_bytes(image)
    executable.chmod(0o755)
    result = subprocess.run([str(executable)], capture_output=True, timeout=10, env={'PATH': ''})
    assert (result.returncode, result.stdout, result.stderr) == (0, b'', b'')

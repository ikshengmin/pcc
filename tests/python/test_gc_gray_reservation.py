"""Production atomic shader/count bodies under competing color publication."""
import ast
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/freestanding_gc_root_operations.py"
WHITE, GRAY, BLACK, PIN, DYING = 8, 16, 32, 64, 524288


class Shader:
    def __init__(self, flags=WHITE):
        self.words = {(0x1000, 12): flags, ('pcc_gc_gray_count', 0): int(bool(flags & GRAY))}
        self.before_cas = None
        self.events = []
        self.ns = dict(i64=int, PYOBJECTHEADER_FLAGS_OFFSET=12,
            global_addr=lambda name: name, atomic_load_i32=self.load,
            atomic_store_i32=self.store, atomic_rmw_i32=self.rmw,
            atomic_cas_i32=self.cas)
        names = {'pcc_gc_shade_gray_atomic', 'pcc_gc_set_mark_color',
                 'pcc_gc_gray_count_increment_acq_rel', 'pcc_gc_gray_count_decrement_acq_rel'}
        functions = [node for node in ast.parse(SOURCE.read_text()).body
                     if isinstance(node, ast.FunctionDef) and node.name in names]
        assert {node.name for node in functions} == names
        for node in functions:
            node.decorator_list = []
        exec(compile(ast.Module(functions, type_ignores=[]), str(SOURCE), 'exec'), self.ns)

    def load(self, base, offset=0, *order):
        return self.words.get((base, offset), 0)

    def store(self, base, offset, value, *order):
        self.words[base, offset] = value
        self.events.append((base, value))

    def rmw(self, kind, base, offset, value, *order):
        old = self.load(base, offset)
        assert kind == 'add'
        self.store(base, offset, old + value)
        return old

    def cas(self, base, offset, expected, desired, *order):
        if base == 0x1000 and self.before_cas:
            callback, self.before_cas = self.before_cas, None
            callback()
        old = self.load(base, offset)
        if old == expected:
            self.store(base, offset, desired)
        return old

    def shade(self, allow_black=1):
        self.ns['pcc_gc_shade_gray_atomic'](0x1000, allow_black)

    def drain(self):
        assert self.load(0x1000, 12) & GRAY
        self.ns['pcc_gc_gray_count_decrement_acq_rel']()
        self.ns['pcc_gc_set_mark_color'](0x1000, BLACK)

    def count(self):
        return self.load('pcc_gc_gray_count')


@pytest.mark.parametrize('flags', [WHITE, GRAY, BLACK])
@pytest.mark.parametrize('allow_black', [0, 1])
def test_existing_color_policy_and_single_credit(flags, allow_black):
    model = Shader(flags | PIN)
    model.shade(allow_black)
    expected = BLACK if flags == BLACK and allow_black == 0 else GRAY
    assert model.load(0x1000, 12) == expected | PIN
    assert model.count() == int(expected == GRAY)


def test_two_white_shaders_publish_only_one_credit():
    model = Shader()
    model.before_cas = model.shade
    model.shade()
    assert model.load(0x1000, 12) == GRAY and model.count() == 1


@pytest.mark.parametrize('allow_black', [0, 1])
def test_reserved_credit_survives_another_shader_and_complete_drain(allow_black):
    model = Shader()
    def competing_drain():
        model.shade()
        model.drain()
    model.before_cas = competing_drain
    model.shade(allow_black)
    assert model.load(0x1000, 12) == (GRAY if allow_black else BLACK)
    assert model.count() == allow_black


@pytest.mark.parametrize('allow_black', [0, 1])
def test_dying_before_entry_never_reserves_or_changes_color(allow_black):
    model = Shader(WHITE | DYING | PIN)
    model.shade(allow_black)
    assert model.load(0x1000, 12) == WHITE | DYING | PIN
    assert model.count() == 0 and model.events == []


@pytest.mark.parametrize('allow_black', [0, 1])
def test_dying_during_cas_refunds_reserved_credit(allow_black):
    model = Shader(WHITE | PIN)
    model.before_cas = lambda: model.store(0x1000, 12, WHITE | PIN | DYING)
    model.shade(allow_black)
    assert model.load(0x1000, 12) == WHITE | PIN | DYING
    assert model.count() == 0


def test_pin_update_during_cas_is_preserved_with_one_credit():
    model = Shader()
    model.before_cas = lambda: model.store(0x1000, 12, WHITE | PIN)
    model.shade()
    assert model.load(0x1000, 12) == GRAY | PIN and model.count() == 1

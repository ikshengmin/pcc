"""Execute the production byte-regex core with checked host memory intrinsics.

This is a host semantic oracle, not emitted-native or managed-GC acceptance.
No runtime archive, external compiler, or native bootstrap is built here.
"""
from __future__ import annotations

import ast
from bisect import bisect_right
from pathlib import Path
import re

import pytest


SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/freestanding_re_engine.py"


class CoreMemory:
    """Little-endian raw storage; reject out-of-allocation reads and writes."""

    def __init__(self):
        self.next_address = 4096
        self.blocks = {}
        self.bases = []
        self.stack = []
        self.globals = {}
        self.namespace = {
            "i64": int,
            "null": lambda: 0,
            "ptr_is_null": lambda pointer: int(pointer == 0),
            "ptr_add": lambda pointer, offset: pointer + offset,
            "stack_alloc": self.stack_alloc,
            "page_alloc": self.allocate,
            "page_free": self.free,
            "global_addr": self.global_addr,
            "global_load_ptr": lambda name: self.read(self.global_addr(name), 0, 8),
            "global_store_ptr": lambda name, value: self.write(self.global_addr(name), 0, value, 8),
            "logical_shift_right_i64": lambda value, bits: (value & ((1 << 64) - 1)) >> bits,
            "atomic_load_i64": lambda base, offset, _order: self.read(base, offset, 8, True),
            "atomic_store_i32": lambda base, offset, value, _order: self.write(base, offset, value, 4),
            "atomic_rmw_i32": lambda op, base, offset, value, _order: self.atomic(op, base, offset, value, 4),
            "atomic_rmw_i64": lambda op, base, offset, value, _order: self.atomic(op, base, offset, value, 8),
            "thread_safepoint": lambda: pytest.fail("single-thread core host model unexpectedly contended"),
        }
        for name, width in (("i8", 1), ("i32", 4), ("i64", 8), ("ptr", 8)):
            self.namespace["load_" + name] = (
                lambda base, offset, width=width, signed=name != "ptr":
                self.read(base, offset, width, signed)
            )
            self.namespace["store_" + name] = (
                lambda base, offset, value, width=width: self.write(base, offset, value, width)
            )
        definitions = []
        for node in ast.parse(SOURCE.read_text(), filename=str(SOURCE)).body:
            if isinstance(node, ast.FunctionDef):
                node.decorator_list = []
                definitions.append(node)
        exec(compile(ast.Module(body=definitions, type_ignores=[]), str(SOURCE), "exec"), self.namespace)

    def allocate(self, size):
        base = self.next_address
        self.next_address += size + 16
        self.blocks[base] = bytearray(size)
        self.bases.append(base)
        return base

    def stack_alloc(self, size):
        base = self.allocate(size)
        self.stack.append(base)
        return base

    def free(self, base, size):
        assert len(self.blocks[base]) == size
        del self.blocks[base]
        self.bases.remove(base)

    def region(self, base, offset, width):
        address = base + offset
        index = bisect_right(self.bases, address) - 1
        assert index >= 0, (address, width)
        owner = self.bases[index]
        block = self.blocks[owner]
        offset = address - owner
        assert offset >= 0 and offset + width <= len(block), (address, width, owner, len(block))
        return block, offset

    def read(self, base, offset, width, signed=False):
        block, offset = self.region(base, offset, width)
        return int.from_bytes(block[offset:offset + width], "little", signed=signed)

    def write(self, base, offset, value, width):
        block, offset = self.region(base, offset, width)
        block[offset:offset + width] = (value & ((1 << (8 * width)) - 1)).to_bytes(width, "little")

    def atomic(self, op, base, offset, value, width):
        old = self.read(base, offset, width)
        assert op in ("add", "xchg"), op
        self.write(base, offset, old + value if op == "add" else value, width)
        return old

    def global_addr(self, name):
        if name not in self.globals:
            self.globals[name] = self.allocate(8)
        return self.globals[name]

    def raw(self, value):
        encoded = value.encode("utf-8") + b"\0"
        base = self.stack_alloc(len(encoded))
        self.blocks[base][:] = encoded
        return base

    def run(self, pattern, text, *, flags=0, mode="fullmatch", start=0, end=None, capacity=64):
        mark = len(self.stack)
        try:
            pattern_pointer = self.raw(pattern)
            text_pointer = self.raw(text)
            caps = self.stack_alloc(512)
            groups = self.stack_alloc(8)
            if end is None:
                end = len(text.encode("utf-8"))
            if mode == "fullmatch":
                status = self.namespace["pcc_re_engine_fullmatch_flags"](
                    pattern_pointer, flags, text_pointer, end, start, caps, capacity, groups,
                )
            else:
                status = self.namespace["pcc_re_engine_run_flags"](
                    pattern_pointer, flags, text_pointer, end, start,
                    1 if mode == "search" else 0, caps, capacity, groups,
                )
            spans = []
            if status == 1:
                spans = [
                    (self.read(caps, group * 16, 8, True), self.read(caps, group * 16 + 8, 8, True))
                    for group in range(self.read(groups, 0, 8, True) + 1)
                ]
            return status, spans
        finally:
            for base in reversed(self.stack[mark:]):
                self.free(base, len(self.blocks[base]))
            del self.stack[mark:]


@pytest.fixture
def core():
    return CoreMemory()


def expected(pattern, text, flags=0, mode="fullmatch", start=0, end=None):
    compiled = re.compile(pattern, flags)
    method = getattr(compiled, mode)
    match = method(text, start) if end is None else method(text, start, end)
    return (0, []) if match is None else (1, [match.span(group) for group in range(compiled.groups + 1)])


def test_prefix_postcheck_cannot_implement_fullmatch(core):
    assert core.run("(a|ab)", "ab", mode="match") == (1, [(0, 1), (0, 1)])
    assert expected("(a|ab)", "ab") == (1, [(0, 2), (0, 2)])
    assert core.run("(a|ab)", "ab") == expected("(a|ab)", "ab")


@pytest.mark.parametrize("pattern,text,flags", [
    ("(a|ab)", "ab", 0),
    ("(a|ab)+", "abab", 0),
    ("a*?", "aaa", 0),
    ("a+?", "aaa", 0),
    ("a??", "a", 0),
    ("(a*?)(a*)", "aaa", 0),
    ("(?P<word>a|ab)", "ab", 0),
    ("(?=a)ab", "ab", 0),
    ("(?!x)ab", "ab", 0),
    ("(?=a)", "ab", 0),
    (r"(a|ab)\1", "abab", 0),
    ("(a)?b", "b", 0),
    ("", "", 0),
    ("", "a", 0),
    ("a$", "a\n", 0),
    ("a$", "a\n", re.M),
    (".*", "a\nb", re.S),
    (".*?", "a\nb", re.S),
    (".", "\0", 0),
    ("a.b", "a\0b", 0),
    ("(a|ab)", "AB", re.I),
    (r"\A(?:a|ab)\Z", "ab", 0),
])
def test_fullmatch_backtracking_and_captures_match_cpython(core, pattern, text, flags):
    assert core.run(pattern, text, flags=flags) == expected(pattern, text, flags)


@pytest.mark.parametrize("pattern,text,start,end,flags", [
    ("(a|ab)", "xaby", 1, 3, 0),
    ("(?=a)ab", "xaby", 1, 3, 0),
    ("^ab", "xaby", 1, 3, 0),
    ("^ab", "x\nabY", 2, 4, re.M),
    ("ab$", "xaby", 1, 3, 0),
    ("", "abc", 2, 2, 0),
    ("", "abc", 2, 1, 0),
    ("a*", "abc", 2, 1, 0),
    ("", "abc", 3, 3, 0),
    ("(a|ab)", "abc", 0, 2, 0),
    ("a", "a\n", 0, 1, 0),
])
def test_fullmatch_effective_window_preserves_original_offsets(core, pattern, text, start, end, flags):
    assert core.run(pattern, text, flags=flags, start=start, end=end) == expected(
        pattern, text, flags, start=start, end=end,
    )


def test_cached_program_does_not_remember_fullmatch_mode(core):
    for mode in ("fullmatch", "match", "search", "fullmatch", "search", "match"):
        assert core.run("(a|ab)", "ab", mode=mode) == expected("(a|ab)", "ab", mode=mode)
    assert core.namespace["pcc_re_engine_compile_count"]() == 1


@pytest.mark.parametrize("pattern,text,flags,capacity,status", [
    ("(?<=a)b", "ab", 0, 64, -1),
    ("(?=(a))ab", "ab", 0, 64, -1),
    ("a", "a", 4, 64, -1),
    (".", "é", 0, 64, -4),
    ("(a)", "a", 0, 2, -3),
])
def test_fullmatch_preserves_explicit_engine_failures(core, pattern, text, flags, capacity, status):
    assert core.run(pattern, text, flags=flags, capacity=capacity) == (status, [])


def test_fullmatch_differential_corpus_keeps_match_and_search_unchanged(core):
    patterns = ("", "a", "a|ab", "a*?", "a+?", "a??", "(a|ab)+", "(a)?b",
                "(a*)(b*)", "(?=a)a.*", "(?!b).*", "[ab]{1,3}?", r"(a|ab)\1")
    texts = ("", "a", "b", "ab", "aa", "aba", "abab", "aab", "bbb", "xabab")
    for pattern in patterns:
        for text in texts:
            for mode in ("match", "search", "fullmatch"):
                assert core.run(pattern, text, mode=mode) == expected(pattern, text, mode=mode), (
                    pattern, text, mode,
                )


def test_fullmatch_emits_the_typed_raw_abi_without_managed_roots(tmp_path):
    from pcc.frontends.python.pipeline import compile_python

    output = tmp_path / "freestanding_re_engine.ll"
    compile_python(str(SOURCE), str(output), emit_llvm_only=True, python_library=True,
                   libpython_mode="off", backend="self", target_triple="x86_64-unknown-linux-gnu")
    text = output.read_text()
    entry = re.search(r"^define (?:external )?i32 @pcc_re_engine_fullmatch_flags\(([^\n]*)\).*?^}", text, re.M | re.S)
    assert entry is not None
    assert [argument.strip().split()[0] for argument in entry.group(1).split(",")] == [
        "ptr", "i64", "ptr", "i64", "i64", "ptr", "i32", "ptr",
    ]
    assert "@py_" not in entry.group()
    assert re.search(r"\bcall\b[^\n]*@pcc_gc_frame_enter", text) is None
    assert re.search(r"\bload\b[^\n]*@pcc_thread_stop_requested", text) is None

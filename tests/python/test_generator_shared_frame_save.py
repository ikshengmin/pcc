"""The delegated-save optimization leaves ordinary generator parks unchanged."""

import re
import sys

import pytest

from tests.python.process_timeout import run_process_group_timeout


def _source(parks: int) -> str:
    lines = ["def parks(seed):"]
    # The static locals are individually rooted before any park. Keep them
    # live across every park, including aliases, and change their values.
    for i in range(12):
        lines.append(f"    local{i} = [seed, {i}]")
    for i in range(parks):
        lines.append(f"    yield {i}")
        lines.append(f"    local{i % 12}.append(seed)")
    lines.append("    return len(local0)")
    lines.extend(["for value in parks(7):", "    print(value)"])
    return "\n".join(lines) + "\n"


def test_ordinary_generator_parks_keep_original_inline_save(tmp_path, monkeypatch):
    from pcc.frontends.python.pipeline import compile_python

    src = tmp_path / "shared_saves.py"
    src.write_text(_source(24))
    bodies = []
    for enabled in ("0", "1"):
        monkeypatch.setenv("PCC_GENERATOR_SHARED_FRAME_SAVE", enabled)
        output = tmp_path / ("saves_" + enabled + ".ll")
        compile_python(str(src), str(output), backend="self",
                       ir_scaffold_mode="on", emit_llvm_only=True)
        body = re.search(r"define[^\n]*parks__gen_resume[^\n]*\{\n(.*?)\n\}", output.read_text(), re.S)
        assert body is not None
        bodies.append(body.group(1))
    old, new = bodies
    count = lambda body: len(re.findall(r"call[^\n]*@py_gen_frame_move\(", body))
    assert count(old) >= 24 * 12
    assert new == old
    assert "gen.save.shared" not in new


def test_shared_generator_suffix_requires_exact_ordered_owners():
    from pcc.frontends.python.codegen.generator_lowering import (
        generator_save_suffix_matches,
    )

    class EqualOwner:
        def __eq__(self, other):
            return True

    a, b, c, f, g, h = [EqualOwner() for _ in range(6)]
    first = [(10, a, f), (11, b, g)]
    longer = first + [(12, c, h)]
    assert generator_save_suffix_matches(first, longer, 10)
    assert generator_save_suffix_matches(longer, first, 10)
    assert generator_save_suffix_matches([], longer, 10)
    assert generator_save_suffix_matches(first, [], 10)
    assert not generator_save_suffix_matches([(10, EqualOwner(), f)], first, 10)
    assert not generator_save_suffix_matches([(10, a, EqualOwner())], first, 10)
    assert not generator_save_suffix_matches([(10, a, None)], first, 10)
    assert not generator_save_suffix_matches([(11, b, g)], first, 10)
    assert not generator_save_suffix_matches([(10, a, f), (12, c, h)], first, 10)
    assert not generator_save_suffix_matches([(10, b, g), (11, a, f)], first, 10)


def _delegated_suffix_program():
    from tests.python.test_generator_delegate_handoff import PROGRAM

    # A nested park after evaluating a managed operand introduces an actual
    # late compiler operand root; simple top-level delegate calls alone only
    # use the initial frame slots and cannot exercise the suffix spine.
    program = PROGRAM.replace("    two = leaf(seed+1)\n",
                              "    two = ([seed], leaf(seed+1))[1]\n")
    program = program.replace("    three = leaf(seed+2)\n",
                              "    three = ([seed], leaf(seed+2))[1]\n")
    assert program != PROGRAM
    return program


def test_delegated_save_suffix_uses_one_ordered_spine(tmp_path, monkeypatch):
    from pcc.frontends.python.pipeline import compile_python
    from pcc.backend.self_backend_parse import parse_self_backend_module
    from pcc.backend.self_backend_verify import verify_parsed_module
    from tests.python.test_generator_resume_gate_reload import _function_blocks

    monkeypatch.setenv("PCC_GENERATOR_SHARED_FRAME_SAVE", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "delegate_suffix.py"
    source.write_text(_delegated_suffix_program())
    output = tmp_path / "delegate_suffix.ll"
    compile_python(str(source), str(output), backend="self",
                   ir_scaffold_mode="on", emit_llvm_only=True)
    text = output.read_text()
    verify_parsed_module(parse_self_backend_module(text))
    _name, blocks, order = _function_blocks(text, r"[^ (]*_work__gen_resume")
    spine = [label for label in order if label.startswith("gen.save.spine.")]
    assert spine
    entries = []
    for label in spine:
        entries.extend(int(index) for line in blocks[label]
                       for index in re.findall(r"@py_gen_frame_move\(ptr [^,]+, i64 (\d+),", line))
    assert entries and entries == list(range(entries[0], entries[0] + len(entries)))
    # A shorter state stops at its own original continuation instead of
    # saving the cells only introduced by a later delegated operand.
    assert len(spine) > 1
    shared = [label for label in order if label.startswith("gen.save.shared.")]
    assert len(shared) == 1

    def successor(label, state):
        rows = blocks[label]
        term = rows[-1]
        if term.startswith("switch i64 "):
            default = re.search(r", label %([-.\w]+) \[", term).group(1)
            cases = {int(number): target for number, target in
                     re.findall(r"i64 (\d+), label %([-.\w]+)", term)}
            return cases.get(state, default)
        if term.startswith("br i1 "):
            cutoff = re.search(r"icmp eq i64 [^,]+, (\d+)$", rows[-2])
            assert cutoff is not None
            targets = re.findall(r"label %([-.\w]+)", term)
            assert len(targets) == 2
            return targets[0 if state == int(cutoff.group(1)) else 1]
        return re.fullmatch(r"br label %([-.\w]+)", term).group(1)

    # Exact ordered plans recorded from this source's unshared-suffix
    # baseline: states 1/2 had no tail, then 3, 10, and 14 cells respectively.
    # Execute all dispatch paths symbolically, not just a representative one.
    targets = []
    for state in range(1, 21):
        label = successor(shared[0], state)
        seen = set()
        path = []
        while label in spine:
            assert label not in seen
            seen.add(label)
            path.extend(int(index) for line in blocks[label]
                        for index in re.findall(r"@py_gen_frame_move\(ptr [^,]+, i64 (\d+),", line))
            label = successor(label, state)
        assert label.startswith("gen.save.suffix.")
        targets.append(label)
        cutoff = 0 if state <= 2 else 3 if state == 3 else 10 if state == 4 else 14
        assert path == entries[:cutoff]
    assert len(set(targets)) == 20


@pytest.mark.integration
@pytest.mark.parametrize("direct", [False, True])
def test_delegated_save_suffix_native_outputs(tmp_path, monkeypatch,
                                              pcc_runtime_archive, direct):
    from pcc.frontends.python.pipeline import compile_python
    from tests.python.test_generator_delegate_handoff import _EXPECTED

    monkeypatch.setenv("PCC_WITH_THREADS", "1")
    if direct:
        monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
        monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "1")
        monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "1")
        monkeypatch.setenv("PCC_DIRECT_INLINE_ERROR_EDGE_CAPTURE", "1")
    else:
        monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "0")
        monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    source = tmp_path / "delegate_suffix.py"
    source.write_text(_delegated_suffix_program())
    for enabled in ("0", "1"):
        monkeypatch.setenv("PCC_GENERATOR_SHARED_FRAME_SAVE", enabled)
        binary = tmp_path / ("delegate_suffix_" + enabled)
        compile_python(str(source), str(binary), backend="self",
                       ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
        for carriers in ("1", "4"):
            ran = run_process_group_timeout([str(binary), carriers], timeout=30)
            assert (ran.returncode, ran.stderr, ran.stdout.strip()) == (0, "", _EXPECTED)


# Active handled exceptions and yielded heap values exercise the conservative
# fallback; tagged/None yields, send, close and resume exercise sharing.
PROGRAM = '''
class Canary:
    def __init__(self, name):
        self.name = name
    def __del__(self):
        print("del", self.name)

def parks(seed):
    a = [seed]
    b = a
    c = [3]
    d = [4]
    e = [5]
    f = [6]
    g = [7]
    h = [8]
    held = Canary("held")
    try:
        x = yield 1
        b.append(x)
        yield None
        print(a, c, d, e, f, g, h)
        yield [2, 3]
        try:
            raise ValueError("inside")
        except ValueError as err:
            yield 4
            print(str(err))
        yield 5
    finally:
        print(held.name, len(a))
        held = None

class Pause:
    def __await__(self):
        yield ["pause"]
        return 7

async def coroutine():
    a = [1]
    b = [2]
    c = [3]
    d = [4]
    e = [5]
    f = [6]
    g = [7]
    h = [8]
    first = await Pause()
    second = await Pause()
    return first+second+a[0]+b[0]+c[0]+d[0]+e[0]+f[0]+g[0]+h[0]

def run():
    t = parks(42)
    print(next(t))
    print(t.send(9))
    print(next(t))
    print(next(t))
    print(next(t))
    t.close()
    q = parks(10)
    print(next(q))
    try:
        q.throw(KeyError("external"))
    except KeyError as err:
        print(str(err))
    coro = coroutine()
    print(coro.send(None))
    print(coro.send(None))
    try:
        coro.send(None)
    except StopIteration as done:
        print("coroutine", done.value)

run()
'''


@pytest.mark.integration
@pytest.mark.parametrize("direct", [False, True])
def test_shared_frame_save_native_outputs_match_inline_and_cpython(
        tmp_path, monkeypatch, pcc_runtime_archive, direct):
    from pcc.frontends.python.pipeline import compile_python

    if direct:
        monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
        monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "1")
        monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "1")
        monkeypatch.setenv("PCC_DIRECT_INLINE_ERROR_EDGE_CAPTURE", "1")
    else:
        monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "0")
        monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    source = tmp_path / "program.py"
    source.write_text(PROGRAM)
    reference = run_process_group_timeout([sys.executable, "-E", "-B", str(source)], timeout=30)
    assert reference.returncode == 0 and not reference.stderr
    for enabled in ("0", "1"):
        monkeypatch.setenv("PCC_GENERATOR_SHARED_FRAME_SAVE", enabled)
        binary = tmp_path / ("program_" + enabled)
        compile_python(str(source), str(binary), backend="self", ir_scaffold_mode="on",
                       runtime_archive=str(pcc_runtime_archive))
        ran = run_process_group_timeout([str(binary)], timeout=30)
        assert (ran.returncode, ran.stderr) == (0, "")
        assert ran.stdout == reference.stdout


def test_shared_frame_save_toggle_is_in_frontend_cache_identity():
    from pcc.frontends.python import compile_cache

    assert "PCC_GENERATOR_SHARED_FRAME_SAVE" in compile_cache._CODEGEN_ENV_NAMES
    assert "PCC_GENERATOR_SHARED_EXIT" in compile_cache._CODEGEN_ENV_NAMES

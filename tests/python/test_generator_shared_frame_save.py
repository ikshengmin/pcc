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

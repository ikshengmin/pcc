"""Generator parks and returns share one owned-local cleanup per function.

A generator activation ends at every park (yield, or a may-park call that
suspends) and at every return. Each of those exits used to release every
owned local inline, so a resume function's release sequences grew with
parks times owned locals: `pcc_gateway.server._proxy_exchange_attempt`
(73 parks) lowered to 21935 release-if-flagged sequences and a 327865-line
function. Now each exit does its own part (save the frame, retire handled
exception scopes, publish the return value) and branches to one shared exit
that releases the union of the owned locals of all exits once.

The release at a shared exit is decided by each slot's runtime owned flag, so
releasing the union is exact: a slot whose flag is clear is not touched, and
the yielded result's slot clears its flag before the branch and is taken
after the cleanup, as before. The execution test compares finalizer order and
output with CPython (Python Language Reference 3.3.1 "__del__", 6.2.9 "Yield
expressions", 8.4 "The try statement"). Each event kills one object: where
several locals die at one exit pcc releases them in name order, not CPython's
order, and CPython's traceback keeps a thrown-into generator's frame alive
until its except block ends; both predate the shared exit.
"""
from __future__ import annotations

import re
import sys

import pytest

from tests.python.process_timeout import run_process_group_timeout


def _generator_source(parks: int) -> str:
    lines = [
        "class Canary:",
        "    def __init__(self, name):",
        "        self.name = name",
        "",
        "def parks():",
        "    held = [1]",
        "    other = [2]",
        "    try:",
        "        raise ValueError('x')",
        "    except ValueError as error:",
        "        caught = [str(error)]",
    ]
    for index in range(parks):
        lines.append(f"        step{index} = [held, {index}]")
        lines.append(f"        yield step{index}")
    lines.extend([
        "    if len(held) > 5:",
        "        return other",
        "    return caught",
        "",
        "for value in parks():",
        "    print(value[1])",
    ])
    return "\n".join(lines) + "\n"


def _resume_body(tmp_path, monkeypatch, parks: int) -> str:
    from pcc.frontends.python.pipeline import compile_python

    monkeypatch.setenv("PCC_LOG", "")
    monkeypatch.setenv("PCC_GENERATOR_SHARED_EXIT", "1")
    source = tmp_path / f"parks_{parks}.py"
    source.write_text(_generator_source(parks))
    output = tmp_path / f"parks_{parks}.ll"
    compile_python(str(source), str(output), backend="self",
                   ir_scaffold_mode="on", emit_llvm_only=True)
    text = output.read_text()
    body = re.search(r"define[^\n]*parks__gen_resume[^\n]*\{\n(.*?)\n\}", text, re.S)
    assert body is not None
    return body.group(1)


def _release_sites(body: str) -> int:
    return len(re.findall(r"^[^ \n][^\n]*\.owned\.release[.0-9]*:", body, re.M))


def test_release_sites_grow_linearly_with_parks(tmp_path, monkeypatch):
    four = _release_sites(_resume_body(tmp_path, monkeypatch, 4))
    eight = _release_sites(_resume_body(tmp_path, monkeypatch, 8))
    sixteen = _release_sites(_resume_body(tmp_path, monkeypatch, 16))
    # Each park adds one owned local (`stepN`: released where it is bound and
    # at the two shared exits) and one hidden handled-exception owner (at the
    # two exits). Doubling the parks doubles the increment; when every park
    # released every owned local inline it quadrupled (16 parks: 693 sites).
    assert sixteen - eight <= 2 * (eight - four) + 4, (four, eight, sixteen)
    assert sixteen <= 5 * 16 + 40, sixteen


def test_every_park_and_return_branches_to_one_shared_exit(tmp_path, monkeypatch):
    body = _resume_body(tmp_path, monkeypatch, 6)
    parks = re.findall(r"^(gen\.shared\.exit[.0-9]*):", body, re.M)
    finishes = re.findall(r"^(gen\.shared\.finish[.0-9]*):", body, re.M)
    assert len(parks) == 1 and len(finishes) == 1, (parks, finishes)
    park_branches = re.findall(r"br label %\"?" + re.escape(parks[0]) + r"\b", body)
    finish_branches = re.findall(r"br label %\"?" + re.escape(finishes[0]) + r"\b", body)
    assert len(park_branches) == 6, len(park_branches)
    # The two returns (a fall-through finish follows a return: none).
    assert len(finish_branches) == 2, len(finish_branches)
    # Linear in parks (see the growth test): 58 here, 400 when inline.
    assert _release_sites(body) <= 5 * 6 + 40


PROGRAM = '''
class Canary:
    def __init__(self, name):
        self.name = name
    def __del__(self):
        print("del", self.name)


def looping(n):
    for i in range(n):
        a = Canary("a" + str(i))
        b = [a, i]
        yield i
        c = Canary("c" + str(i))
        yield len(b)
        c = None
    return "done"


def handler():
    try:
        raise ValueError("x")
    except ValueError as error:
        held = Canary("held")
        yield str(error)
        yield held.name
    yield "after"


def finally_return():
    kept = Canary("kept")
    try:
        yield 1
        return kept.name
    finally:
        late = Canary("late")
        yield 2
        print("finally", late.name)
        late = None


def throw_into():
    t = handler()
    first = next(t)
    try:
        t.throw(KeyError("k"))
    except KeyError as err:
        message = str(err)
    return first, message


def run():
    for value in looping(3):
        print("got", value)
    print("looping done")
    h = handler()
    print(next(h))
    print(next(h))
    print(next(h))
    try:
        next(h)
    except StopIteration:
        print("handler stop")
    f = finally_return()
    print(next(f))
    print(next(f))
    try:
        next(f)
    except StopIteration as stop:
        print("value", stop.value)
    g = looping(5)
    print(next(g))
    g.close()
    print("closed")
    first, message = throw_into()
    print(first)
    print("threw", message)
    print("end")


run()
print("exit")
'''


def test_native_parks_and_returns_release_like_cpython(tmp_path, monkeypatch, pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python

    monkeypatch.setenv("PCC_GENERATOR_SHARED_EXIT", "1")

    source = tmp_path / "shared_exit.py"
    source.write_text(PROGRAM)
    reference = run_process_group_timeout(
        [sys.executable, "-E", "-B", str(source)], timeout=30,
    )
    assert reference.returncode == 0 and not reference.stderr, reference.stderr
    binary = tmp_path / "shared_exit"
    compile_python(str(source), str(binary), backend="self", ir_scaffold_mode="on",
                   runtime_archive=str(pcc_runtime_archive))
    ran = run_process_group_timeout([str(binary)], timeout=30)
    assert (ran.returncode, ran.stderr) == (0, ""), ran.stderr[-2000:]
    assert ran.stdout == reference.stdout

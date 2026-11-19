"""Handler tests run on the dispatch edge, without a jump-only intermediary."""

import re

import pytest


PROGRAM = """def selected(events, first):
    if first:
        events.append(1)
        return TypeError
    events.append(2)
    return ValueError

def route(events):
    try:
        try:
            yield 7
        except selected(events, True):
            events.append(10)
        except selected(events, False):
            events.append(20)
    finally:
        events.append(30)
"""


def test_host_handler_order_and_finally():
    scope = {}
    exec(compile(PROGRAM, "handler_dispatch.py", "exec"), scope)
    for exception, outcome, expected in (
        (None, StopIteration, [30]),
        (TypeError("one"), StopIteration, [1, 10, 30]),
        (ValueError("two"), StopIteration, [1, 2, 20, 30]),
        (KeyError("out"), KeyError, [1, 2, 30]),
    ):
        events = []
        g = scope["route"](events)
        assert next(g) == 7 and events == []
        with pytest.raises(outcome) as result:
            if exception is None:
                next(g)
            else:
                g.throw(exception)
        if outcome is KeyError:
            assert result.value is exception
        assert events == expected


def test_generator_handler_tests_are_not_reached_through_forwarding_blocks(tmp_path):
    from pcc.frontends.python.pipeline import compile_python
    from tests.owned_ir_validation import verify_ir_text

    path = tmp_path / "handler_dispatch.py"
    path.write_text(PROGRAM, encoding="utf-8")
    output = path.with_suffix(".ll")
    compile_python(str(path), str(output), emit_llvm_only=True, python_library=True,
                   backend="self", ir_scaffold_mode="on", target_triple="x86_64-unknown-linux-gnu")
    text = output.read_text(encoding="utf-8")
    verify_ir_text(text)
    match = re.search(r"(?ms)^define [^\n]*@user_handler_dispatch_route__gen_resume\([^\n]*\n.*?^}", text)
    assert match is not None
    body = match.group(0)
    # Both dynamic choices and the enclosing finally must still be emitted.
    assert re.search(r"(?m)^except\.body\.0\.[\w.$-]+:", body)
    assert re.search(r"(?m)^except\.body\.1\.[\w.$-]+:", body)
    assert "@py_handled_context_push(" in body and "@py_handled_context_pop(" in body
    assert "@py_current_exception(" in body
    assert not re.search(r"(?m)^\s*br label %except\.test\.", body)
    # A miss on the first handler must start testing the next handler, not
    # just jump again. Ignore PHIs: they are not the dynamic class test.
    next_tests = re.findall(r"(?m)^except\.next\.\d+\.[\w.$-]+:\n((?:[ \t][^\n]*\n)+)", body)
    assert next_tests
    for block in next_tests:
        instructions = [line.strip() for line in block.splitlines() if " = phi " not in line]
        assert not (len(instructions) == 1 and instructions[0].startswith("br label "))

"""Run provider-using callbacks through the actual compiler and native ABI."""
from pathlib import Path

from tests.python.owned_regression_support import (
    assert_owned_program,
    explicit_owned_runtime,
)


PROGRAM = '''from pcc.ir.compat import ir
import gc
WORD = ir.IntType(64)

class Frame:
    def __init__(self, first, second):
        self.args = [first, second]

class Recorder:
    def __init__(self, tag, events):
        self.tag = tag
        self.events = events
    def call(self, target, args, name):
        self.events.append(self.tag + ":" + name)
        return target + args[0] + int(str(args[1]))

class Forward:
    def __init__(self, events):
        self.builder = Recorder("first", events)
        self.runtime = {"target": 100}
    def callbacks(self, frame):
        load = lambda index, label: self.builder.call(
            self.runtime["target"], [frame.args[1], ir.Constant(WORD, index)], name=label)
        captures = lambda: frame.args[0]
        frame = Frame(20, 21)
        return load, captures, frame

def use(load, captures, frame, forward, events):
    assert load(1, "a") == 122
    assert captures() == 20
    forward.builder = Recorder("second", events)
    frame.args[0] = 30
    frame.args[1] = 31
    gc.collect()
    assert load(2, "b") == 133
    assert captures() == 30
    assert events == ["first:a", "second:b"]

def remember(events, value):
    events.append(value)
    return ir.Constant(WORD, value)

def defaults(events):
    value = 7
    callback = lambda saved=remember(events, value): (str(saved), value)
    value = 9
    return callback

class Ordinary:
    def Constant(self, kind, value):
        return value + 1

def shadow(ir):
    return lambda value: ir.Constant(None, value)

def cells():
    value = 5
    callback = lambda: ir.Constant(WORD, value)
    def clear():
        nonlocal value
        del value
    return callback, clear

def main():
    events = []
    forward = Forward(events)
    load, captures, frame = forward.callbacks(Frame(0, 1))
    use(load, captures, frame, forward, events)
    another, other_captures, other_frame = Forward([]).callbacks(Frame(2, 3))
    assert another(3, "c") == 124 and other_captures() == 20
    nested = lambda: load(4, "nested")
    assert nested() == 135
    defaults_seen = []
    callback = defaults(defaults_seen)
    assert defaults_seen == [7]
    assert callback() == ("7", 9)
    assert callback() == ("7", 9)
    assert defaults_seen == [7]
    assert shadow(Ordinary())(41) == 42
    read, clear = cells()
    assert str(read()) == "5"
    clear()
    try:
        read()
    except NameError:
        pass
    else:
        raise AssertionError("deleted captured cell stayed bound")
    print("LAMBDA_CAPTURE_OWNER_OK")
main()
'''


def test_provider_callbacks_preserve_cells_defaults_and_invocation_effects(
    request, monkeypatch, python_program_compiler, explicit_owned_runtime, tmp_path, capfd,
):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("PYTHONPATH", str(root))
    archive = explicit_owned_runtime
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(
        PROGRAM, "LAMBDA_CAPTURE_OWNER_OK\n", tmp_path,
        python_program_compiler, mode, archive, capfd,
    )

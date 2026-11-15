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

def take_constant(*, value, later=None, **extras):
    gc.collect()
    return value

def none_payload_type(events):
    events.append("type")
    gc.collect()
    return WORD

def fail_after_none(events):
    events.append("later")
    gc.collect()
    raise ValueError("after none constant")

def none_payload_callbacks(events):
    # The constructor is an actual keyword-call operand, not merely a local
    # assignment. Its slot stays owned across the callee's collection.
    read = lambda: take_constant(value=ir.Constant(none_payload_type(events), None))
    fail = lambda: take_constant(
        value=ir.Constant(none_payload_type(events), None),
        later=fail_after_none(events))
    return read, fail

def check_none_payload():
    events = []
    read, fail = none_payload_callbacks(events)
    first = read()
    second = read()
    assert first is not second
    assert first.value is None and second.value is None
    assert str(first) == "zeroinitializer" and str(first.type) == "i64"
    assert events == ["type", "type"]
    try:
        fail()
    except ValueError as error:
        assert str(error) == "after none constant"
    else:
        raise AssertionError("lost later None-constant exception")
    assert events == ["type", "type", "type", "later"]
    gc.collect()
    assert str(first) == "zeroinitializer"
    assert read().value is None
    assert events == ["type", "type", "type", "later", "type"]

def call_operand(events, label, value):
    events.append(label)
    gc.collect()
    return value

def fail_call_operand(events):
    events.append("fail")
    gc.collect()
    raise ValueError("later builder operand")

class CleanupProbe:
    def __init__(self, events):
        self.events = events
    def __del__(self):
        # A handled cleanup exception must not replace the provider error
        # selected by the new sink check. No error helper is mocked here.
        try:
            raise ValueError("cleanup exception")
        except ValueError:
            self.events.append("cleanup")

def check_builder_call_callbacks():
    events = []
    module = ir.Module(name="builder_call_owners")
    target = ir.Function(module, ir.FunctionType(WORD, [WORD, WORD]), name="callee")
    empty_target = ir.Function(module, ir.FunctionType(ir.VoidType(), []), name="empty_callee")
    caller = ir.Function(module, ir.FunctionType(WORD, []), name="caller")
    builder = ir.IRBuilder(caller.append_basic_block("entry"))
    # Same call2 lambda-body result boundary as the real Stage1 failure.
    literal = lambda index, label: builder.call(
        call_operand(events, "target", target),
        [call_operand(events, "first", ir.Constant(WORD, 20)), ir.Constant(WORD, index)],
        name=call_operand(events, "name", label))
    dynamic = lambda arguments, label: builder.call(
        call_operand(events, "target", target),
        call_operand(events, "arguments", arguments),
        name=call_operand(events, "name", label))
    fail = lambda: builder.call(
        call_operand(events, "target", target),
        [call_operand(events, "first", ir.Constant(WORD, 20)), fail_call_operand(events)],
        name=call_operand(events, "name", "unreached"))
    void_call = lambda: builder.call(empty_target, [])
    first = literal(22, "first")
    second = literal(23, "second")
    assert first is not second
    assert str(first.type) == "i64" and str(second.type) == "i64"
    assert events == ["target", "first", "name", "target", "first", "name"]
    arguments = [ir.Constant(WORD, 24), ir.Constant(WORD, 25)]
    third = dynamic(arguments, "third")
    assert str(third.type) == "i64"
    assert events[-3:] == ["target", "arguments", "name"]
    try:
        fail()
    except ValueError as error:
        assert str(error) == "later builder operand"
    else:
        raise AssertionError("lost later builder operand exception")
    assert events[-3:] == ["target", "first", "fail"]
    assert "unreached" not in str(module)
    # The new result sink must route a real provider exception through the
    # existing output/capture cleanup, not return NULL as a successful value.
    invalid = lambda unused: builder.call(target, arguments, name=None)
    cleanup_events = []
    try:
        invalid(CleanupProbe(cleanup_events))
    except TypeError:
        cleanup_events.append("provider error")
    else:
        raise AssertionError("lost provider call exception")
    gc.collect()
    gc.collect()
    assert cleanup_events.count("provider error") == 1
    assert cleanup_events.count("cleanup") == 1
    void_first = void_call()
    void_second = void_call()
    assert void_first is not void_second
    assert str(void_first.type) == "void" and str(void_second.type) == "void"
    gc.collect()
    assert str(first.type) == "i64" and "first" in str(first)
    recovered = literal(26, "recovered")
    assert str(recovered.type) == "i64" and "recovered" in str(recovered)
    # Both the tuple-literal and no-caller-sink expression keep their owner.
    standalone = builder.call(target, (ir.Constant(WORD, 27), ir.Constant(WORD, 28)), "standalone")
    gc.collect()
    assert str(standalone.type) == "i64" and "standalone" in str(standalone)
    assert "callee" in str(module)

def check_constant_obj_callbacks():
    # Unannotated captured/parameter objects exercise Constant_obj, including
    # the real lambda-body and tuple-item ownership boundaries.
    def captured(value):
        return lambda: ir.Constant(WORD, value)
    direct = lambda kind, value: ir.Constant(kind, value)
    paired = lambda kind, value: (ir.Constant(kind, value), value)
    read = captured(41)
    first = read()
    second = read()
    assert first is not second
    assert str(first) == "41" and str(second) == "41"
    value = direct(WORD, 42)
    pair = paired(WORD, 43)
    assert str(value) == "42" and str(pair[0]) == "43" and pair[1] == 43
    gc.collect()
    assert str(first) == "41" and str(value) == "42"
    # A real provider formatting failure returns NULL with a pending native
    # TypeError. The sink must select exception-preserving output cleanup.
    invalid = lambda unused: ir.Constant(WORD, "not an integer")
    cleanup_events = []
    try:
        invalid(CleanupProbe(cleanup_events))
    except TypeError:
        cleanup_events.append("constant provider error")
    else:
        raise AssertionError("lost Constant_obj provider exception")
    gc.collect()
    gc.collect()
    assert cleanup_events.count("constant provider error") == 1
    assert cleanup_events.count("cleanup") == 1
    # A later argument can fail after result publication; it must not erase
    # the later error or corrupt an earlier independent result.
    events = []
    later = lambda item: take_constant(
        value=ir.Constant(WORD, item), later=fail_after_none(events))
    try:
        later(44)
    except ValueError as error:
        assert str(error) == "after none constant"
    else:
        raise AssertionError("lost later Constant_obj operand exception")
    assert events == ["later"]
    gc.collect()
    assert str(first) == "41" and str(read()) == "41"
    assert str(direct(WORD, 45)) == "45"

def main():
    check_constant_obj_callbacks()
    check_builder_call_callbacks()
    check_none_payload()
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

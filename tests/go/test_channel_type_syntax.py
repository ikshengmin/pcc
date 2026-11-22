"""The original benchmark's make channel operand is syntax, not allocation."""
import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from pcc.frontends.go.syntax import GoParser


@pytest.mark.parametrize("type_source,directions", (
    ("chan bool", ("both",)),
    ("chan<- bool", ("send",)),
    ("<-chan bool", ("receive",)),
    ("<-chan<-chan bool", ("receive", "receive")),
))
def test_make_keeps_channel_type_and_direction(type_source, directions):
    code = f"package main\nfunc main() {{ _ = make({type_source}, 2) }}\n"
    unit = GoParser().parse(code, "channel.go")
    operand = unit.functions[-1].body[0].exprs[0].args[0].value
    for direction in directions:
        assert operand.direction == direction
        operand = operand.element
    assert operand == "bool"
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(code, "channel.go")
    assert caught.value.code == "go.unsupported.channel-storage"


def test_receive_of_channel_conversion_is_not_a_directional_conversion():
    unit = GoParser().parse(
        "package main\nfunc main() { _ = <-chan bool(c); _ = (<-chan bool)(c) }\n",
        "channel.go",
    )
    first, second = [stmt.exprs[0] for stmt in unit.functions[-1].body]
    assert first.kind == "receive" and first.args[0].callee.value.direction == "both"
    assert second.kind == "call" and second.callee.value.direction == "receive"


@pytest.mark.parametrize("arguments,diagnostic", (
    ("chan bool, -1", "size must be a nonnegative int"),
    ("chan bool, 1.5", "size must be a nonnegative int"),
    ("chan bool, false", "size must be an integer"),
    ("chan bool, 1, 2", "requires a type and at most one size"),
    ("chan Missing", "undefined type"),
))
def test_invalid_channel_make_has_a_source_error(arguments, diagnostic):
    with pytest.raises(GoDiagnostic, match=diagnostic) as caught:
        compile_go(f"package main\nfunc main() {{ _ = make({arguments}) }}\n", "channel.go")
    assert not isinstance(caught.value, UnsupportedGo)


def test_context_shape_channel_direction_is_kept_in_interface_metadata():
    source = (
        "package main\n"
        "type Context interface { Done() <-chan struct{}; Notify(target chan<- bool) }\n"
        "type Empty struct{}\n"
        "func (Empty) Done() <-chan struct{} { return nil }\n"
        "func New() chan bool { return nil }\n"
        "func main() {}\n"
    )
    unit = GoParser().parse(source, "context_shape.go")
    done, notify = unit.types[0].type.elements
    assert (done.result.direction, notify.parameters[0].direction) == ("receive", "send")
    assert done.result.element.fields == []
    assert [fn.result.direction for fn in unit.functions[:-1]] == ["receive", "both"]
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(source, "context_shape.go")
    assert caught.value.code == "go.unsupported.channel-storage"

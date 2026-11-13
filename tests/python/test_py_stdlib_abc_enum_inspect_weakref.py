from __future__ import annotations

from pcc.stdlib import abc
from pcc.stdlib import enum
from pcc.stdlib import inspect
from pcc.stdlib import weakref


def test_abc_abstractmethod_register_and_cache_token():
    class Base(abc.ABC):
        @abc.abstractmethod
        def f(self):
            pass

    assert "f" in Base.__abstractmethods__

    class Impl:
        def f(self):
            return 1

    before = abc.get_cache_token()
    Base.register(Impl)
    after = abc.get_cache_token()
    assert after == before + 1
    assert isinstance(Impl(), Base)


def test_enum_auto_value_lookup_iteration_and_unique():
    @enum.unique
    class Color(enum.Enum):
        RED = enum.auto()
        BLUE = enum.auto()

    assert Color.RED.name == "RED"
    assert Color.RED.value == 1
    assert Color(2) is Color.BLUE
    assert list(Color) == [Color.RED, Color.BLUE]
    assert Color.__members__["RED"] is Color.RED


def test_intenum_behaves_like_int_value():
    class Code(enum.IntEnum):
        OK = 200

    assert int(Code.OK) == 200
    assert Code(200) is Code.OK


def test_inspect_signature_predicates_members_and_docs():
    def f(a, b: int) -> str:
        " doc "
        return str(a + b)

    sig = inspect.signature(f)
    assert list(sig.parameters.keys()) == ["a", "b"]
    assert inspect.isfunction(f)
    assert inspect.isroutine(f)
    assert inspect.getdoc(f) == "doc"

    class C:
        x = 1

    assert inspect.isclass(C)
    assert ("x", 1) in inspect.getmembers(C)


def test_weakref_provider_holds_only_true_weak_containers():
    """The provider holds CPython's WeakSet; builtin dispatch owns ref/proxy.

    Its former strong-reference stand-ins (proxy returning its referent,
    dictionaries and finalize holding strong references) are gone: a name the
    provider lacks fails instead of silently keeping objects alive.
    """
    import gc

    class Box:
        pass

    b = Box()
    ws = weakref.WeakSet([b])
    assert b in ws and len(ws) == 1
    del b
    gc.collect()
    assert len(ws) == 0
    for stand_in in ("proxy", "WeakValueDictionary", "WeakKeyDictionary", "finalize"):
        assert not hasattr(weakref, stand_in), stand_in

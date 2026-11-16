"""Execute inherited callback lookup on a source-matched owned runtime."""
from tests.python.owned_regression_support import assert_owned_program, explicit_owned_runtime

PROGRAM = '''import gc

events = []
def record(value):
    events.append('argument')
    return value

class Left:
    def declare_flag(self):
        self.flag = True
class OtherLeft:
    def declare_prefix(self):
        self.first_prefix = True
        self.second_prefix = True
class Base:
    def declare_builder(self, builder):
        self.builder = builder
    def callback(self, expected):
        return lambda: self.builder == record(expected)
class Sub(Left, Base):
    pass
class Other(OtherLeft, Base):
    pass
class PropertyOwner(Left, Base):
    @property
    def builder(self):
        return self.live_builder
    @builder.setter
    def builder(self, value):
        self.live_builder = value

def check(owner):
    setattr(owner, 'builder', 41)
    callback = owner.callback(41)
    assert callback()
    setattr(owner, 'builder', 71)
    gc.collect()
    assert not callback()
    assert owner.callback(71)()

check(Base())
check(Sub())
check(Other())
check(PropertyOwner())

class RaisingOwner(Base):
    @property
    def builder(self):
        raise ValueError('getter-before-arguments')
class Unrelated:
    pass
before = len(events)
raising = RaisingOwner().callback(41)
try:
    raising()
except ValueError as error:
    assert str(error) == 'getter-before-arguments'
else:
    raise AssertionError('lost getter error')
assert len(events) == before
missing = Base.callback(Unrelated(), 41)
try:
    missing()
except AttributeError:
    pass
else:
    raise AssertionError('lost missing builder error')
assert len(events) == before
gc.collect()
print('DYNAMIC_SELF_LAYOUT_OK')
'''


def test_dynamic_self_layout_and_error_order_native(
    request, monkeypatch, python_program_compiler, explicit_owned_runtime, tmp_path, capfd,
):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(PROGRAM, "DYNAMIC_SELF_LAYOUT_OK\n", tmp_path,
                         python_program_compiler, mode, explicit_owned_runtime, capfd,
                         provenance_probe="2")

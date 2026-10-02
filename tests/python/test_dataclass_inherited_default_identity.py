"""Inherited ordinary dataclass defaults preserve identity and evaluation count.

Native cases use the owned self backend without libpython, with separate
pcc0 and pcc1 fixture parameters. Every emitted binary executes under GC0-4.
PCC_RUNTIME_ARCHIVE must explicitly select the matching immutable archive;
PCC_NO_AUTO_PCC1=1 and PCC_TEST_NO_NATIVE_PROVISIONING=1 are the suite gates.
"""

import pytest

from tests.python.owned_regression_support import (
    assert_owned_program,
    assert_reference_program,
    explicit_owned_runtime,
)


TUPLE = r"""from dataclasses import dataclass
@dataclass
class Base:
    fields: tuple = ()
@dataclass
class Child(Base):
    pass
def main():
    assert Child().fields == ()
    print('INHERITED_TUPLE_DEFAULT_OK')
main()
"""


IDENTITY = r"""from dataclasses import dataclass, field
events = []
factory_events = []
class Token:
    pass
def make(label):
    events.append(label)
    return Token()
def make_rows():
    factory_events.append('rows')
    return []
@dataclass
class Base:
    required: object
    fields: tuple = ()
    token: object = make('token')
    kind: object = Token
    optional: object = None
    number: int = 31
    wrapped: object = field(default=make('wrapped'))
    rows: object = field(default_factory=make_rows)
original_token = Base.token
original_wrapped = Base.wrapped
fields = ('shadow',)
token = 'shadow'
kind = 'shadow'
Base.token = Token()
@dataclass
class Child(Base):
    child: bool = True
def replacement_init(self):
    raise AssertionError('inherited defaults used rebound __init__')
Child.__init__ = replacement_init
@dataclass
class Grandchild(Child):
    grandchild: bool = True
def main():
    assert events == ['token', 'wrapped']
    assert factory_events == []
    first = Grandchild('first')
    constructor = Grandchild
    second = constructor('second')
    assert first.fields == ()
    assert first.fields is second.fields
    assert first.token is original_token
    assert second.token is original_token
    assert first.wrapped is original_wrapped
    assert second.wrapped is original_wrapped
    assert first.kind is Token
    assert first.optional is None
    assert first.number == 31
    assert first.child and first.grandchild
    assert first.rows is not second.rows
    assert events == ['token', 'wrapped']
    assert factory_events == ['rows', 'rows']
    supplied = Token()
    explicit = constructor('explicit', token=supplied, optional=supplied, rows=None)
    assert explicit.token is supplied
    assert explicit.optional is supplied
    assert explicit.rows is None
    assert factory_events == ['rows', 'rows']
    try:
        constructor()
        raise AssertionError('inherited required field became optional')
    except TypeError:
        pass
    print('INHERITED_DEFAULT_IDENTITY_OK')
main()
"""


FACTORY_ERROR = r"""from dataclasses import dataclass, field
events = []
def make():
    events.append('factory')
    if len(events) == 1:
        raise ValueError('factory')
    return []
@dataclass
class Base:
    fields: tuple = ()
    rows: object = field(default_factory=make)
@dataclass
class Child(Base):
    pass
def main():
    assert events == []
    try:
        Child()
        raise AssertionError('factory error was lost')
    except ValueError:
        pass
    second = Child()
    assert second.fields == ()
    assert second.rows == []
    explicit = Child(rows=None)
    assert explicit.rows is None
    assert events == ['factory', 'factory']
    print('INHERITED_FACTORY_ERROR_OK')
main()
"""


PROGRAMS = {
    'tuple': (
        TUPLE,
        'INHERITED_TUPLE_DEFAULT_OK\n'
    ),
    'identity': (
        IDENTITY,
        'INHERITED_DEFAULT_IDENTITY_OK\n'
    ),
    'factory_error': (
        FACTORY_ERROR,
        'INHERITED_FACTORY_ERROR_OK\n'
    ),
}


@pytest.mark.parametrize("case", tuple(PROGRAMS))
def test_inherited_default_reference(case, tmp_path):
    program, expected = PROGRAMS[case]
    assert_reference_program(program, expected, tmp_path)


@pytest.mark.integration
@pytest.mark.parametrize("case", tuple(PROGRAMS))
def test_inherited_default_native_five_gc(
    case,
    tmp_path,
    explicit_owned_runtime,
    python_program_compiler,
    request,
    capfd,
):
    program, expected = PROGRAMS[case]
    assert_owned_program(
        program,
        expected,
        tmp_path,
        python_program_compiler,
        request.node.callspec.params["python_program_compiler"],
        explicit_owned_runtime,
        capfd,
        provenance_probe='2',
    )

"""Native text conversions own outputs before checks or operand disposal."""
from __future__ import annotations
import textwrap
import pytest
from tests.python.test_shared_call_binding import _emit
from tests.python.test_slot_call_subscript_producers import _assert_immediate_publication
from tests.python.owned_regression_support import assert_owned_program, explicit_owned_runtime


@pytest.mark.parametrize('builtin', ('repr', 'ascii'))
@pytest.mark.parametrize('site', ('argument', 'default', 'return', 'later-error'))
def test_text_conversion_publishes_before_cleanup(builtin, site):
    prefix = ('from pcc.unsafe import null\n'
              'def take(*, value, later=None):\n    return value\n'
              'def fail():\n    raise ValueError("later")\n')
    expression = builtin + '(item)'
    if site == 'default':
        body = '    def target(value=' + expression + '):\n        return value\n    return target()\n'
    elif site == 'return':
        body = '    return ' + expression + '\n'
    elif site == 'later-error':
        body = '    return take(value=' + expression + ', later=fail())\n'
    else:
        body = '    return take(value=' + expression + ')\n'
    text = _emit(prefix + 'def probe(item):\n' + body)
    _assert_immediate_publication(text, 'py_obj_' + builtin)


PROGRAM = textwrap.dedent('''\
    import gc
    events = []
    class Token:
        def __repr__(self):
            gc.collect()
            events.append('repr')
            return 'TOKEN'
        def __del__(self):
            events.append('disposed')
    class Bad:
        def __repr__(self):
            gc.collect()
            raise ValueError('representation')
    def take(*, value, later=None):
        gc.collect()
        return value
    def later():
        gc.collect()
        events.append('later')
        raise ValueError('later')
    def returned(item):
        return repr(item)
    def main():
        assert take(value=repr(Token())) == 'TOKEN'
        gc.collect()
        assert events.count('disposed') == 1
        assert take(value=ascii(Token())) == 'TOKEN'
        gc.collect()
        assert events.count('disposed') == 2
        token = Token()
        assert returned(token) == 'TOKEN'
        def target(value=repr(token)):
            gc.collect()
            return value
        assert target() == 'TOKEN'
        large = 1234567890123456789012345678901234567890
        assert take(value=repr(large)) == '1234567890123456789012345678901234567890'
        try:
            take(value=repr(Token()), later=later())
        except ValueError:
            pass
        else:
            raise AssertionError('missing later exception')
        gc.collect()
        assert events.count('disposed') == 3
        before = events.count('later')
        try:
            take(value=ascii(Bad()), later=later())
        except ValueError:
            pass
        else:
            raise AssertionError('missing representation exception')
        assert events.count('later') == before
        print('TEXT_CONVERSIONS_OK')
    main()
''')


@pytest.mark.integration
@pytest.mark.parametrize('python_program_compiler', ('pcc0', 'pcc1'), indirect=True)
def test_text_conversions_native_five_gc(python_program_compiler, request,
                                         explicit_owned_runtime, tmp_path, capfd):
    mode = request.node.callspec.params['python_program_compiler']
    assert_owned_program(PROGRAM, 'TEXT_CONVERSIONS_OK\n', tmp_path, python_program_compiler,
                         mode, explicit_owned_runtime, capfd, provenance_probe='2')
    assert (tmp_path/'compiler-wrapper.stderr').read_text() == ''

"""An ordinary possibly-throwing call must not skip an active local's D1."""
import pytest
from pcc.frontends.cpp import CppParser, CppDiagnostic

COMMON = ('int state=0; int unknown();'
          'struct Guard{Guard(){state=1;} ~Guard(){state=2;}};'
          'struct H{int n;H(int v):n(v){} H(const H&h):n(h.n){}};')


@pytest.mark.parametrize('source', [
    'H make(){Guard g;return H(42);}',
    'H make(){unknown();return H(42);}',
    'H make(int flag){Guard g;if(flag)return H(42);return H(43);}',
    'H make(const H* p){Guard g;return *p;}',
])
def test_closed_effects_or_no_outstanding_cleanup(source):
    CppParser('x86_64-unknown-linux-gnu').parse(COMMON + source)


@pytest.mark.parametrize('source', [
    'H make(){Guard g;unknown();return H(42);}',
    'H make(){unknown();Guard g;return H(42);}',
    'H make(){Guard g;return H(unknown());}',
    'H make(){Guard g;if(unknown())return H(42);return H(43);}',
    'H make(){Guard g;while(unknown()){}return H(42);}',
    'H make(){int n=unknown();Guard g;return H(n);}',
])
def test_unwinding_is_not_faked_for_an_unknown_call(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser('x86_64-unknown-linux-gnu').parse(COMMON + source)
    assert error.value.code=='cpp.unsupported.class-result-effects'


def test_forward_through_foreign_result_with_live_guard_is_not_owned():
    with pytest.raises(CppDiagnostic) as error:
        CppParser('x86_64-unknown-linux-gnu').parse(COMMON+'H other(); H f(){Guard g;return other();}')
    assert error.value.code=='cpp.unsupported.class-result-lifetime'


def test_default_argument_call_is_visible_to_a_live_cleanup():
    source = ('int unknown(); struct Guard{Guard(){}~Guard(){}};'
              'struct H{int n;H(int v=unknown()):n(v){}H(const H&h):n(h.n){}};'
              'H make(){Guard g;return H();}')
    with pytest.raises(CppDiagnostic) as error:
        CppParser('x86_64-unknown-linux-gnu').parse(source)
    assert error.value.code=='cpp.unsupported.class-result-effects'

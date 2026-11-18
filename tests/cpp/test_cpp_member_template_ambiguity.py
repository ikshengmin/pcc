"""Pure overload-owner controls; source-level/ABI acceptance remains separate."""
import itertools
import pytest
from pcc.frontends.cpp.sema import Symbol, resolve_overload
from pcc.frontends.cpp.types import ArithmeticTypes, ReferenceType, ConstType
from pcc.frontends.cpp.diagnostics import CppDiagnostic


def candidate(parameters, template=True):
    value = Symbol('f', 'function', None, dtype='int', parameters=parameters)
    if template:
        value.template_origin = Symbol('f', 'function', None)
    return value


def choose(candidates, arguments=('int', 'int'), lvalues=None, scope=True):
    group = Symbol('f', 'function', None)
    group.overloads = list(candidates)
    return resolve_overload(group, list(arguments), ArithmeticTypes('x86_64-unknown-linux-gnu'), None,
                            lvalues=lvalues, distinguish_template_ambiguity=scope)


@pytest.mark.parametrize('extra', [(), (('long','long'),), (('int','long'),)])
def test_crossed_conversions_are_ambiguous_even_with_dominated_or_tied_rivals(extra):
    candidates = [candidate(p) for p in [('int','long'), ('long','int'), *extra]]
    for order in itertools.permutations(candidates):
        with pytest.raises(CppDiagnostic) as err:
            choose(order)
        assert err.value.code == 'cpp.ambiguous-overload'


def test_indistinguishable_template_references_still_require_partial_ordering():
    # T& versus const T&, after deduction from a const int lvalue, both have
    # this exact concrete signature; ranking must not invent a preference.
    candidates = [candidate((ReferenceType(ConstType('int')),)) for _ in range(2)]
    with pytest.raises(CppDiagnostic) as err:
        choose(candidates, (ConstType('int'),), [True])
    assert err.value.code == 'cpp.unsupported.template-ordering'


def test_tie_with_dominated_candidate_is_still_unresolved():
    cs = [candidate(('int','int')), candidate(('int','int')), candidate(('long','long'))]
    for order in itertools.permutations(cs):
        with pytest.raises(CppDiagnostic) as err:
            choose(order)
        assert err.value.code == 'cpp.unsupported.template-ordering'


def test_unique_conversion_winner_and_nontemplate_preference_survive():
    winner = candidate(('int','int'))
    for order in itertools.permutations([winner, candidate(('int','long')), candidate(('long','int'))]):
        assert choose(order) is winner
    ordinary = candidate(('int','int'), False)
    for order in itertools.permutations([ordinary, candidate(('int','int')), candidate(('int','int'))]):
        assert choose(order) is ordinary


def test_default_ordinary_resolver_diagnostic_is_unchanged():
    with pytest.raises(CppDiagnostic) as err:
        choose([candidate(('int','long')), candidate(('long','int'))], scope=False)
    assert err.value.code == 'cpp.unsupported.template-ordering'

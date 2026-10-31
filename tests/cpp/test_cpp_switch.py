"""C++ switch: promoted/nominal conditions, checked cases, scopes and jumps.

Original N4659 [stmt.switch], [stmt.dcl]/3, [stmt.break], [stmt.cont] cases.
No class conversion, dependent switch or nonlocal-label capability is implied.
https://www.open-std.org/JTC1/SC22/WG21/docs/papers/2017/n4659.pdf
"""
import pytest

from pcc.frontends.cpp import CppDiagnostic
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_class_execution import execute_and_record


CASES = (
    ('matching-case', 'int cpp_test_entry(){int n=2;switch(n){case 1:return 1;case 2:return 42;default:return 3;}}'),
    ('default', 'int cpp_test_entry(){switch(7){case 1:return 1;default:return 42;}}'),
    ('no-match-no-default', 'int cpp_test_entry(){int x=42;switch(7){case 1:x=1;}return x;}'),
    ('fallthrough', 'int cpp_test_entry(){int x=0;switch(1){case 1:x+=17;case 2:x+=25;break;default:x=1;}return x;}'),
    ('grouped-labels', 'int cpp_test_entry(){switch(3){case 1:case 2:case 3:return 42;default:return 1;}}'),
    ('default-in-middle-fallthrough', 'int cpp_test_entry(){int x=0;switch(7){case 1:x=1;break;'
     'default:x=17;case 2:x+=25;}return x;}'),
    ('condition-evaluated-once', 'int value(int*n){*n+=1;return 2;}int cpp_test_entry(){int n=0;int x=0;'
     'switch(value(&n)){case 2:x=42;break;default:x=1;}if(n!=1)return 2;return x;}'),
    ('bool-promotes-to-int', 'int cpp_test_entry(){bool flag=true;switch(flag){case 0:return 1;case 1:return 42;}return 2;}'),
    ('signed-small-promotion', 'int cpp_test_entry(){signed char n=-1;switch(n){case -1:return 42;default:return 1;}}'),
    ('unsigned-small-promotion', 'int cpp_test_entry(){unsigned char n=255;switch(n){case 255:return 42;default:return 1;}}'),
    ('wide-unsigned-condition', 'int cpp_test_entry(){unsigned long long n=0xfedcba9876543210ULL;'
     'switch(n){case 0xfedcba9876543210ULL:return 42;case 0x76543210ULL:return 1;default:return 2;}}'),
    ('signed-wide-condition', 'int cpp_test_entry(){long long n=-5000000042LL;'
     'switch(n){case -5000000042LL:return 42;default:return 1;}}'),
    ('unscoped-enum-promotion', 'enum Mode{Read=1,Write=42};int cpp_test_entry(){Mode n=Write;'
     'switch(n){case Read:return 1;case 42:return 42;default:return 2;}}'),
    ('scoped-enum-nominal', 'enum class Mode:unsigned char{Read=1,Write=42};int cpp_test_entry(){Mode n=Mode::Write;'
     'switch(n){case Mode::Read:return 1;case Mode::Write:return 42;}return 2;}'),
    ('const-integral-case-proof', 'int cpp_test_entry(){const int key=6*7;switch(42){case key:return 42;default:return 1;}}'),
    # Keep the original declaration/use and provide its definition too: the
    # unevaluated-at-runtime RHS is still potentially evaluated for ODR use.
    ('constant-case-short-circuit', 'int runtime();int cpp_test_entry(){switch(0){case false&&runtime():return 42;default:return 1;}}'
     'int runtime(){return 1;}'),
    ('case-explicit-enum-cast', 'enum class K{Answer=42};int cpp_test_entry(){switch(42){'
     'case static_cast<int>(K::Answer):return 42;default:return 1;}}'),
    ('break-leaves-only-switch', 'int cpp_test_entry(){int x=0;for(int n=0;n<3;++n){'
     'switch(n){case 0:x+=14;break;default:x+=14;break;}}return x;}'),
    ('continue-targets-enclosing-loop', 'int cpp_test_entry(){int x=0;for(int n=0;n<4;++n){'
     'switch(n){case 1:continue;default:x+=14;} }return x;}'),
    ('nested-switch-has-independent-cases', 'int cpp_test_entry(){int x=0;switch(1){case 1:'
     'switch(1){case 1:x=42;break;default:x=1;}break;default:x=2;}return x;}'),
    ('case-block-local-lifetime', 'int cpp_test_entry(){int x=0;switch(1){case 1:{int n=42;x=n;break;}'
     'case 2:{int n=1;x=n;break;}}return x;}'),
    ('uninitialized-scalar-can-be-entered', 'int cpp_test_entry(){switch(2){int value;case 1:value=1;break;'
     'case 2:value=42;return value;}return 3;}'),
    ('uninitialized-array-can-be-entered', 'int cpp_test_entry(){switch(2){int a[2];case 1:return 1;'
     'case 2:a[0]=17;a[1]=25;return a[0]+a[1];}return 3;}'),
    ('unbraced-switch-body', 'int cpp_test_entry(){switch(42)case 42:return 42;return 1;}'),
    ('no-label-body-is-unreachable', 'int cpp_test_entry(){int x=0;switch(++x){int y=++x;}'
     'if(x!=1)return 1;return 42;}'),
    ('switch-body-binding-does-not-leak', 'int cpp_test_entry(){int value=42;switch(1){case 1:{int value=1;break;}}return value;}'),
)

INVALID = (
    ('int f(){case 1:return 0;}', 'cpp.case-outside-switch'),
    ('int f(){default:return 0;}', 'cpp.case-outside-switch'),
    ('int f(){switch(0){case 1:return 1;case 1U:return 2;}return 0;}', 'cpp.duplicate-case'),
    ('int f(){switch(true){case true:return 1;case 1:return 2;}return 0;}', 'cpp.duplicate-case'),
    ('int f(){switch(0){default:return 1;default:return 2;}}', 'cpp.duplicate-default'),
    ('int f(){switch(1.0){case 1:return 1;}return 0;}', 'cpp.switch-condition-type'),
    ('int f(int*p){switch(p){case 0:return 1;}return 0;}', 'cpp.switch-condition-type'),
    ('int f(){switch(1){case 1.0:return 1;}return 0;}', 'cpp.case-type'),
    ('int f(int n){switch(1){case n:return 1;}return 0;}', 'cpp.case-constant-expression'),
    ('int f(){switch(1){case 1/0:return 1;}return 0;}', 'cpp.case-constant-expression'),
    ('int f(){switch(1){case 2147483647+1:return 1;}return 0;}', 'cpp.case-constant-expression'),
    ('int f(){switch(0U){case -1:return 1;}return 0;}', 'cpp.narrowing-conversion'),
    ('enum class E{A};int f(){switch(E::A){case 0:return 1;}return 0;}', 'cpp.invalid-conversion'),
    ('enum class E{A};enum class F{A};int f(){switch(E::A){case F::A:return 1;}return 0;}', 'cpp.invalid-conversion'),
    ('int f(){switch(1){int x=0;case 1:return x;}return 0;}', 'cpp.switch-jump-initialization'),
    ('int f(){switch(2){case 1:int x=0;break;case 2:return x;}return 0;}', 'cpp.switch-jump-initialization'),
    ('int f(){switch(2){case 1:int a[2]={};break;default:return 0;}return 0;}', 'cpp.switch-jump-initialization'),
    ('int f(){switch(1){case 1:}return 0;}', 'cpp.case-statement-required'),
    ('int f(){switch(1){case 1:continue;}return 0;}', 'cpp.jump-outside-loop'),
)

DEFERRED = (
    ('template<class T>int f(T n){switch(n){case 1:return 1;}return 0;}', 'cpp.unsupported.switch-templates'),
    ('int f(){switch(int n=1){case 1:return n;}return 0;}', 'cpp.unsupported.switch-condition-declaration'),
    ('int f(){int n=0;switch(n=1;n){case 1:return n;}return 0;}', 'cpp.unsupported.switch-init-statement'),
    ('struct R{};int f(){R r;switch(r){default:return 0;}}', 'cpp.unsupported.switch-class-conversion'),
    ('int f(){switch(1){{case 1:return 1;}}return 0;}', 'cpp.unsupported.nonlocal-case-label'),
    ('int f(){switch(1){if(false)case 1:return 1;}return 0;}', 'cpp.unsupported.nonlocal-case-label'),
    ('struct R{};int f(){switch(1){R r;case 1:return 1;}return 0;}', 'cpp.unsupported.switch-class-lifetime'),
)


@pytest.mark.parametrize('name,source', CASES, ids=[row[0] for row in CASES])
def test_switch_compiles_twice_identically(name, source):
    units = ((name + '.cpp', source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('source,code', INVALID + DEFERRED)
def test_switch_rules_precede_emission(source, code, monkeypatch):
    from pcc.backend import owned_object_emit
    def forbidden(*args, **kwargs):
        raise AssertionError('unchecked switch reached object emission')
    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((('invalid-switch.cpp', source),))
    assert error.value.code == code


@pytest.mark.integration
@pytest.mark.parametrize('name,source', CASES, ids=[row[0] for row in CASES])
def test_switch_executes_against_explicit_cpp17_reference(name, source, tmp_path, monkeypatch):
    execute_and_record(tmp_path, monkeypatch, ((name + '.cpp', source),), 42)

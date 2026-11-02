"""Shared address-only C lowering, not a template-specific dot-call shortcut."""
import pytest
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only
from tests.cpp.test_cpp_member_functions import DEFERRED_DOT_CASES
from tests.cpp.test_cpp_class_template_members import RETAINED_PROGRAMS

CASES = (
    ('receiver-before-argument-once', 'int state=0;struct S{int n;int add(int v){n+=v;return n;}};S*pick(S*p){if(state!=0)return nullptr;state=1;return p;}int arg(){if(state!=1)return 1;state=2;return 42;}int cpp_test_entry(){S s{0};int r=(*pick(&s)).add(arg());if(state!=2)return 1;return r;}'),
    ('array-index-receiver-once', 'int calls=0;int index(){calls++;return 1;}struct S{int n;int get(){return n;}};int cpp_test_entry(){S a[2];S*p=&a[1];p->n=42;int r=a[index()].get();if(calls!=1)return 1;return r;}'),
    ('nested-subobject-receiver', 'struct S{int n;int get()const{return n;}};struct Outer{S s;};int cpp_test_entry(){const Outer o{{42}};return o.s.get();}'),
    ('pattern-dot-through-dereference', 'template<class T>struct Box{T n;T get(){return n;}T other(){return (*this).get();}};int cpp_test_entry(){Box<int>b{42};return b.other();}'),
)
PROGRAMS = tuple(('original-'+name, ((name+'.cpp', source),)) for name, source in DEFERRED_DOT_CASES)
PROGRAMS += tuple(('template-'+name, units) for name, units in RETAINED_PROGRAMS)
PROGRAMS += tuple((name, ((name+'.cpp', source),)) for name, source in CASES)
PROGRAMS += (
    ('retained-sizeof-dot-original', (
        ('original.cpp', 'struct S{int f();};int g(){S s{};return sizeof(s.f());}'),
        ('entry.cpp', 'int g();int cpp_test_entry(){if(g()==sizeof(int))return 42;return 1;}'),
    )),
)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_object_address_originals_are_byte_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)

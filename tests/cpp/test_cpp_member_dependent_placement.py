"""C++17 [expr.new], [new.delete.placement], [temp.point] owned regressions.

Uses existing placement declaration/real reference-header fixture. Same program
TU bytes, explicitly different standard-header closures; NOT TinyXML2 acceptance.
"""
import struct
import subprocess

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import Execution, compile_host_cpp, write_host_executable
from tests.cpp.test_cpp_class_execution import record_execution
from tests.cpp.test_cpp_placement_new import DECLARATION, compile_reference_with_new
from tests.cpp.test_cpp_owned_execution import owned_only


PROGRAMS = (
    ('pooled-this-address-and-effects', '''
struct Maker;
struct Node{Maker* owner;int value;Node(Maker*,int);Maker* get(){return owner;}};
Node::Node(Maker* p,int n):owner(p),value(n){}
struct Pool{void* storage;int count;void* Alloc(){count+=1;return storage;}};
struct Maker{int count;template<class T>T* make(Pool& p){count+=1;return new(p.Alloc())T(this,42);}};
int cpp_test_entry(){long long raw[8];Pool pool;pool.storage=raw;pool.count=0;
Maker maker;maker.count=0;Node* p=maker.make<Node>(pool);
if(p!=pool.storage||p->get()!=&maker||pool.count!=1||maker.count!=1)return 1;
return p->value;}
'''),
    ('placement-before-constructor-argument-once', '''
void* place(void*p,int*log){*log=*log*10+1;return p;}
int argument(int*log){*log=*log*10+2;return 42;}
struct Node{int value;Node(int,int*);};
Node::Node(int n,int*log):value(n){*log=*log*10+3;}
struct Maker{template<class T>T* make(void*p,int*log){return new(place(p,log))T(argument(log),log);}};
int cpp_test_entry(){long long raw[8];int log=0;Maker maker;
Node* p=maker.make<Node>(raw,&log);if((void*)p!=raw||log!=123)return 1;return p->value;}
'''),
    ('two-pools-two-object-identities', '''
struct Node{int value;Node(int);};Node::Node(int n):value(n){}
struct Maker{template<class T>T* make(void*p,int n){return ::new(p)T(n);}};
int cpp_test_entry(){long long first[8];long long second[8];Maker maker;
Node* a=maker.make<Node>(first,19);Node* b=maker.make<Node>(second,23);
if((void*)a!=first||(void*)b!=second||a==b)return 1;return a->value+b->value;}
'''),
)


@pytest.mark.parametrize('name,body', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_member_placement_owned_artifact_stability(name, body, owned_only):
    units=((name+'.cpp', DECLARATION+body),)
    assert compile_host_cpp(units)==compile_host_cpp(units)


@pytest.mark.integration
@pytest.mark.parametrize('name,body', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_member_placement_actual_owned_storage_and_effects(name,body,tmp_path,monkeypatch):
    units=((name+'.cpp',DECLARATION+body),)
    with monkeypatch.context() as guard:
        def forbidden(*args,**kwargs):
            raise AssertionError('owned member placement invoked external process')
        guard.setattr(subprocess,'Popen',forbidden)
        artifact=compile_host_cpp(units)
        assert artifact==compile_host_cpp(units)
    owned=tmp_path/'owned'
    write_host_executable(artifact,owned)
    oracle=compile_reference_with_new(units,tmp_path/'reference')
    expected=Execution(42,struct.pack('<i',42),b'')
    record_execution(owned,expected)
    record_execution(oracle,expected)


DEFERRED = (
    ('unknown-constructor-effects',
     'struct N{N(int);};struct F{template<class T>T* make(void*p){return new(p)T(7);}};'
     'int f(void*p){F maker;return (void*)maker.make<N>(p)!=p;}',
     'cpp.unsupported.placement-constructor-effects'),
    ('nonreserved-overload',
     'void* operator new(size_type,int*);struct N{N(int);};N::N(int){}'
     'struct F{template<class T>T* make(int*p){return new(p)T(7);}};'
     'int f(int*p){F maker;return (void*)maker.make<N>(p)!=p;}',
     'cpp.unsupported.placement-allocation-overload'),
    ('class-allocation-owner',
     'struct N{static void* operator new(size_type,void*);N(int);};N::N(int){}'
     'struct F{template<class T>T* make(void*p){return new(p)T(7);}};'
     'int f(void*p){F maker;return (void*)maker.make<N>(p)!=p;}',
     'cpp.unsupported.class-allocation-functions'),
)


@pytest.mark.parametrize('name,body,code',DEFERRED,ids=[row[0] for row in DEFERRED])
def test_dependent_placement_never_substitutes_an_unowned_path(name,body,code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse(DECLARATION+body)
    assert error.value.code==code


@pytest.mark.parametrize('name,body,code', (
    ('nonthrowing-contract','void* operator new(unsigned long,void*);','cpp.invalid-placement-function'),
    ('reserved-definition','void* operator new(unsigned long,void*p)noexcept{return p;}','cpp.reserved-placement-definition'),
))
def test_original_allocation_contracts_are_not_relaxed_for_member_templates(name,body,code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse(body)
    assert error.value.code==code


INVALID = (
    ('private-constructor',
     'class N{N(int);};N::N(int){}', 'cpp.member-access'),
    ('no-matching-constructor',
     'struct N{N();};N::N(){}', 'cpp.no-viable-overload'),
)


@pytest.mark.parametrize('name,definition,code',INVALID,ids=[row[0] for row in INVALID])
def test_member_placement_preserves_constructor_rejections(name,definition,code):
    source=(DECLARATION+definition+
            'struct F{template<class T>T* make(void*p){return new(p)T(7);}};'
            'int f(void*p){F maker;return (void*)maker.make<N>(p)!=p;}')
    with pytest.raises(CppDiagnostic) as error:
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source)
    assert error.value.code==code


# No specialization is requested in any of these sources. The nondependent
# expression must be checked while parsing the template definition itself.
COLD_INVALID = (
    ('integer-placement', 'struct Concrete{Concrete();};Concrete::Concrete(){}',
     'new(1) Concrete()', 'cpp.no-viable-overload'),
    ('constructor-not-viable', 'struct Concrete{Concrete(int);};Concrete::Concrete(int){}',
     'new(p) Concrete()', 'cpp.no-viable-overload'),
    ('private-constructor', 'class Concrete{Concrete();};Concrete::Concrete(){}',
     'new(p) Concrete()', 'cpp.member-access'),
    ('deleted-defaulted-constructor', 'struct Concrete{const int n;Concrete()=default;};',
     'new(p) Concrete()', 'cpp.deleted-default-constructor'),
    ('abstract-class', 'struct Concrete{virtual void f()=0;Concrete();};Concrete::Concrete(){}',
     'new(p) Concrete()', 'cpp.abstract-class'),
)


def cold_source(definition, expression):
    return (DECLARATION + definition +
            'struct Cold{template<class Unused>Concrete* f(void*p){return ' +
            expression + ';}};int cpp_test_entry(){return 42;}')


@pytest.mark.parametrize('name,definition,expression,code', COLD_INVALID,
                         ids=[case[0] for case in COLD_INVALID])
def test_cold_nondependent_placement_is_checked(name, definition, expression, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse(cold_source(definition, expression))
    assert error.value.code == code


@pytest.mark.integration
@pytest.mark.parametrize('name,definition,expression,code', COLD_INVALID,
                         ids=[case[0] for case in COLD_INVALID])
def test_cold_nondependent_reference_rejects(name, definition, expression, code, tmp_path):
    import os
    import shutil
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'existing project C++ reference compiler required'
    path = tmp_path / (name + '.cpp')
    path.write_text(cold_source(definition, expression))
    # Genuine installed <new>, no invented implementation. Syntax rejection
    # cannot be confused with a linker failure or absent test entry point.
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors',
                             '-include', 'new', '-fsyntax-only', str(path)],
                            capture_output=True, timeout=30)
    (tmp_path / 'reference.stdout').write_bytes(result.stdout)
    (tmp_path / 'reference.stderr').write_bytes(result.stderr)
    if name in ('private-constructor', 'deleted-defaulted-constructor'):
        # [temp.res]/8: this cold template admits no valid specialization.
        # IFNDR permits either acceptance or rejection; record, don't equate
        # compiler acceptance with source validity or require a diagnostic.
        import json
        (tmp_path / 'cold-observation.json').write_text(json.dumps({
            'case': name, 'classification': 'cold IFNDR observation',
            'returncode': result.returncode, 'command': result.args,
        }, indent=2))
        assert result.returncode >= 0, 'reference compiler terminated by signal'
    else:
        assert result.returncode != 0
        assert result.stderr


@pytest.mark.parametrize('definition,expression,code', (
    ('struct Concrete{Concrete()=delete;};', 'new(p) Concrete()',
     'cpp.unsupported.inline-member-functions'),
    ('struct Concrete{Concrete();};', 'new(p) Concrete()',
     'cpp.unsupported.placement-constructor-effects'),
))
def test_cold_unqualified_constructor_paths_remain_unsupported(definition, expression, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse(cold_source(definition, expression))
    assert error.value.code == code


def test_cold_concrete_placement_with_closed_constructor_is_accepted():
    source = cold_source('struct Concrete{int n;Concrete(int);};Concrete::Concrete(int v):n(v){}',
                         'new(p) Concrete(42)')
    CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source)


@pytest.mark.integration
@pytest.mark.parametrize('name,definition,expression,code',
                         [case for case in COLD_INVALID if case[0] in
                          ('private-constructor', 'deleted-defaulted-constructor')])
def test_concrete_placement_actual_odr_use_reference_rejects(
        name, definition, expression, code, tmp_path):
    import os
    import shutil
    import json
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    # Preserve the exact cold TU as a prefix. Only this NEW control requests
    # the member specialization. Neither original fixture nor PCC gate changes.
    source = cold_source(definition, expression)
    source += '\nConcrete* actual_use(void*p){Cold c;return c.f<int>(p); }\n'
    path = tmp_path / (name + '-odr-use.cpp')
    path.write_text(source)
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors',
                             '-include', 'new', '-fsyntax-only', str(path)],
                            capture_output=True, timeout=30,
                            env={**os.environ, 'LC_ALL': 'C'})
    (tmp_path / 'reference.stdout').write_bytes(result.stdout)
    (tmp_path / 'reference.stderr').write_bytes(result.stderr)
    (tmp_path / 'reference.json').write_text(json.dumps({
        'command': result.args, 'returncode': result.returncode,
        'case': name, 'purpose': 'actual member specialization constructor rejection'
    }, indent=2))
    assert result.returncode > 0
    diagnostic = result.stderr.decode(errors='replace').lower()
    assert 'concrete' in diagnostic
    assert ('private' if name == 'private-constructor' else 'deleted') in diagnostic

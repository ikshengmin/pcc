"""Structural pointer deduction; no substitute for function partial ordering."""
from types import SimpleNamespace
import os
import shutil
import subprocess
import sys

import pytest
from pcc.frontends.cpp import CppParser, CppDiagnostic
from pcc.frontends.cpp.templates import TypeParameter, deduce, substitute, same_type
from pcc.frontends.cpp.types import PointerType as P, ConstType as C

@pytest.mark.parametrize('formal,actual,expected', [
    (lambda t:P(t), P('int'), 'int'),
    (lambda t:P(t), P(C('int')), C('int')),
    (lambda t:P(C(t)), P('int'), 'int'),
    (lambda t:P(C(t)), P(C('int')), 'int'),
    (lambda t:P(P(t)), P(P(C('int'))), C('int')),
    (lambda t:P(t), C(P('int')), 'int'),
])
def test_pointer_deduction_walks_every_level_and_preserves_pointee_cv(formal, actual, expected):
    t=TypeParameter(0,'T',None)
    f=SimpleNamespace(parameters=(formal(t),),template_parameters=(t,))
    result=deduce(f,[actual])
    assert result is not None and same_type(result[0],expected)
    assert same_type(substitute(f.parameters[0],result),formal(expected))

def test_pointer_deduction_rejects_wrong_shape_and_repeated_conflict():
    t=TypeParameter(0,'T',None)
    f=SimpleNamespace(parameters=(P(t),P(t)),template_parameters=(t,))
    assert deduce(f,[P('int'),P('long')]) is None
    assert deduce(f,[P('int'),'int']) is None
    f.parameters=(P(P(t)),)
    assert deduce(f,[P('int')]) is None

SOURCE = '''struct Maker {int f(int){return 11;}
template<class T> int f(T){return 22;}
template<class T> int f(T*){return 33;}};
int main(){Maker m;int i=0;int a=m.f(2);int b=m.f<>(2);int c=m.f(&i);int d=m.f(2L);
return a==11 && b==22 && c==33 && d==22 ? 42 : 1;}'''

def _pointer_member_is_selected(source):
    from pcc.frontends.c.ast import c_ast
    tree = CppParser('x86_64-unknown-linux-gnu').parse(source, 'pointer-order.cpp')
    definitions = {node.decl.name for node in tree.ext if isinstance(node, c_ast.FuncDef)}
    assert '_ZN5Maker1fIiEEiPT_' in definitions
    assert '_ZN5Maker1fIPiEEiT_' not in definitions


def test_real_template_overload_set_selects_pointer_specialization():
    _pointer_member_is_selected(SOURCE)


@pytest.mark.integration
def test_same_source_gcc_object_and_executable_return_42(tmp_path):
    import subprocess
    path=tmp_path/'source.cpp';path.write_text(SOURCE,encoding='utf-8')
    obj=tmp_path/'source.o'; exe=tmp_path/'source'
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    nm = shutil.which('nm')
    assert compiler and nm
    result=subprocess.run([compiler,'-std=c++17','-pedantic-errors','-O0','-c',str(path),'-o',str(obj)],capture_output=True,text=True)
    assert result.returncode == 0,result.stderr
    symbols=subprocess.run([nm,str(obj)],capture_output=True,text=True,check=True).stdout
    assert '_ZN5Maker1fIiEEiPT_' in symbols
    subprocess.run([compiler,str(obj),'-o',str(exe)],check=True)
    assert subprocess.run([str(exe)]).returncode == 42

ORIGINAL_SOURCE = 'extern "C" int printf(const char*, ...);\n\nstruct Maker {int f(int){return 11;}\ntemplate<class T> int f(T){return 22;}\ntemplate<class T> int f(T*){return 33;}\n};\nint main(){Maker m;int i=0;int a=m.f(2);int b=m.f<>(2);int c=m.f(&i);int d=m.f(2L);printf("%d %d %d %d\\n",a,b,c,d);}\n\n'

@pytest.mark.integration
def test_original_bytes_object_inspection(tmp_path):
    # The original program keeps its main and variadic printf. Compare actual
    # relocations without replacing its runtime or claiming it was executed.
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    readelf = shutil.which('readelf')
    assert compiler and readelf, 'C++17 compiler and ELF metadata reader required'
    path = tmp_path / 'original.cpp'
    path.write_bytes(ORIGINAL_SOURCE.encode('utf-8'))
    _pointer_member_is_selected(ORIGINAL_SOURCE)
    expected = ['_ZN5Maker1fEi', '_ZN5Maker1fIiEEiT_', '_ZN5Maker1fIiEEiPT_', '_ZN5Maker1fIlEEiT_']
    for owner in ('owned', 'reference'):
        obj = tmp_path / (owner + '.o')
        if owner == 'owned':
            command = [sys.executable, '-m', 'pcc', '--backend', 'self', '--no-cache',
                       '--target', 'x86_64-unknown-linux-gnu', '--cpp-arg=-std=c++17',
                       '-O0', str(path), '--emit-obj', str(obj)]
        else:
            command = [compiler, '-std=c++17', '-pedantic-errors', '-O0', '-c', str(path), '-o', str(obj)]
        result = subprocess.run(command, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr
        metadata = subprocess.run([readelf, '-rW', str(obj)], capture_output=True, check=True)
        (tmp_path / (owner + '-relocations.txt')).write_bytes(metadata.stdout)
        calls = [line.split()[4] for line in metadata.stdout.decode().splitlines()
                 if 'R_X86_64_PLT32' in line and '_ZN5Maker1f' in line]
        assert calls == expected
    assert path.read_bytes() == ORIGINAL_SOURCE.encode('utf-8')

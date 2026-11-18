"""Real x86-64 ELF and GCC oracles, separate from native pcc1 and whole-project acceptance."""
from pathlib import Path
import os
import platform
import subprocess
import sys

import pytest

from tests.cpp.sizeof_execution import execute_row_pair

FIXTURES = Path(__file__).with_name('fixtures') / 'class_result_sysv'
pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not sys.platform.startswith('linux') or platform.machine() not in ('x86_64', 'amd64'),
    reason='physical transport under test is SysV x86-64 ELF',
)]


def units(*names):
    return tuple((name,(FIXTURES/name).read_text()) for name in names)


H = 'struct H{int n;H(int x):n(x){}H(const H& h):n(h.n){}};'


@pytest.mark.parametrize('sources', [
    units('local-forward.cpp'),
    units('caller.cpp','callee.cpp'),
    (('one.cpp', 'int trace=0; struct Guard{Guard(){trace=trace*10+1;}~Guard(){trace=trace*10+3;}};'
      'struct H{int n;H(int x):n(x){trace=trace*10+2;}H(const H& h):n(h.n){trace=trace*10+4;}};'
      'H make(){Guard g;return H(42);} H copy(const H*p){Guard g;return *p;}'
      'int cpp_test_entry(){H x=make();if(trace!=123)return 3;H y=copy(&x);return trace==123143?y.n:1;}'),),
    (('one.cpp', H+'const H make(){return H(41);} const H f(){return make();}'
      'int cpp_test_entry(){H h=f();++h.n;return h.n;}'),),
    (('one.cpp', 'class H{H(const H&);public:int x;explicit H(int v):x(v){}};'
      'H make(){return H(42);} int cpp_test_entry(){H x=make();return x.x;}'),),
    (('one.cpp', 'int effects=0; int arg(){++effects;return 42;}'
      'struct H{int n;H(int x):n(x){effects=effects*10+2;}H(const H& h):n(h.n){effects=999;}};'
      'H make(){return H(arg());} int cpp_test_entry(){make();if(effects!=12)return 1;'
      'H h=make();return effects==132?h.n:2;}'),),
    (('one.cpp', 'int order=0;struct H{int n;H(int x):n(x){}H(const H&h):n(h.n){}'
      'H add(int x=2)const{return H(n+x);}};'
      'H* get(H*p){order=1;return p;}int rhs(){if(order!=1)return 200;order=2;return 2;}'
      'int cpp_test_entry(){H h(38);H x=get(&h)->add(rhs());H y=x.add();return order==2?y.n:3;}'),),
    (('one.cpp', 'int order=0;struct H{int n;H(int x):n(x){}H(const H&h):n(h.n){}'
      'static H produce(int x){return H(x);}}; H* get(H*p){order=1;return p;}'
      'int rhs(){int x=order;order=2;return x+41;}'
      'int cpp_test_entry(){H h(0);H x=get(&h)->produce(rhs());return order==2?x.n:3;}'),),
], ids=('no-copy-forward','register-stack-cross-tu','closed-C1-D1-order','source-cv','private-copy-elision',
        'discard-side-effects','receiver-before-arguments','static-discarded-receiver'))
def test_self_elf_matches_identical_gcc_sources(sources, tmp_path, monkeypatch):
    monkeypatch.setenv('PCC_CPP_ORACLE','g++')
    execute_row_pair(tmp_path, monkeypatch, sources, value=42)


def command(arguments, *, expected=0):
    result = subprocess.run(list(map(str, arguments)), capture_output=True, timeout=30)
    assert result.returncode == expected, (arguments,result.returncode,result.stdout,result.stderr)
    return result


def object_file(compiler, source, destination):
    if compiler == 'pcc':
        command([sys.executable, '-m','pcc','--backend','self','--no-cache','--cpp-arg=-std=c++17',
                 '-O0',source,'--emit-obj',destination])
    else:
        command(['g++','-std=c++17','-pedantic-errors','-O0','-fno-pie','-c',source,'-o',destination])
    assert destination.read_bytes().startswith(b'\x7fELF')


@pytest.mark.parametrize('family', ['mixed-', ''])
def test_public_object_compilers_interoperate_in_both_directions(family,tmp_path):
    # System link is intentional: it checks actual ABI interoperability; the
    # independent test above uses PCC's own linker with no external compiler.
    for compiler in ('pcc','gpp'):
        for role in ('caller','callee'):
            object_file(compiler,FIXTURES/(family+role+'.cpp'),tmp_path/(compiler+role+'.o'))
    if family:
        driver=[]
    else:
        observer=tmp_path/'observe.cpp'
        observer.write_text('#include <cstdio>\nint cpp_test_entry();int main(){unsigned v=cpp_test_entry();'
                            'for(int i=0;i<4;i++)std::putchar(v>>(8*i));return v;}')
        driver=[observer]
    for caller in ('gpp','pcc'):
        for callee in ('gpp','pcc'):
            exe=tmp_path/(caller+'-'+callee)
            command(['g++','-no-pie',*driver,tmp_path/(caller+'caller.o'),tmp_path/(callee+'callee.o'),'-o',exe])
            result=command([exe],expected=(0 if family else 42))
            assert result.stdout == (b'' if family else b'*\0\0\0') and result.stderr == b''

@pytest.mark.parametrize('filename,code', [
    ('destruction-boundary.cpp','class-value-abi'),
    ('receiver-boundary.cpp','class-value-abi'),
    ('named-copy-boundary.cpp','class-result-prvalue'),
])
def test_valid_cxx17_outside_this_lifetime_slice_has_a_truthful_boundary(filename, code, tmp_path):
    from pcc.frontends.cpp import CppDiagnostic, CppParser
    from tests.cpp.owned_execution import Execution, assert_exact, compile_reference_cpp, run_executable
    originals = units(filename)
    reference = compile_reference_cpp(originals,tmp_path/'gcc',compiler='g++')
    assert_exact(run_executable(reference),Execution(42,b'*\0\0\0',b''))
    with pytest.raises(CppDiagnostic) as exc:
        CppParser('x86_64-unknown-linux-gnu').parse(originals[0][1],filename)
    assert exc.value.code == 'cpp.unsupported.' + code


def test_amd64_indirect_result_returns_callers_exact_destination_pointer(tmp_path):
    # The assembly observer is deliberately assembled/linked by system tools.
    # It does not claim that PCC's assembler supports this observer's complete ISA.
    # Both real C++ TUs compile through the public interface for each compiler.
    symbols = {
        'MAKE':'probe::make(int*, double, long, long, long, long, long, long, long)',
        'PRESS':'probe::Handle::press(double, long, long, long, long, long, long, long) const',
        'COPY':'probe::Handle::copy() const',
        'CTOR':'probe::Handle::Handle(long, int*)',
    }
    observed = {}
    for compiler in ('pcc','gpp'):
        for role in ('assembly-caller','callee'):
            object_file(compiler,FIXTURES/(role+'.cpp'),tmp_path/(compiler+role+'.o'))
        nm=command(['nm','--defined-only',tmp_path/(compiler+'callee.o')]).stdout.decode()
        raw=[line.split()[-1] for line in nm.splitlines() if len(line.split())>=3]
        decoded=command(['c++filt',*raw]).stdout.decode().splitlines()
        mapping={}
        for name,signature in symbols.items():
            matches=[sym for sym,full in zip(raw,decoded) if full==signature and (name!='CTOR' or 'C1' in sym)]
            assert len(matches)==1,(name,matches)
            mapping[name]=matches[0]
        observed[compiler]=mapping
    assert observed['pcc']==observed['gpp']
    asm=(FIXTURES/'rax_oracle.s.in').read_text()
    for name,mangled in observed['gpp'].items():
        asm=asm.replace('@'+name+'@',mangled)
    assert '@' not in asm
    observer=tmp_path/'result_observer.s';observer.write_text(asm)
    main=tmp_path/'main.cpp';main.write_text('int cpp_test_entry();int main(){return cpp_test_entry();}')
    for compiler in ('pcc','gpp'):
        exe=tmp_path/(compiler+'-result')
        command(['g++','-no-pie',main,observer,tmp_path/(compiler+'assembly-caller.o'),
                 tmp_path/(compiler+'callee.o'),'-o',exe])
        result=command([exe],expected=42)
        assert result.stdout==b'' and result.stderr==b''

"""C++17 <stdlib.h> uses the same owned C ABI and standard overload entities."""
from pathlib import Path
import platform
import subprocess
import sys

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.driver import preprocess_cpp

TARGET = 'x86_64-unknown-linux-gnu'
POSITIVE = '''#include <stdlib.h>
#include <cstdlib>
int main(){return ::abs(-::atoi("25"))+std::atoi("17");}
'''


def _compile(text, filename, target=TARGET):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    units = CEvaluator(backend='self', target_triple=target).compile_translation_units(
        [TranslationUnit(filename, None, text)], use_system_cpp=False,
        frontend_opt_level=0, use_compile_cache=False,
    )
    ir = units[0][1]
    return ir, emit_owned_object(ir, target)


@pytest.mark.parametrize('includes', [('<stdlib.h>', '<cstdlib>'), ('<cstdlib>', '<stdlib.h>'),
                                      ('<stdlib.h>', '<stdlib.h>')])
def test_stdlib_exposes_the_same_global_and_std_overload_entities(includes, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('owned header preprocessing launched an external compiler')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    source = '\n'.join('#include ' + header for header in includes) + '\n'
    pp = preprocess_cpp(source, target_triple=TARGET)
    assert pp.count('using __pcc_c_exit_handler') == 1
    parser = CppParser(target_triple=TARGET)
    parser.parse(pp, 'stdlib-declarations.cpp')
    standard = parser.scope.names['std'].namespace_scope.names
    for name, count in [('atexit', 2), ('at_quick_exit', 2), ('bsearch', 2), ('qsort', 2),
                        ('abs', 6), ('div', 3)]:
        global_group = parser.scope.names[name].overloads
        assert len(global_group) == len(standard[name].overloads) == count
        assert set(global_group) == set(standard[name].overloads)
        assert not any(symbol.defined for symbol in global_group)
    for name in ('size_t', 'div_t', 'ldiv_t', 'lldiv_t'):
        assert parser.scope.names[name] is standard[name]
    for name in ('atoi', 'malloc', 'free', 'strtol'):
        assert parser.scope.names[name].overloads == standard[name].overloads
        assert all(symbol.language_linkage == 'C' for symbol in standard[name].overloads)


def test_stdlib_selects_full_width_overload_without_narrowing_to_c_int(monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external compiler invoked'))
    text = '#include <stdlib.h>\nint main(){long x=::abs(-4294967296L);return x==4294967296L?42:1;}'
    ir, obj = _compile(text, 'wide.cpp')
    assert 'call i64 (i64) @_ZSt3absl(' in ir
    assert 'declare i64 @_ZSt3absl(i64)' in ir
    assert 'call i32 (i32) @abs(' not in ir
    from pcc.backend.elf_x86_64 import parse_relocatable
    symbol, = [sym for sym in parse_relocatable(obj).symbols if sym.name == '_ZSt3absl']
    # The declaration alone cannot claim a C++ runtime provider for this overload.
    assert symbol.section_index == 0


@pytest.mark.parametrize('tail,code', [
    ('int main(){::abort();return 0;}', 'cpp.unsupported.exceptions'),
    ('int main(){::div(17L,2L);return 0;}', 'cpp.unsupported.class-value-abi'),
    ('int main(){::strtold(nullptr,nullptr);return 0;}', 'cpp.unsupported.extended-float'),
    ('void cleanup(){} int main(){::atexit(cleanup);return 0;}', 'cpp.unsupported.syntax'),
    ('int main(){return RAND_MAX;}', 'cpp.incomplete-class'),
    ('int main(){return MB_CUR_MAX;}', 'cpp.incomplete-class'),
])
def test_stdlib_does_not_erase_unimplemented_runtime_contracts(tail, code):
    pp = preprocess_cpp('#include <stdlib.h>\n' + tail, target_triple=TARGET)
    with pytest.raises(CppDiagnostic) as error:
        CppParser(target_triple=TARGET).parse(pp)
    assert error.value.code == code


def run_stdlib_native(directory, archive, monkeypatch, target):
    from pcc.backend.owned_elf_link import link_inputs
    obj, exe = directory / 'stdlib.o', directory / 'stdlib'
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external compiler/linker invoked'))
        ir, image = _compile(POSITIVE, 'stdlib.cpp', target)
        assert ir.count('call i32 (ptr) @atoi(') == 2
        obj.write_bytes(image)
        link_inputs(target=target, output=str(exe), objects=[str(obj)], archives=[str(archive)])
    proc = subprocess.run([str(exe)], capture_output=True, timeout=10)
    assert (proc.returncode, proc.stdout, proc.stderr) == (42, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if sys.platform == 'linux' and platform.machine().lower() in (
    'amd64', 'x86_64', 'aarch64', 'arm64') else 'requires owned Linux stdlib runtime execution')
def test_stdlib_global_and_standard_atoi_and_abs_execute(tmp_path, pcc_runtime_archive, monkeypatch):
    from pcc.frontends.python.pipeline_targets import host_target_triple
    run_stdlib_native(tmp_path, Path(pcc_runtime_archive), monkeypatch, host_target_triple())

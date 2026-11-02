"""Same-source native/reference obligations for class unnamed enums."""
import pytest

from tests.cpp.sizeof_execution import execute_public_row_pair, execute_row_pair
from tests.cpp.test_cpp_anonymous_class_enums import PROGRAMS, PRIVATE_SET_PROGRAM, INVALID_ORIGINAL_PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[item[0] for item in PROGRAMS])
def test_anonymous_enum_originals_execute_against_real_cpp17_reference(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
def test_original_private_enum_after_set_executes_through_owned_public_route(tmp_path, monkeypatch):
    execute_public_row_pair(PRIVATE_SET_PROGRAM, 0, tmp_path, monkeypatch)


@pytest.mark.integration
def test_unnamed_enum_abi_symbols_match_genuine_cpp17_reference(tmp_path, monkeypatch):
    import json
    import os
    import re
    import shutil
    import subprocess

    from pcc.backend.elf_x86_64 import parse_relocatable
    from tests.cpp.test_cpp_enum_execution import owned_artifact

    source = ('struct S{enum{Unused};enum Named{};enum{A};enum{B};};'
              'int f(decltype(S::A)x,S*p){return 0;}'
              'int f(S*p,decltype(S::B)x){return 0;}'
              'int f(decltype(S::A)x,decltype(S::A)y){return 0;}'
              'int cpp_test_entry(){return 0;}')
    artifact = owned_artifact((('unnamed-enum-symbols.cpp', source),), monkeypatch)
    unit, assembly = tmp_path/'unnamed-enum-symbols.cpp', tmp_path/'reference.s'
    unit.write_text(source)
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit external C++17 reference unavailable'
    command = [compiler, '-std=c++17', '-pedantic-errors', '-O0', '-S', str(unit), '-o', str(assembly)]
    result = subprocess.run(command, capture_output=True, timeout=20)
    (tmp_path/'reference.stdout.bin').write_bytes(result.stdout)
    (tmp_path/'reference.stderr.bin').write_bytes(result.stderr)
    (tmp_path/'reference-command.json').write_text(json.dumps({'command':command, 'returncode':result.returncode}))
    assert result.returncode == 0, result.stderr
    reference = set(re.findall(r'^\s*\.globl\s+(_Z\w+)', assembly.read_text(), re.M))
    actual = {symbol.name for symbol in parse_relocatable(artifact.objects[0]).symbols
              if symbol.binding == 1 and symbol.section_index and symbol.name.startswith('_Z')}
    (tmp_path/'owned.o').write_bytes(artifact.objects[0])
    (tmp_path/'exports.json').write_text(json.dumps({'owned':sorted(actual), 'reference':sorted(reference)}, indent=2))
    assert actual == reference and len(actual) == 4


@pytest.mark.integration
@pytest.mark.parametrize('name,units', INVALID_ORIGINAL_PROGRAMS, ids=[item[0] for item in INVALID_ORIGINAL_PROGRAMS])
def test_original_empty_enum_sources_are_rejected_by_owned_and_genuine_cpp17(name, units, tmp_path):
    import json
    import os
    import shutil
    import subprocess

    from pcc.frontends.cpp import CppDiagnostic, CppParser

    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit external C++17 reference unavailable'
    for filename, source in units:
        unit = tmp_path/filename
        unit.write_text(source)
        with pytest.raises(CppDiagnostic) as error:
            CppParser().parse(source, filename)
        assert error.value.code == 'cpp.enum-member-name-required'
        command = [compiler, '-std=c++17', '-pedantic-errors', '-fsyntax-only', str(unit)]
        result = subprocess.run(command, capture_output=True, timeout=20)
        unit.with_suffix('.stdout.bin').write_bytes(result.stdout)
        unit.with_suffix('.stderr.bin').write_bytes(result.stderr)
        unit.with_suffix('.reference.json').write_text(json.dumps({'command':command, 'returncode':result.returncode}))
        assert result.returncode != 0
        assert b'empty unnamed enum' in result.stderr

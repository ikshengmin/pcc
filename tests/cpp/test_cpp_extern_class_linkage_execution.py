"""Extern class declarations: no storage, real unresolved use, external owner.

Same consumer bytes go through host PCC and explicit G++ C++17. The independent
provider is compiled by G++, never counted as PCC class-definition support.
Its unmodified object is inspected with GNU readelf and linked with G++:
the owned reader does not admit the GNU .comment section's metadata.
No native compiler provisioning, runtime archive, fallback or ABI-list change.
"""
import builtins
from contextlib import contextmanager
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess

import pytest

from pcc.backend.elf_x86_64 import (
    ElfError, SHF_ALLOC, SHF_EXECINSTR, SHN_UNDEF, STT_OBJECT,
    emit_relocatable, link_static_executable, parse_relocatable,
)
from tests.cpp.owned_execution import (
    Execution, HostArtifact, TARGET, compile_reference_cpp, entry_definition,
    write_host_executable,
)
from tests.cpp.test_cpp_class_execution import record_execution

pytestmark = pytest.mark.pcc_gate(platforms=('linux',), machines=('x86_64',))
FIXTURES = Path(__file__).parent / 'fixtures' / 'extern_class_linkage'
OBJECT = '_ZN7fixture5valueE'
UNUSED = '_ZN7fixture6unusedE'


def unit(name):
    return name, (FIXTURES / name).read_text(encoding='utf-8')


@contextmanager
def owned_guard(monkeypatch):
    original_import = builtins.__import__
    def checked_import(name, *args, **kwargs):
        if name == 'llvmlite' or name.startswith('llvmlite.'):
            raise AssertionError('owned extern-class compilation imported LLVM')
        return original_import(name, *args, **kwargs)
    def forbidden(*args, **kwargs):
        raise AssertionError('owned extern-class compilation launched a process')
    with monkeypatch.context() as guard:
        guard.setenv('PCC_NO_AUTO_PCC1', '1')
        guard.setenv('PCC_TEST_NO_NATIVE_PROVISIONING', '1')
        guard.setattr(builtins, '__import__', checked_import)
        guard.setattr(subprocess, 'Popen', forbidden)
        yield


def owned_object(source_unit):
    # Same bounded parser/codegen/object owners as compile_host_cpp, stopping
    # before linkage so undefined references can be inspected, not stubbed.
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.cpp import CppParser
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator, postprocess_ir_text
    from pcc.frontends.c.passes.context import PassContext
    name, source = source_unit
    parser = CppParser(target_triple=TARGET)
    ast = parser.parse(source, name)
    generator = CCodeGenerator(translation_unit_name=name,
        pass_ctx=PassContext(opt_level=0, target_triple=TARGET))
    generator.generate_code(ast)
    ir = postprocess_ir_text(str(generator.module))
    return ir, emit_owned_object(ir, TARGET), entry_definition(parser.scope.lookup('cpp_test_entry'))


def startup(entry):
    from pcc.backend.x86_64_asm_driver import assemble_file
    assert entry and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', entry)
    # Existing owned_execution observer contract: four result bytes plus status.
    return assemble_file('.intel_syntax noprefix\n.text\n.globl _start\n_start:\n'
        'sub rsp,16\ncall ' + entry + '\nmov dword ptr [rsp],eax\n'
        'mov rax,1\nmov rdi,1\nmov rsi,rsp\nmov rdx,4\nsyscall\n'
        'mov edi,dword ptr [rsp]\nmov rax,60\nsyscall\n')


def compiler():
    selected = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert selected, 'explicit C++17 reference compiler is required'
    return selected


def reference_object(source_unit, directory, selected):
    directory.mkdir()
    name, source = source_unit
    path = directory / name
    path.write_text(source, encoding='utf-8', newline='')
    output = directory / (name + '.o')
    result = subprocess.run([selected, '-std=c++17', '-pedantic-errors', '-O0',
        '-fno-pie', '-fno-stack-protector', '-fno-asynchronous-unwind-tables',
        '-c', str(path), '-o', str(output)], capture_output=True, timeout=30)
    (directory / 'compile.stdout').write_bytes(result.stdout)
    (directory / 'compile.stderr').write_bytes(result.stderr)
    assert result.returncode == 0, result.stderr
    assert path.read_bytes() == source.encode('utf-8')
    return output


def assert_unused_object(data):
    obj = parse_relocatable(data)
    names = {symbol.name for symbol in obj.symbols}
    assert UNUSED not in names
    assert not any('Unused' in name or '__cxa_atexit' in name or
                   '_GLOBAL__sub_I' in name for name in names)
    assert not any(symbol.type == STT_OBJECT and symbol.section_index != SHN_UNDEF
                   for symbol in obj.symbols)
    assert not any(section.name in ('.init_array', '.ctors', '.fini_array', '.dtors')
                   for section in obj.sections)
    assert not any(section.flags & SHF_ALLOC and not section.flags & SHF_EXECINSTR
                   and (section.data or section.mem_size) for section in obj.sections)


def assert_external_use(data):
    obj = parse_relocatable(data)
    matches = [(index, symbol) for index, symbol in enumerate(obj.symbols)
               if symbol.name == OBJECT]
    assert len(matches) == 1
    index, symbol = matches[0]
    assert symbol.section_index == SHN_UNDEF and symbol.value == 0
    assert any(relocation.symbol_index == index for section in obj.sections
               for relocation in section.relocations)
    return obj


def reference_observations(path):
    """Read original GNU objects, without stripping/repacking their sections."""
    reader = shutil.which('readelf')
    assert reader, 'GNU readelf is required for the independent ELF observations'
    before = path.read_bytes()
    reports = {}
    for kind, option in (('symbols', '--symbols'), ('relocs', '--relocs'), ('sections', '--section-headers')):
        result = subprocess.run([reader, '--wide', option, str(path)],
                                capture_output=True, timeout=30)
        path.with_suffix('.' + kind + '.txt').write_bytes(result.stdout)
        assert result.returncode == 0, result.stderr
        reports[kind] = result.stdout.decode('utf-8')
    assert path.read_bytes() == before
    symbols = []
    for line in reports['symbols'].splitlines():
        fields = line.split()
        if len(fields) == 8 and fields[0].rstrip(':').isdigit():
            symbols.append(dict(name=fields[7], value=int(fields[1], 16),
                                size=int(fields[2]), type=fields[3], ndx=fields[6]))
    assert symbols, reports['symbols']
    return reports, symbols


def assert_reference_external_use(path):
    reports, symbols = reference_observations(path)
    matches = [symbol for symbol in symbols if symbol['name'] == OBJECT]
    assert len(matches) == 1 and matches[0]['ndx'] == 'UND' and matches[0]['value'] == 0
    assert any(OBJECT in line.split() for line in reports['relocs'].splitlines())


def assert_reference_unused(path):
    reports, symbols = reference_observations(path)
    names = {symbol['name'] for symbol in symbols}
    assert UNUSED not in names
    assert not any('Unused' in name or '__cxa_atexit' in name or '_GLOBAL__sub_I' in name
                   for name in names)
    assert not any(symbol['type'] == 'OBJECT' and symbol['ndx'] != 'UND' for symbol in symbols)
    assert not any(name in reports['sections'] for name in
                   ('.init_array', '.ctors', '.fini_array', '.dtors'))
    assert UNUSED not in reports['relocs'] and 'Unused' not in reports['relocs']
    assert '__cxa_atexit' not in reports['relocs']


@pytest.mark.integration
def test_unused_extern_class_has_no_storage_construction_or_initialization(tmp_path, monkeypatch):
    original = unit('unused.cpp')
    control_source = unit('unused_without_extern.cpp')[1]
    # Independently retained fixture, not a rewritten compiler input. Verify
    # that precisely the declaration distinguishes these original sources.
    declaration = 'extern const Unused unused;\n'
    assert original[1].count(declaration) == 1
    assert original[1].replace(declaration, '') == control_source
    # Identical TU basename avoids introducing a different ELF STT_FILE name.
    control = (original[0], control_source)
    with owned_guard(monkeypatch):
        ir, data, entry = owned_object(original)
        assert (ir, data, entry) == owned_object(original)
        assert_unused_object(data)
        _, control_data, control_entry = owned_object(control)
        assert_unused_object(control_data)
        assert entry == control_entry and data == control_data
        executable = link_static_executable([startup(entry), parse_relocatable(data)], entry='_start')
    (tmp_path / 'unused-owned.o').write_bytes(data)
    binary = tmp_path / 'unused-owned'
    write_host_executable(HostArtifact((ir,), (data,), executable), binary)
    expected = Execution(42, struct.pack('<i', 42), b'')
    record_execution(binary, expected)
    selected = compiler()
    reference = reference_object(original, tmp_path / 'unused-object-reference', selected)
    assert_reference_unused(reference)
    reference_control = reference_object(control, tmp_path / 'unused-object-control', selected)
    assert_reference_unused(reference_control)
    # Compare the entire untouched objects, including unwind metadata. No
    # section allowlist, stripping or assumption that alloc-only means storage.
    assert reference.read_bytes() == reference_control.read_bytes()
    oracle = compile_reference_cpp((original,), tmp_path / 'unused-reference', compiler=selected)
    record_execution(oracle, expected)


@pytest.mark.integration
@pytest.mark.parametrize('provider_name,value', [('provider.cpp', 42), ('provider_other.cpp', 17)])
def test_extern_class_odr_use_requires_real_provider_and_preserves_address(tmp_path, monkeypatch,
                                                                         provider_name, value):
    originals = (unit('consumer.cpp'), unit('peer.cpp'))
    with owned_guard(monkeypatch):
        rows = tuple(owned_object(source) for source in originals)
        assert rows == tuple(owned_object(source) for source in originals)
        objects = [assert_external_use(row[1]) for row in rows]
        observer = startup(rows[0][2])
        # This TU has no absent helper calls: the object itself must cause the
        # failure, independently of provider_address's separate obligation.
        with pytest.raises(ElfError) as object_missing:
            link_static_executable([objects[1]], entry='peer_address')
        assert str(object_missing.value) == 'undefined static ELF symbols: ' + OBJECT
        with pytest.raises(ElfError) as missing:
            link_static_executable([observer, *objects], entry='_start')
        assert str(missing.value).startswith('undefined static ELF symbols: ')
        assert set(str(missing.value).split(': ', 1)[1].split(', ')) == {
            OBJECT, 'provider_address', 'provider_value'}
    # No invented provider, storage or zero-valued replacement may link.
    assert not (tmp_path / 'missing-owned').exists()
    (tmp_path / 'missing-owned.txt').write_text(str(missing.value), encoding='utf-8')
    for (name, _), (_, data, _) in zip(originals, rows):
        (tmp_path / (name + '.owned.o')).write_bytes(data)
    selected = compiler()
    references = [reference_object(source, tmp_path / ('reference-' + source[0]), selected)
                  for source in originals]
    for path in references:
        assert_reference_external_use(path)
    # Use the same observation-only startup for a real reference link failure.
    observer_path = tmp_path / 'observer.o'
    observer_path.write_bytes(emit_relocatable(observer))
    missing_binary = tmp_path / 'missing-reference'
    result = subprocess.run([selected, '-nostdlib', '-no-pie', '-Wl,-e,_start',
        str(observer_path), *(str(path) for path in references), '-o', str(missing_binary)],
        capture_output=True, timeout=30)
    (tmp_path / 'missing-reference.stdout').write_bytes(result.stdout)
    (tmp_path / 'missing-reference.stderr').write_bytes(result.stderr)
    assert result.returncode != 0 and not missing_binary.exists()
    assert b'fixture::value' in result.stderr or OBJECT.encode() in result.stderr
    assert b'provider_address' in result.stderr and b'provider_value' in result.stderr
    provider_source = unit(provider_name)
    provider = reference_object(provider_source, tmp_path / 'external-provider', selected)
    _, provider_symbols = reference_observations(provider)
    definitions = [symbol for symbol in provider_symbols if symbol['name'] == OBJECT]
    assert len(definitions) == 1 and definitions[0]['ndx'] != 'UND'
    assert definitions[0]['type'] == 'OBJECT' and definitions[0]['size'] == 4
    # Genuine mixed-object interoperability, using the GNU linker. Do not
    # normalize .comment or claim the owned linker accepts this GNU object.
    binary = tmp_path / 'owned-consumers-external-provider'
    mixed_objects = [tmp_path / (name + '.owned.o') for name, _ in originals]
    original_bytes = {path: path.read_bytes() for path in [observer_path, *mixed_objects, provider]}
    result = subprocess.run([selected, '-nostdlib', '-no-pie', '-Wl,-e,_start',
        str(observer_path), *(str(path) for path in mixed_objects), str(provider),
        '-o', str(binary)], capture_output=True, timeout=30)
    (tmp_path / 'mixed-link.stdout').write_bytes(result.stdout)
    (tmp_path / 'mixed-link.stderr').write_bytes(result.stderr)
    assert result.returncode == 0 and binary.is_file(), result.stderr
    assert all(path.read_bytes() == data for path, data in original_bytes.items())
    expected = Execution(value, struct.pack('<i', value), b'')
    record_execution(binary, expected)
    oracle = compile_reference_cpp(originals + (provider_source,), tmp_path / 'full-reference',
                                   compiler=selected)
    record_execution(oracle, expected)

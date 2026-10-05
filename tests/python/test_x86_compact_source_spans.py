import gc
import weakref

import pytest

from pcc.backend import self_backend_ir as ir
from pcc.backend import x86_64_asm_driver as assembler
from pcc.backend import elf_x86_64 as elf


def test_retired_mapping_drops_cached_key_owners():
    class Key(str):
        pass
    key = Key('retired_lookup_key')
    reference = weakref.ref(key)
    mapping = {key: 123}
    assert ir.text_key_mapping_get(mapping, 'absent') is None
    assert id(mapping) in ir._TEXT_KEY_INDEX_CACHE
    del key
    ir.clear_text_key_mapping(mapping)
    assert not mapping
    assert id(mapping) not in ir._TEXT_KEY_INDEX_CACHE
    gc.collect()
    assert reference() is None


@pytest.mark.parametrize('separator', ['\n', '\r\n', '\r', '\v', '\f', '\x1c', '\x85', '\u2028'])
@pytest.mark.parametrize('chunk_size', [1, 7, 65536])
def test_compact_plan_preserves_source_instructions_and_object_bytes(monkeypatch, separator, chunk_size):
    lines = ['.intel_syntax noprefix', '.text', '.globl probe', '.type probe, @function', 'probe:', '  mov eax, 42  ', '\tcall external', '  ret', '.size probe, .-probe']
    text = separator.join(lines) + separator
    monkeypatch.setattr(assembler, '_ASSEMBLY_LINE_CHUNK_CHARS', chunk_size)
    legacy, order, symbols = assembler._parse_file(text)
    compact, compact_order, compact_symbols = assembler._parse_file(text, compact_instructions=True)
    assert compact_order == order and compact_symbols == symbols
    old_text = [entry.text for entry in legacy['.text'].entries if isinstance(entry, assembler._Instruction)]
    spans = [entry for entry in compact['.text'].entries if isinstance(entry, int)]
    assert len(spans) == len(old_text) == 3
    assert all(entry < 0 for entry in spans)
    assert compact['.text'].source_text is text
    assert [assembler._instruction_text(entry, text) for entry in spans] == old_text
    expected = elf.emit_relocatable(assembler.assemble_file('\n'.join(lines) + '\n'))
    assert elf.emit_relocatable(assembler.assemble_file(text)) == expected


def test_long_instruction_line_uses_untruncated_text_record():
    line = 'mov' + ' ' * 65536 + 'eax, 42'
    text = '.intel_syntax noprefix\n.text\nprobe:\n' + line + '\nret\n'
    compact, _, _ = assembler._parse_file(text, compact_instructions=True)
    instructions = [e for e in compact['.text'].entries if isinstance(e, (int, assembler._Instruction))]
    assert isinstance(instructions[0], assembler._Instruction)
    assert assembler._instruction_text(instructions[0], text) == line
    assert isinstance(instructions[1], int)
    assert assembler._instruction_text(instructions[1], text) == 'ret'


@pytest.mark.parametrize('record, source', [(0, 'nop'), (1, 'nop'), (-1, 'nop'), (-65540, 'nop')])
def test_invalid_compact_span_fails_closed(record, source):
    with pytest.raises(assembler.X86EncodeError):
        assembler._instruction_text(record, source)

"""Exact py_obj object execution with test-owned dependency models.

CLI: helper OBJECT OUTPUT_DIRECTORY REPOSITORY.

This is a host-pcc owned-assembler/ELF-linker test, not native pcc1 self-host
qualification. The complete runtime object is linked unchanged in the positive
case. All unused undefined functions trap. Data globals and graph-lock,
provenance, barrier, relocation and safepoint dependencies are controlled models.
No real collector, parallel carrier, moving-object survival or QPS is tested.
The observation adapter asserts current field StorePlan rbp-184 machine layout.
"""
import copy
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import replace
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[3]).resolve()) if len(sys.argv) > 3 else str(Path.cwd()))
from pcc.backend.elf_x86_64 import (
    link_static_executable,
    parse_relocatable,
)
from pcc.backend.owned_elf_link import assemble
R = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).parent
R.mkdir(parents=True, exist_ok=True)
P = Path(sys.argv[1])
obj = parse_relocatable(P.read_bytes())
field = next((s for s in obj.symbols if s.name == 'pcc_gc_store_field_ptr'))
field_bytes = obj.sections[field.section_index - 1].data[field.value:field.value + field.size]
assert field_bytes.count(bytes.fromhex('4c8d9d48ffffff')) == 1, 'harness observation requires exact lea r11,[rbp-184] field StorePlan layout'
undef = {s.name for s in obj.symbols if s.section_index == 0 and s.name}
funcs = {obj.symbols[r.symbol_index].name for sec in obj.sections for r in sec.relocations if r.type == 4} & undef
ir = P.with_suffix('.ll')
if ir.exists():
    funcs |= set(re.findall('^declare .*?@([\\w.$]+)\\(', ir.read_text(), re.M)) & undef
models = {}

def model(n, b):
    models[n] = b
model('pcc_py_gc_minor_graph_lock_for', 'cmp rdi, 3\njne fail\njmp pcc_py_gc_minor_graph_lock')
model('pcc_py_gc_minor_graph_lock', 'cmp DWORD PTR [rip+depth], 0\njne fail\nmov DWORD PTR [rip+depth], 1\nlea rax, [rbp-184]\nmov QWORD PTR [rip+plan], rax\nret')
check = 'mov rax, QWORD PTR [rip+plan]\nmov rcx, QWORD PTR [rip+expected]\ncmp QWORD PTR [rax], rcx\njne fail\ncmp QWORD PTR [rax+112], 4\njne fail\ncmp DWORD PTR [rax+120], 1\njne fail\ncmp DWORD PTR [rax+124], 3\njne fail\n' + ''.join((f'cmp QWORD PTR [rax+{i}], 0\njne fail\n' for i in range(8, 112, 8)))
model('pcc_py_gc_minor_graph_unlock', 'cmp DWORD PTR [rip+depth], 1\njne fail\n' + check + 'mov rcx, QWORD PTR [rip+expected]\ncmp QWORD PTR [rip+slot], rcx\njne fail\nmov DWORD PTR [rip+depth], 0\nmov QWORD PTR [rip+slot], 0\nmov ecx, DWORD PTR [rip+late]\nmov DWORD PTR [rip+pcc_thread_stop_requested], ecx\nadd DWORD PTR [rip+unlocks], 1\nret')
model('pcc_thread_safepoint', 'cmp DWORD PTR [rip+depth], 0\njne fail\n' + check + 'mov DWORD PTR [rip+pcc_thread_stop_requested], 0\nadd DWORD PTR [rip+polls], 1\nret')
model('pcc_gc_note_store', 'cmp DWORD PTR [rip+depth], 1\njne fail\nadd DWORD PTR [rip+stores], 1\nret')
for name, counter in [('pcc_gc_backend4_note_heap_store_locked', 'heap'), ('pcc_gc_note_slot_write_barrier', 'root')]:
    model(name, f'cmp DWORD PTR [rip+depth], 1\njne fail\ncmp rdi, QWORD PTR [rip+expected_owner]\njne fail\nlea rax, [rip+slot]\ncmp rsi, rax\njne fail\ncmp rdx, QWORD PTR [rip+expected]\njne fail\nadd DWORD PTR [rip+{counter}], 1\nret')
model('pcc_gc_pointer_is_managed', 'lea rax, [rip+object]\ncmp rdi, rax\nje managed\nlea rax, [rip+copy_object]\ncmp rdi, rax\nje managed\nmov eax, 0\nret\nmanaged:\nmov eax, 1\nret')
model('pcc_gc_note_relocation_read', 'cmp DWORD PTR [rip+depth], 1\njne fail\nlea rax, [rip+copy_object]\nadd DWORD PTR [rip+relocs], 1\nret')
a = ['.intel_syntax noprefix', '.text', '.globl _start', '_start:']
cases = []
a += ['mov rdi, 0', 'mov rsi, 0', 'mov rdx, 35', 'call pcc_gc_store_field_ptr', 'mov r11d, DWORD PTR [rip+unlocks]', 'cmp r11d, 0', 'jne fail']
for owner, value, forward, mark, late in product((0, 1), ('0', '35', 'object', 'copy_object'), (0, 1), (0, 1), (0, 1)):
    expected = 'copy_object' if value == 'object' and forward else value

    def addr(reg, val):
        return f'lea {reg}, [rip+{val}]' if val in ('object', 'copy_object') else f'mov {reg}, {val}'
    a += ['mov rax, 4294983680'] + [f'mov QWORD PTR [rsp-{i}], rax' for i in range(8, 513, 8)]
    a += [f'mov DWORD PTR [rip+{n}], 0' for n in ('depth', 'polls', 'stores', 'heap', 'root', 'unlocks', 'relocs', 'pcc_thread_stop_requested')]
    a += [f'mov DWORD PTR [rip+late], {late}', f'mov DWORD PTR [rip+pcc_gc_mark_active], {mark}', f'mov DWORD PTR [rip+pcc_gc_forwarding_population], {forward}', addr('rax', expected), 'mov QWORD PTR [rip+expected], rax', 'mov QWORD PTR [rip+slot], 123', f'mov QWORD PTR [rip+expected_owner], {owner * 4096}', f'mov rdi, {owner * 4096}', 'lea rsi, [rip+slot]', addr('rdx', value), 'call pcc_gc_store_field_ptr']
    a += ['mov rax, QWORD PTR [rip+plan]', 'cmp DWORD PTR [rax+124], 7', 'jne fail']
    for n, v in [('polls', late), ('stores', 1), ('unlocks', 1), ('heap', int(owner and value not in ('0', '35'))), ('root', int(not owner and mark and (value not in ('0', '35')))), ('relocs', int(value == 'object' and forward))]:
        a += [f'cmp DWORD PTR [rip+{n}], {v}', 'jne fail']
    cases.append([owner, value, forward, mark, late])
a += ['mov edi, 0', 'mov eax, 60', 'syscall', 'fail:', 'mov edi, 91', 'mov eax, 60', 'syscall']
for trap_index, n in enumerate(sorted(funcs)):
    a += ['.globl ' + n, n + ':', models.get(n, f'mov edi, {100 + trap_index}\nmov eax, 60\nsyscall')]
assert set(models) <= funcs, set(models) - funcs
a += ['.globl model_finish_without_poll', 'model_finish_without_poll:', 'mov DWORD PTR [rdi+124], 7', 'ret']
a += ['.data']
values = {'pcc_gc_config_initialized': 1, 'pcc_gc_backend_selected': 4, 'pcc_gc_backend4_no_refcounts': 1}
for n in sorted(undef - funcs):
    a += ['.p2align 3', '.globl ' + n, n + ':', f'.quad {values.get(n, 0)}']
for n in ('depth', 'plan', 'expected', 'expected_owner', 'slot', 'late', 'unlocks', 'stores', 'polls', 'heap', 'root', 'relocs'):
    a += ['.p2align 3', n + ':', '.quad 0']
a += ['.p2align 4', 'object:', '.quad 0', '.long 0', '.long 2048', '.quad 0', '.p2align 4', 'copy_object:', '.quad 0', '.quad 0', '.quad 0']
asm = re.sub('\\[rip\\+([A-Za-z_][A-Za-z_0-9]*)\\]', '\\1[rip]', '\n'.join(a) + '\n')
(R / 'driver.s').write_text(asm)
lines = []
for line in asm.splitlines():
    m = re.fullmatch('cmp (DWORD|QWORD) PTR (.*), (.*)', line)
    if m:
        reg = 'r11d' if m[1] == 'DWORD' else 'r11'
        lines += [f'mov {reg}, {m[1]} PTR {m[2]}', f'cmp {reg}, {m[3]}']
    else:
        m = re.fullmatch('cmp (\\w+), (DWORD|QWORD) PTR (.*)', line)
        if m:
            reg = 'r11d' if m[2] == 'DWORD' else 'r11'
            lines += [f'mov {reg}, {m[2]} PTR {m[3]}', f'cmp {m[1]}, {reg}']
        else:
            m = re.fullmatch('add DWORD PTR (.*), 1', line)
            if m:
                lines += [f'mov r11d, DWORD PTR {m[1]}', 'add r11d, 1', f'mov DWORD PTR {m[1]}, r11d']
            else:
                lines.append(line)
asm = '\n'.join(lines) + '\n'
(R / 'driver.s').write_text(asm)
driver = assemble(asm, 'x86_64-unknown-linux-gnu')

def run(o, name):
    b = link_static_executable([driver, o])
    p = R / name
    p.write_bytes(b)
    p.chmod(493)
    r = subprocess.run([str(p)], timeout=5)
    return r.returncode
rc = run(obj, 'field-exact')
assert rc == 0, (rc, sorted(funcs)[rc - 100] if 100 <= rc < 100 + len(funcs) else 'assertion/signal')
mut = copy.deepcopy(obj)
f = next((s for s in mut.symbols if s.name == 'pcc_gc_store_field_ptr'))
sec = mut.sections[f.section_index - 1]
rels = [r for r in sec.relocations if f.value <= r.offset < f.value + f.size and mut.symbols[r.symbol_index].name == 'user_py_obj__pcc_gc_store_plan_finish']
assert rels
b = bytearray(sec.data)
for r in rels:
    assert r.type == 4 and r.addend == -4 and (b[r.offset - 1:r.offset + 4] == bytes.fromhex('e800000000'))
    b[r.offset - 1:r.offset + 4] = b'\x90' * 5
sections = list(mut.sections)
sections[f.section_index - 1] = replace(sec, data=bytes(b), relocations=tuple((r for r in sec.relocations if r not in rels)))
mut = replace(mut, sections=tuple(sections))
mutrc = run(mut, 'field-no-finish')
assert mutrc == 91, mutrc
mut2 = copy.deepcopy(obj)
syms = list(mut2.symbols)
syms.append(replace(syms[0], name='model_finish_without_poll', binding=1))
sections = list(mut2.sections)
origsec = sections[f.section_index - 1]
sections[f.section_index - 1] = replace(origsec, relocations=tuple((replace(r, symbol_index=len(syms) - 1) if r in rels else r for r in origsec.relocations)))
mut2 = replace(mut2, symbols=tuple(syms), sections=tuple(sections))
pollrc = run(mut2, 'field-finish-without-poll')
assert pollrc == 91, pollrc
report = {'finish_without_poll_exit': pollrc, 'object': str(P), 'object_sha256': hashlib.sha256(P.read_bytes()).hexdigest(), 'case_count': len(cases), 'positive_exit': rc, 'removed_finish_exit': mutrc, 'removed_calls': len(rels), 'cases': cases, 'ownership': 'host pcc owned assembler and ELF linker, exact full runtime object, no external compiler', 'limits': 'x86_64 Linux; modeled dependencies only; not concurrency, moving collector survival or throughput; post-return state7 is residual machine memory, live-frame root checks occur only at unlock and late safepoint; plan captured via asserted artifact-specific rbp-184 stack layout at graph lock', 'function_sha256': {s.name: hashlib.sha256(obj.sections[s.section_index - 1].data[s.value:s.value + s.size]).hexdigest() for s in obj.symbols if s.name in ('pcc_gc_store_field_ptr', 'user_py_obj__pcc_gc_store_plan_finish')}, 'unused_function_traps': sorted(funcs - set(models))}
(R / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))

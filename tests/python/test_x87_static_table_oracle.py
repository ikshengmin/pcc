"""Real source-derived IR positive and fail-closed x87 oracle mutations.

No native acceptance claim: the unchanged native test still executes real
source-derived lookups under five collectors in the existing gate.
"""
import ast
from pathlib import Path
import re

import pytest

from tests.x87_static_table_oracle import require_x87_static_tables

ROOT = Path(__file__).resolve().parents[2]
INIT = '_pcc_py_module_top_pcc_backend_x86_64_encode'


@pytest.fixture(scope='module')
def real_tables():
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.type_infer import infer_module
    source = (ROOT / 'pcc/backend/x86_64_encode.py').read_text()
    nodes = [node for node in ast.parse(source).body if isinstance(node, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id in ('_X87_FIXED', '_X87_STACK')
                     for t in node.targets)]
    program = '\n'.join(ast.get_source_segment(source, node) for node in nodes)
    typed = infer_module(parse_and_lift(program, 'x87_tables.py', 'pcc.backend.x86_64_encode'))
    codegen = L1CodeGen(typed, ir_scaffold_mode='on')
    codegen._strict_no_libpython = True
    codegen._skip_program_main = True
    section = str(codegen.generate(typed))
    return source, section


def body(section):
    return re.search(r'^define[^\n]*@' + INIT + r'\([^\n]*\)[^\n]*\{\n(.*?)^}',
                     section, re.M | re.S)[1]


def test_real_production_tables_have_static_values_and_registration(real_tables):
    source, section = real_tables
    require_x87_static_tables(source, section, body(section))
    # Reproduce the baseline predicate's disagreement with the established
    # static-literal contract, without changing its threshold to manufacture a pass.
    assert len(re.findall(r'\bcall\b[^\n]*@"?py_bytes_new"?\(', body(section))) == 0


@pytest.mark.parametrize('mutation', [
    'opcode', 'key', 'tag', 'flags', 'owner', 'length', 'nul', 'registration',
    'registration-call', 'constructor', 'size', 'entry', 'duplicate-key',
    'swapped-value', 'tuple-key', 'store', 'initialized', 'publish', 'declaration',
    'dormant-registration', 'wrong-source', 'publish-suffix', 'publish-before-store',
])
def test_corruption_does_not_satisfy_x87_contract(real_tables, mutation):
    source, section = real_tables
    original = section
    if mutation == 'opcode':
        section = section.replace(r'\DE\C1\00', r'\DE\C2\00', 1)
    elif mutation == 'key':
        section = section.replace(r'\66\61\64\64\70\00', r'\66\61\64\64\71\00', 1)
    elif mutation in ('tag', 'flags', 'owner', 'length'):
        old = 'i64 1, i32 17, i32 1, i64 2'
        new = {'tag': 'i64 1, i32 4, i32 1, i64 2',
               'flags': 'i64 1, i32 17, i32 0, i64 2',
               'owner': 'i64 0, i32 17, i32 1, i64 2',
               'length': 'i64 1, i32 17, i32 1, i64 1'}[mutation]
        section = section.replace(old, new, 1)
    elif mutation == 'nul':
        section = section.replace(r'\DE\C1\00', r'\DE\C1\01', 1)
    elif mutation == 'registration':
        section = re.sub(r'@pcc_gc_pointer_register_static_literal\(ptr (@.pybytes.obj.\d+)\)',
                         r'@other( ptr \1)', section, count=1)
    elif mutation == 'registration-call':
        section = section.replace('@_pcc_py_static_literals_pcc_backend_x86_64_encode()',
                                  '@other()', 1)
    elif mutation == 'dormant-registration':
        section = section.replace('define internal void @_pcc_py_static_literals_pcc_backend_x86_64_encode()',
                                  'define internal void @other()', 1)
    elif mutation == 'constructor':
        section = section.replace('@py_dict_new_presized(i64 10)', '@other(i64 10)', 1)
    elif mutation == 'size':
        section = section.replace('@py_dict_new_presized(i64 10)', '@py_dict_new_presized(i64 9)', 1)
    elif mutation in ('entry', 'duplicate-key', 'swapped-value'):
        entries = list(re.finditer(r'@py_dict_set\(ptr (%[\w.]+), ptr (@.pystr.obj.\d+), ptr (@.pybytes.obj.\d+)\)', section))
        first, second = entries[:2]
        new = first[0]
        if mutation == 'entry': new = new.replace('@py_dict_set', '@other')
        elif mutation == 'duplicate-key': new = new.replace(first[2], second[2])
        else: new = new.replace(first[3], second[3])
        section = section[:first.start()] + new + section[first.end():]
    elif mutation == 'tuple-key':
        section = re.sub(r'(@.pcc.static.tuple.\d+ = internal constant) \[2 x ptr\]', r'\1 [3 x ptr]', section, count=1)
    elif mutation == 'store':
        section = section.replace('@pcc_gc_store_root(ptr @.modvar.pcc_backend_x86_64_encode._X87_FIXED,',
                                  '@other(ptr @.modvar.pcc_backend_x86_64_encode._X87_FIXED,', 1)
    elif mutation == 'initialized':
        section = section.replace('store i1 1, ptr @.modvar.pcc_backend_x86_64_encode._X87_FIXED_initialized',
                                  'store i1 0, ptr @.modvar.pcc_backend_x86_64_encode._X87_FIXED_initialized', 1)
    elif mutation == 'publish':
        section = section.replace('@.pyattr._X87_FIXED,', '@.pyattr.wrong,')
    elif mutation == 'publish-suffix':
        section = section.replace('@.pyattr._X87_FIXED,', '@.pyattr._X87_FIXED_wrong,')
    elif mutation == 'publish-before-store':
        publication = re.search(r'^  [^\n]*@py_module_attr_set\([^\n]*@\.pyattr\._X87_FIXED,[^\n]*%dict.new[^\n]*\n', section, re.M)
        assert publication
        line = publication[0]
        section = section[:publication.start()] + section[publication.end():]
        store = re.search(r'^  [^\n]*@pcc_gc_store_root\(ptr @\.modvar.pcc_backend_x86_64_encode._X87_FIXED,', section, re.M)
        section = section[:store.start()] + line + section[store.start():]
    elif mutation == 'declaration':
        section = re.sub(r'^@.pybytes.obj.\d+ =[^\n]*\n', '', section, count=1, flags=re.M)
    else:
        source = source.replace('b"\\xde\\xc1"', 'b"\\xde\\xc2"', 1)
    assert section != original or mutation == 'wrong-source', 'mutation did not apply'
    with pytest.raises(AssertionError):
        require_x87_static_tables(source, section, body(section))

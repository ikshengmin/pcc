"""GNU asm statements are parsed and decided, never erased as text.

GCC manual, "Extended Asm" and "Asm Labels". The owned path used to erase
asm by macro and by a text pass, which also dropped real instruction
templates and the assignments an empty template's tied operands perform. An
empty template now becomes the C it means (c_asm) before AST passes; a real
template, asm goto, file-scope asm and asm labels are explicit gaps.
"""
import os
import shutil
import subprocess

import pytest

from pcc.frontends.c.evaluator.c_evaluator import CEvaluator

PROGRAMS = {
    "barriers": 'int main(void) { int x = 40; asm volatile ("" ::: "memory"); __asm__ __volatile__(""); return x + 2; }',
    "input-only": 'int main(void) { int c = 42; asm ("" :: "r"(c)); return c; }',
    "tied-output": 'int main(void) { int x = 0, y = 42; __asm__ ("" : "=r"(x) : "0"(y)); return x; }',
    "named-tie": 'int main(void) { long x = 0, y = 42; asm ("" : [out] "=r"(x) : "[out]"(y)); return (int)x; }',
    "read-write-keeps-value": 'int main(void) { int x = 42; asm ("" : "+r"(x)); return x; }',
    "operand-evaluated-once":
        'int main(void) { int a[3] = {0, 0, 0}; int *p = a; asm ("" : "+m"(*p++)); return (int)(p - a) * 42; }',
    "tie-converts-to-output-type":
        'int main(void) { char out = 0; int in = 298; asm ("" : "=r"(out) : "0"(in)); return out; }',
}

GAPS = {
    "real-template": ('int main(void) { int x; asm ("mov $1, %0" : "=r"(x)); return x; }',
                      "inline assembly templates"),
    "asm-goto": ('int main(void) { asm goto ("" :::: out); return 0; out: return 1; }', "asm goto"),
    "file-scope": ('asm(".globl foo"); int main(void) { return 0; }', "file-scope asm"),
    "asm-label": ('extern int foo(void) __asm__("bar"); int main(void) { return 0; }', "asm labels"),
}


@pytest.mark.parametrize("name", PROGRAMS)
def test_empty_template_asm_keeps_its_operand_semantics(name):
    assert CEvaluator().evaluate(PROGRAMS[name]) == 42


def test_empty_file_scope_asm_has_no_effect():
    assert CEvaluator().evaluate('asm(""); int main(void) { return 42; }') == 42


@pytest.mark.parametrize("name", GAPS)
def test_unmodeled_asm_is_an_explicit_gap(name):
    from pcc.frontends.c.codegen.c_declaration_state import CodegenError
    source, message = GAPS[name]
    with pytest.raises(CodegenError, match=message):
        CEvaluator().evaluate(source)


def test_asm_is_parsed_with_operands_not_erased():
    from pcc.frontends.c.ast import c_ast
    from pcc.frontends.c.parse import make_c_parser
    function = make_c_parser().parse('void f(int y) { int x; asm volatile ("" : "=r"(x) : "0"(y) : "memory"); }').ext[0]
    statement = function.body.block_items[1]
    assert isinstance(statement, c_ast.AsmStatement) and statement.qualifiers == ["volatile"]
    assert len(statement.outputs) == 1 and len(statement.inputs) == 1 and len(statement.clobbers) == 1


def reference_compiler():
    return shutil.which(os.environ.get("PCC_C_REFERENCE", "cc"))


@pytest.mark.integration
@pytest.mark.skipif(reference_compiler() is None, reason="no reference C compiler")
@pytest.mark.parametrize("name", PROGRAMS)
def test_empty_template_asm_matches_the_reference_compiler(name, tmp_path):
    unit = tmp_path / "original.c"
    unit.write_text(PROGRAMS[name])
    binary = tmp_path / "reference"
    built = subprocess.run([reference_compiler(), "-std=gnu11", "-O0", "-w", str(unit), "-o", str(binary)],
                           capture_output=True, timeout=60)
    assert built.returncode == 0, built.stderr
    assert subprocess.run([str(binary)], capture_output=True, timeout=30).returncode == 42

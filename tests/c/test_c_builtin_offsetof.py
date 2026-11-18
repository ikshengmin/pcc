"""The GNU builtin shares owned offsetof parsing/layout, not a runtime call."""

import platform
import subprocess
import sys

import pytest

from pcc.backend.owned_elf_link import link_inputs
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.driver.project import TranslationUnit
from pcc.frontends.c.codegen.c_declaration_state import CodegenError
from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
from pcc.frontends.c.parse.c_parse_driver import CParseDriver
from pcc.frontends.c.parse.c_parser import CParser
from tests.c.test_c_natural_record_layout import SOURCE as NATURAL_RECORD_SOURCE


@pytest.mark.parametrize('parser_factory', [CParseDriver, CParser])
def test_builtin_offsetof_retains_type_argument_and_builtin_name(parser_factory):
    ast = parser_factory().parse(
        'struct S {char c; int value;};'
        'int main(void){return __builtin_offsetof(struct S,value);}'
    )
    call = ast.ext[1].body.block_items[0].expr
    assert call.name.name == '__builtin_offsetof'
    assert call.args.exprs[0].type.type.name == 'S'
    assert call.args.exprs[1].name == 'value'


def _compile(source):
    units = CEvaluator(backend='self', target_triple='x86_64-unknown-linux-gnu').compile_translation_units(
        [TranslationUnit('offsetof.c', 'offsetof.c', source)],
        use_system_cpp=False, use_compile_cache=False, frontend_opt_level=0,
    )
    return emit_owned_object(units[0][1], 'x86_64-unknown-linux-gnu')


@pytest.mark.pcc_gate(probe=lambda: sys.platform == 'linux' and platform.machine() == 'x86_64')
@pytest.mark.parametrize('source', [
    NATURAL_RECORD_SOURCE,
    'struct S {char c; int value;};'
    'int main(void){return __builtin_offsetof(struct S,value)+38;}',
    'typedef struct {char c; int value;} S;'
    'enum {N=__builtin_offsetof(S,value)};'
    '_Static_assert(N==4,"offset");'
    'static unsigned long off=__builtin_offsetof(S,value);'
    'int main(void){int a[__builtin_offsetof(S,value)];return sizeof(a)+off+22;}',
    'struct Inner {char c; int value;};'
    'struct Outer {char c; struct Inner inner;};'
    'int main(void){return __builtin_offsetof(struct Outer,inner)'
    '+__builtin_offsetof(struct Inner,value)+34;}',
    'struct S {char c; int value;};'
    '_Static_assert((1 ? -1 : __builtin_offsetof(struct S,value)) > 0, "size_t");'
    'int main(void){return __builtin_offsetof(struct S,value)+38;}',
    'union U {char c; int value;};'
    'int main(void){return __builtin_offsetof(union U,value)+42;}',
])
def test_builtin_offsetof_owned_execution(source, tmp_path, monkeypatch):
    # These libc-free originals need only an owned syscall entry. This does not
    # replace the separate CEvaluator/reference integration obligation.
    obj, start, binary = (tmp_path / name for name in ('probe.o', 'start.s', 'probe'))
    start.write_text('.intel_syntax noprefix\n.text\n.globl _start\n_start:\n'
                     '  call main\n  mov edi, eax\n  mov eax, 60\n  syscall\n')
    def forbidden(*args, **kwargs):
        raise AssertionError('owned compilation invoked an external process')
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, 'Popen', forbidden)
        obj.write_bytes(_compile(source))
        link_inputs(target='x86_64-unknown-linux-gnu', output=str(binary),
                    objects=[str(obj)], assembly=[str(start)])
    result = subprocess.run([str(binary)], capture_output=True, timeout=10)
    assert result.returncode == 42
    assert result.stdout == result.stderr == b''


@pytest.mark.parametrize('source, message', [
    ('struct S {int value;};int main(void){return __builtin_offsetof(struct S,missing);}',
     "Field 'missing' not found"),
    ('struct S {unsigned value:3;};int main(void){return __builtin_offsetof(struct S,value);}',
     'bit-field'),
])
def test_builtin_offsetof_rejects_invalid_members(source, message):
    with pytest.raises(CodegenError, match=message):
        _compile(source)

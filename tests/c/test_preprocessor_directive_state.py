"""Directive state and macro token boundaries use only owned preprocessing."""

import pytest

from pcc.frontends.c.preprocessor import (
    _source_lines,
    preprocess,
)


def test_line_directive_expands_operands_and_numbers_following_lines():
    result = preprocess(
        '#define NUMBER 1000\n#define FILE "generated.c"\n#line NUMBER FILE\n'
        '#if __LINE__ != 1000\n#error bad logical line\n#endif\n'
        'int line = __LINE__;\nconst char *file = __FILE__;\n'
    )
    assert 'int line = 1003;' in result
    assert 'const char *file = "generated.c";' in result


def test_gnu_linemarker_and_escaped_filename():
    result = preprocess('# 42 "dir\\\\name\\\".c" 1 3\n__LINE__ __FILE__\n')
    assert result == '42 "dir\\\\name\\\".c"'


def test_splicing_keeps_physical_line_numbers():
    result = preprocess('#define VALUE \\\n  3\nint line = __LINE__;\n')
    assert 'int line = 3;' in result


def test_include_restores_logical_origin_and_once_uses_physical_path(tmp_path):
    (tmp_path / 'inner.h').write_text('#line 700 "virtual/inner.c"\n#pragma once\nint inner = __LINE__;\nconst char *inner_file = __FILE__;\n')
    result = preprocess('#line 90 "virtual/main.c"\n#include "inner.h"\n#include "inner.h"\nint outer = __LINE__;\nconst char *outer_file = __FILE__;\n', base_dir=str(tmp_path))
    assert result.count('int inner = 701;') == 1
    assert 'const char *inner_file = "virtual/inner.c";' in result
    assert 'int outer = 92;' in result
    assert 'const char *outer_file = "virtual/main.c";' in result


def test_skipped_line_directive_does_not_change_origin():
    result = preprocess('#if 0\n#line 700 "wrong.c"\n#endif\n__LINE__ __FILE__\n')
    assert result == '4 "<string>"'


@pytest.mark.parametrize('directive', ['#line wrong', '#line 1 "x" garbage', '# 1 "x" 9'])
def test_malformed_line_directive_is_a_diagnostic(directive):
    with pytest.raises(RuntimeError, match='malformed line'):
        preprocess(directive + '\nint x;\n')


def test_nested_macro_stacks_restore_object_function_and_undefined_states():
    result = preprocess(
        '#define push_macro ignored\n#define pop_macro ignored\n'
        '#pragma push_macro("VALUE")\n#define VALUE(x) x + 1\n'
        '#pragma push_macro("VALUE")\n#undef VALUE\n#define VALUE 99\n'
        'VALUE\n#pragma pop_macro("VALUE")\nVALUE(4)\n'
        '#pragma pop_macro("VALUE")\n#ifdef VALUE\n#error leaked macro\n#endif\n'
        '#pragma pop_macro("VALUE")\nVALUE\n'
    )
    assert result.splitlines() == ['99', '4 + 1', 'VALUE']


def test_macro_stack_crosses_include_and_invalidates_expansion_cache(tmp_path):
    (tmp_path / 'restore.h').write_text('#pragma pop_macro("VALUE")\nVALUE\n')
    result = preprocess('#define VALUE 1\nVALUE\n#pragma push_macro("VALUE")\n#undef VALUE\n#define VALUE 2\nVALUE\n#include "restore.h"\nVALUE\n', base_dir=str(tmp_path))
    assert result.splitlines() == ['1', '2', '1', '1']


@pytest.mark.parametrize(('left', 'right'), [('+', '+'), ('-', '-'), ('<', '<'), ('>', '='), ('&', '&'), ('/', '*'), ('/', '/'), ('.', '.'), ('L', '"x"'), ('1e', '+2')])
def test_empty_paste_does_not_merge_separate_tokens(left, right):
    result = preprocess('#define Q(A,B) A ## B' + right + '\nQ(' + left + ',)\n')
    assert result == left + ' ' + right


def test_paste_can_create_one_operator_and_preserves_adjacent_increment():
    result = preprocess('#define CAT(a,b) a##b\n#define BUMP(x) x++\nCAT(+, +) BUMP(n)\n')
    assert result == '++ n++'


def test_macro_expansion_outer_boundaries_do_not_make_operators_or_comments():
    result = preprocess('#define PLUS +\n#define SLASH /\nPLUS+1 SLASH*value\n')
    assert result == '+ +1 / *value'


def test_preprocessing_numbers_keep_exponent_signs_when_stringified():
    result = preprocess('#define STR(x) #x\nSTR(1e+2) STR(0x1p-3)\n')
    assert result == '"1e+2" "0x1p-3"'


def test_atomic_memory_order_predefines_match_owned_header_contract():
    result = preprocess('__ATOMIC_RELAXED __ATOMIC_CONSUME __ATOMIC_ACQUIRE __ATOMIC_RELEASE __ATOMIC_ACQ_REL __ATOMIC_SEQ_CST\n')
    assert result == '0 1 2 3 4 5'


def test_prescanned_dynamic_macro_alias_is_not_cached_across_source_lines():
    result = preprocess('#define LINE __LINE__\n#define ID(x) x\nID(LINE)\nID(LINE)\n')
    assert result.splitlines() == ['3', '4']


def test_invalid_paste_does_not_silently_become_two_tokens():
    with pytest.raises(RuntimeError, match='invalid token paste'):
        preprocess('#define CAT(a,b) a##b\nCAT(+,*)\n')


def test_source_lines_retains_string_list_contract():
    assert _source_lines('int first;\n/* comment */ int second;\n') == [
        'int first;', '  int second;',
    ]


@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
def test_source_newline_conventions_preserve_line_numbers(newline):
    source = newline.join(['#define X \\', '1', 'int line = __LINE__;', ''])
    assert preprocess(source) == 'int line = 3;'


def test_non_newline_whitespace_does_not_split_source_records():
    assert _source_lines('int\vvalue;\f\n') == ['int\vvalue;\f']

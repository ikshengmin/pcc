"""C++ diagnostics keep the presumed file and line of included text.

N4659 [cpp.include], [cpp.line] and [lex.phases]/1-4. The owned preprocessor
inlines headers and drops directive and skipped lines, so C++ preprocessing
marks every discontinuity with a GNU line marker (`# line "file"`), which the
shared lexer already applies to token coordinates. A unit without such a
discontinuity is unchanged, and C preprocessing never emits markers.
"""

import pytest

from pcc.frontends.c.preprocessor import preprocess
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.driver import preprocess_cpp


TARGET = 'x86_64-unknown-linux-gnu'
HEADER = '// header\n\n\nstruct A {\n  int x;\n  virtual int y;\n};\n'


def diagnose(tmp_path, files, main='m.cpp'):
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    path = str(tmp_path / main)
    text = preprocess_cpp((tmp_path / main).read_text(), base_dir=str(tmp_path),
                          target_triple=TARGET, source_name=path)
    with pytest.raises(CppDiagnostic) as caught:
        CppParser(TARGET).parse(text, path)
    coord = caught.value.coord
    return coord.file, coord.line, coord.column


def test_header_diagnostic_names_the_header_line(tmp_path):
    files = {'h.h': HEADER, 'm.cpp': 'int before;\n#include "h.h"\nint f(){return 1;}\n'}
    assert diagnose(tmp_path, files) == (str(tmp_path / 'h.h'), 6, 3)


def test_main_lines_after_an_include_keep_their_numbers(tmp_path):
    files = {'h.h': 'struct A{int x;};\n\n\n\n', 'm.cpp': '#include "h.h"\n\nint x = ;\n'}
    assert diagnose(tmp_path, files) == (str(tmp_path / 'm.cpp'), 3, 9)


def test_nested_headers_restore_their_including_file(tmp_path):
    files = {'b.h': 'int b;\n', 'a.h': '#include "b.h"\n\nint a = ;\n',
             'm.cpp': '#include "a.h"\nint main(){return 0;}\n'}
    assert diagnose(tmp_path, files) == (str(tmp_path / 'a.h'), 3, 9)


def test_directives_and_skipped_lines_keep_later_numbers(tmp_path):
    files = {'m.cpp': '#define X 1\n#if 0\nskipped\n#endif\nint x = ;\n'}
    assert diagnose(tmp_path, files) == (str(tmp_path / 'm.cpp'), 5, 9)


def test_line_directive_sets_the_presumed_file_and_line(tmp_path):
    files = {'m.cpp': 'int a;\n#line 100 "virtual.cpp"\nint x = ;\n'}
    assert diagnose(tmp_path, files) == ('virtual.cpp', 100, 9)


def test_multiline_macro_invocation_keeps_the_following_line(tmp_path):
    files = {'m.cpp': '#define F(a,b) a+b\nint x = F(1,\n2);\nint y = ;\n'}
    assert diagnose(tmp_path, files) == (str(tmp_path / 'm.cpp'), 4, 9)


def test_header_with_only_directives_keeps_the_next_line(tmp_path):
    files = {'d.h': '#define ONLY 1\n', 'm.cpp': '#include "d.h"\nint x = ;\n'}
    assert diagnose(tmp_path, files) == (str(tmp_path / 'm.cpp'), 2, 9)


def test_unit_without_discontinuity_is_unchanged(tmp_path):
    source = 'int a;\nint b;\n'
    assert preprocess_cpp(source, target_triple=TARGET, source_name='m.cpp') == preprocess_cpp(source, target_triple=TARGET)
    assert '#' not in preprocess_cpp(source, target_triple=TARGET, source_name='m.cpp')


def test_c_preprocessing_emits_no_markers(tmp_path):
    (tmp_path / 'h.h').write_text('int x;\n')
    text = preprocess('#include "h.h"\n#define A 1\nint y;\n', base_dir=str(tmp_path))
    assert '#' not in text


def test_public_driver_reports_the_header_coordinate(tmp_path):
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    (tmp_path / 'h.h').write_text(HEADER)
    main = tmp_path / 'm.cpp'
    main.write_text('#include "h.h"\nint main(){return 0;}\n')
    with pytest.raises(CppDiagnostic) as caught:
        CEvaluator(backend='self', target_triple=TARGET).compile_translation_units(
            [TranslationUnit(str(main), str(main), main.read_text())],
            use_compile_cache=False, use_system_cpp=False)
    assert (caught.value.coord.file, caught.value.coord.line) == (str(tmp_path / 'h.h'), 6)

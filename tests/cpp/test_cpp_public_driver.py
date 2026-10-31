"""Public owned C++ dispatch; host tests are not native pcc1 qualification."""
from pathlib import Path

import pytest

from pcc.driver.project import TranslationUnit
from pcc.frontends.c.evaluator import c_evaluator
from pcc.frontends.cpp import CppDiagnostic
from pcc.frontends.cpp.driver import is_cpp_path, preprocess_cpp

TARGET = "x86_64-unknown-linux-gnu"


def compile_cpp(source, name="example.cpp", **kwargs):
    evaluator = c_evaluator.CEvaluator(target_triple=TARGET, backend="self")
    return evaluator.compile_translation_units(
        [TranslationUnit(name, None, source)], use_compile_cache=False,
        frontend_opt_level=0, **kwargs,
    )


@pytest.mark.parametrize("extension", [".cc", ".cpp", ".cxx", ".c++", ".C"])
def test_cpp_suffix_selects_owned_semantics(extension):
    units = compile_cpp("int pick(int x){return x+1;} int pick(double x){return 9;}"
                        "int main(){return pick(41);}", "unit" + extension)
    assert "@_Z4picki" in units[0][1]
    assert "@_Z4pickd" in units[0][1]
    assert "@main(" in units[0][1]


@pytest.mark.parametrize("name", ["file.c", "file.h", "file.CPP", "cpp", "x.cpp.py"])
def test_other_paths_do_not_select_cpp(name):
    assert not is_cpp_path(name)


def test_owned_cpp_never_calls_c_normalization_or_system_cpp(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("C-only cleanup or external preprocessor invoked")
    monkeypatch.setattr(c_evaluator, "_normalize_preprocessed_source", forbidden)
    monkeypatch.setattr(c_evaluator.CEvaluator, "_system_cpp", forbidden)
    units = compile_cpp("template<typename T> T twice(T x){return x+x;}"
                        "int main(){return twice(21);}")
    assert "_Z5twiceIiET_S0_" in units[0][1]


def test_cpp_preprocessing_sets_language_and_preserves_macros(tmp_path):
    (tmp_path / "value.hpp").write_text("#define ANSWER 42\n")
    source = '#include "value.hpp"\n#if __cplusplus != 201703L\n#error wrong language\n#endif\n'
    source += '#ifdef __STDC_VERSION__\n#error C version leaked\n#endif\nint main(){return ANSWER;}'
    result = preprocess_cpp(source, base_dir=str(tmp_path), cpp_args=["-std=c++17"], target_triple=TARGET)
    assert "return 42" in result
    assert "__cplusplus" not in result


@pytest.mark.parametrize("argument", ["-std=c11", "-std=c++14", "-std=gnu++17", "-ansi"])
def test_other_dialects_fail_explicitly(argument):
    with pytest.raises(ValueError, match="requires -std=c\\+\\+17"):
        preprocess_cpp("int main(){}", cpp_args=[argument])


def test_cpp_attribute_is_not_silently_erased_by_c_cleanup():
    with pytest.raises(CppDiagnostic):
        compile_cpp("[[nodiscard]] int f(){return 1;} int main(){return f();}")


def test_system_preprocessor_request_is_rejected():
    with pytest.raises(ValueError, match="does not support the system preprocessor"):
        compile_cpp("int main(){return 0;}", use_system_cpp=True)


def test_plain_c_stays_unmangled():
    units = compile_cpp("int f(int x){return x;} int main(){return f(42);}", "unit.c")
    assert "@f(" in units[0][1] and "_Z1f" not in units[0][1]


def test_cpp_frontend_sources_participate_in_cache_identity():
    files = c_evaluator._compiler_cache_tracked_files()
    assert any(path.endswith("/frontends/cpp/parser.py") for path in files)
    assert any(path.endswith("/frontends/cpp/driver.py") for path in files)


def test_direct_cli_run_preserves_cpp_translation_unit(tmp_path, monkeypatch):
    from pcc.driver.cli_core import cli_main
    source = tmp_path / "entry.cpp"
    source.write_text("int main(){return 42;}")
    seen = []
    def run(self, units, **kwargs):
        seen.extend(units)
        return 42
    monkeypatch.setattr(c_evaluator.CEvaluator, "evaluate_translation_units", run)
    monkeypatch.setattr(c_evaluator.CEvaluator, "evaluate", lambda *a, **k: pytest.fail("lost language"))
    assert cli_main([str(source), "--no-cache", "-O0"]) == 42
    assert len(seen) == 1 and seen[0].name == "entry.cpp" and seen[0].source == source.read_text()


@pytest.mark.parametrize("expression,truth", [
    ("true", True), ("false", False), ("not false", True),
    ("true and 1", True), ("false or true", True), ("true not_eq false", True),
    ("(3 bitand 1) == 1", True), ("(2 bitor 1) == 3", True),
    ("(3 xor 1) == 2", True), ("(compl 0) < 0", True),
    ("false and (1 / 0)", False), ("true or (1 / 0)", True),
    ("'t' == 116 and true", True), ("UNKNOWN or false", False),
])
def test_cpp_preprocessor_keywords_are_not_undefined_identifiers(expression, truth):
    source = "#if " + expression + "\nint selected;\n#else\nint other;\n#endif"
    result = preprocess_cpp(source, target_triple=TARGET)
    assert ("int selected;" in result) == truth
    assert ("int other;" in result) != truth


def test_cpp_preprocessor_expands_macros_then_keeps_keyword_meaning():
    result = preprocess_cpp("#define BOOL true\n#define OP and\n#if BOOL OP not false\nint selected;\n#endif")
    assert "int selected;" in result


def test_c_preprocessor_keeps_existing_undefined_identifier_behavior():
    from pcc.frontends.c.preprocessor import preprocess
    source = "#if true\nint selected;\n#else\nint other;\n#endif"
    assert "int other;" in preprocess(source)
    assert "int selected;" in preprocess("#define true 1\n" + source)


@pytest.mark.parametrize("operator", ["and_eq", "or_eq", "xor_eq"])
def test_cpp_assignment_operator_is_not_valid_in_if_expression(operator):
    with pytest.raises(RuntimeError, match="failed to evaluate"):
        preprocess_cpp("#if 1 " + operator + " 1\nint selected;\n#endif")


@pytest.mark.parametrize('source,expected', [
    ('int main(){return 0;}', True), ('int main();', False),
    ('#if false\nint main(){return 0;}\n#endif', False),
    ('#define ENTRY main\nint ENTRY(){return 0;}', True),
    ('int helper(){return 2;}', False),
    ('namespace N{int main(){return 1;}}', False),
    ('namespace N{int main(){return 1;}} using namespace N;', False),
    ('namespace N{int main(){return 1;}} using N::main;', False),
    ('namespace {int main(){return 1;}}', False),
    ('namespace N{} int main(){return 0;}', True),
])
def test_cpp_main_discovery_is_owned_and_semantic(source, expected, monkeypatch):
    from pcc.driver.project import _has_main
    def forbidden(*args, **kwargs):
        pytest.fail('C++ discovery invoked system preprocessing')
    monkeypatch.setattr(c_evaluator.CEvaluator, '_system_cpp', forbidden)
    assert _has_main(source, '/tmp/unit.cpp') == expected


def test_cpp_discovery_rejects_unsupported_semantics_without_regex_fallback(monkeypatch):
    from pcc.driver.project import _has_main
    monkeypatch.setattr(c_evaluator.CEvaluator, '_system_cpp', lambda *a, **k: pytest.fail('external tool'))
    with pytest.raises(CppDiagnostic):
        _has_main('namespace N{struct Guard{};} int main(){return 0;}', '/tmp/unit.cpp')


@pytest.mark.parametrize('expression,expected', [
    ('-1 < 1U', 0), ('-1 > 1U', 1), ('1U - 2', 18446744073709551615),
    ('-1U', 18446744073709551615), ('~0U', 18446744073709551615),
    ('18446744073709551615U + 1', 0), ('0xffffffffffffffff + 1', 0),
    ('(1 ? -1 : 1U) > 0', 1), ('(0 ? 1U : -1) > 0', 1),
    ('(1 ? -1 : (1U / 0)) > 0', 1), ('0 && (1 / 0)', 0), ('1 || (1 / 0)', 1),
    ('-9223372036854775807 / 3', -3074457345618258602),
    ('-9223372036854775807 % 3', -1), ('9223372036854775807 / -3', -3074457345618258602),
    ('9223372036854775807 % -3', 1), ('18446744073709551615U / 3', 6148914691236517205),
    ('1 << 63', -9223372036854775808), ('1U << 63', 9223372036854775808),
    ('(-1U) >> 63', 1), ('(-2) >> 1', -1), ('0U - 1 < 1', 0),
])
def test_cpp_if_intmax_signedness_and_exact_arithmetic(expression, expected):
    from pcc.frontends.cpp.preprocessor_expr import evaluate_cpp17_condition
    assert evaluate_cpp17_condition(expression) == expected


@pytest.mark.parametrize('expression', [
    '9223372036854775807 + 1', '18446744073709551616U', '9223372036854775808',
    '1 << 64', '1 >> -1', '-1 << 1', '1UU', '1lL',
    '(-9223372036854775807 - 1) / -1',
])
def test_cpp_if_invalid_or_undefined_live_arithmetic_is_explicit(expression):
    with pytest.raises(RuntimeError, match='failed to evaluate'):
        preprocess_cpp('#if ' + expression + '\nint selected;\n#endif')



@pytest.mark.parametrize("main_suffix,dependency_suffix", [(".cpp", ".c"), (".c", ".cpp")])
def test_cpp_mixed_dependency_rejected_before_legacy_c_discovery(tmp_path, monkeypatch, capsys, main_suffix, dependency_suffix):
    from pcc.driver.cli_core import cli_main
    main = tmp_path / ("main" + main_suffix)
    main.write_text("int main(){return 0;}")
    dependency = tmp_path / ("other" + dependency_suffix)
    dependency.write_text("int main(){return 1;}")
    monkeypatch.setattr(c_evaluator.CEvaluator, "_system_cpp", lambda *a, **k: pytest.fail("external preprocessor"))
    output = tmp_path / "mixed.o"
    assert cli_main([str(main), "--depends-on", str(dependency), "--jobs", "1",
                     "--emit-obj", str(output), "--no-cache", "-O0"]) == 1
    assert not output.exists()
    assert "mixed-language/directory discovery is not implemented" in capsys.readouterr().err



@pytest.mark.parametrize("suffix", [".cpp", ".C"])
def test_c_directory_name_does_not_select_cpp_frontend(tmp_path, monkeypatch, suffix):
    from pcc.driver.cli_core import cli_main
    directory = tmp_path / ("project" + suffix)
    directory.mkdir()
    (directory / "main.c").write_text("int main(void){return 0;}")
    monkeypatch.setattr(c_evaluator.CEvaluator, "_system_cpp", lambda source, *a, **k: source)
    seen = []
    def compile_units(self, units, **kwargs):
        seen.extend(units)
        return []
    monkeypatch.setattr(c_evaluator.CEvaluator, "compile_translation_units", compile_units)
    monkeypatch.setattr(c_evaluator.CEvaluator, "emit_compiled_units", lambda *a, **k: None)
    assert cli_main([str(directory), "--emit-obj", str(tmp_path / "out.o"), "--no-cache", "-O0"]) == 0
    assert len(seen) == 1 and seen[0].name == "__merged_project__.c"
    monkeypatch.setattr(c_evaluator.CEvaluator, "evaluate", lambda *a, **k: 17)
    monkeypatch.setattr(c_evaluator.CEvaluator, "evaluate_translation_units", lambda *a, **k: pytest.fail("directory selected C++"))
    assert cli_main([str(directory), "--no-cache", "-O0"]) == 17


def test_cpp_dependency_discovery_preserves_target_model(tmp_path, monkeypatch):
    from pcc.driver.project import collect_translation_units
    main = tmp_path / "main.cpp"
    main.write_text("#if __SIZEOF_LONG__ == 4\nint main(){return 0;}\n#endif")
    helper = tmp_path / "helper.cpp"
    helper.write_text("int helper(){return 1;}")
    monkeypatch.setattr(c_evaluator.CEvaluator, "_system_cpp", lambda *a, **k: pytest.fail("external preprocessor"))
    units, _ = collect_translation_units(str(main), dependencies=[str(helper)],
                                         target_triple="x86_64-pc-windows-msvc")
    assert len(units) == 2
    with pytest.raises(ValueError, match="must define exactly one main"):
        collect_translation_units(str(main), dependencies=[str(helper)],
                                  target_triple="x86_64-unknown-linux-gnu")


@pytest.mark.parametrize("target,expression,expected", [
    ("x86_64-unknown-linux-gnu", "u'a' < -1", 1),
    ("x86_64-unknown-linux-gnu", "U'a' < -1", 1),
    ("x86_64-unknown-linux-gnu", "U'\\xffffffff' < -1", 1),
    ("x86_64-unknown-linux-gnu", "L'a' < -1", 0),
    ("x86_64-pc-windows-msvc", "L'a' < -1", 1),
    ("x86_64-unknown-linux-gnu", "'a' < -1", 0),
    ("aarch64-unknown-linux-gnu", "'a' < -1", 1),
    ("aarch64-unknown-linux-gnu", "'ab' < -1", 0),
    ("aarch64-unknown-linux-gnu", "L'a' < -1", 1),
])
def test_cpp_if_character_type_preserves_target_signedness(target, expression, expected):
    from pcc.frontends.cpp.preprocessor_expr import evaluate_cpp17_condition
    assert evaluate_cpp17_condition(expression, target) == expected

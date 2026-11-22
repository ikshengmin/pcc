"""GNU weak function definitions in the owned C path.

GCC manual, "Common Function Attributes" (weak): a weak definition is emitted
as a weak symbol that a strong definition elsewhere overrides. The IR carries
``define weak``; the self backend lowers it to the same weak symbol as
``weak_odr`` (Mach-O N_WEAK_DEF, ELF STB_WEAK). Because the body may be
replaced, no pass may rewrite its callers from that body: the IR inliner,
function-attribute inference and the AST eta reduction all leave it alone.
Weak objects and undefined weak references remain explicit gaps.
"""
import os
import shutil
import subprocess

import pytest

from pcc.driver.project import TranslationUnit
from pcc.frontends.c.evaluator.c_evaluator import CEvaluator

PROGRAM = r'''
__attribute__((weak)) int hook(void) { return 30; }
extern int proto(void) __attribute__((weak));
int proto(void) { return 2; }
__attribute__((weak)) int wrap(int x) { return x + 10; }
int main(void) { int (*volatile f)(void) = hook; return f() + proto() + wrap(0); }
'''


def _ir(source, level=2):
    evaluator = CEvaluator()
    units = evaluator.compile_translation_units(
        [TranslationUnit("weak.c", "weak.c", source)], use_system_cpp=False, frontend_opt_level=level)
    return units[0]


def test_weak_definitions_execute():
    assert CEvaluator().evaluate(PROGRAM) == 42


def test_weak_definition_is_weak_in_ir_and_yields_across_units():
    _name, ir_text, _return_type, external_defs = _ir(PROGRAM)
    defines = {line.split("@", 1)[1].split("(", 1)[0]: line for line in ir_text.splitlines()
               if line.startswith("define")}
    assert defines["hook"].startswith("define weak ") and defines["proto"].startswith("define weak ")
    # A body that may be replaced gets no attributes proven from that body.
    assert "willreturn" not in defines["hook"] and "nounwind" not in defines["hook"]
    assert ("weak function", "hook", "hook") in external_defs
    assert ("function", "main", "main") in external_defs


def _entry_units(evaluator, sources):
    return evaluator.compile_translation_units(
        [TranslationUnit(f"entry{i}.c", "", source) for i, source in enumerate(sources)],
        use_system_cpp=False, use_compile_cache=False, frontend_opt_level=2)


@pytest.mark.integration
@pytest.mark.parametrize("definitions,expected", [
    ((("weak", 7), ("strong", 42)), 42), ((("strong", 42), ("weak", 7)), 42),
    ((("weak", 7), ("weak", 42)), 7), ((("weak", 42), ("weak", 7)), 42),
], ids=["weak-strong", "strong-weak", "first-weak", "reversed-weaks"])
def test_result_bridge_preserves_linker_selection(definitions, expected):
    from pcc.backend.definition_linkage import supports_external_odr_function_definitions
    from pcc.frontends.c.codegen.c_declaration_state import CodegenError
    evaluator = CEvaluator(backend="self")
    # Both definitions keep their own body. The third TU can see the entry
    # only as an external function; it must get the same prevailing address.
    sources = [
        ('__attribute__((weak)) ' if kind == "weak" else '') +
        'int main(void) { int (*external_address(void))(void); '
        f'return main == external_address() ? {value} : -999; }}'
        for kind, value in definitions
    ] + ['int main(void); int (*external_address(void))(void) { return main; }']
    if not supports_external_odr_function_definitions(evaluator.target_triple):
        with pytest.raises(CodegenError, match="weak function definitions are not implemented for target"):
            _entry_units(evaluator, sources)
        return
    units = _entry_units(evaluator, sources)
    assert evaluator.evaluate_compiled_translation_units(units, optimize=True) == expected


@pytest.mark.integration
def test_result_bridge_uses_winning_main_with_real_argv():
    from pcc.backend.definition_linkage import supports_external_odr_function_definitions
    from pcc.frontends.c.codegen.c_declaration_state import CodegenError
    evaluator = CEvaluator(backend="self")
    sources = ["__attribute__((weak)) int main(int argc, char **argv) { return 7; }",
               "int main(int argc, char **argv) { return argc == 2 && argv[1][0] == 'X' ? 42 : 1; }"]
    if not supports_external_odr_function_definitions(evaluator.target_triple):
        with pytest.raises(CodegenError, match="weak function definitions are not implemented for target"):
            _entry_units(evaluator, sources)
        return
    assert evaluator.evaluate_compiled_translation_units(_entry_units(evaluator, sources),
                                                         prog_args=["X"]) == 42


@pytest.mark.integration
@pytest.mark.parametrize("ctype,value,expected", [
    ("int", "-1", -1), ("unsigned int", "4294967295U", 4294967295),
])
def test_result_bridge_uses_definition_signedness_not_ir_comments(ctype, value, expected):
    from pcc.backend.definition_linkage import supports_external_odr_function_definitions
    from pcc.frontends.c.codegen.c_declaration_state import CodegenError
    evaluator = CEvaluator(backend="self")
    sources = [f"__attribute__((weak)) {ctype} main(void) {{ return 7; }}",
               f"{ctype} main(void) {{ return {value}; }}"]
    if not supports_external_odr_function_definitions(evaluator.target_triple):
        with pytest.raises(CodegenError, match="weak function definitions are not implemented for target"):
            _entry_units(evaluator, sources)
        return
    units = _entry_units(evaluator, sources)
    name, ir, ret, definitions = units[1]
    opening = next(row for row in ir.splitlines() if row.startswith("define ") and "@main(" in row)
    assert opening.endswith("{")
    ir = ir.replace(opening, opening + ' ; "pcc-c-unsigned-return" is comment text', 1)
    units[1] = name, ir, ret, definitions
    assert evaluator.evaluate_compiled_translation_units(units) == expected


@pytest.mark.parametrize("left,right", [
    ("int main(void) {return 7;}", "double main(void) {return 42.0;}"),
    ("int main(int n) {return n;}", "int main(void) {return 42;}"),
    ("unsigned int main(void) {return 7u;}", "int main(void) {return 42;}"),
])
def test_result_bridge_requires_one_transport_signature(left, right):
    from pcc.backend.definition_linkage import supports_external_odr_function_definitions
    from pcc.frontends.c.codegen.c_declaration_state import CodegenError
    evaluator = CEvaluator(backend="self")
    sources = ["__attribute__((weak)) " + left, right]
    if not supports_external_odr_function_definitions(evaluator.target_triple):
        with pytest.raises(CodegenError, match="weak function definitions are not implemented for target"):
            _entry_units(evaluator, sources)
        return
    with pytest.raises(ValueError, match="incompatible call ABI for coalesced C entry 'main'"):
        evaluator.evaluate_compiled_translation_units(_entry_units(evaluator, sources))


def test_two_strong_entry_definitions_fail_even_for_independently_compiled_units():
    evaluator = CEvaluator(backend="self")
    sources = ["int main(void) {return 7;}", "int main(void) {return 42;}"]
    with pytest.raises(ValueError, match="duplicate external function definition for 'main'"):
        _entry_units(evaluator, sources)
    units = [unit for source in sources for unit in _entry_units(evaluator, [source])]
    with pytest.raises(ValueError, match="multiple strong definitions of C entry 'main'"):
        evaluator.evaluate_compiled_translation_units(units)


@pytest.mark.parametrize("header,diagnostic", [
    ("define internal i32 @main()", "ambiguous C entry"),
    ("define fastcc i32 @main()", "unsupported call ABI"),
    ("define i32 @main(i32 signext %arg)", "unsupported call ABI"),
    ("define i32 @main(i32 %argc, ...)", "unsupported call ABI"),
    ("define i32 @main() #0", "unsupported call ABI"),
])
def test_coalescing_never_invents_an_abi_for_unsupported_ir(header, diagnostic):
    from pcc.backend import BackendUnavailable
    from pcc.backend.definition_linkage import supports_external_odr_function_definitions
    evaluator = CEvaluator(backend="self")
    def unit(name, definition):
        return (name, f'target triple = "{evaluator.target_triple}"\n' + definition
                + " {\nentry:\n ret i32 7\n}\nattributes #0 = { nounwind }\n", None, [])
    units = [unit("fallback", "define weak i32 @main()"), unit("other", header)]
    if diagnostic != "ambiguous C entry" and not supports_external_odr_function_definitions(evaluator.target_triple):
        with pytest.raises(BackendUnavailable, match="external_odr function definitions unavailable"):
            evaluator.evaluate_compiled_translation_units(units)
    else:
        with pytest.raises(ValueError, match=diagnostic):
            evaluator.evaluate_compiled_translation_units(units)


def test_calls_through_a_weak_wrapper_are_not_redirected():
    source = ("int target(int x) { return x + 1; }\n"
              "__attribute__((weak)) int wrap(int x) { return target(x); }\n"
              "int use(int y) { return wrap(y); }\n")
    ir_text = _ir(source)[1]
    use = ir_text[ir_text.index("@use("):]
    use = use[:use.index("\n}")]
    assert "@wrap(" in use and "@target(" not in use


GAPS = {
    "weak-object": ("int w __attribute__((weak)) = 1; int main(void) { return w; }", "GNU attribute 'weak'"),
    "undefined-weak-reference": ("extern int maybe(void) __attribute__((weak));"
                                 "int main(void) { return maybe ? maybe() : 0; }", "weak reference"),
    "static-weak": ("__attribute__((weak)) static int s(void) { return 1; } int main(void) { return s(); }",
                    "must be public"),
}


@pytest.mark.parametrize("name", GAPS)
def test_unmodeled_or_invalid_weak_uses_are_rejected(name):
    from pcc.frontends.c.codegen.c_declaration_state import CodegenError
    source, message = GAPS[name]
    with pytest.raises(CodegenError, match=message):
        CEvaluator().evaluate(source)


def reference_compiler():
    return shutil.which(os.environ.get("PCC_C_REFERENCE", "cc"))


@pytest.mark.integration
# no reference C compiler
@pytest.mark.pcc_gate(probe=lambda: reference_compiler() is not None)
def test_weak_definitions_match_the_reference_compiler(tmp_path):
    unit = tmp_path / "original.c"
    unit.write_text(PROGRAM)
    binary = tmp_path / "reference"
    built = subprocess.run([reference_compiler(), "-std=gnu11", "-O0", "-w", str(unit), "-o", str(binary)],
                           capture_output=True, timeout=60)
    assert built.returncode == 0, built.stderr
    assert subprocess.run([str(binary)], capture_output=True, timeout=30).returncode == 42

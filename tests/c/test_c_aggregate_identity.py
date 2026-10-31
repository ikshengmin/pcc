"""Module-local C aggregate identities must be isolated and reproducible."""

import platform
import subprocess
import sys

import pytest

from pcc.backend.owned_object_emit import emit_owned_object
from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
from pcc.frontends.c.parse import make_c_parser
from pcc.frontends.c.passes.context import PassContext


TARGET = "x86_64-unknown-linux-gnu"


def compile_unit(source, name):
    generator = CCodeGenerator(translation_unit_name=name,
                              pass_ctx=PassContext(opt_level=0, target_triple=TARGET))
    generator.generate_code(make_c_parser().parse(source))
    text = str(generator.module)
    return generator, text, emit_owned_object(text, TARGET)


@pytest.mark.parametrize("name", [None, "same.c", "path/to/unit.c"])
def test_identical_aggregate_input_has_identical_ir_and_object_bytes(name):
    source = ("struct S {int value;};union U {long number;int word;};"
              "int probe(void){struct S s={3};union U u={4};"
              "{struct S {long value;};struct S inner={5};s.value+=inner.value;}"
              "return s.value+u.number;}")
    first, first_text, first_object = compile_unit(source, name)
    # Unrelated intervening aggregate emission must not change any input ID.
    compile_unit("struct Other {char x;};int other(void){return sizeof(struct Other);}", "other.c")
    second, second_text, second_object = compile_unit(source, name)
    assert first.module.context is not second.module.context
    assert first_text == second_text
    assert first_object == second_object


def test_same_spelled_tags_in_separate_contexts_do_not_share_bodies():
    first, first_text, first_object = compile_unit(
        "struct S {int x;};int probe(void){return sizeof(struct S);}", "same.c")
    second, second_text, second_object = compile_unit(
        "struct S {long x;};int probe(void){return sizeof(struct S);}", "same.c")
    first_type = next(iter(first.module.context.identified_types.values()))
    second_type = next(iter(second.module.context.identified_types.values()))
    assert first_type is not second_type
    assert first_type.name == second_type.name
    assert first._ir_type_size(first_type) == 4
    assert second._ir_type_size(second_type) == 8
    assert str(first.module) == first_text
    assert str(second.module) == second_text
    assert first_object != second_object


@pytest.mark.pcc_gate(probe=lambda: sys.platform == "linux"
                      and platform.machine().lower() in ("x86_64", "amd64"))
@pytest.mark.parametrize("names", [("same.c", "same.c"), ("a-b.c", "a_b.c")])
def test_same_spelled_tags_in_separate_units_link_and_execute(tmp_path, monkeypatch, names):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.x86_64_asm_driver import assemble_file

    def forbidden(*args, **kwargs):
        raise AssertionError("owned C aggregate compilation launched an external process")

    with monkeypatch.context() as guard:
        guard.setattr(subprocess, "Popen", forbidden)
        first, first_text, first_object = compile_unit(
            "struct S {long x;};int left(void){struct S s={12};return sizeof(s)+s.x;}", names[0])
        second, second_text, second_object = compile_unit(
            "struct S {int x;};int left(void);"
            "int main(void){struct S s={18};return left()+sizeof(s)+s.x;}", names[1])
        startup = assemble_file(".intel_syntax noprefix\n.text\n.globl _start\n_start:\n"
                                "  call main\n  mov edi, eax\n  mov eax, 60\n  syscall\n")
        binary_bytes = link_static_executable(
            [startup, parse_relocatable(first_object), parse_relocatable(second_object)], entry="_start")
    binary = tmp_path / "same_tags"
    binary.write_bytes(binary_bytes)
    binary.chmod(0o700)
    (tmp_path / "left.ll").write_text(first_text)
    (tmp_path / "right.ll").write_text(second_text)
    result = subprocess.run([str(binary)], capture_output=True, timeout=5)
    assert (result.returncode, result.stdout, result.stderr) == (42, b"", b"")


@pytest.mark.parametrize("names", [("a-b.c", "a_b.c"), ("one/unit.c", "one_unit.c"),
                                   ("\u03b1.c", "\u03b2.c")])
def test_public_combined_ir_has_stable_collision_free_aggregate_names(tmp_path, names):
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator

    sources = ("struct S {int x;};int left(void){return sizeof(struct S);}",
               "struct S {long x;};int right(void){return sizeof(struct S);}")
    units = [TranslationUnit(name, str(tmp_path / name), source)
             for name, source in zip(names, sources)]
    evaluator = CEvaluator(backend="self", target_triple=TARGET)
    outputs = []
    for index in range(2):
        compiled = evaluator.compile_translation_units(
            units, use_system_cpp=False, use_compile_cache=False, frontend_opt_level=0)
        output = tmp_path / ("combined-" + str(index) + ".ll")
        evaluator.emit_compiled_units(compiled, emit_llvm=str(output), optimize=0)
        outputs.append(output.read_bytes())
        declarations = [line for line in output.read_text().splitlines()
                        if line.startswith("%") and " = type " in line]
        assert len(declarations) == 2
        assert len({line.split(" = ")[0] for line in declarations}) == 2
        assert any("i32" in line for line in declarations)
        assert any("i64" in line for line in declarations)
    assert outputs[0] == outputs[1]


def test_public_combined_ir_rejects_duplicate_unit_identity_before_writing(tmp_path):
    from pcc.backend import BackendUnavailable
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator

    units = [TranslationUnit("same.c", str(tmp_path / "left.c"),
                             "struct S {int x;};int left(void){return sizeof(struct S);}"),
             TranslationUnit("same.c", str(tmp_path / "right.c"),
                             "struct S {long x;};int right(void){return sizeof(struct S);}")]
    evaluator = CEvaluator(backend="self", target_triple=TARGET)
    compiled = evaluator.compile_translation_units(
        units, use_system_cpp=False, use_compile_cache=False, frontend_opt_level=0)
    output = tmp_path / "combined.ll"
    with pytest.raises(BackendUnavailable, match="distinct translation-unit names"):
        evaluator.emit_compiled_units(compiled, emit_llvm=str(output), optimize=0)
    assert not output.exists()

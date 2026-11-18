"""Build the one locked C dependency; this is not a Rust object/extension build."""
import os

from scripts.ci_source_frontier_support import bounded_bytes, sha256, verify_snapshot

# A compiler output bound, independent of (and not charged against) frozen
# source input bytes. The real 3.13.0 dependency produces a ~310 KiB object.
MAX_OBJECT_BYTES = 8 * 1024 * 1024
PUBLIC_API = ("yyjson_alc_pool_init", "yyjson_read_opts")


def require_dependency_object(raw):
    from pcc.backend.elf_x86_64 import (
        EM_X86_64, ElfError, SHF_ALLOC, SHF_EXECINSTR, SHT_PROGBITS, STB_GLOBAL, STT_FUNC, parse_relocatable,
    )

    try:
        unit = parse_relocatable(raw)
    except ElfError as error:
        raise ValueError("invalid dependency ELF object: " + str(error)) from error
    if unit.machine != EM_X86_64:
        raise ValueError("dependency object is not ELF x86_64")
    names = []
    for sym in unit.symbols:
        if (sym.name not in PUBLIC_API or sym.binding != STB_GLOBAL or sym.type != STT_FUNC or sym.size <= 0
                or not 1 <= sym.section_index <= len(unit.sections)):
            continue
        section = unit.sections[sym.section_index - 1]
        if (section.type == SHT_PROGBITS and section.flags & (SHF_ALLOC | SHF_EXECINSTR) == SHF_ALLOC | SHF_EXECINSTR
                and sym.value + sym.size <= len(section.data)):
            names.append(sym.name)
    if sorted(names) != list(PUBLIC_API):
        raise ValueError("dependency object needs exactly one section-defined global function for each of "
                         + ", ".join(PUBLIC_API))
    return {"artifact_bytes": len(raw), "artifact_sha256": sha256(raw), "format": "ELF64-LE/ET_REL/x86_64",
            "defined_api_functions": list(PUBLIC_API)}


def emit_c_object(unit, include, output, target, defines):
    """Use the owned evaluator directly: no driver subprocess/system CPP/assembler/linker."""
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    if target != "x86_64-unknown-linux-gnu":
        raise ValueError("the locked yyjson dependency requires the owned x86_64 Linux object target")
    if os.environ.get("PCC_SELF_OBJ") not in (None, "", "pcc"):
        raise ValueError("external C object emitters are not allowed in the source frontier")
    if os.environ.get("PCC_USE_PLY_C_PARSER") == "1":
        raise ValueError("the source frontier requires the owned C parser")
    if output.exists() or output.is_symlink():
        raise ValueError("dependency compiler requires a fresh object output")
    evaluator = CEvaluator(backend="self", target_triple=target)
    # The compiler only receives the same materialized pinned input and owned
    # system headers. No implicit project include directory or compile cache.
    units = evaluator.compile_translation_units(
        [TranslationUnit(str(unit), str(unit), unit.read_bytes().decode("utf-8"))],
        include_dirs=[str(include)], cpp_args=["-D" + name + "=" + value for name, value in defines],
        use_system_cpp=False, use_compile_cache=False,
    )
    evaluator.emit_compiled_units(units, emit_obj=str(output), optimize=False)
    if output.is_symlink() or not output.is_file():
        raise ValueError("PCC emitted no regular dependency object")
    return bounded_bytes(output, MAX_OBJECT_BYTES)


def emit_dependency(directory, snapshot, files, *, unit_name, target, defines):
    """Inputs are the two frozen byte strings already authenticated by our caller."""
    verify_snapshot(snapshot, files)
    unit = snapshot / unit_name
    output = directory / "yyjson.o"
    raw = emit_c_object(unit, unit.parent, output, target, defines)
    verify_snapshot(snapshot, files)
    return {**require_dependency_object(raw), "artifact": output.name,
            "owner": "host pcc0 public C evaluator, owned preprocessor/backend/direct object writer",
            "linked": False, "executed": False}

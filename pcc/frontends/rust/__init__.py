"""Experimental Rust scalar/aggregate frontend; not a safe primary compiler.

No complete borrow checker is implemented. An actual explicit external check
mints a source-bound token enabling reference address/read-write lowering without
the legacy partial loan/move admission pass. The owned compiler never invokes
rustc or consumes MIR; the separately named transition adapter invokes rustc.
"""
from __future__ import annotations

from dataclasses import dataclass

from .certification import ExternalRustcCheck
from .editions import require_edition
from .external_check import (
    CheckedRustSource, ExternalRustcLints, RustcToolchain, RustExternalCheckError,
    check_rust_source, compile_rust_checked,
)
from .builtin_macros import CompileEnvironment
from .configuration import CfgOptions, OwnedRustConfiguration, TargetPlatformFacts
from .moves import MoveChecker
from .semantics import Checker
from .syntax import Parser, RustError
from .targets import DEFAULT_TARGET, LINUX_LAYOUT, resolve_target


@dataclass(frozen=True)
class RustCompilation:
    module: object
    symbols: dict[str, str]
    experimental: bool = True
    borrow_checked_by_pcc: bool = False
    overflow_checks: bool = False
    externally_checked: bool = False
    target_triple: str = DEFAULT_TARGET
    data_layout: str = LINUX_LAYOUT
    standard_marker_contract: object = None
    edition: int = 2021
    external_rustc_lints: ExternalRustcLints | None = None
    crate_cfg_attributes: tuple = ()
    conditional_items: tuple = ()
    # Advisory source metadata only; owned codegen currently applies no hint.
    function_inline_hints: tuple = ()

    @property
    def ir_text(self) -> str:
        return str(self.module)

    def emit_object(self) -> bytes:
        """Emit a real owned ELF or arm64 Mach-O object, not a header rewrite.

        Cross-emission does not execute or link a program on the target machine.
        Manually retargeting the mutable IR module is rejected: select the target
        at compile_rust's public entry so layout and emitter share one contract.
        """
        target = resolve_target(self.target_triple)
        if (self.module.triple != target.triple
                or self.module.data_layout != target.data_layout
                or self.data_layout != target.data_layout):
            raise RustError("Rust module target/layout changed after compilation; recompile with target_triple")
        from pcc.backend.owned_object_emit import emit_owned_object

        return emit_owned_object(self.ir_text, target.triple)


def compile_rust(source: str, *, experimental_rustc_checked: bool = False,
                 overflow_checks: bool = False,
                 rustc_check: CheckedRustSource | None = None,
                 module_name: str = "rust",
                 target_triple: str = DEFAULT_TARGET,
                 edition: int = 2021,
                 cfg: CfgOptions | TargetPlatformFacts | None = None,
                 env: CompileEnvironment | dict | None = None) -> RustCompilation:
    """Lower a rustc-checked, nonheap scalar/aggregate crate into PCC's public IR.

    `cfg` is an explicit owned cfg configuration (rustc's --cfg/--test/-C
    debug-assertions/-C panic) evaluated against PCC's record of the target's
    built-in cfg. TargetPlatformFacts admits just arch/OS/byte-order values,
    never assuming package options are absent. Without cfg, configuration
    still needs an actual CheckedRustSource command.
    `env` is the explicit compile-time environment env!/option_env! read
    (a CompileEnvironment or a str->str mapping); the host environment is
    never consulted.

    Input is admitted by PCC's owned checks alone (types, mutability, moves
    through branches, loops, while let, if let and array for, shared and
    mutable loans with non-lexical liveness, implicit and two-phase reborrows
    for references in bodies, parameters and `&self`/`&mut self` receivers,
    match-guard borrows, exhaustiveness and the arithmetic_overflow lint);
    `experimental_rustc_checked` is accepted for compatibility and has no
    effect. Explicit lifetimes and receiver types, reference returns,
    references in aggregates and the other features listed in README
    "Replacing the rustc admission gate" still need an actual CheckedRustSource
    during that transition. The rustc_check path
    accepts only an adapter-minted CheckedRustSource after the explicit external
    check_rust_source phase. Ordinary provenance records do not authorize this
    path. This owned phase invokes no rustc process and never consumes MIR.
    Edition is an explicit source/options identity (default 2021 for existing
    callers), not proof of complete edition semantics or project dependencies.
    Arithmetic uses Rust's overflow-checks-off wrapping semantics;
    foldable literal/operator overflow is rejected; panic-capable division fails
    closed. This does not claim rustc lint or variable-state constprop parity.
    """
    edition = require_edition(edition)
    if rustc_check is not None and type(rustc_check) is not CheckedRustSource:
        raise RustError("a caller-constructed external record is not proof of checking; use check_rust_source or compile_rust_checked")
    if type(experimental_rustc_checked) is not bool:
        raise RustError("experimental_rustc_checked is a legacy boolean and has no effect")
    if overflow_checks is not False:
        raise RustError("Rust overflow checks require owned panic support, which is not implemented; only overflow_checks=False is supported")
    if type(module_name) is not str:
        raise RustError("invalid Rust module_name: expected a string label")
    target = resolve_target(target_triple)
    if rustc_check is not None:
        rustc_check.validate(source, target_triple=target.triple,
                             overflow_checks=overflow_checks, edition=edition)
    configuration = None
    if cfg is not None:
        if type(cfg) not in (CfgOptions, TargetPlatformFacts):
            raise RustError('compile_rust cfg must be CfgOptions or TargetPlatformFacts')
        configuration = OwnedRustConfiguration(target.triple, edition, cfg)
    environment = env
    if env is not None and type(env) is not CompileEnvironment:
        environment = CompileEnvironment.of(env)
    parser = Parser(source, edition=edition, rustc_check=rustc_check, configuration=configuration,
                    environment=environment)
    functions = parser.parse()
    resolution = None
    records, enums, consts = parser.records, parser.enums, parser.consts + parser.statics
    if parser.modules or parser.crate_aliases or parser.uses:
        from .module_resolution import ModuleResolution, inline_crate
        resolution = ModuleResolution(inline_crate(parser, functions))
        functions, records, enums = resolution.functions, resolution.records, resolution.enums
        consts = resolution.consts + resolution.statics
    if rustc_check is None and parser.crate_lint_attributes:
        from .missing_docs import check_missing_docs
        from .module_resolution import inline_crate
        check_missing_docs(resolution.crate if resolution is not None else inline_crate(parser, functions),
                           parser.crate_lint_attributes, test=cfg is not None and cfg.test)
    from .inline_attributes import collect_inline_hints
    inline_hints = collect_inline_hints(functions, parser.traits)
    if rustc_check is None:
        # References are owned (stages 4-5): sema rejects explicit lifetimes
        # and receiver types, reference returns and references in aggregates,
        # and the move checker owns shared and mutable loans. Slices have no
        # owned layout yet.
        from .reference_admission import reject_slices
        reject_slices(functions, records, enums, parser.implementations, parser.traits)
    checker = Checker(functions, records, enums, parser.implementations, parser.traits, consts=consts,
                      externally_checked=rustc_check is not None, pointer_bits=target.pointer_bits,
                      module_resolution=resolution, crate_policy=parser.crate_policy)
    checker.traits.require_checked_context(rustc_check, source, target.triple, overflow_checks)
    checker.check()
    # A syntactic macro expansion is not an implementation of std::io. Check
    # even uninstantiated/unused functions before issuing an owned compilation.
    for expansion in parser.macro_expansions:
        if expansion.name in {'println', 'eprintln'}:
            raise expansion.missing_runtime()
    if rustc_check is None:
        from .reference_admission import reject_unowned_reference_shapes
        reject_unowned_reference_shapes(checker)
    # rustc's build-time arithmetic_overflow lint, owned in every admission
    # mode (a metadata check never runs it).
    from .known_panics import KnownPanics
    KnownPanics(checker).check(functions)
    marker_context = checker.standard_markers.checked_context(rustc_check, source, target.triple, overflow_checks)
    from .reference_admission import contains_references
    if rustc_check is None or not contains_references(functions, records, enums, parser.implementations,
                                                      parser.traits):
        # Owned moves decide every reference-free program, checked or not;
        # reference-bearing checked input awaits owned loans (stage 4).
        MoveChecker(checker).check()
    from .lowering import Lowerer

    lowerer = Lowerer(functions, checker, module_name, target)
    module = lowerer.lower()
    return RustCompilation(module, dict(lowerer.symbols), externally_checked=rustc_check is not None,
                           target_triple=target.triple, data_layout=target.data_layout,
                           standard_marker_contract=marker_context, edition=edition,
                           external_rustc_lints=(ExternalRustcLints(
                               parser.crate_lint_attributes, rustc_check.stderr,
                               rustc_check.provenance) if rustc_check is not None else None),
                           crate_cfg_attributes=parser.crate_cfg_attributes,
                           conditional_items=parser.conditional_items,
                           function_inline_hints=inline_hints)


__all__ = ["ExternalRustcCheck", "CheckedRustSource", "RustcToolchain",
           "ExternalRustcLints",
           "RustExternalCheckError", "check_rust_source", "compile_rust_checked",
           "RustCompilation", "RustError", "compile_rust", "CfgOptions", "CompileEnvironment", "TargetPlatformFacts"]
